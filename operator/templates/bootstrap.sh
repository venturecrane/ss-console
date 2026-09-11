#!/usr/bin/env bash
# bootstrap.sh — container entrypoint for the Operator customer Machine
#
# Per §6 of the locked build plan and ADRs 0007/0010/0016/0019, this script
# runs a sequenced startup under tini (PID 1, zombie reaper). Steps 3-6 (the
# Honcho data plane) are DEFERRED to Phase 2 per the revised ADR 0016 — see
# that block below — so the Phase-1 sequence is:
#
#   1.  Validate required env vars.
#   2.  Verify (or fetch from R2) /opt/data/customer.yaml.
#   3-6. Honcho data plane — deferred to Phase 2 (no Postgres/Redis/Honcho).
#   7.  Run `hermes-smd bootstrap` (customer.yaml -> per-profile config + SOUL.md).
#   7b. Disable the Hermes curator in each profile config (ADR 0017).
#   8.  Run the safety-substrate invariant checks (Phase A.5 gate).
#   9.  Pause guard.
#   10. customer-sync sidecar (R2 poller) — NOT launched in Phase 1 (the overlay
#       reload path is unimplemented; see that step).
#   11. exec the Hermes gateway for the active persona profile (`-p <slug>`;
#       becomes the foreground child of tini).
#
# Memory (ADR 0016, revised 2026-05-30): Phase 1 runs on Hermes' always-on
# flat-file core (MEMORY.md/USER.md). Honcho (inferred memory) is a swappable
# provider deferred to Phase 2; the customer-owned memory file lives in D1/R2.
#
# Storage model:
#   - customer.yaml is volume-mounted, NOT baked into the image.
#   - Provisioning writes it to R2 at vaults/<slug>/customer.yaml.
#   - First boot: fetch from R2 -> /opt/data/customer.yaml.
#   - Subsequent boots: use the volume copy.
#
# Process supervision:
#   - tini (PID 1) reaps zombies and forwards signals.
#   - The gateway runs as the foreground (exec) child.
#
# Fails fast on any of:
#   - missing required env vars
#   - customer.yaml missing AND not fetchable from R2
#   - `hermes-smd bootstrap` error (bad customer.yaml structure)
#   - safety-substrate invariant test failures

set -euo pipefail

# Activate the Hermes venv for the whole entrypoint. The overlay CLI
# (hermes-smd) and the hermes gateway are installed ONLY in /opt/hermes/.venv;
# bootstrap.sh invokes `hermes-smd` and `hermes` as bare commands. Without the
# venv on PATH, a bare `hermes`/`hermes-smd` would not resolve and the Machine
# would crash at step 7 / step 11.
export PATH="/opt/hermes/.venv/bin:${PATH}"

log() {
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [bootstrap] $*"
}

die() {
  log "FATAL: $*"
  exit 1
}

# Replace a volume dir with a fresh copy of ${src}, TOLERANT of stray root-owned
# files inside the old ${dest}. We `mv` the old dir aside instead of `rm -rf`-ing
# it: a rename only needs write+exec on the (hermes-owned) PARENT dir, not on the
# dir's contents — so a root-owned file left inside (e.g. a __pycache__/*.pyc
# written by a root `ssh console` that imported the overlay) cannot make this fail
# under `set -e` and crash-loop the boot (the ss-console#1285 self-inflicted
# outage: bootstrap runs as hermes and `rm -rf` choked on root-owned .pyc). The
# moved-aside copy is best-effort cleaned; if its root-owned files survive the rm
# that is harmless — it is out of the plugin/hook search path.
replace_dir_tolerant() {
  _rdt_src="$1"; _rdt_dest="$2"
  if [ -e "${_rdt_dest}" ]; then
    mv "${_rdt_dest}" "${_rdt_dest}.stale.$$" \
      || die "could not move aside stale dir ${_rdt_dest} (is its parent hermes-writable?)"
    rm -rf "${_rdt_dest}.stale.$$" 2>/dev/null || true
  fi
  cp -r "${_rdt_src}" "${_rdt_dest}"
}

# NOTE: the wait_for() bounded-retry helper and supervise() restart wrapper
# were removed with the Honcho data plane (steps 3-6); Postgres/Redis/Honcho
# were their only callers. Phase 2 reintroduces supervision when it vendors the
# real Honcho api + deriver processes.

CUSTOMER_SLUG="${CUSTOMER_SLUG:-}"
[ -n "${CUSTOMER_SLUG}" ] || die "CUSTOMER_SLUG env var is unset"

log "Starting bootstrap for customer: ${CUSTOMER_SLUG}"
log "Hermes SHA: $(cat /opt/hermes/HERMES_SHA 2>/dev/null || echo unknown)"

# ============================================================================
# Step 1: validate required env vars
# ============================================================================
# Secrets are set via `fly secrets set` from provision-customer.sh. Never echo
# values; only check presence.
#
# REQUIRED_ENV / OPTIONAL_ENV are GENERATED from operator/contracts/env-consumption.yaml
# (the single source of truth) by operator/bin/gen-env-arrays.py and COPY'd next to this
# script in the image. Sourcing — not hand-maintaining — kills the drift #1324 was: the
# arrays used to be a second copy of the contract's required/optional facts. Per-var
# rationale (why R2_SKILL_BODIES_* are optional + fail-soft, OP-P0-2, the Phase-2 Honcho
# forward-compat) now lives in the contract's `note:` fields. A malformed contract fails
# the generator in CI, never this live boot (the file is a baked, validated build artifact).
_env_arrays="$(dirname "${BASH_SOURCE[0]}")/_env-arrays.generated.sh"
if [ ! -f "${_env_arrays}" ]; then
  die "env-arrays file missing (${_env_arrays}) — image build defect; REQUIRED_ENV/OPTIONAL_ENV are generated from operator/contracts/env-consumption.yaml"
fi
# shellcheck source=/dev/null
source "${_env_arrays}"

for var in "${REQUIRED_ENV[@]}"; do
  if [ -z "${!var:-}" ]; then
    die "Required env var ${var} is unset (set via fly secrets)"
  fi
  log "env check OK: ${var} present"
done

# Per-customer optional env. DWD customers set GOOGLE_SERVICE_ACCOUNT_JSON;
# legacy user-OAuth customers set GOOGLE_TOKEN_JSON. Specific connector skills
# check for the credential they need.
for var in "${OPTIONAL_ENV[@]}"; do
  if [ -n "${!var:-}" ]; then
    log "env check OK: ${var} present (optional)"
  else
    log "env check: ${var} not set (default will apply)"
  fi
done

# ============================================================================
# Step 2: locate the root-owned customer.yaml (fetched by the entrypoint)
# ============================================================================
# Keystone (audit 2026-06-15): the authoritative customer.yaml is fetched from R2
# (the SOURCE OF TRUTH) and OWNED BY ROOT by the entrypoint, BEFORE this script
# drops to the hermes uid. It lives at ${SMD_CUSTOMER_YAML_PATH} inside a
# root-owned dir — world-readable but NOT writable or renameable by this (hermes)
# process. bootstrap no longer fetches it: the former hermes-side fetch produced
# an agent-writable copy on /opt/data, which let the agent rewrite its own trust
# ceiling (the self-loopback hole). bootstrap only READS it to materialize the
# per-profile config; the root applier (ADR 0044) propagates live edits with no
# restart.
CUSTOMER_YAML="${SMD_CUSTOMER_YAML_PATH:-/opt/data/customer.yaml}"
[ -f "${CUSTOMER_YAML}" ] \
  || die "customer.yaml not present at ${CUSTOMER_YAML} (the root entrypoint fetches it before the hermes drop)"
log "Using root-owned customer.yaml: ${CUSTOMER_YAML}"

# ============================================================================
# Step 2a: sync the voice vault to the volume (agent holds NO R2 credential)
# ============================================================================
# The voice plugin reads the customer's content-free voice samples from R2
# (vaults/<slug>/voice/). We fetch them HERE at boot — with the same boot-time
# creds used for the customer.yaml fetch above, which are STRIPPED before the
# gateway exec (OP-P0-2) — and point the plugin at the local mirror via
# SMD_VOICE_VAULT_DIR. Net: the agent process holds no R2 credential for voice,
# yet voice works. Samples change only when an operator re-ingests a corpus (a
# deliberate out-of-band action), so a boot snapshot is sufficient. This runs as
# the hermes user (entrypoint already dropped privilege), so synced files are
# hermes-owned — no extra chown, no race with entrypoint's blanket chown.
export SMD_VOICE_VAULT_DIR="${HERMES_HOME:-/opt/data}/voice"
# CONTENT-FREE CONTRACT (ss#2223 AC5): the seat mirrors ONLY cohort/ — the
# content-free structural fingerprints (cohort/<cohort>/<id>.json) that
# LocalVaultSampleReader consumes. Nothing else under vaults/<slug>/voice/
# (e.g. a raw samples/ corpus staged during ingest) may ever land on a seat:
# this fetch is SCOPED so it cannot write raw text, regardless of what sits
# in R2.
R2_VOICE_COHORT_PREFIX="s3://${R2_BUCKET_CONFIG}/vaults/${CUSTOMER_SLUG}/voice/cohort/"
# Empty/absent vault is the COMMON case (no corpus ingested yet) and MUST NOT
# fail the boot under `set -e`. Three states must stay DISTINCT (ss#2223): a
# genuinely empty vault, a successful sync, and a FAILED probe. The pre-fix
# code used `aws s3 ls ... 2>/dev/null | grep -q .` as the existence gate,
# which collapsed "errored/propagation-lagged listing" into "no corpus
# ingested" and silently left voice inactive while the samples sat in R2 at
# the right keys. So: no separate LIST gate — `cp --recursive` IS the probe
# (no-op success on an empty prefix), state is decided from the JSON that
# actually landed, and cp's stderr is surfaced instead of discarded.
#
# Refresh-on-boot via stage + swap: the volume survives reprovision BY DESIGN,
# so each boot must CONVERGE the mirror on authored R2 state (gone means gone)
# — a sample deleted from R2 disappears from the seat on the next boot, and
# any pre-scoping raw samples/ dir is swapped away. Staging keeps a FAILED
# probe non-destructive: the previous mirror is retained for that boot.
_voice_stage="${SMD_VOICE_VAULT_DIR}.stage"
rm -rf "${_voice_stage}"
mkdir -p "${_voice_stage}/cohort"
_voice_cp_rc=0
# `|| _voice_cp_rc=$?` keeps a failing probe from killing the boot: under
# `set -e` a bare `var="$(failing cmd)"` assignment exits the script, which
# would turn a transient R2 error into a seat crash-loop and makes the FAILED
# branch below unreachable.
_voice_cp_err="$(
  AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID}" \
  AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY}" \
    aws s3 cp --recursive --only-show-errors \
      --endpoint-url "${R2_ENDPOINT_URL}" \
      "${R2_VOICE_COHORT_PREFIX}" "${_voice_stage}/cohort/" 2>&1
)" || _voice_cp_rc=$?
# The reader consumes cohort/<cohort>/*.json; count those as the ground truth,
# independent of the LIST view that made the old probe flaky.
_voice_n="$(find "${_voice_stage}/cohort" -name '*.json' 2>/dev/null | wc -l | tr -d ' ')"
if [ "${_voice_cp_rc}" -eq 0 ]; then
  rm -rf "${SMD_VOICE_VAULT_DIR}"
  mv "${_voice_stage}" "${SMD_VOICE_VAULT_DIR}"
  if [ "${_voice_n}" -gt 0 ]; then
    log "voice vault synced to ${SMD_VOICE_VAULT_DIR}: ${_voice_n} cohort sample(s) (agent holds no R2 credential for voice)"
  else
    log "voice vault empty at ${R2_VOICE_COHORT_PREFIX} (no corpus ingested) — voice stays inactive"
  fi
else
  # DISTINCT from empty: the probe itself failed. Surface it — a silent failure
  # here reads as "no corpus" and voice never activates (ss#2223). The stale
  # mirror (if any) stays in place rather than losing voice to a transient.
  rm -rf "${_voice_stage}"
  _voice_prev_n="$(find "${SMD_VOICE_VAULT_DIR}/cohort" -name '*.json' 2>/dev/null | wc -l | tr -d ' ')"
  log "WARN: voice vault sync FAILED (rc=${_voice_cp_rc}) — previous mirror retained (${_voice_prev_n} cohort sample(s)) (non-fatal). aws: ${_voice_cp_err}"
fi

# ============================================================================
# Step 2b: verify mediated Workspace broker readiness
# ============================================================================
# entrypoint.sh started the broker as a separate uid and removed every Google
# credential variable from this gateway process. The broker reads the authored
# subject/scopes directly from customer.yaml and is the only process permitted
# to read its credential file.
[ -n "${SMD_WORKSPACE_BROKER_SOCKET:-}" ] \
  || die "SMD_WORKSPACE_BROKER_SOCKET is unset; refusing ambient Workspace auth"
[ -S "${SMD_WORKSPACE_BROKER_SOCKET}" ] \
  || die "Workspace broker socket missing: ${SMD_WORKSPACE_BROKER_SOCKET}"
if ! /opt/hermes/.venv/bin/python3 - \
  "${SMD_WORKSPACE_BROKER_SOCKET}" "${CUSTOMER_YAML}" \
  "${SMD_AUDIT_BROKER_SOCKET:-}" "${SMD_D1_AUDIT_BINDING:-}" <<'PY'
import json
import socket
import sqlite3
import sys

import yaml

socket_path, customer_path, audit_socket, audit_db = sys.argv[1:]
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
    client.settimeout(5)
    client.connect(socket_path)
    client.sendall(b'{"action":"health"}\n')
    response = json.loads(client.makefile("rb").readline())
if response.get("ok") is not True or response.get("customer_ready") is not True:
    raise SystemExit("broker health check failed")
customer = yaml.safe_load(open(customer_path, encoding="utf-8")) or {}
google_auth = customer.get("google_auth") or {}
if google_auth and response.get("credential_ready") is not True:
    raise SystemExit("customer has google_auth but broker has no credential")
# OP-P1-4: when audit writes route through the broker, refuse to launch an
# unaudited gateway. Assert the broker opened the ledger AND that the hermes
# mode=ro read seam can read it while the broker holds the only RW handle.
if audit_socket:
    if response.get("audit_ready") is not True:
        raise SystemExit("broker audit ledger not ready (OP-P1-4)")
    ro = sqlite3.connect(f"file:{audit_db}?mode=ro", uri=True)
    try:
        ro.execute("SELECT COUNT(*) FROM audit_log").fetchone()
    finally:
        ro.close()
PY
then
  die "Workspace broker health/readiness check failed"
fi
log "Workspace broker ready; gateway environment is credential-free"

# ============================================================================
# Step 2d: seed the Clio MCP OAuth token to the volume (if Clio connector enabled)
# ============================================================================
# The clio-mcp stdio server (wired into mcp_servers by the overlay materializer,
# v0.4.6+) reads its OAuth token from ~/.clio-mcp/tokens.enc (hermes home =
# /opt/data), AES-256-GCM encrypted under ENCRYPTION_KEY. The consent was
# captured off-box (no browser in a headless Machine); we seed the encrypted
# token here from the CLIO_TOKENS_ENC_B64 Fly secret. The connector REFRESHES
# and rewrites the file in place thereafter, so we only seed when ABSENT — never
# clobber a refreshed token on the persistent volume. The client_id/secret +
# ENCRYPTION_KEY reach the subprocess via the materialized mcp_servers env block
# (ENCRYPTION_KEY <- CLIO_ENCRYPTION_KEY remap), not from here.
CLIO_ENABLED="$(/opt/hermes/.venv/bin/python3 - "${CUSTOMER_YAML}" <<'PY'
import sys
import yaml

data = yaml.safe_load(open(sys.argv[1])) or {}
conns = data.get("connectors") or {}
for rec in conns.values():
    if (
        isinstance(rec, dict)
        and rec.get("enabled")
        and str(rec.get("backend", "")) == "mcp:clio-oktopeak"
    ):
        print("yes")
        break
PY
)" || die "failed to read connectors from customer.yaml"

if [ "${CLIO_ENABLED}" = "yes" ]; then
  CLIO_TOKEN_DIR="/opt/data/.clio-mcp"
  CLIO_TOKEN_FILE="${CLIO_TOKEN_DIR}/tokens.enc"
  # Marker recording the seed that produced the current on-volume token. The
  # connector refreshes (and rewrites) tokens.enc in place, so we must NEVER
  # clobber a refreshed token while the SEED is unchanged. But when the seed in
  # Infisical CHANGES (an operator re-vaults CLIO_TOKENS_ENC_B64 after the
  # on-volume refresh token has gone stale), the old "seed only when absent"
  # rule stranded the dead token forever — every Clio call 401'd and the only
  # recovery was an SSH delete. Compare a hash of the seed against this marker:
  # re-seed iff the seed differs (or the marker/token is missing). Steady state
  # (seed unchanged) leaves the connector-refreshed token untouched. Updating
  # the Infisical seed + reprovisioning now self-heals — no SSH, no recreation.
  CLIO_SEED_MARKER="${CLIO_TOKEN_DIR}/.seed_sha256"
  if [ -n "${CLIO_TOKENS_ENC_B64:-}" ]; then
    [ -n "${CLIO_ENCRYPTION_KEY:-}" ] \
      || die "mcp:clio-oktopeak enabled with a seed token but CLIO_ENCRYPTION_KEY is unset (token could not be decrypted at runtime)"
    CLIO_SEED_HASH="$(printf '%s' "${CLIO_TOKENS_ENC_B64}" | sha256sum | awk '{print $1}')"
    CLIO_PRIOR_HASH=""
    [ -f "${CLIO_SEED_MARKER}" ] && CLIO_PRIOR_HASH="$(cat "${CLIO_SEED_MARKER}" 2>/dev/null || true)"
    if [ -f "${CLIO_TOKEN_FILE}" ] && [ "${CLIO_SEED_HASH}" = "${CLIO_PRIOR_HASH}" ]; then
      log "Clio token on volume matches the current seed; leaving in place (connector refreshes it)"
    else
      mkdir -p "${CLIO_TOKEN_DIR}"
      ( umask 077; printf '%s' "${CLIO_TOKENS_ENC_B64}" | base64 -d > "${CLIO_TOKEN_FILE}" ) \
        || die "CLIO_TOKENS_ENC_B64 is not valid base64 (expected base64 of ~/.clio-mcp/tokens.enc)"
      chmod 600 "${CLIO_TOKEN_FILE}"
      ( umask 077; printf '%s' "${CLIO_SEED_HASH}" > "${CLIO_SEED_MARKER}" )
      chmod 600 "${CLIO_SEED_MARKER}"
      if [ -n "${CLIO_PRIOR_HASH}" ]; then
        log "Clio seed token changed since last boot; re-seeded ${CLIO_TOKEN_FILE} (0600)"
      else
        log "Clio OAuth token seeded to ${CLIO_TOKEN_FILE} (0600)"
      fi
    fi
  elif [ -f "${CLIO_TOKEN_FILE}" ]; then
    log "Clio token on volume; no CLIO_TOKENS_ENC_B64 seed provided; leaving in place (connector refreshes it)"
  else
    log "mcp:clio-oktopeak enabled but no CLIO_TOKENS_ENC_B64 seed; connector unauthenticated until a token is provided"
  fi
fi

# ============================================================================
# Steps 3-6: Honcho data plane — DEFERRED to Phase 2 (ADR 0016, revised)
# ============================================================================
# Previously: start Postgres, start Redis, run `python -m honcho.migrations`,
# start `python -m honcho.server`. That integration was fictional — `honcho-ai`
# is the Honcho CLIENT SDK, not the server, so those module invocations never
# existed and the Machine died here at every boot. Real Honcho v3.0.7 is the
# plastic-labs/honcho SOURCE repo (fastapi api + `python -m src.deriver`,
# pgvector, mandatory LLM provider).
#
# Per the revised ADR 0016 (Option 2): the customer-owned memory file lives in
# our D1/R2; Honcho is a swappable INFERRED-memory provider that sits behind it
# and is deferred to Phase 2. Phase 1 runs on Hermes' always-on flat-file core
# (MEMORY.md/USER.md), which Hermes auto-creates at profile boot. Postgres +
# Redis remain installed in the image (for Phase 2) but are not started here.

# ============================================================================
# Step 6b: seed/refresh the repo skill catalog onto the volume (#1206)
# ============================================================================
# Repo skills are baked into the image at /app/skills (Dockerfile
# `COPY operator/skills/ /app/skills/`), but the catalog the overlay's
# pin-resolver reads at step 7 is ${HERMES_HOME}/skills (= /opt/data/skills, the
# Fly VOLUME). On a persisted volume the baked catalog is SHADOWED — the SAME
# failure mode handled for the overlay plugin pack further below — so a skill
# added to the repo and bound in customer.yaml is present in the image but
# ABSENT on the volume the resolver checks, and `hermes-smd bootstrap` (step 7)
# crash-loops with "skill '<name>' not found at /opt/data/skills/<name>"
# (#1197, #1206).
#
# Fix: additively overlay /app/skills onto ${HERMES_HOME}/skills on every boot.
# ADDITIVE, never a destructive mirror — agent-authored skills that live only on
# the volume (ADR 0017) are preserved; repo skills are refreshed to the image
# version (the image is the deploy unit). The copy runs as the hermes user into
# the hermes-owned volume. FAIL-CLOSED: a Machine whose bound catalog cannot be
# seeded MUST NOT proceed to a guaranteed crash-loop with a misleading error.
if [ -d /app/skills ]; then
  log "Seeding repo skill catalog onto the volume (/app/skills -> ${HERMES_HOME}/skills)..."
  # If the catalog ROOT is itself a stale symlink (an older seeding approach
  # aliased the whole dir back into /app/skills), drop the link before seeding —
  # never write THROUGH it into the read-only image tree, and never let the
  # per-skill clear below recurse through it and delete the source. `-L` tests
  # the link; `rm -f` drops only the link, not its target.
  if [ -L "${HERMES_HOME}/skills" ]; then
    rm -f "${HERMES_HOME}/skills" \
      || die "cannot clear stale ${HERMES_HOME}/skills symlink for the skill catalog seed"
  fi
  mkdir -p "${HERMES_HOME}/skills" \
    || die "cannot create ${HERMES_HOME}/skills for the skill catalog seed"
  # Per-skill replace, scoped to one repo skill name at a time. Each repo skill
  # overwrites any stale volume entry of the SAME name — including a symlink
  # alias left by an older seeding approach, which is what made a bare
  # `cp -a /app/skills/. ${HERMES_HOME}/skills/` abort with "are the same file"
  # and crash-loop the boot on a persisted volume. Agent-authored skills (present
  # only on the volume, never under /app/skills) are never iterated, so the
  # overlay stays ADDITIVE (ADR 0017). The `-L` guard removes an aliasing entry
  # as a LINK (never recursing into /app/skills); a real stale dir is removed in
  # place. FAIL-CLOSED: any unseedable bound skill stops the boot here rather
  # than at a misleading "skill not found" crash in step 7.
  for _src in /app/skills/*/; do
    [ -e "${_src}" ] || continue
    _name=$(basename "${_src}")
    _dst="${HERMES_HOME}/skills/${_name}"
    if [ -L "${_dst}" ]; then
      rm -f "${_dst}" \
        || die "skill catalog seed: cannot clear stale alias ${_dst}"
    else
      rm -rf "${_dst}" \
        || die "skill catalog seed: cannot clear stale ${_dst}"
    fi
    cp -a "${_src}" "${_dst}" \
      || die "skill catalog seed failed for ${_name} (/app/skills -> ${HERMES_HOME}/skills) — refusing to boot into a crash-loop"
  done
  log "Skill catalog seeded ($(find "${HERMES_HOME}/skills" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ') skill dir(s) on volume)"
else
  log "WARNING: /app/skills absent from image; skipping catalog seed (bound skills must already be on the volume)"
fi

# Publish the seat's timezone on the env channel Hermes' clock resolves first
# (hermes_time._resolve_timezone_name: HERMES_TIMEZONE env > global config.yaml
# `timezone` > server local). The container clock is UTC, so without this every
# authored cron expression silently ran in UTC while the customer.yaml comments
# claimed local time — caught 2026-07-03 when the pilot's "0623 PT" morning
# digest turned out to mean 11:23 PM Pacific and the pre-existing escalator's
# "0700 PT" had been firing at midnight Pacific since it shipped. Source of
# truth is customer.yaml `business_hours.timezone` (IANA, validated by the
# console); when the block is unauthored nothing is exported and Hermes keeps
# server-local (UTC) — the prior behavior, not a new default (ADR 0037 tenet 3).
# An invalid IANA name is safe: hermes_time logs a warning and falls back.
#
# ORDERING (ss-console#1691): this export MUST precede step 7. Cron
# materialization (hermes-smd bootstrap -> cron_materialize -> Hermes
# create_job) PERSISTS each job's first next_run_at computed via
# hermes_time.now() in the step-7 process, and hermes_time caches its timezone
# per process at first call. When this export sat below step 7 (with the
# step-11 gateway exports), every boot re-created every managed job with a
# UTC-computed first fire: the gateway then fired it at the UTC-interpreted
# time AND at the correct seat-local time after advance_next_run recomputed —
# the 2026-07-04 escalator double-fire (midnight PT + 7:00 AM PT).
SEAT_TIMEZONE="$(/opt/hermes/.venv/bin/python3 - "${CUSTOMER_YAML}" <<'PY'
import sys
import yaml

data = yaml.safe_load(open(sys.argv[1])) or {}
hours = data.get("business_hours") or {}
tz = hours.get("timezone") if isinstance(hours, dict) else ""
print(tz.strip() if isinstance(tz, str) else "")
PY
)" || SEAT_TIMEZONE=""
if [ -n "${SEAT_TIMEZONE}" ]; then
  export HERMES_TIMEZONE="${SEAT_TIMEZONE}"
  log "Hermes timezone: ${SEAT_TIMEZONE} (cron + clock run in seat-local time)"
else
  log "Hermes timezone: unset (business_hours.timezone unauthored) — cron + clock run in UTC"
fi

# ============================================================================
# Step 7: hermes-smd bootstrap (customer.yaml -> per-profile config)
# ============================================================================
# Installed at image build time via `pip install hermes-smd-overlay`. Reads
# /opt/data/customer.yaml, writes N profile directories under
# $HERMES_HOME/profiles/<slug>/ with config.yaml and SOUL.md. Phase 1 emits no
# memory-provider block (flat-file core); the `hermes-smd bootstrap` CLI takes
# no --honcho-* flags (those were never valid args — passing them aborted the
# step). Honcho wiring returns in Phase 2 (ADR 0016 revised).
log "Running hermes-smd bootstrap (customer.yaml -> profiles)..."
hermes-smd bootstrap \
  --customer-yaml "${CUSTOMER_YAML}" \
  --hermes-home "${HERMES_HOME}" \
  || die "hermes-smd bootstrap failed; check customer.yaml structure"
log "Profile config(s) generated under ${HERMES_HOME}/profiles/"

# Step 7a: write customer-owned operator identity facts into SOUL.md. The
# overlay-generated SOUL covers persona and tone, but the agent also needs the
# authored customer Workspace identity and connector path to answer identity
# questions correctly over Telegram.
log "Writing customer-owned operator identity facts into SOUL.md..."
/opt/hermes/.venv/bin/python3 /app/ensure-operator-identity.py "${CUSTOMER_YAML}" "${HERMES_HOME}" \
  || die "Failed to write operator identity facts into SOUL.md"
log "Operator identity facts written"

# ============================================================================
# Step 7b: disable the Hermes curator (ADR 0017 / ss-console#1135)
# ============================================================================
# Hermes' curator runs an autonomous LLM consolidation pass over agent-authored
# skills on a 7-day cron (agent/curator.py:_run_llm_review()), rewriting and
# consolidating skill CONTENT via skill_manage. That out-of-band rewrite
# corrupts our audit provenance and produces unsupervised structural skill
# drift, so ADR 0017 disables it per-customer. We enforce it here — after the
# profile configs exist (step 7) and BEFORE Hermes (and its gateway cron
# ticker) starts in step 11 — which also closes the fresh-install ticker
# footgun (NousResearch/hermes-agent#18373). In-conversation skill
# auto-creation (skill_manage) stays enabled; only the background curator is
# off. Consolidation is run on demand under Captain supervision via
# `hermes curator run --dry-run` (see docs/runbooks/operator/curator-supervised-consolidation.md).
#
# The declarative home for this flag is the overlay's customer.yaml -> config
# translation; this entrypoint guard is the belt that holds even if the overlay
# has not yet shipped the same flag. Idempotent.
log "Disabling Hermes curator in profile configs (ADR 0017)..."
/opt/hermes/.venv/bin/python3 /app/ensure-curator-disabled.py "${HERMES_HOME}" \
  || die "Failed to disable curator in profile configs (ADR 0017)"
log "Curator disabled in profile config(s)"

# Step 7b.1: enforce persona-disabled bundled skills. Hermes bundles a broad
# universal skill catalog into every profile; customer.yaml skills_disabled is
# the per-customer authority. Apply it after hermes-smd bootstrap writes the
# profile skill tree and prompt snapshot, before the gateway can expose a
# disabled connector path to the model.
log "Removing customer-disabled bundled skills from profile catalogs..."
/opt/hermes/.venv/bin/python3 /app/ensure-disabled-skills.py "${CUSTOMER_YAML}" "${HERMES_HOME}" \
  || die "Failed to enforce persona skills_disabled entries"
log "Disabled skill guard passed"

# Hermes' gateway startup sync can rehydrate bundled skill directories after
# this preflight guard runs. Keep a reconciler alive during gateway startup so
# disabled bundled skills are removed again after that sync without mutating
# the overlay's profile `skills` list shape.
#
# ss#2230: the original fixed 6×5s window was a losing race by construction —
# on 2026-08-10 a clean reprovision failed boot smoke because the rehydration
# landed after the 30s window and nothing re-pruned. Converge instead: re-prune
# every 5s until the --check pass comes back clean on three consecutive probes,
# but never exit before 120s of coverage (a clean streak in the first seconds
# only proves the sync has not STARTED yet — exiting on it would re-create the
# race), under a 300s ceiling. A rehydration later than 300s would still win;
# boot smoke's own --check step remains the arbiter either way (Law 12 — the
# gate can still fail, and did, which is how this defect was found).
# SEC-23, corrected (ss#2420): run the converge loop in an EXEC'd shell with the
# account-wide R2 key scrubbed. The original `( ... ) &` fork carried the key in
# /proc/<pid>/environ for its whole 120-300s life REGARDLESS of the `unset`
# inside it — a fork is never exec'd, so its environ stays the exec-time
# snapshot (the same mechanism the webhook-gate launch below documents). That
# lingering fork is what the r2-account-key-strip probe caught on every cold
# boot, misread four times as a gateway-up race. `env -u … bash -c` EXECs,
# rebuilding a fresh environ without the key; ensure-disabled-skills.py
# operates on local HERMES_HOME skill dirs and never needs R2.
env -u R2_ACCESS_KEY_ID -u R2_SECRET_ACCESS_KEY bash -c '
  _clean_streak=0
  _ticks=0
  while [ "${_ticks}" -lt 60 ]; do
    _ticks=$((_ticks + 1))
    sleep 5
    /opt/hermes/.venv/bin/python3 /app/ensure-disabled-skills.py "$1" "$2" \
      || true
    if /opt/hermes/.venv/bin/python3 /app/ensure-disabled-skills.py --check "$1" "$2" \
      > /dev/null 2>&1; then
      _clean_streak=$((_clean_streak + 1))
      [ "${_clean_streak}" -ge 3 ] && [ "${_ticks}" -ge 24 ] && break
    else
      _clean_streak=0
    fi
  done
' _ "${CUSTOMER_YAML}" "${HERMES_HOME}" &

# Step 7c: fail closed if Telegram would run without an allowlist (ADR 0033).
# TELEGRAM_BOT_TOKEN alone auto-enables Hermes' Telegram platform, and the pinned
# ref fails OPEN on an empty allowlist (telegram.py: `if not allowed_csv: return
# True`) — so a token without an allowlist = a bot anyone can talk to. This guard
# refuses to launch unless an allowlist is resolvable from TELEGRAM_ALLOWED_USERS
# or a profile config's telegram.allow_from (authored via customer.yaml). No-op
# when TELEGRAM_BOT_TOKEN is unset.
log "Verifying Telegram allowlist (fail-closed, ADR 0033)..."
/opt/hermes/.venv/bin/python3 /app/ensure-telegram-allowlist.py "${HERMES_HOME}" \
  || die "Telegram allowlist guard failed (ADR 0033): refusing to launch an unrestricted bot"
log "Telegram allowlist guard passed"

# ============================================================================
# Step 8: safety substrate invariant checks — MOVED below the overlay refresh
# ============================================================================
# The Phase A.5 gate now runs AFTER the overlay refresh + activation-hook seed
# (search "Phase A.5 gate — runs AFTER overlay refresh" below), not here.
#
# WHY (ss-console#1285 follow-up — crash-loop deadlock). invariant_8 validates
# that the volume overlay carries the fan-out __init__.py. The refresh that
# REPAIRS the overlay from the image-pinned pack runs later in this script. With
# the gate HERE (before the refresh), a volume overlay left partial — e.g. an
# interrupted refresh where `rm -rf` completed but `cp` did not, which a restart
# racing a boot can cause — failed invariant_8 and the `die` killed the boot
# BEFORE the refresh could repair it. Every reboot repeated it: an unbreakable
# crash-loop. Moving the gate to after the refresh makes each boot repair the
# overlay first, then validate what will actually launch — self-healing.

# ============================================================================
# Step 9: pause guard
# ============================================================================
# PRESERVED VERBATIM from the prior bootstrap.sh. pause-customer.sh writes a
# sentinel at /opt/data/.paused; if present, we log and park on tail so
# `fly ssh console` still works but the agent loop stays dormant.
if [ -f /opt/data/.paused ]; then
  log "PAUSE sentinel present at /opt/data/.paused — agent will not start"
  log "Reason: $(cat /opt/data/.paused)"
  # Keep the container alive so `fly ssh console` still works; just don't run agent.
  exec tail -f /dev/null
fi

# ============================================================================
# Step 10: customer-sync sidecar (R2 poller) — NOT launched (unimplemented)
# ============================================================================
# The sidecar's purpose is to poll R2 at vaults/<slug>/customer.yaml and apply
# non-structural changes in place (SIGHUP reload). It is NOT launched in Phase 1
# for two reasons: (1) the overlay's `start_customer_sync` raises
# NotImplementedError (the reload path is a tracked follow-on), and (2) the
# `hermes-smd customer-sync` CLI does not accept the `--r2-endpoint` flag this
# script previously passed, so argparse aborted it immediately. Launching it
# backgrounded only spammed a guaranteed crash into the logs on every boot.
# Structural changes already require a Captain re-provision (ADR 0019); until
# the reload path is implemented, non-structural edits also go through a
# re-provision. Re-enable here once the overlay ships the sidecar.

# ============================================================================
# Step 11: launch the Hermes gateway for the active persona (foreground/tini)
# ============================================================================
# The unattended runtime is `hermes gateway run`, NOT `hermes chat`. `chat` is
# an interactive REPL — as PID-1's foreground child with no TTY it would hit
# EOF on stdin and exit, never staying up. The gateway is the long-lived daemon
# that listens for cron triggers (e.g. the daily inbox-triage run) and inbound
# webhook events and drives them through the agent + overlay plugin surface
# (audit / trust / inbound / outbound / voice). `gateway run` is the upstream-
# documented foreground mode for containers.
#
# The gateway MUST target the persona profile, not the bare default profile.
# A plain `hermes gateway run` runs Hermes' built-in `default` profile, which
# has no model, no SOUL.md, no skills, and no connector wiring — i.e. NOT the
# customer's agent. Step 7 wrote the persona profiles under
# $HERMES_HOME/profiles/<slug>/; we select the active one with `-p <slug>`
# (a global flag that works in any position; the profile dir is sufficient,
# no separate `hermes profile create` needed).
#
# Active persona = the first persona with `status: active` (else the first
# persona). Phase 1 customers run a single active persona, so one gateway. The
# multi-active-persona case (one gateway per persona) is an ADR 0011 Phase-2
# concern; until then we launch the single active persona's gateway.
ACTIVE_PROFILE="$(/opt/hermes/.venv/bin/python3 - "${CUSTOMER_YAML}" <<'PY'
import sys
import yaml

data = yaml.safe_load(open(sys.argv[1])) or {}
personas = data.get("personas") or []
active = [p for p in personas if p.get("status") == "active" and p.get("slug")]
fallback = [p for p in personas if p.get("slug")]
chosen = (active or fallback or [None])[0]
print(chosen["slug"] if chosen else "")
PY
)" || die "failed to read active persona from customer.yaml"
[ -n "${ACTIVE_PROFILE}" ] || die "no active persona with a slug in customer.yaml"
log "Active persona profile: ${ACTIVE_PROFILE}"

# Publish the active persona on the env channel the overlay governance plugins
# resolve it from. The ADR 0056 trust gate (hermes-smd-trust/enforce.py), the
# audit emitter, and peer-memory all read the active persona from
# HERMES_ACTIVE_PROFILE (SMD_ACTIVE_PERSONA is only a fallback) to look up that
# persona's authored `entitlements.exposure` in customer.yaml. Hermes core's
# `-p <slug>` flag rewrites HERMES_HOME but NEVER sets HERMES_ACTIVE_PROFILE
# (hermes_cli/main.py:_apply_profile_override sets HERMES_HOME only), so without
# this export the plugins resolve the active persona to "" -> exposure {} ->
# EVERY governed action class (internal_write/external_send/destructive) fail-
# closes on every channel, leaving the agent unable to perform any authored work
# (caught on the first real Smokeball matter.updated: the agent's writes were all
# refused "no authored exposure"). The overlay unit tests pass because they
# monkeypatch this env; production boot is the only place it must be set, and
# bootstrap — the boundary that selects the profile — is where it belongs.
export HERMES_ACTIVE_PROFILE="${ACTIVE_PROFILE}"

# (HERMES_TIMEZONE is exported ABOVE step 7, not here — cron materialization at
# step 7 persists each job's first next_run_at, so the timezone must already be
# on the env when that process starts. See the ordering note at the export,
# ss-console#1691.)

PROFILE_HERMES_HOME="${HERMES_HOME}/profiles/${ACTIVE_PROFILE}"

# `exec` so the gateway inherits the foreground slot under tini cleanly.
#
# Overlay plugins were installed at image build time under ~/.hermes/plugins/.
# customer.yaml + skills + connector wiring resolve through the overlay's
# bootstrap CLI invoked in step 7.
#
# Ensure the gateway's log directories exist and are writable by the hermes
# user. Hermes writes a rotating log and does NOT create the parent dir itself
# — on a fresh volume the open() would fail. The volume root and the profile
# dir are hermes-owned, so these mkdirs create hermes-owned, writable dirs.
mkdir -p "${HERMES_HOME}/logs" "${PROFILE_HERMES_HOME}/logs"

# ---------- Ensure overlay plugins are PRESENT on the volume (before gateway) ----------
# The Dockerfile's `hermes plugins install` lands the overlay pack under
# ${HERMES_HOME} (= /opt/data) — the Fly VOLUME mountpoint. On a persisted volume
# the build-time install is SHADOWED, so the overlay pack (trust / inbound / audit
# / voice hooks — the safety harness) is ABSENT at runtime and the gateway would
# boot WITHOUT it. Re-install here, idempotently, as the hermes user, after the
# volume mount and before the gateway launches.
#
# Use a deterministic DIRECTORY check, NOT `hermes plugins list` — the list does
# not report an installed-but-not-yet-loaded pack before the gateway starts (that
# false-negative previously dragged a healthy install into a fail-closed abort).
# FAIL-CLOSED on a genuine install failure: a Machine whose overlay pack cannot be
# installed MUST NOT serve (matches the Dockerfile build-time hard gate).
# The dir check is a no-op skip on the common path: a fresh volume receives the
# build-time-installed pack via Fly's empty-volume copy, and a persisted volume
# keeps it across deploys — so the slow `git clone` only runs in the rare reseed
# case and does not normally delay the public :8643 listener below.
OVERLAY_PLUGIN_DIR="${HERMES_HOME}/plugins/hermes-smd-overlay"
OVERLAY_PACK="/app/overlay-pack"
# REFRESH the volume's overlay from the image-pinned pack on EVERY boot — do NOT
# skip when a dir is merely present. The volume (/opt/data) persists across
# deploys and shadows the build-time install, so a presence-only check kept a
# STALE overlay forever: every OVERLAY_REF bump rebuilt the image but the running
# gateway kept loading the old volume copy, and the overlay sat inert — no audit,
# no trust enforcement (ss-console#1285). Refreshing from the pinned, non-volume
# pack makes a bump actually take effect, and is idempotent on a steady-state
# boot (same bytes).
if [ -d "${OVERLAY_PACK}" ]; then
  log "Refreshing overlay on the volume from the image-pinned pack (${OVERLAY_PACK})..."
  mkdir -p "${HERMES_HOME}/plugins"
  # replace_dir_tolerant (mv-aside, not rm -rf) so a stray root-owned file in the
  # old overlay dir can't crash-loop the boot under set -e. cp (not cp -a): the
  # copies are owned by the running hermes user, not the root-owned image source —
  # a root-owned volume file would break a later hermes-user write (the
  # .skills_prompt_snapshot.json FATAL we hit).
  replace_dir_tolerant "${OVERLAY_PACK}" "${OVERLAY_PLUGIN_DIR}"
  /opt/hermes/.venv/bin/hermes plugins enable hermes-smd-overlay >/dev/null 2>&1 || true
  [ -f "${OVERLAY_PLUGIN_DIR}/__init__.py" ] \
    || die "overlay fan-out __init__.py missing after refresh — refusing to launch an ungoverned gateway"
  log "Overlay refreshed on the volume (fan-out register present)"
elif [ -d "${OVERLAY_PLUGIN_DIR}" ]; then
  # No staged pack (older image) but a volume copy exists — leave it; the
  # activation invariant is the backstop that halts boot if it is inert.
  log "Overlay present on the volume; no image pack to refresh from (${OVERLAY_PLUGIN_DIR})"
else
  log "Overlay absent and no image pack; installing at runtime (unpinned fallback)..."
  rm -rf "${HERMES_HOME}/plugins" 2>/dev/null || true
  /opt/hermes/.venv/bin/hermes plugins install venturecrane/hermes-smd-overlay --enable \
    || die "runtime overlay plugin install failed — refusing to launch a harness-less gateway"
  [ -d "${OVERLAY_PLUGIN_DIR}" ] \
    || die "overlay plugin dir missing after install — refusing to launch a harness-less gateway"
  log "Overlay plugin installed + enabled at runtime"
fi

# `hermes -p <profile>` rewrites HERMES_HOME to the profile directory before
# importing Hermes modules. Plugin discovery therefore scans the profile's
# plugins directory, not the root volume directory refreshed above. Materialize
# the same pinned pack into the active profile and enable it in that profile's
# config so force-discovery from the gateway process can load it.
PROFILE_OVERLAY_PLUGIN_DIR="${PROFILE_HERMES_HOME}/plugins/hermes-smd-overlay"
if [ -d "${OVERLAY_PACK}" ]; then
  mkdir -p "${PROFILE_HERMES_HOME}/plugins"
  replace_dir_tolerant "${OVERLAY_PACK}" "${PROFILE_OVERLAY_PLUGIN_DIR}"
elif [ -d "${OVERLAY_PLUGIN_DIR}" ]; then
  mkdir -p "${PROFILE_HERMES_HOME}/plugins"
  replace_dir_tolerant "${OVERLAY_PLUGIN_DIR}" "${PROFILE_OVERLAY_PLUGIN_DIR}"
else
  die "overlay source missing before profile materialization — refusing to launch an ungoverned gateway"
fi
[ -f "${PROFILE_OVERLAY_PLUGIN_DIR}/__init__.py" ] \
  || die "profile overlay fan-out missing (${PROFILE_OVERLAY_PLUGIN_DIR}) — refusing to launch an ungoverned gateway"
/opt/hermes/.venv/bin/hermes -p "${ACTIVE_PROFILE}" plugins enable hermes-smd-overlay >/dev/null \
  || die "failed to enable hermes-smd-overlay in active profile ${ACTIVE_PROFILE}"
log "Overlay materialized + enabled in active profile"

# ---------- Seed the gateway-startup ACTIVATION GATE onto the volume ----------
# The overlay's LIVE governance gate is a HookRegistry handler
# (hooks/smd-overlay-activation) that fires at gateway:startup IN the gateway
# process: it force-loads the overlay into the live PluginManager singleton, then
# drives a REAL pre_tool_call dispatch self-check and fails closed (os._exit) if the
# operator is not actually governed. This closes ss-console#1285 — registered hooks
# were inert on the live gateway because its plugin singleton was cached (idempotent
# discovery) WITHOUT the overlay; the pre-gateway safety-substrate invariant could
# not catch it (it runs in a different process and asserts its own singleton).
#
# Hermes' `-p <profile>` handling rewrites HERMES_HOME before gateway/hooks.py
# defines its module-level HOOKS_DIR. The live gateway therefore loads handlers
# from ${PROFILE_HERMES_HOME}/hooks, not the root volume's hooks directory. Seed
# the selected profile directly. Refresh on every boot so an OVERLAY_REF bump
# takes effect. Uses cp -r (not -a) so files land hermes-owned.
if [ -d "${OVERLAY_PACK}/hooks" ]; then
  _HOOKS_SRC="${OVERLAY_PACK}/hooks"
elif [ -d "${OVERLAY_PLUGIN_DIR}/hooks" ]; then
  _HOOKS_SRC="${OVERLAY_PLUGIN_DIR}/hooks"
else
  _HOOKS_SRC=""
fi
PROFILE_HOOKS_DIR="${PROFILE_HERMES_HOME}/hooks"
ACTIVATION_HOOK_DIR="${PROFILE_HOOKS_DIR}/smd-overlay-activation"
if [ -n "${_HOOKS_SRC}" ]; then
  log "Seeding overlay gateway hooks into active profile from ${_HOOKS_SRC}..."
  mkdir -p "${PROFILE_HOOKS_DIR}"
  for _hookdir in "${_HOOKS_SRC}"/*/; do
    [ -d "${_hookdir}" ] || continue
    _name="$(basename "${_hookdir}")"
    # mv-aside (same root-owned-.pyc tolerance as the overlay refresh): a root
    # `ssh console` that loaded handler.py would leave a root-owned __pycache__
    # here, which a plain `rm -rf` under set -e would choke on.
    replace_dir_tolerant "${_hookdir}" "${PROFILE_HOOKS_DIR}/${_name}"
  done
  log "Overlay gateway hooks seeded into active profile"
fi
# FAIL-CLOSED: the live activation gate must be wired no matter which overlay branch
# ran above. Without it the gateway has no in-process check that the overlay governs
# live turns — the exact unverified state ss-console#1285 shipped. Better to crash-loop
# (Fly restarts) than to serve an operator we cannot prove is governed.
{ [ -f "${ACTIVATION_HOOK_DIR}/HOOK.yaml" ] && [ -f "${ACTIVATION_HOOK_DIR}/handler.py" ]; } \
  || die "overlay activation gate missing (${ACTIVATION_HOOK_DIR}) — refusing to launch a gateway with no live governance self-check (ss-console#1285)"

# ============================================================================
# Step 8 (moved): safety substrate invariant checks — Phase A.5 gate — runs AFTER overlay refresh
# ============================================================================
# Runs here, AFTER the overlay refresh + activation-hook seed above, so
# invariant_8 validates the freshly-repaired overlay (fan-out __init__.py
# present) rather than a stale/partial volume copy left by an interrupted prior
# boot — which previously deadlocked into a crash-loop (see "Step 8" note above).
# The five invariants must still hold across compaction, restart, tool failure,
# prompt injection, and ceiling-escalation attempts; re-run every boot so a
# Hermes SHA bump can't regress the floor (OpenClaw mitigation).
log "Running safety substrate invariant checks (Phase A.5 gate)..."
if ! /opt/hermes/.venv/bin/python3 /app/safety-substrate/run_invariants.py \
       --customer "${CUSTOMER_SLUG}" \
       --fixtures /app/safety-substrate/tests \
       --strict ; then
  die "Safety substrate invariant check FAILED — agent will not start. \
Inspect /app/safety-substrate/logs/$(date -u +%Y%m%d).log for which invariant failed."
fi
log "Safety substrate invariants PASSED"

# Author-built connector classification agreement (ADR 0053). With the baked
# connector manifests (/app/connectors) and the installed overlay both present,
# assert every declared tool_class agrees with the overlay's enforced
# classify_tool() under the runtime-prefixed name mcp_<server>_<tool>. This is the
# manifest<=map check run where both artifacts exist (it cannot run in either
# repo's unit CI alone). A drift — including an OVERLAY_REF that does not classify
# a baked connector's tools — fails the boot rather than shipping a governance
# disagreement.
log "Running connector classification probe (ADR 0053)..."
if ! /opt/hermes/.venv/bin/python3 /app/connector-classification-probe.py /app/connectors; then
  die "Connector classification probe FAILED — a baked author-built connector's \
manifest disagrees with the overlay action-class map (see stderr above). Agent \
will not start."
fi
log "Connector classification probe PASSED"

# Strip the account-wide R2 credential NOW — before launching ANY persistent
# same-uid background child OR the gateway (OP-P0-2 + OP-P2-1,
# docs/security/operator-threat-model.md). R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY
# are an ACCOUNT-WIDE R2 key (R/W on every bucket); their only in-Machine
# consumers are the customer.yaml fetch (Step 2) and the voice-vault sync
# (Step 2a) above — both BOOT-time and BOTH BEFORE this point. The agent's
# skill-body writer uses the bucket-SCOPED R2_SKILL_BODIES_* pair plus
# R2_ENDPOINT_URL (KEPT — an endpoint URL, not a credential); voice reads its
# local SMD_VOICE_VAULT_DIR mirror.
#
# ORDERING IS LOAD-BEARING: this MUST stay AFTER Step 2/2a (which read the key)
# and BEFORE the webhook-gate launch + the gateway exec. It previously sat just
# before the exec — but the webhook gate is launched as a same-uid (hermes)
# background child a few lines below, and a child forked BEFORE the unset KEEPS
# the key in its own env. A code-executing agent can read that sibling's
# /proc/<pid>/environ (same uid; verified on staging:
# hermes_can_read_sibling_account_key=1) and use the account-wide key to write
# the R2 config object — re-opening the self-loopback ceiling-raise one layer up
# (ADR 0044 Decision 8). Stripping here, before any child is forked, closes the
# leak: no hermes-owned process ever holds the account-wide key.
unset R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY

# Egress webhook subscriptions (the PUSH half): ensure each push connector's
# vendor tenant has the subscriptions customer.yaml declares — the mirror of the
# inbound gate routes. Boot is the change-hash-gated BACKSTOP: a steady intent is
# a pure local no-op with zero vendor calls; the PRIMARY trigger is the OAuth
# connect callback (a fresh firm activates without waiting for a reboot). NON-FATAL
# and bounded (each per-vendor reconcile subprocess has its own timeout), so a
# reconcile hiccup never blocks the gateway from coming up. Runs after the R2 strip
# (it needs only customer.yaml + the vendor secrets, never the account-wide key).
log "Reconciling egress webhook subscriptions (boot backstop)..."
/opt/hermes/.venv/bin/python3 /app/webhook_reconcile.py "${CUSTOMER_YAML}" --trigger boot \
  || log "WARN: egress webhook reconcile non-fatal failure (retries at connect / next boot)"

# Inbound webhook front-door gate (overlay `hermes-smd-webhook-gate`). It binds
# the public port (8643), verifies the vendor signature (AgentMail), and forwards
# to the gateway's machine-local :8644 with the Generic header. FAIL-CLOSED: only
# launched when a per-vendor webhook secret is present — no public webhook surface
# without a verifying secret. Runs as a supervised background child under tini; a
# restart loop keeps it up, while the gateway exec below stays PID-1's foreground.
if [ -n "${WEBHOOK_SECRET_AGENTMAIL:-}" ]; then
  # Run the respawn loop in an EXEC'd shell with the account-wide R2 key scrubbed
  # from its environment (OP-P2-1). The `unset` above removed the key from THIS
  # bash's variables, but a forked `( ) &` subshell is NOT exec'd — its
  # /proc/<pid>/environ still exposes the exec-time snapshot of the key, readable
  # by a same-uid code-executing agent (verified on staging: the wrapper subshell
  # held R2_ACCESS_KEY_ID in /proc/environ even though its children were clean).
  # `env -u … bash -c` EXECs, rebuilding a fresh environ WITHOUT the key, so
  # neither this persistent wrapper nor the webhook-gate it runs carries the key
  # in /proc. (env -u is belt-and-suspenders over the unset; the exec is what
  # actually scrubs the wrapper's /proc/environ.)
  env -u R2_ACCESS_KEY_ID -u R2_SECRET_ACCESS_KEY bash -c '
    while true; do
      /opt/hermes/.venv/bin/hermes-smd-webhook-gate || true
      echo "[bootstrap] webhook-gate exited non-zero; restarting in 2s" >&2
      sleep 2
    done
  ' &
  log "Inbound webhook gate launched (public :8643 -> gateway :8644)"
else
  log "WEBHOOK_SECRET_AGENTMAIL unset; webhook gate NOT launched (no inbound webhook)"
fi

# (R2 account-wide key already stripped above, before the webhook-gate launch —
# OP-P2-1. No same-uid child holds it.)

# SEC-28: strip the runtime-read seam key from the AGENT (hermes gateway) env.
# The seam is served + validated by the webhook gate (launched above, which keeps
# its inherited copy); the agent has no reason to hold it, and leaving it in the
# gateway env lets a code-executing agent mint its own read-seam bearer. Stripped
# AFTER the webhook-gate fork and BEFORE the gateway exec, so the gate still
# authenticates the seam while the agent cannot self-issue.
#
# Same pattern for the ADR 0023 heartbeat secrets (they too are read only by the
# gate's emitter, forked above): MACHINE_HEARTBEAT_KEY is this seat's own bearer
# since migration 0114 (per-seat, verified for this slug only), so a code-executing
# agent holding it could no longer forge ANOTHER tenant's heartbeat, but it could
# still write a false "green" for its own seat or mask its own death. Before 0114
# it was the fleet-wide shared key and the cross-tenant forge was live (the Wave-1
# weakness, ADR 0023 locked-decision #10). HEALTHCHECKS_PING_URL is the external
# dead-man ping target; an agent holding it could spoof liveness at healthchecks.io.
# Neither has any use in the agent; the gate keeps its inherited copies.
unset OPERATOR_RUNTIME_READ_KEY MACHINE_HEARTBEAT_KEY HEALTHCHECKS_PING_URL
log "Launching Hermes gateway for profile '${ACTIVE_PROFILE}' (overlay plugins enabled)..."

exec /opt/hermes/.venv/bin/hermes -p "${ACTIVE_PROFILE}" gateway run
