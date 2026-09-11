#!/usr/bin/env bash
# Root-only process launcher for the broker and non-root gateway.

set -euo pipefail

log() {
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [entrypoint] $*"
}

[ "$(id -u)" -eq 0 ] || {
  log "FATAL: entrypoint must start as root to establish separate principals"
  exit 1
}

BROKER_DIR="/var/lib/smd-workspace-broker"
BROKER_SOCKET="/run/smd-workspace-broker/broker.sock"
BROKER_CUSTOMER_PATH="${BROKER_DIR}/customer.yaml"

# Keystone (audit 2026-06-15 — SEC-07/08/09/18/30, EFF-14, proven-live on
# hermes-smd-staging). The live customer.yaml is the source every trust-ceiling /
# vertical-floor / scope decision resolves against, read fresh per action. It MUST
# NOT live on the agent-writable /opt/data volume: the hermes uid owns that tree,
# so the agent could rewrite its own ceiling (proven: one sed flipped
# external_send draft_for_review->autonomous) or rename the file (it owns the dir).
# It lives in a fully root-owned directory, world-readable (0644) but NEVER
# writable or renameable by the hermes uid. Root — this entrypoint plus the
# ADR-0044 config applier — is the only writer.
CONFIG_DIR="/var/lib/smd-config"
LIVE_CUSTOMER_YAML="${CONFIG_DIR}/customer.yaml"

# Root fetches the authoritative customer.yaml from R2 (source of truth) on EVERY
# boot into the root-owned ${CONFIG_DIR}. This REPLACES the former hermes-side
# fetch in bootstrap.sh Step 2, which wrote an agent-writable copy on /opt/data —
# the keystone hole. The broker `cp` further down and the gateway both read this
# root-owned copy. R2 is the only source on a fresh volume. A pre-keystone
# persisted volume may still carry an agent-owned /opt/data/customer.yaml: migrate
# it once, then remove it so no writable copy survives. Idempotent every boot.
mkdir -p "${CONFIG_DIR}"
chown root:root "${CONFIG_DIR}"
chmod 0755 "${CONFIG_DIR}"
: "${R2_BUCKET_CONFIG:?R2_BUCKET_CONFIG required to fetch customer.yaml}"
: "${CUSTOMER_SLUG:?CUSTOMER_SLUG required to fetch customer.yaml}"
if [ -f /opt/data/customer.yaml ] && [ ! -f "${LIVE_CUSTOMER_YAML}" ]; then
  mv /opt/data/customer.yaml "${LIVE_CUSTOMER_YAML}"
  log "migrated legacy /opt/data/customer.yaml -> ${LIVE_CUSTOMER_YAML} (keystone relocation)"
fi
# Validate a CANDIDATE customer.yaml before it becomes the live one, using the
# SAME on-box validator the ADR 0044 live applier uses. Until ss #2082 the boot
# fetch did a bare `cp` then `mv -f` with no check at all, and only the poller
# validated. That asymmetry was survivable while the R2 object was written by
# hand at provision time; auto-publish arms it. An object the poller REJECTS
# stays in R2, and the next restart (a Fly migration, an OOM, an unrelated
# redeploy) adopted it unvalidated, hours or days after the merge that put it
# there and with nothing tying the crash to that merge.
#
# Structural validation ONLY. The applier's safety layer (config_applier.safety)
# is deliberately NOT run here: it enforces live-apply transition rules, and the
# rebuild-class fields it refuses on the live path (persona tone, vertical,
# model) are precisely the ones a RESTART exists to deliver. Running it here
# would refuse the changes this path is for.
#
# Exit 2 means the validator itself is unimportable. That is treated exactly
# like an invalid config: a substrate we cannot check is not a substrate we
# adopt from (the standing fail-closed posture, same as the invariant_7 gate
# further down, which refuses boot on a missing module rather than skipping).
validate_candidate_config() {
  /opt/hermes/.venv/bin/python3 - "$1" <<'PY'
import sys
from pathlib import Path

try:
    from bootstrap.validate import validate_customer_yaml
except Exception as exc:  # noqa: BLE001 - any import failure is a refusal
    print(f"candidate config validator unimportable: {exc}", file=sys.stderr)
    raise SystemExit(2)

errors = validate_customer_yaml(Path(sys.argv[1]))
for err in errors:
    print(f"  {err}", file=sys.stderr)
raise SystemExit(1 if errors else 0)
PY
}

_seed_endpoint="${R2_ENDPOINT_URL:-https://${R2_ACCOUNT_ID:-}.r2.cloudflarestorage.com}"
if AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID:?}" \
     AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY:?}" \
       aws s3 cp \
         --endpoint-url "${_seed_endpoint}" \
         --only-show-errors \
         "s3://${R2_BUCKET_CONFIG}/vaults/${CUSTOMER_SLUG}/customer.yaml" \
         "${LIVE_CUSTOMER_YAML}.r2.tmp"; then
  _r2_fetched=1
else
  _r2_fetched=0
fi

_r2_usable=0
if [ "${_r2_fetched}" -eq 1 ]; then
  if validate_candidate_config "${LIVE_CUSTOMER_YAML}.r2.tmp"; then
    _r2_usable=1
  elif [ $? -eq 2 ]; then
    log "WARN: cannot validate the R2 customer.yaml (validator unimportable); refusing to adopt it"
  else
    log "WARN: R2 customer.yaml REFUSED by the on-box validator (errors above); refusing to adopt it"
  fi
fi

if [ "${_r2_usable}" -eq 1 ]; then
  mv -f "${LIVE_CUSTOMER_YAML}.r2.tmp" "${LIVE_CUSTOMER_YAML}"
  log "customer.yaml refreshed from R2 (source of truth) into ${CONFIG_DIR}"
elif [ -f "${LIVE_CUSTOMER_YAML}" ]; then
  # Fail-static: the Machine comes up on the config it was already serving. A
  # bad publish costs the seat its update, never its uptime.
  rm -f "${LIVE_CUSTOMER_YAML}.r2.tmp" 2>/dev/null || true
  if [ "${_r2_fetched}" -eq 1 ]; then
    log "WARN: keeping the existing root-owned customer.yaml (the R2 object was not adopted)"
  else
    log "WARN: R2 fetch failed; using existing root-owned customer.yaml"
  fi
else
  rm -f "${LIVE_CUSTOMER_YAML}.r2.tmp" 2>/dev/null || true
  if [ "${_r2_fetched}" -eq 1 ]; then
    log "FATAL: no local customer.yaml and the R2 object was refused (${R2_BUCKET_CONFIG}/vaults/${CUSTOMER_SLUG}); nothing valid to boot on"
  else
    log "FATAL: customer.yaml not present and R2 fetch failed (${R2_BUCKET_CONFIG}/vaults/${CUSTOMER_SLUG})"
  fi
  exit 1
fi
# Root owns it; the agent reads (0644) but cannot write or rename it (the parent
# dir is root-owned). This is the structural close of the self-loopback.
chown root:root "${LIVE_CUSTOMER_YAML}"
chmod 0644 "${LIVE_CUSTOMER_YAML}"
rm -f /opt/data/customer.yaml

# ss#2614: the chronology runner's per-firm config rides the same vault prefix.
# Same fail-static shape as customer.yaml above, without the FATAL arm: a seat
# without it boots fine and the runner refuses every job with one loud line.
# Root-owned, group medchron read-only (the driver child reads it; the agent
# uid has no group membership and no need to read the firm's tables).
MEDCHRON_FIRM_CONFIG="${CONFIG_DIR}/medchron-firm.yaml"
if AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID:?}" \
     AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY:?}" \
       aws s3 cp \
         --endpoint-url "${_seed_endpoint}" \
         --only-show-errors \
         "s3://${R2_BUCKET_CONFIG}/vaults/${CUSTOMER_SLUG}/medchron-firm.yaml" \
         "${MEDCHRON_FIRM_CONFIG}.r2.tmp" 2>/dev/null; then
  mv -f "${MEDCHRON_FIRM_CONFIG}.r2.tmp" "${MEDCHRON_FIRM_CONFIG}"
  log "medchron-firm.yaml refreshed from R2 into ${CONFIG_DIR}"
elif [ -f "${MEDCHRON_FIRM_CONFIG}" ]; then
  rm -f "${MEDCHRON_FIRM_CONFIG}.r2.tmp" 2>/dev/null || true
  log "WARN: R2 fetch of medchron-firm.yaml failed; keeping the existing root-owned copy"
else
  rm -f "${MEDCHRON_FIRM_CONFIG}.r2.tmp" 2>/dev/null || true
  log "No medchron-firm.yaml in the vault for ${CUSTOMER_SLUG}; the chronology runner will refuse jobs"
fi
if [ -f "${MEDCHRON_FIRM_CONFIG}" ]; then
  chown root:medchron "${MEDCHRON_FIRM_CONFIG}"
  chmod 0640 "${MEDCHRON_FIRM_CONFIG}"
fi

# OP-P1-4 audit ledger: owned by the broker uid, readable (not writable) by the
# agent uid via the audit-readers group. The agent's only write path is the
# broker's append-only audit_append verb.
AUDIT_DIR="/opt/data/audit"
AUDIT_DB="${AUDIT_DIR}/audit.db"
LEGACY_AUDIT_DB="/opt/data/audit.db"
# The ss#2488 gateway-liveness kill ledger. Declared HERE, next to the audit
# dir, purely so the chown sweep below can prune it: the supervisor that uses it
# is defined much further down, but the sweep runs first and would otherwise
# hand the agent ownership of its own restart budget on the second boot onward
# (the dir is re-created root-owned every boot, but the `kills` FILE inside it
# would already have flipped). Same reasoning, same fix, as the audit subtree.
GATEWAY_LIVENESS_LEDGER_DIR="/opt/data/gateway-liveness"
# ss#2614: the chronology runner's queue + job workdirs. Root-owned on the
# volume (a job's exhibits are hundreds of MB and a crash must resume), pruned
# from the sweep for the same reason as the audit subtree, and reached by the
# broker and the daemon through a bind mount below (the gateway's mid-boot
# chmod of /opt/data severs a child dir's group-traverse; see the audit note).
MEDCHRON_DATA_DIR="/opt/data/medchron"
# Group-readable default so the broker's rollback journal is readable by the
# hermes mode=ro read seam during a write window. Explicit chmods below for the
# 0700/0600 broker paths are unaffected by this.
umask 027

# Agent owns its data EXCEPT the broker-owned audit subtree (R1). This REPLACES
# a plain `chown -R hermes:hermes /opt/data`, which would re-own the ledger back
# to hermes on every reboot and silently false-close the tamper-resistance.
find /opt/data \( -path "${AUDIT_DIR}" -o -path "${GATEWAY_LIVENESS_LEDGER_DIR}" -o -path "${MEDCHRON_DATA_DIR}" \) -prune -o -print0 | xargs -0 -r chown hermes:hermes

# NOTE: the broker reaches the ledger via the bind mount established below, NOT
# by traversing /opt/data. The Hermes gateway chmods its home (/opt/data) to
# 0700 mid-boot, which strips any group-traverse we could grant here — so the
# write path must not depend on the home dir's mode.

# Staging area for file transfers into the seat. `fly ssh sftp put` runs as
# ROOT, so if this directory does not already exist the first push creates it
# root-owned — and the sweep above cannot help, because it ran at boot, before
# the directory existed. hermes then cannot unlink there (unlink permission
# comes from the DIRECTORY), so every transfer copy stays until someone runs a
# root op. On ashton-price that reached 18 files and 157 MiB of client medical
# material before it was noticed (2026-08-26).
#
# Creating it here, explicitly owned and moded rather than relying on mkdir -p
# defaults, means a root-created FILE inside it is still removable by hermes.
# Lifecycle of the contents belongs to whatever pushes them: this establishes
# the directory, it does not purge it.
STAGE_DIR="/opt/data/tmp-deliverable"
install -d -o hermes -g hermes -m 0700 "${STAGE_DIR}"

# Convergent (idempotent, every-boot) audit-ledger establishment. Never drops
# rows. Fails loud rather than silently diverging two ledgers (R5 / DA #5).
mkdir -p "${AUDIT_DIR}"
if [ -f "${LEGACY_AUDIT_DB}" ] && [ -f "${AUDIT_DB}" ]; then
  log "FATAL: both ${LEGACY_AUDIT_DB} and ${AUDIT_DB} exist; refusing to diverge the audit ledger (manual merge required)"
  exit 1
fi
if [ -f "${LEGACY_AUDIT_DB}" ]; then
  mv "${LEGACY_AUDIT_DB}" "${AUDIT_DB}"
  for _s in -journal -wal -shm; do
    if [ -f "${LEGACY_AUDIT_DB}${_s}" ]; then mv "${LEGACY_AUDIT_DB}${_s}" "${AUDIT_DB}${_s}"; fi
  done
fi
# Pre-create with the correct owner/mode so the broker opens an existing 0640
# file (sqlite preserves a file's mode/owner; a broker-created file would
# inherit umask and risk a 0600 the read seam cannot read).
if [ ! -f "${AUDIT_DB}" ]; then
  install -o workspace-broker -g audit-readers -m 0640 /dev/null "${AUDIT_DB}"
fi
# Re-assert owner/mode every boot (convergent, never conditional-on-legacy).
chown workspace-broker:audit-readers "${AUDIT_DIR}"
chmod 2750 "${AUDIT_DIR}"
chown workspace-broker:audit-readers "${AUDIT_DB}"
chmod 0640 "${AUDIT_DB}"
for _s in -journal -wal -shm; do
  if [ -f "${AUDIT_DB}${_s}" ]; then
    chown workspace-broker:audit-readers "${AUDIT_DB}${_s}"
    chmod 0640 "${AUDIT_DB}${_s}"
  fi
done
# Fail-closed: the hermes read seam must be able to read the ledger.
setpriv --reuid=hermes --regid=hermes --init-groups test -r "${AUDIT_DB}" \
  || { log "FATAL: ${AUDIT_DB} not hermes-readable after perm convergence"; exit 1; }

# Bind-mount the ledger dir to a root-owned path the broker can always traverse,
# independent of /opt/data's mode (the gateway flips the home to 0700 mid-boot).
# Same underlying volume inodes as ${AUDIT_DIR}; the broker writes via this path,
# the hermes read seam reads via ${AUDIT_DB} (hermes owns its home). /run is a
# fresh tmpfs each boot, so re-create the mountpoint and bind idempotently.
AUDIT_BIND_DIR="/run/smd-audit"
AUDIT_BIND_DB="${AUDIT_BIND_DIR}/audit.db"
mkdir -p "${AUDIT_BIND_DIR}"
mountpoint -q "${AUDIT_BIND_DIR}" \
  || mount --bind "${AUDIT_DIR}" "${AUDIT_BIND_DIR}" \
  || { log "FATAL: could not bind-mount ${AUDIT_DIR} -> ${AUDIT_BIND_DIR}"; exit 1; }

# ss#2614: the chronology runner's tree, converged on every boot with explicit
# owners and modes (never mkdir -p defaults; the establish-spool note below
# explains why). queue/ is written by the broker uid (submit) and read by root
# (the daemon); jobs/ is root only; both reach their users through the bind
# mount, exactly like the audit ledger. The Smokeball refresh token becomes
# group-shared (setgid dir, 0660 file) so the medchron uid can mint and rotate
# it alongside the connector; the connector preserves that mode on rotation.
install -d -o root -g root -m 0755 "${MEDCHRON_DATA_DIR}"
install -d -o root -g workspace-broker -m 0770 "${MEDCHRON_DATA_DIR}/queue"
# jobs/: root-owned, group medchron EXECUTE-ONLY (0710) — the driver child
# must traverse it to reach its own job dir (live-caught 2026-08-31: 0700
# gave the child PermissionError on its job.yaml) but must not be able to
# list or open sibling jobs; each job dir is 0700 medchron.
install -d -o root -g medchron -m 0710 "${MEDCHRON_DATA_DIR}/jobs"
MEDCHRON_RUN_DIR="/run/smd-medchron"
mkdir -p "${MEDCHRON_RUN_DIR}"
mountpoint -q "${MEDCHRON_RUN_DIR}" \
  || mount --bind "${MEDCHRON_DATA_DIR}" "${MEDCHRON_RUN_DIR}" \
  || { log "FATAL: could not bind-mount ${MEDCHRON_DATA_DIR} -> ${MEDCHRON_RUN_DIR}"; exit 1; }
export SMD_MEDCHRON_QUEUE_DIR="${MEDCHRON_RUN_DIR}/queue"

# 2026-09-04: the chronology runner's INSTALL-level artifacts — the scanned-page
# classifier's authored control pages (`controls.json` + the PDFs it names) and
# the vendored ICD tables (`icd/`) — ride the same vault prefix as the firm
# config, under medchron-controls/. Every job gets a fresh data_root under
# jobs/<id>/, so no job can carry them; the runner resolves them against this
# tree (Job.install_root, which the daemon points at the run dir). Seeded from
# the vault on EVERY boot, so a volume recreate converges on the authored set
# instead of refusing every job until someone copies files in by hand. Same
# fail-static shape as medchron-firm.yaml, no FATAL arm: a seat with nothing in
# its vault boots fine and the runner refuses the classify stage of each job
# with one loud line. Root-owned, group medchron read-only: the child reads
# its falsifier and can never rewrite it (a classifier that can edit its own
# controls measures nothing).
MEDCHRON_CONTROLS_DIR="${MEDCHRON_DATA_DIR}/controls"
rm -rf "${MEDCHRON_CONTROLS_DIR}.r2.tmp"
if AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID:?}" \
     AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY:?}" \
       aws s3 cp \
         --endpoint-url "${_seed_endpoint}" \
         --only-show-errors \
         --recursive \
         "s3://${R2_BUCKET_CONFIG}/vaults/${CUSTOMER_SLUG}/medchron-controls/" \
         "${MEDCHRON_CONTROLS_DIR}.r2.tmp/" 2>/dev/null \
   && [ -f "${MEDCHRON_CONTROLS_DIR}.r2.tmp/controls.json" ]; then
  rm -rf "${MEDCHRON_CONTROLS_DIR}"
  mv "${MEDCHRON_CONTROLS_DIR}.r2.tmp" "${MEDCHRON_CONTROLS_DIR}"
  log "medchron controls refreshed from R2 into ${MEDCHRON_CONTROLS_DIR}"
elif [ -f "${MEDCHRON_CONTROLS_DIR}/controls.json" ]; then
  rm -rf "${MEDCHRON_CONTROLS_DIR}.r2.tmp"
  log "WARN: R2 fetch of medchron-controls/ failed; keeping the existing root-owned copy"
else
  rm -rf "${MEDCHRON_CONTROLS_DIR}.r2.tmp"
  log "No medchron-controls/ in the vault for ${CUSTOMER_SLUG}; the chronology runner will refuse the classify stage of every job"
fi
if [ -d "${MEDCHRON_CONTROLS_DIR}" ]; then
  chown -R root:medchron "${MEDCHRON_CONTROLS_DIR}"
  find "${MEDCHRON_CONTROLS_DIR}" -type d -exec chmod 0750 {} +
  find "${MEDCHRON_CONTROLS_DIR}" -type f -exec chmod 0640 {} +
fi
SMOKEBALL_TOKEN_DIR="/opt/data/.smokeball-mcp"
SMOKEBALL_TOKEN_RUN_DIR="/run/smd-smokeball-token"
if [ -d "${SMOKEBALL_TOKEN_DIR}" ]; then
  chown hermes:smokeball-token "${SMOKEBALL_TOKEN_DIR}"
  chmod 2770 "${SMOKEBALL_TOKEN_DIR}"
  if [ -f "${SMOKEBALL_TOKEN_DIR}/refresh_token" ]; then
    chown hermes:smokeball-token "${SMOKEBALL_TOKEN_DIR}/refresh_token"
    chmod 0660 "${SMOKEBALL_TOKEN_DIR}/refresh_token"
  fi
  mkdir -p "${SMOKEBALL_TOKEN_RUN_DIR}"
  mountpoint -q "${SMOKEBALL_TOKEN_RUN_DIR}" \
    || mount --bind "${SMOKEBALL_TOKEN_DIR}" "${SMOKEBALL_TOKEN_RUN_DIR}" \
    || log "WARN: could not bind-mount the Smokeball token dir; the chronology runner cannot reach the matter"
fi

rm -rf /opt/data/workspace-broker
rm -f /opt/data/oauth/google.json
mkdir -p "${BROKER_DIR}" "$(dirname "${BROKER_SOCKET}")"
rm -f "${BROKER_DIR}/google.json"
cp "${LIVE_CUSTOMER_YAML}" "${BROKER_CUSTOMER_PATH}"
chown -R workspace-broker:workspace-broker "${BROKER_DIR}"
chmod 0700 "${BROKER_DIR}"
chown workspace-broker:workspace-connectors "$(dirname "${BROKER_SOCKET}")"
chmod 2750 "$(dirname "${BROKER_SOCKET}")"

# Establishment spool (ADR 0085, ss#2161/#2162). The tree an admin-instructed
# voice/shape submission crosses on its way from the agent to the root
# establish_intake daemon: the broker uid writes staging/ and runs/, root
# writes results/. hermes gets NO access at any level — the client corpus and
# the submitted spec transit this tree, and the agent uid must not be able to
# rewrite a submission after broker validation (the intake stat-verifies the
# writer uid on the other side).
#
# EVERY directory is explicitly owned and moded, converged on every boot —
# never mkdir -p defaults. A default-moded ancestor silently widens the whole
# tree (the spec_applier _harden_ancestors incident); the tree's guarantee is
# only as strong as its loosest dir. The root dir denies hermes (0750, group
# workspace-broker); the children add group-write because the broker creates
# staging sets and run dirs there and unlinks a result after its one-shot
# read. Root writes results/ files 0640 root:workspace-broker.
# NOT under /opt/data. The Hermes gateway chmods its home (/opt/data) to 0700
# mid-boot — the same behavior the audit-ledger note above documents, which is
# why the ledger reaches the broker through a bind mount. A spool under that
# tree is reachable by root (which ignores modes, so the intake daemon, its
# heartbeat, and every boot smoke check look healthy) and UNREACHABLE by the
# workspace-broker uid, the principal that creates staging sets and run dirs.
# The failure is invisible from the spool's own permissions: its dirs read a
# correct 0770 root:workspace-broker; the ANCESTOR severs them. Live-caught on
# hermes-pilot-smokeball 2026-08-02, the first establishment call:
# PermissionError on .../establish-spool/staging. The spool is transient by
# design (30-minute TTL, short-lived runs), so it lives beside the broker's
# other state rather than on the volume.
ESTABLISH_SPOOL_DIR="/var/lib/smd-establish-spool"
export SMD_ESTABLISH_SPOOL_DIR="${ESTABLISH_SPOOL_DIR}"
# The intake's poll cadence (root child inherits this env; default matches the
# intake's own built-in default, stated here so it is tunable per seat).
export SMD_ESTABLISH_POLL_SECONDS="${SMD_ESTABLISH_POLL_SECONDS:-5}"
install -d -o root -g workspace-broker -m 0750 "${ESTABLISH_SPOOL_DIR}"
install -d -o root -g workspace-broker -m 0770 "${ESTABLISH_SPOOL_DIR}/staging"
install -d -o root -g workspace-broker -m 0770 "${ESTABLISH_SPOOL_DIR}/runs"
install -d -o root -g workspace-broker -m 0770 "${ESTABLISH_SPOOL_DIR}/results"

export SMD_WORKSPACE_BROKER_SOCKET="${BROKER_SOCKET}"
export SMD_WORKSPACE_CREDENTIAL_PATH="${BROKER_DIR}/google.json"
export SMD_CUSTOMER_YAML="${BROKER_CUSTOMER_PATH}"
export SMD_GATEWAY_PID="$$"

PYTHONPATH="/opt/workspace-broker" \
  /opt/workspace-broker/.venv/bin/python -c \
  'import os; from pathlib import Path; from workspace_broker.google_auth import materialize_credential; materialize_credential(Path(os.environ["SMD_WORKSPACE_CREDENTIAL_PATH"]))'
[ -f "${SMD_WORKSPACE_CREDENTIAL_PATH}" ] || {
  log "FATAL: Workspace broker credential was not materialized"
  exit 1
}
chown workspace-broker:workspace-broker "${SMD_WORKSPACE_CREDENTIAL_PATH}"
chmod 0600 "${SMD_WORKSPACE_CREDENTIAL_PATH}"

# ss#2258: the AgentMail SEND credential, same custody shape as the Google one
# above. The gateway keeps a DIFFERENT AgentMail key (AGENTMAIL_API_KEY) that is
# inbox-scoped with message_send/draft_send withheld, so the agent can read and
# draft its own mailbox but is refused by the vendor if it tries to transmit.
# The send-capable key exists only here, in a 0600 broker-owned file, and
# AGENTMAIL_SEND_API_KEY is unset below before any hermes-uid process exists.
#
# Absent on a seat with no AgentMail connector — not fatal (unlike the Google
# credential, which is only staged when authored). The broker's transmit verbs
# fail closed when the file is missing, so absence cannot become permission.
export SMD_AGENTMAIL_CREDENTIAL_PATH="${BROKER_DIR}/agentmail.json"
if [ -n "${AGENTMAIL_SEND_API_KEY:-}" ]; then
  PYTHONPATH="/opt/workspace-broker" \
    /opt/workspace-broker/.venv/bin/python -c \
    'import os; from pathlib import Path; from workspace_broker.agentmail_auth import materialize_credential; materialize_credential(Path(os.environ["SMD_AGENTMAIL_CREDENTIAL_PATH"]))'
  [ -f "${SMD_AGENTMAIL_CREDENTIAL_PATH}" ] || {
    log "FATAL: AgentMail send credential was staged but not materialized"
    exit 1
  }
  chown workspace-broker:workspace-broker "${SMD_AGENTMAIL_CREDENTIAL_PATH}"
  chmod 0600 "${SMD_AGENTMAIL_CREDENTIAL_PATH}"
  log "AgentMail send credential materialized to the broker store"
else
  log "AGENTMAIL_SEND_API_KEY unset; broker transmit verbs stay fail-closed"
fi

# ss#2258 (msgraph wave): the Graph SEND credential, same custody shape again.
#
# Read the next paragraph before "fixing" the missing unset at the bottom of this
# file. Unlike AGENTMAIL_SEND_API_KEY, the gateway's MSGRAPH_* is NOT stripped
# before the exec-drop, and that is deliberate: the agent needs Graph credentials
# for READS it legitimately performs — the inbound delta poller in the gateway,
# and the msgraph-mail MCP server that is its mail tool surface. Graph app-only
# auth has no read-only variant of a send-capable credential (a client-credentials
# token is always `/.default` — every permission the app registration holds), so
# stripping MSGRAPH_* here would blind the seat rather than harden it.
#
# What closes the rest is the SECOND app registration — read-only for the agent,
# send-capable only here. As of 2026-08-13 that is REQUIRED, not aspirational:
# provision-customer.sh refuses an msgraph seat whose MSGRAPH_SEND_* credentials
# are not a distinct registration from the gateway's MSGRAPH_*. So on any seat
# that provisioned successfully, the MSGRAPH_* the gateway keeps below holds
# Mail.ReadWrite and NOT Mail.Send, and the Graph token an in-agent path could
# mint with it is refused at /sendMail by Microsoft (403 ErrorAccessDenied —
# proven on the sandbox seat 2026-08-13, vfy_01KZXX523V6JNWEETG4PSZDQY3). Both
# fences are live for msgraph: the vendor makes the agent's credential incapable
# of transmitting, and the broker verbs recipient-fence and audit the governed
# path. Setup: docs/runbooks/operator/ms-graph-azure-ad-setup.md.
export SMD_MSGRAPH_CREDENTIAL_PATH="${BROKER_DIR}/msgraph.json"
if [ -n "${MSGRAPH_SEND_CLIENT_SECRET:-}" ]; then
  PYTHONPATH="/opt/workspace-broker" \
    /opt/workspace-broker/.venv/bin/python -c \
    'import os; from pathlib import Path; from workspace_broker.msgraph_auth import materialize_credential; materialize_credential(Path(os.environ["SMD_MSGRAPH_CREDENTIAL_PATH"]))'
  [ -f "${SMD_MSGRAPH_CREDENTIAL_PATH}" ] || {
    log "FATAL: msgraph send credential was staged but not materialized"
    exit 1
  }
  chown workspace-broker:workspace-broker "${SMD_MSGRAPH_CREDENTIAL_PATH}"
  chmod 0600 "${SMD_MSGRAPH_CREDENTIAL_PATH}"
  log "msgraph send credential materialized to the broker store"
else
  log "MSGRAPH_SEND_CLIENT_SECRET unset; broker msgraph verbs stay fail-closed"
fi

# overlay#280: the broker ALSO carries the READ app's credential (the same
# registration the gateway keeps — Mail.ReadWrite, no Mail.Send), because the
# reply verb's sender-verification GET is 403 on the send app under the two-app
# fence. Reading is the lower privilege and the agent already holds this exact
# credential, so nothing widens; without this file no reply can ever transmit
# on a two-app seat. Export is unconditional so launch_broker()'s env -i
# allowlist line below always references a set variable.
export SMD_MSGRAPH_READ_CREDENTIAL_PATH="${BROKER_DIR}/msgraph-read.json"
if [ -n "${MSGRAPH_CLIENT_SECRET:-}" ]; then
  PYTHONPATH="/opt/workspace-broker" \
    /opt/workspace-broker/.venv/bin/python -c \
    'import os; from pathlib import Path; from workspace_broker.msgraph_auth import materialize_read_credential; materialize_read_credential(Path(os.environ["SMD_MSGRAPH_READ_CREDENTIAL_PATH"]))'
  [ -f "${SMD_MSGRAPH_READ_CREDENTIAL_PATH}" ] || {
    log "FATAL: msgraph read credential was staged but not materialized"
    exit 1
  }
  chown workspace-broker:workspace-broker "${SMD_MSGRAPH_READ_CREDENTIAL_PATH}"
  chmod 0600 "${SMD_MSGRAPH_READ_CREDENTIAL_PATH}"
  log "msgraph read credential materialized to the broker store (reply sender-verification)"
else
  log "MSGRAPH_CLIENT_SECRET unset; broker msgraph reply verb stays fail-closed"
fi

# The broker is the SECOND principal that BOTH the Google capability path AND the
# OP-P1-4 audit_append path depend on. Define its launch ONCE; the supervisor
# below uses it for the first start and every respawn. env -i with a fixed
# allowlist — the broker reads its Google credential from the materialized file
# (SMD_WORKSPACE_CREDENTIAL_PATH), never from env, so a respawn needs nothing the
# parent later unsets.
launch_broker() {
  setpriv \
    --reuid=workspace-broker \
    --regid=workspace-broker \
    --init-groups \
    --no-new-privs \
    /usr/bin/env -i \
    PATH="/opt/workspace-broker/.venv/bin:/usr/bin:/bin" \
    PYTHONPATH="/opt/workspace-broker" \
    PYTHONUNBUFFERED=1 \
    CUSTOMER_SLUG="${CUSTOMER_SLUG}" \
    SMD_WORKSPACE_BROKER_SOCKET="${SMD_WORKSPACE_BROKER_SOCKET}" \
    SMD_WORKSPACE_CREDENTIAL_PATH="${SMD_WORKSPACE_CREDENTIAL_PATH}" \
    SMD_CUSTOMER_YAML="${SMD_CUSTOMER_YAML}" \
    SMD_GATEWAY_PID="${SMD_GATEWAY_PID}" \
    SMD_AGENT_UID="$(id -u hermes)" \
    SMD_AUDIT_DB_PATH="${AUDIT_BIND_DB}" \
    SMD_ESTABLISH_SPOOL_DIR="${SMD_ESTABLISH_SPOOL_DIR}" \
    SMD_MEDCHRON_QUEUE_DIR="${SMD_MEDCHRON_QUEUE_DIR}" \
    SMD_AGENTMAIL_CREDENTIAL_PATH="${SMD_AGENTMAIL_CREDENTIAL_PATH}" \
    SMD_MSGRAPH_CREDENTIAL_PATH="${SMD_MSGRAPH_CREDENTIAL_PATH}" \
    SMD_MSGRAPH_READ_CREDENTIAL_PATH="${SMD_MSGRAPH_READ_CREDENTIAL_PATH}" \
    /opt/workspace-broker/.venv/bin/python \
    -m workspace_broker.server
}

# Root-side respawn supervisor (OP-P1-4 follow-up). WITHOUT it, a broker that dies
# mid-run is never restarted: audit_append then fails OPEN (rows silently dropped,
# the exact gap OP-P1-4 closes) and Google capability stops, with no signal. We
# fork the supervisor while STILL ROOT — before the exec-drop to hermes at the
# bottom — so each respawn can re-setpriv a fresh broker to uid workspace-broker
# (a hermes process could not re-acquire that uid). The server unlinks its stale
# socket on bind (server.py), so respawns rebind cleanly. SMD_GATEWAY_PID is the
# entrypoint PID, preserved across the exec, so the SO_PEERCRED gate still admits
# the gateway after a respawn. A broker that is broken from the FIRST boot is NOT
# masked: the parent's socket-wait below still FATALs the whole Machine (Fly
# restarts it); the supervisor only covers death AFTER a healthy first start. The
# `if` guard keeps the inherited `set -e` from killing the loop on a non-zero
# broker exit. (Fail-open-with-respawn is intentional for this PR; the stronger
# fail-closed ack-before-dispatch is deferred to the autonomous-send workstream.)
(
  while true; do
    if launch_broker; then _brk_rc=0; else _brk_rc=$?; fi
    log "Workspace broker exited (rc=${_brk_rc}); respawning in 2s"
    sleep 2
  done
) &
SUPERVISOR_PID=$!

for _ in 1 2 3 4 5; do
  [ -S "${BROKER_SOCKET}" ] && break
  sleep 1
done
[ -S "${BROKER_SOCKET}" ] || {
  log "FATAL: Workspace broker socket was not created"
  kill "${SUPERVISOR_PID}" 2>/dev/null || true
  exit 1
}

unset GOOGLE_SERVICE_ACCOUNT_JSON GOOGLE_TOKEN_JSON GOOGLE_CLIENT_SECRET_JSON
unset GOOGLE_IMPERSONATE_SUBJECT GOOGLE_OAUTH_SCOPES GOOGLE_TOKEN_PATH
# ss#2258: the AgentMail SEND key dies with root's environment. It must be
# stripped HERE — before the exec-drop below and before any hermes-uid process
# exists — because ADR 0044 Decision 8 proved a same-uid sibling can read a
# credential out of /proc/<pid>/environ, so a strip that happens after the first
# fork is cosmetic. What the gateway inherits is AGENTMAIL_API_KEY, the
# inbox-scoped key the vendor refuses to let transmit.
unset AGENTMAIL_SEND_API_KEY
# The Graph SEND app credential dies here too, for the same reason and at the same
# moment. Since 2026-08-13 it carries a DIFFERENT app registration from the
# MSGRAPH_* the gateway keeps for reads — provisioning refuses the seat otherwise
# — so this strip is the whole point: the only credential in the tenant that holds
# Mail.Send becomes unreachable from the agent. The gateway's read-side MSGRAPH_*
# deliberately survives; see the materialization block above for why.
unset MSGRAPH_SEND_TENANT_ID MSGRAPH_SEND_CLIENT_ID MSGRAPH_SEND_CLIENT_SECRET

export HOME=/opt/data

# Keystone wiring: point the ADR-0044 applier (the root writer) and every
# agent-side reader at the root-owned live config. SMD_APPLIER_VOLUME_PATH is the
# applier's write/read target; SMD_CUSTOMER_YAML_PATH is what
# shared.customer_config.from_volume() (trust gate, reply channel, webhook-router)
# resolves at runtime. The gateway exec below inherits both (no env -i), so the
# agent reads the root-owned copy and has no writable path to its own ceiling.
export SMD_APPLIER_VOLUME_PATH="${LIVE_CUSTOMER_YAML}"
export SMD_CUSTOMER_YAML_PATH="${LIVE_CUSTOMER_YAML}"

# Authored-spec tree (ss ADR 0083, #2084). The customer's per-output-class voice
# and format specifications, installed ROOT-OWNED under the same root-owned
# ${CONFIG_DIR} as customer.yaml, for the same reason and by the same argument.
#
# WHY ROOT, restated because it is the item this change exists for: `read_file`
# is a READ-class tool — unfenced, and it does not taint the session. A spec the
# agent could WRITE would therefore be a persistent, untainted, self-authored
# prompt-injection channel that survives restarts: strictly worse than a tainted
# inbound email, which at least fences the turn. That is the same self-loopback
# this file's keystone comment above records being proven live on
# hermes-smd-staging 2026-06-15, where one `sed` against an agent-writable config
# flipped external_send from draft_for_review to autonomous. The fix then was
# root ownership rather than policy, and it is root ownership here.
#
# 0755 dir / 0644 files: the agent MUST read these (an unread spec fails its
# send gate) and must never write them. The boot invariant below refuses to
# serve if that asymmetry does not hold on disk.
SPEC_DIR="${CONFIG_DIR}/specs"
export SMD_SPEC_DIR="${SPEC_DIR}"
mkdir -p "${SPEC_DIR}"
chown root:root "${SPEC_DIR}"
chmod 0755 "${SPEC_DIR}"

# Boot fetch, synchronous and BEFORE the privilege drop. The poller further down
# would install the same tree seconds later, which is too late: bootstrap.sh runs
# translate.py right after the drop, and translate renders each profile's
# SKILL.md spec POINTER from the installed manifest. Fetch-then-stamp, or the
# first boot stamps nothing and the agent cannot find the spec it is gated on.
#
# Never fatal. A missing vault object is the ordinary state of a seat whose
# customer has authored nothing; a refused document leaves the previously
# installed tree standing (fail-static). An un-adoptable spec must not cost the
# seat its uptime — the runtime gate, not the boot, decides whether an output
# whose declared spec never arrived may be produced.
if [ -n "${R2_ACCESS_KEY_ID:-}" ] && [ -n "${R2_BUCKET_CONFIG:-}" ] \
   && /opt/hermes/.venv/bin/python -c "import spec_applier" 2>/dev/null; then
  if /opt/hermes/.venv/bin/python -m spec_applier --once; then
    log "authored-spec boot fetch complete (${SPEC_DIR})"
  else
    log "WARN: authored-spec boot fetch exited non-zero; keeping the installed spec tree"
  fi
else
  log "Authored-spec boot fetch NOT run (R2 config creds absent, or spec_applier not in this overlay)"
fi

# Re-assert ownership after the fetch. The applier hardens each file it writes,
# but asserting here too means a tree left behind by an OLDER overlay build — one
# whose applier predates the hardening — is corrected on this boot rather than
# tripping the invariant below and refusing an otherwise healthy Machine.
chown -R root:root "${SPEC_DIR}"
find "${SPEC_DIR}" -type d -exec chmod 0755 {} +
find "${SPEC_DIR}" -type f -exec chmod 0644 {} +

log "Workspace broker started as uid $(id -u workspace-broker); dropping gateway to hermes"

# Root-side config applier (ADR 0044 live reconfiguration). Forked here as a
# ROOT background child — BEFORE the exec-drop to hermes below — so it survives
# the exec and keeps uid 0. It polls R2 for an updated customer.yaml, validates +
# safety-checks it (config_applier + the parity validator), and atomically writes
# it to ${LIVE_CUSTOMER_YAML} (via SMD_APPLIER_VOLUME_PATH) so the agent picks up
# entitlement / scope / skill / webhook / demo changes on its NEXT action — no reboot.
#
# WHY ROOT (ADR 0044 Decision 5, hardened by the 2026-06-15 keystone): the live
# customer.yaml now lives in the root-owned ${CONFIG_DIR}, so root is the ONLY
# principal that can write it — the hermes agent reads it (0644) but cannot rewrite
# its own ceiling or rename the file (previously it could; proven live on
# hermes-smd-staging 2026-06-15). The R2 pull credential lives in THIS root
# process's env, which the hermes agent cannot read from /proc (different uid) — so
# the control-plane apply credential never reaches the data plane (ADR 0026 +
# OP-P2-1). The agent holds no config-write credential and no inbound verb that can
# trigger an apply.
#
# Forked AFTER the GOOGLE_* unset above so it never carries Google creds. Respawn
# loop self-heals; a dead applier never blocks the gateway (config changes simply
# stop applying until it restarts — fail-static, not fail-open: the running config
# and its enforced ceilings are untouched). v1 is instant-tier only (the
# live-writable fields are read fresh per action; no gateway reload). Launched
# only when the R2 config credentials are present.
if [ -n "${R2_ACCESS_KEY_ID:-}" ] && [ -n "${R2_BUCKET_CONFIG:-}" ] \
   && /opt/hermes/.venv/bin/python -c "import config_applier" 2>/dev/null; then
  ( while true; do
      /opt/hermes/.venv/bin/python -m config_applier || true
      log "config applier exited; restarting in 5s"
      sleep 5
    done ) &
  log "Root config applier launched (uid 0; polls R2 for live customer.yaml changes)"
else
  log "Root config applier NOT launched (R2 config creds absent, or config_applier not in this overlay)"
fi

# Root-side chronology runner daemon (routine 11, ss#2614). Same fork point and
# respawn discipline as the appliers above, for the same reason: it owns the
# root-only job dirs and the queue, and it drops each driver child to the
# medchron uid itself (setpriv), which a hermes process could never do. What
# the child gets is an ALLOW-LIST from this env (the daemon copies only the
# Anthropic key, the Smokeball credentials and the firm config path; R2 and
# Google credentials never reach it). Import-gated so an image whose venv is
# missing degrades to a loud "NOT launched" line, never a broken boot. Runs
# from its own root-owned venv, not the Hermes one.
if [ -x /opt/medchron/.venv/bin/python ] \
   && /opt/medchron/.venv/bin/python -c "import medchron.daemon" 2>/dev/null; then
  ( while true; do
      SMD_MEDCHRON_RUN_DIR="${MEDCHRON_RUN_DIR}" \
      SMOKEBALL_REFRESH_TOKEN_FILE="${SMOKEBALL_TOKEN_RUN_DIR}/refresh_token" \
      MEDCHRON_FIRM_CONFIG="${MEDCHRON_FIRM_CONFIG}" \
      MEDCHRON_PRICING_JSON="/opt/medchron/anthropic_pricing.json" \
      /opt/medchron/.venv/bin/python -m medchron.daemon || true
      log "medchron daemon exited; restarting in 5s"
      sleep 5
    done ) &
  log "Root medchron daemon launched (uid 0; runs chronology jobs as the medchron uid from ${MEDCHRON_RUN_DIR}/queue)"
else
  log "Root medchron daemon NOT launched (the runner venv is not in this image)"
fi

# Root-side authored-spec applier (ss ADR 0083 #2084). Same shape, same
# principal, same respawn discipline as the config applier above, and forked at
# the same point for the same reason: it must survive the exec-drop below and
# keep uid 0, because root is the only principal that may write ${SPEC_DIR}.
# It polls the portal-written vault object and installs a verified, root-owned
# spec tree, so a customer's correction reaches the running Machine without a
# reboot — the runtime read of a spec is a plain file read, so a replaced body
# takes effect on the next read with nothing baked into a running process.
#
# Two writers, two key spaces, never the same object: the git->R2 publisher owns
# vaults/<slug>/customer.yaml, the portal owns vaults/<slug>/output-classes.json.
# A dead applier never blocks the gateway — fail-static, not fail-open: spec
# updates simply stop arriving and the installed tree keeps serving.
if [ -n "${R2_ACCESS_KEY_ID:-}" ] && [ -n "${R2_BUCKET_CONFIG:-}" ] \
   && /opt/hermes/.venv/bin/python -c "import spec_applier" 2>/dev/null; then
  ( while true; do
      /opt/hermes/.venv/bin/python -m spec_applier || true
      log "spec applier exited; restarting in 5s"
      sleep 5
    done ) &
  log "Root authored-spec applier launched (uid 0; polls R2 for live spec changes)"
else
  log "Root authored-spec applier NOT launched (R2 config creds absent, or spec_applier not in this overlay)"
fi

# Root-side establishment intake (ADR 0085, ss#2161/#2162). Same shape, same
# principal, same respawn discipline as the appliers above, forked at the same
# point for the same reason: it must survive the exec-drop below and keep
# uid 0, because it holds the R2 write credential and runs the distillation
# compiler gates over broker-authored submissions in ${ESTABLISH_SPOOL_DIR}/runs,
# installing a gated result into the vault object the spec applier polls.
#
# Gated on `import establish_intake` so a lagging overlay pin degrades to a
# LOUD "not launched" line, never a broken boot — establish_submit runs then
# queue unprocessed until the overlay catches up (fail-static: the installed
# spec tree keeps serving). The intake emits its own boot line on launch; the
# rehearsal pre-flight asserts one of these two lines, so a silent
# not-launched cannot read as healthy.
if [ -n "${R2_ACCESS_KEY_ID:-}" ] && [ -n "${R2_BUCKET_CONFIG:-}" ] \
   && /opt/hermes/.venv/bin/python -c "import establish_intake" 2>/dev/null; then
  ( while true; do
      /opt/hermes/.venv/bin/python -m establish_intake || true
      log "establishment intake exited; restarting in 5s"
      sleep 5
    done ) &
  log "Root establishment intake launched (uid 0; polls ${ESTABLISH_SPOOL_DIR}/runs for admin-instructed voice/shape submissions)"
else
  log "Root establishment intake NOT launched (R2 config creds absent, or establish_intake not in this overlay); establish_submit runs will queue unprocessed"
fi

# Root-side gateway liveness supervisor (P0 ss#2488). On 2026-08-20 the paying
# client's seat wedged for 33 minutes and recovered only because a human
# restarted it. Hermes' OWN loop-liveness watchdog fired and logged "...exiting
# with code 75 so the service supervisor can restart it" — and then did not
# exit. At the pin we run (v2026.8.18@e624e9fd) that path is already a hard
# os._exit(75) (gateway/shutdown_watchdog.py:196), so there was no graceful
# shutdown to blame: the thread reached its logger.critical (the line is in
# gateway.log) and never reached the os._exit two statements later. That
# module's docstring names why such a thing happens — "every asyncio-based
# recovery path is structurally unable to fire: they need the same loop that is
# stuck" — and the rule extends one step further: an IN-PROCESS recovery path
# can be blocked by whatever blocked the process. Recovery has to come from
# outside it. Nothing outside it existed: this entrypoint EXECS the gateway as
# the container's main process, the supervisor above covers the BROKER, and Fly
# does not restart a Machine on a failing health check.
#
# Two facts make the fix cheap. Hermes already rewrites a loop heartbeat every
# 30s from an asyncio task ON the loop that wedges, so the file goes stale the
# instant the loop freezes; and this entrypoint already forks root children that
# survive the exec-drop. We were simply not reading the heartbeat.
#
# The path is PROBED, not read off the source. shutdown_watchdog's
# _process_hermes_home() reads HERMES_HOME (= /opt/data here), but on a live
# seat `stat /opt/data/state/gateway.heartbeat` is "No such file or directory" —
# the file sits under the PROFILE home. Hence the argv-derived path below.
# vfy_01M0H9BKDCTFKSC5WSS9Z9DYVG.
GATEWAY_LIVENESS_RUN_DIR="/run/smd-gateway-liveness"
# GATEWAY_LIVENESS_LEDGER_DIR is declared beside AUDIT_DIR at the top of this
# file so the boot-time chown sweep can prune it. See the note there.
GATEWAY_LIVENESS_PROFILES_DIR="${GATEWAY_LIVENESS_PROFILES_DIR:-/opt/data/profiles}"
# The one seam that exists for the test harness rather than for the Machine:
# templates/tests/test_gateway_liveness_supervisor.py drives the REAL loop text
# extracted from this file against a fake process tree, which is the only way to
# prove the state machine (the arming guard, the recovery re-check, the kill
# ledger) rather than merely assert that its source contains certain words. It
# also lets the suite run on a developer's macOS, which has no /proc at all.
GATEWAY_LIVENESS_PROC_DIR="${GATEWAY_LIVENESS_PROC_DIR:-/proc}"
# The module that WRITES the heartbeat, in the installed Hermes. Probed, not
# assumed: hermes-smd-staging runs 0.18.0 (7c1a029) today, and that pin predates
# the loop heartbeat entirely -- the module does not exist in its tree and no
# heartbeat file exists on its volume (vfy_01M0HBR1NZHSRMWSFPSQM32D1E). On such
# a pin "no heartbeat has ever appeared" means "this build has no heartbeat",
# NOT "the gateway is wedged", and the boot-deadline path below would SIGKILL a
# perfectly healthy seat every 15 minutes until the ledger stopped it. Same
# import-gate discipline as the establishment intake above: a lagging pin
# degrades to a LOUD not-watching line, never to a wrong action.
GATEWAY_LIVENESS_HEARTBEAT_WRITER="${GATEWAY_LIVENESS_HEARTBEAT_WRITER:-/opt/hermes/gateway/shutdown_watchdog.py}"
# Every threshold is per-seat tunable, same shape as SMD_ESTABLISH_POLL_SECONDS
# above. 240s of staleness is 8 missed beats, and the margin is sized against
# CPU STARVATION rather than out of deference to Hermes' own watchdog — that
# watchdog took 22 minutes to notice this incident and then failed to exit, so
# waiting on it buys nothing. The evidence for the margin is the incident: the
# webhook gate answered /health every 30s on the dot throughout, so the box —
# 1 vCPU at loadavg 15.25 — was never so starved that a 30s task could not run.
SMD_GATEWAY_LIVENESS_POLL_SECONDS="${SMD_GATEWAY_LIVENESS_POLL_SECONDS:-30}"
SMD_GATEWAY_LIVENESS_STALE_SECONDS="${SMD_GATEWAY_LIVENESS_STALE_SECONDS:-240}"
SMD_GATEWAY_LIVENESS_DUMP_GRACE_SECONDS="${SMD_GATEWAY_LIVENESS_DUMP_GRACE_SECONDS:-20}"
SMD_GATEWAY_LIVENESS_TERM_GRACE_SECONDS="${SMD_GATEWAY_LIVENESS_TERM_GRACE_SECONDS:-15}"
SMD_GATEWAY_LIVENESS_BOOT_DEADLINE_SECONDS="${SMD_GATEWAY_LIVENESS_BOOT_DEADLINE_SECONDS:-900}"
# How long a boot is ALLOWED to take before the supervisor says so out loud.
# Two conditions read it, both about a boot that has not finished yet:
#
#   1. argv does not name hermes. Expected for the first minutes of every boot —
#      entrypoint EXECS bootstrap.sh, which does its own several-minute setup
#      before its own `exec` of the gateway at bootstrap.sh:916, so
#      /proc/<SMD_GATEWAY_PID>/cmdline reads `bash /app/bootstrap.sh` until then.
#   2. the heartbeat is on disk but has never been seen FRESH this boot. The
#      volume persists, so a stale beat from the previous boot is always there.
#
# Before the grace both are normal and neither pages. After it, a boot that
# still has not produced a beat is a wedge at startup, which is exactly what no
# instrument on this seat could see during the 2026-09-01 crash loop.
#
# 900s is sized off the incident: the observed cold-start crawl was ~4 minutes
# at 1GB, so the grace is ~3.5x the worst startup actually measured. Raise it on
# a seat with a heavier connector set rather than lowering it anywhere.
SMD_GATEWAY_LIVENESS_STARTUP_GRACE_SECONDS="${SMD_GATEWAY_LIVENESS_STARTUP_GRACE_SECONDS:-900}"
SMD_GATEWAY_LIVENESS_MAX_KILLS="${SMD_GATEWAY_LIVENESS_MAX_KILLS:-3}"
SMD_GATEWAY_LIVENESS_KILL_WINDOW_SECONDS="${SMD_GATEWAY_LIVENESS_KILL_WINDOW_SECONDS:-3600}"
SMD_GATEWAY_LIVENESS_KILL_VERIFY_SECONDS="${SMD_GATEWAY_LIVENESS_KILL_VERIFY_SECONDS:-10}"

# /run is a root-owned tmpfs (the tick is per-boot and must not persist); the
# kill ledger is on the volume because its whole job is to bound restarts ACROSS
# restarts. Both root-owned, alongside /opt/data/audit as the precedent for a
# non-agent directory on the volume. The ledger bounds availability, not
# security: it sits under an agent-writable parent (/opt/data is hermes-owned,
# and write+exec on a parent is enough to unlink a child — the same keystone
# hole this file documents at the top), so a code-executing agent could remove
# it and buy itself unlimited restarts. That is why the authoritative record of
# every kill and every refusal is the log line, which reaches `fly logs` and
# which the agent cannot reach at all.
#
# 0755 / 0644, NOT 0700 (ss#2488 part 2). The webhook gate -- hermes uid, and
# the one process that survives a wedge -- reads the tick, the state line and
# the ledger below and puts them on the control-plane heartbeat, so a stale
# pulse, a restart, a refusing supervisor or one that never armed reaches an
# inbox instead of only `fly logs`. Read-only for the agent is still the whole
# security property: root is the sole writer, and under a root-owned directory
# with no group/other write bit the agent uid can neither forge, edit, nor
# unlink a line. (The unlink caveat in the paragraph above is about the PARENT
# /opt/data, and is unchanged by this.) The cross-repo threshold contract lives
# here too: fleet-alerts' GATEWAY_LOOP_RED_SECONDS must stay BELOW
# SMD_GATEWAY_LIVENESS_STALE_SECONDS x 2 samples + the dump and TERM graces
# (~270s at defaults), or the page lands after the restart it was meant to
# precede. wrangler.toml carries the same sentence.
install -d -o root -g root -m 0755 "${GATEWAY_LIVENESS_RUN_DIR}"
install -d -o root -g root -m 0755 "${GATEWAY_LIVENESS_LEDGER_DIR}"
# Converge the ledger FILE's mode every boot, not only at creation. The volume
# persists, so a seat that restarted under the 0700/umask-027 first cut carries
# a 0640 `kills` forever; `install -m 0644` on creation never re-runs for it.
# Found live on hermes-scott 2026-08-21: the gate read "Permission denied" and
# shipped gateway_restarts_last_hour as absent -- a hold, so not dangerous, but
# the one field a restart cannot race was silently missing. A reconciler, not a
# sweep (CLAUDE.md "prefer structural fixes"): every boot makes the layer
# converge on authored state. `|| true` because a missing file is the normal
# first-boot case and `set -e` is live here.
chmod 0644 "${GATEWAY_LIVENESS_LEDGER_DIR}/kills" 2>/dev/null || true
# The supervisor's state machine, one word, rewritten on every transition so the
# gate (and boot-smoke) can tell an ARMED supervisor from one that is inert,
# not-watching this pin, or refusing further restarts. Same loud-not-silent
# discipline as the log lines; this is the copy that leaves the Machine.
gateway_liveness_state() {
  # A TRANSITION must be spoken at once. gateway_liveness_nag has a 300s floor so
  # a standing condition does not drown `fly logs`, but that floor also silences
  # the FIRST line of a new condition for up to five minutes if an unrelated nag
  # happened to fire just before it — which is exactly when a human most needs
  # the log to say what changed. Zeroing last_nag on a change of word makes the
  # next nag in this iteration speak, then the floor resumes for the repeats.
  # Callers therefore set the state BEFORE they nag.
  if [ "$1" != "$(cat "${GATEWAY_LIVENESS_RUN_DIR}/state" 2>/dev/null)" ]; then
    last_nag=0
  fi
  printf '%s\n' "$1" > "${GATEWAY_LIVENESS_RUN_DIR}/state.tmp" \
    && chmod 0644 "${GATEWAY_LIVENESS_RUN_DIR}/state.tmp" \
    && mv -f "${GATEWAY_LIVENESS_RUN_DIR}/state.tmp" "${GATEWAY_LIVENESS_RUN_DIR}/state"
}

# Resolve the ACTIVE profile from the gateway's own argv, not by mtime-ordering
# /opt/data/profiles/*. A seat may carry several persona homes (ADR 0011;
# boot-smoke Step 6b reconciles the SET of them against personas[]), and only
# one belongs to the running gateway. "Newest mtime" identifies it only while it
# is healthy — precisely the assumption that stops holding in the scenario this
# supervisor exists for, and it would fail SILENTLY by watching a file nobody
# writes. An argv that no longer names hermes means the container is not in the
# state this was written for: return empty and let the caller refuse, loudly.
gateway_heartbeat_path() {
  local cmdline="${GATEWAY_LIVENESS_PROC_DIR}/${SMD_GATEWAY_PID}/cmdline"
  local tok prev='' profile='' seen_hermes=0
  [ -r "${cmdline}" ] || return 1
  # NUL-delimited `read -d` rather than `mapfile -d`, which needs bash >= 4.4.
  # The Machine ships bash 5, but a boot-critical path should not carry a
  # version dependency it does not need — and templates/tests drives this exact
  # function, so it has to run on a developer's macOS too, where /bin/bash is
  # still 3.2. The redirect (not a pipe) keeps the loop in this shell, so the
  # assignments below survive it.
  #
  # All four spellings of the profile flag, because argv is written by
  # bootstrap.sh today but read here forever. bootstrap.sh:916 uses the separated
  # short form (`-p operator`) and the live cmdline confirms it, NUL-separated
  # behind the shebang interpreter:
  #   /opt/hermes/.venv/bin/python\0/opt/hermes/.venv/bin/hermes\0-p\0operator\0gateway\0run
  # The attached and long forms cost two `case` arms and remove a class where a
  # future invocation change would silently un-watch the seat rather than fail.
  # FIRST occurrence wins in every form: `hermes -p a -p b` is the caller's bug,
  # and picking one deterministically beats picking whichever came last.
  while IFS= read -r -d '' tok; do
    case "${tok}" in *hermes*) seen_hermes=1 ;; esac
    case "${tok}" in
      -p=*|--profile=*)
        if [ -z "${profile}" ]; then profile="${tok#*=}"; fi
        ;;
    esac
    if [ -z "${profile}" ]; then
      case "${prev}" in
        -p|--profile) profile="${tok}" ;;
      esac
    fi
    prev="${tok}"
  done < "${cmdline}"
  [ "${seen_hermes}" -eq 1 ] || return 1
  [ -n "${profile}" ] || return 1
  printf '%s\n' "${GATEWAY_LIVENESS_PROFILES_DIR}/${profile}/state/gateway.heartbeat"
}

# Epoch first so the window comparison is integer arithmetic, no date parsing.
gateway_liveness_record_kill() {
  [ -f "${GATEWAY_LIVENESS_LEDGER_DIR}/kills" ] \
    || install -m 0644 -o root -g root /dev/null "${GATEWAY_LIVENESS_LEDGER_DIR}/kills"
  printf '%s %s %s\n' "$(date -u +%s)" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" \
    >> "${GATEWAY_LIVENESS_LEDGER_DIR}/kills"
}

gateway_liveness_kill_budget_ok() {
  local ledger="${GATEWAY_LIVENESS_LEDGER_DIR}/kills" cutoff now line ts count=0
  [ -f "${ledger}" ] || return 0
  now="$(date -u +%s)"
  cutoff=$(( now - SMD_GATEWAY_LIVENESS_KILL_WINDOW_SECONDS ))
  while IFS= read -r line; do
    ts="${line%% *}"
    case "${ts}" in ''|*[!0-9]*) continue ;; esac
    [ "${ts}" -ge "${cutoff}" ] && count=$(( count + 1 ))
  done < "${ledger}"
  [ "${count}" -lt "${SMD_GATEWAY_LIVENESS_MAX_KILLS}" ]
}

gateway_liveness_heartbeat_pid() {
  sed -n 's/.*"pid"[: ]*\([0-9][0-9]*\).*/\1/p' "$1" 2>/dev/null
}

# A refusal, or a supervisor that has gone inert, must never be silent — that is
# the entire defect class this PR is about. Repeat on a 5-minute floor so the
# condition shows continuously in `fly logs` without drowning the stream. Same
# discipline as the establishment intake's NOT-launched line above: a silent
# skip is indistinguishable from a healthy seat.
gateway_liveness_nag() {
  local now
  now="$(date -u +%s)"
  if [ $(( now - last_nag )) -ge 300 ]; then
    log "GATEWAY LIVENESS: $*"
    last_nag="${now}"
  fi
}

# SIGUSR2 is what finally makes gateway_faulthandler.log non-empty: Hermes
# registers an all-thread faulthandler dump on it (gateway/run.py) and nothing
# ever sent the signal, which is why the "0-byte diagnostic" reported in ss#2488
# is expected behaviour and not a defect. Best-effort, and DOUBLY gated, because
# SIGUSR2's default disposition is TERMINATE: if a future pin drops that
# registration, an unguarded send stops being a diagnostic and becomes an
# unlogged kill that skips the recovery re-check and the kill ledger below.
gateway_liveness_request_dump() {
  local hbpid
  hbpid="$(gateway_liveness_heartbeat_pid "$1")"
  if [ "${hbpid}" != "${SMD_GATEWAY_PID}" ]; then
    log "Gateway liveness: heartbeat names pid '${hbpid}', container main is ${SMD_GATEWAY_PID}; skipping the stack dump"
    return 0
  fi
  if ! grep -q 'faulthandler.register' /opt/hermes/gateway/run.py 2>/dev/null; then
    log "Gateway liveness: this Hermes pin registers no SIGUSR2 faulthandler; skipping the stack dump (sending it would terminate the process unlogged)"
    return 0
  fi
  log "Gateway liveness: SIGUSR2 to ${SMD_GATEWAY_PID} for an all-thread stack dump (best-effort — a fully frozen process may never service it)"
  kill -USR2 "${SMD_GATEWAY_PID}" 2>/dev/null
}

gateway_liveness_escalate() {
  local reason="$1" hb hbpid
  if ! gateway_liveness_kill_budget_ok; then
    # A seat that flaps every few minutes on an environmental cause is not
    # better than a seat that is down — it is the same outage plus churn, and it
    # destroys in-flight work each cycle. Stop, and keep saying so, so that the
    # next thing to touch this Machine is a human.
    gateway_liveness_nag "REFUSING to restart (${reason}): ${SMD_GATEWAY_LIVENESS_MAX_KILLS} kill(s) already inside ${SMD_GATEWAY_LIVENESS_KILL_WINDOW_SECONDS}s. This seat is flapping and needs a human."
    gateway_liveness_state refusing
    return 0
  fi
  gateway_liveness_record_kill "${reason}"
  # SIGTERM first. It does nothing for a genuinely wedged loop — the handler
  # would have to run ON that loop — and that is exactly the point: it costs
  # 15s and buys a clean shutdown (audit WAL flushed, spool drained) in the
  # false-positive case where the process is alive and healthy and we misread a
  # starved heartbeat as a dead one.
  log "GATEWAY LIVENESS: restarting the seat (${reason}) — SIGTERM to container main ${SMD_GATEWAY_PID}"
  kill -TERM "${SMD_GATEWAY_PID}" 2>/dev/null
  sleep "${SMD_GATEWAY_LIVENESS_TERM_GRACE_SECONDS}"
  [ -d "${GATEWAY_LIVENESS_PROC_DIR}/${SMD_GATEWAY_PID}" ] || return 0
  log "GATEWAY LIVENESS: SIGKILL to container main ${SMD_GATEWAY_PID}; tini exits non-zero and Fly replaces the Machine"
  kill -KILL "${SMD_GATEWAY_PID}" 2>/dev/null
  for _ in 1 2 3; do
    sleep "${SMD_GATEWAY_LIVENESS_KILL_VERIFY_SECONDS}"
    [ -d "${GATEWAY_LIVENESS_PROC_DIR}/${SMD_GATEWAY_PID}" ] || return 0
  done
  # Still alive 30s after SIGKILL. The kill target is the one thing ss#2488
  # could NOT explain — the gateway pid moved 655 -> 657 while the container
  # never restarted — so rather than assume, fall back to whatever pid the
  # heartbeat itself names, and say plainly that the first target was wrong.
  log "GATEWAY LIVENESS: ${SMD_GATEWAY_PID} SURVIVED SIGKILL for $(( SMD_GATEWAY_LIVENESS_KILL_VERIFY_SECONDS * 3 ))s — falling back to the pid named in the heartbeat"
  hb="$(gateway_heartbeat_path)"
  [ -n "${hb}" ] && [ -e "${hb}" ] || return 0
  hbpid="$(gateway_liveness_heartbeat_pid "${hb}")"
  case "${hbpid}" in
    ''|*[!0-9]*) return 0 ;;
    "${SMD_GATEWAY_PID}") return 0 ;;
  esac
  log "GATEWAY LIVENESS: SIGKILL to heartbeat-named pid ${hbpid}"
  kill -KILL "${hbpid}" 2>/dev/null
}

(
  # This loop MUST outlive every failing probe. entrypoint.sh runs under
  # `set -euo pipefail` (line 4) and a backgrounded subshell INHERITS it, so a
  # vanished heartbeat, a `stat` on a file mid-replace, a `kill` on a pid that
  # just died, or arithmetic on an empty mtime would silently END the supervisor
  # for the life of the container — the exact failure class this PR exists to
  # fix, reproduced one level up and even harder to see. The broker loop above
  # pays for the same hazard with an `if` guard (see its comment); this loop
  # turns `set -e` off outright, because nearly every line in it is a probe that
  # is ALLOWED to fail.
  #
  # On the `( ... ) &` secret-carry hazard (ss#2420): a fork keeps its parent's
  # execve-time environ for life, and no `unset` rewrites it — which is why the
  # skill reconciler and the webhook gate are exec'd with `env -u`. It does not
  # apply here. Those two run at the AGENT uid, where a same-uid process can read
  # /proc/<pid>/environ; this one stays root, like the appliers and the intake
  # above, and the hermes uid cannot read a root process's environ at all. The
  # supervisor also needs no credential of any kind — it reads a file mtime and
  # sends signals.
  set +e
  if [ ! -f "${GATEWAY_LIVENESS_HEARTBEAT_WRITER}" ] || \
     ! grep -q 'loop_heartbeat_forever' "${GATEWAY_LIVENESS_HEARTBEAT_WRITER}"; then
    log "GATEWAY LIVENESS: NOT watching — this Hermes pin has no loop heartbeat (${GATEWAY_LIVENESS_HEARTBEAT_WRITER} absent or without loop_heartbeat_forever). A wedged gateway on this seat will NOT self-recover."
    gateway_liveness_state not-watching
    exit 0
  fi
  armed=0
  stale_streak=0
  last_nag=0
  boot_epoch="$(date -u +%s)"
  gateway_liveness_state not-armed
  while true; do
    # Tick FIRST, every iteration. This file is the supervisor's own liveness
    # proof and boot-smoke asserts its freshness. A pid file would only prove a
    # number was written once — the "check confirmed a process EXISTED rather
    # than that it WORKED" shape boot-smoke-test.sh warns about after the
    # 2026-07-16 scheduler outage ran eight days green on exactly that.
    touch "${GATEWAY_LIVENESS_RUN_DIR}/tick"
    chmod 0644 "${GATEWAY_LIVENESS_RUN_DIR}/tick" 2>/dev/null
    sleep "${SMD_GATEWAY_LIVENESS_POLL_SECONDS}"
    now="$(date -u +%s)"

    hb="$(gateway_heartbeat_path)"
    if [ -z "${hb}" ]; then
      # UNRESOLVED ARGV IS NOT THE SAME CONDITION BEFORE AND AFTER THE EXEC.
      #
      # This branch used to report `inert` unconditionally, and `inert` is a
      # paging state: gateway_loop_check.py forwards the word and fleet-alerts'
      # gateway_supervisor_inert fires on it, saying "it will never act". But the
      # supervisor is forked while still root, MINUTES before the gateway exists.
      # entrypoint execs bootstrap.sh, which stages skills, syncs the voice
      # vault, runs the appliers and enables plugins before its own exec at
      # bootstrap.sh:916 — so for that whole window /proc/<SMD_GATEWAY_PID>/cmdline
      # legitimately reads `bash /app/bootstrap.sh`, names no hermes, and carries
      # no profile.
      #
      # Observed live on pilot-smokeball 2026-09-01T02:30:37Z: this exact line
      # fired for /proc/652 while the bootstrap log two seconds earlier and five
      # seconds later showed it seeding the skill catalog. The same boot resolved
      # the profile normally once the exec landed. So the historical reading of
      # this signal is inverted — a healthy boot produced the alarm, which is the
      # cheapest way to teach everyone to ignore it.
      #
      # The parse is not the defect and was not changed to fix this: it walks
      # NUL-split argv and resolves the real live cmdline correctly. WHAT IT
      # CANNOT DO IS TELL TIME. The clock is the only thing that separates "the
      # gateway has not started yet" from "the gateway will never be found", so
      # the clock is what decides which of the two we say.
      if [ $(( now - boot_epoch )) -lt "${SMD_GATEWAY_LIVENESS_STARTUP_GRACE_SECONDS}" ]; then
        gateway_liveness_state starting
        gateway_liveness_nag "argv at ${GATEWAY_LIVENESS_PROC_DIR}/${SMD_GATEWAY_PID}/cmdline does not name hermes yet — bootstrap has not reached its gateway exec ($(( now - boot_epoch ))s into a ${SMD_GATEWAY_LIVENESS_STARTUP_GRACE_SECONDS}s startup grace). NOT inert: re-checking every ${SMD_GATEWAY_LIVENESS_POLL_SECONDS}s, and if argv still does not resolve at the end of the grace this becomes an INERT page."
      else
        gateway_liveness_state inert
        gateway_liveness_nag "cannot resolve the gateway profile from ${GATEWAY_LIVENESS_PROC_DIR}/${SMD_GATEWAY_PID}/cmdline after $(( now - boot_epoch ))s (startup grace ${SMD_GATEWAY_LIVENESS_STARTUP_GRACE_SECONDS}s); supervisor is INERT and this seat has no automatic recovery"
      fi
      continue
    fi

    if [ ! -e "${hb}" ]; then
      # No heartbeat yet. Never kill a slow boot — but do not hand a boot-time
      # wedge to "Fly's job" either: the Fly check is served by the webhook
      # gate's /health, which is a literal constant, and the process never
      # exits. Left alone, this window is the original bug in miniature.
      if [ "${armed}" -eq 0 ] && [ $(( now - boot_epoch )) -ge "${SMD_GATEWAY_LIVENESS_BOOT_DEADLINE_SECONDS}" ]; then
        gateway_liveness_nag "gateway has written NO loop heartbeat in $(( now - boot_epoch ))s (deadline ${SMD_GATEWAY_LIVENESS_BOOT_DEADLINE_SECONDS}s); ${hb} absent"
        gateway_liveness_escalate never-armed
        # Restart the deadline clock. Without this the condition is still true
        # on the next poll, and the ledger's whole budget burns in three ticks
        # instead of bounding three genuinely separate attempts.
        boot_epoch="$(date -u +%s)"
      fi
      continue
    fi

    mtime="$(stat -c %Y "${hb}" 2>/dev/null)"
    case "${mtime}" in ''|*[!0-9]*) continue ;; esac
    age=$(( now - mtime ))

    if [ "${age}" -le "${SMD_GATEWAY_LIVENESS_STALE_SECONDS}" ]; then
      [ "${armed}" -eq 0 ] && log "Gateway liveness supervisor ARMED (loop heartbeat ${hb} is ${age}s fresh)"
      [ "${armed}" -eq 0 ] && gateway_liveness_state armed
      armed=1
      stale_streak=0
      continue
    fi

    if [ "${armed}" -eq 0 ]; then
      # The volume PERSISTS, so a heartbeat from a PREVIOUS boot is on disk at
      # every cold start. Arming on it would kill every boot, forever. Say so
      # out loud — a silent skip here reads identically to a healthy seat.
      #
      # BUT: not arming has no deadline of its own, and that was the blind spot.
      # A gateway that wedges DURING startup never writes a first beat, so this
      # branch is where it lands — and before this grace it stayed here forever,
      # nagging on a 5-minute floor into `fly logs` and reaching no inbox at all.
      # The state stayed `not-armed`, which fleet-alerts does not page on (nor
      # should it: `not-armed` is also every healthy seat's first 30 seconds).
      # The 2026-09-01 crash loop is the proof — pilot-smokeball emitted this
      # exact line at 02:21:54, 02:27:01 and 02:35:44 across three separate boots
      # while restarting every ~15 minutes, and no instrument on the seat treated
      # it as a fault.
      #
      # We PAGE and we do NOT kill. Killing is what sustained the loop: hermes'
      # own in-process watchdog already hard-exits 75 on its own budget
      # (gateway/shutdown_watchdog.py:196), and a slow-starting gateway that gets
      # signalled here just boots colder and slower next time. There is no
      # recovery action a supervisor can take against "startup is too slow" — the
      # fixes are memory, a lighter connector set, or a lazier import, and all
      # three need a human. So the correct output is a person, not a signal.
      #
      # The page rides the state word, the same channel as every other supervisor
      # condition: gateway_loop_check.py forwards it on the heartbeat and
      # fleet-alerts' gateway_never_healthy fires on it. The entrypoint has no
      # other outbound path — it cannot reach the network and holds no credential
      # — which is by design (see the environ note at the top of this subshell).
      if [ $(( now - boot_epoch )) -ge "${SMD_GATEWAY_LIVENESS_STARTUP_GRACE_SECONDS}" ]; then
        gateway_liveness_state never-healthy
        gateway_liveness_nag "GATEWAY NEVER CAME UP: loop heartbeat ${hb} is ${age}s stale and no fresh beat has appeared in the $(( now - boot_epoch ))s since this supervisor started (startup grace ${SMD_GATEWAY_LIVENESS_STARTUP_GRACE_SECONDS}s). The gateway is wedged DURING startup. NOTHING AUTOMATIC WILL HAPPEN: this supervisor will not kill a slow-starting gateway, and it will keep re-checking every ${SMD_GATEWAY_LIVENESS_POLL_SECONDS}s and clear itself the moment a fresh beat appears. A human must look — check memory headroom and the gateway's startup log."
        continue
      fi
      gateway_liveness_nag "loop heartbeat ${hb} is ${age}s stale but has never been seen fresh this boot; NOT arming (stale beat from a previous boot). $(( now - boot_epoch ))s into a ${SMD_GATEWAY_LIVENESS_STARTUP_GRACE_SECONDS}s startup grace; if no fresh beat arrives by then this becomes a never-healthy page."
      continue
    fi

    stale_streak=$(( stale_streak + 1 ))
    if [ "${stale_streak}" -lt 2 ]; then
      log "Gateway liveness: loop heartbeat ${age}s stale (sample ${stale_streak}); one more before acting"
      continue
    fi

    log "GATEWAY WEDGE: loop heartbeat ${hb} is ${age}s stale across ${stale_streak} consecutive samples (threshold ${SMD_GATEWAY_LIVENESS_STALE_SECONDS}s)"
    # Budget FIRST, before the dump. Signalling a process we have already
    # decided not to restart is perturbation without a plan: the diagnostic
    # dump was captured on the first kill in this window, and attempts 4..N
    # add nothing but risk (SIGUSR2's default disposition is terminate) and a
    # 20s sleep per cycle. escalate() re-checks the budget as the authoritative
    # gate — it also guards the never-armed path, which does not come through
    # here.
    if ! gateway_liveness_kill_budget_ok; then
      gateway_liveness_nag "REFUSING to restart (loop-wedge, heartbeat ${age}s stale): ${SMD_GATEWAY_LIVENESS_MAX_KILLS} kill(s) already inside ${SMD_GATEWAY_LIVENESS_KILL_WINDOW_SECONDS}s. This seat is flapping and needs a human."
      gateway_liveness_state refusing
      stale_streak=0
      continue
    fi
    gateway_liveness_request_dump "${hb}"
    sleep "${SMD_GATEWAY_LIVENESS_DUMP_GRACE_SECONDS}"
    # Never block on the dump, and never kill on a reading we did not
    # re-confirm after it: the loop may have come back while we waited.
    now="$(date -u +%s)"
    mtime="$(stat -c %Y "${hb}" 2>/dev/null)"
    case "${mtime}" in ''|*[!0-9]*) mtime=0 ;; esac
    if [ $(( now - mtime )) -le "${SMD_GATEWAY_LIVENESS_STALE_SECONDS}" ]; then
      log "Gateway liveness: loop recovered during the dump grace ($(( now - mtime ))s); NOT killing"
      stale_streak=0
      continue
    fi
    gateway_liveness_escalate loop-wedge
    stale_streak=0
  done
) &
log "Root gateway liveness supervisor forked (uid 0; watches the Hermes loop heartbeat and kills container main ${SMD_GATEWAY_PID} so Fly replaces the Machine; stale>${SMD_GATEWAY_LIVENESS_STALE_SECONDS}s, max ${SMD_GATEWAY_LIVENESS_MAX_KILLS} kill(s)/${SMD_GATEWAY_LIVENESS_KILL_WINDOW_SECONDS}s)"

# MCP channel cross-process result/thread store (shared/mcp_result_store.py +
# shared/mcp_thread_store.py). The webhook gate (:8643) and the agent's result-sink
# plugin (inside the Hermes gateway) BOTH read/write here to hand a synchronous
# /mcp answer back to the caller; the default path is /run/smd-mcp. /run is a
# root-owned tmpfs, so the unprivileged hermes processes cannot mkdir it themselves
# (Permission denied → the answer never lands and the gate's 55s poll always times
# out; first surfaced on the Machine-hosted /mcp path, hermes-pilot-smokeball
# 2026-06-24). Create it hermes-owned now, while still root — both processes run as
# hermes (the exec-drop below), so a single hermes-owned 0700 dir serves both.
# tmpfs is correct: results are short-lived and scoped to one in-flight request.
MCP_STORE_DIR="/run/smd-mcp"
mkdir -p "${MCP_STORE_DIR}"
chown hermes:hermes "${MCP_STORE_DIR}"
chmod 0700 "${MCP_STORE_DIR}"

# Connector-health ledger dir (ADR 0080, shared/connector_ledger.py): the
# agent-side plugin and the Graph channel chokepoint write per-server call
# outcomes here; the gate's heartbeat connector_check reads them. Same trap as
# smd-mcp above — /run is root-owned, hermes cannot mkdir it (first surfaced
# live on hermes-smd-staging 2026-07-25: every record silently failed and a
# real Graph 401 outage read as legit-empty green). This dir is a BOOT
# CONTRACT: connector_check treats a missing dir as check-broken and PAGES.
CONNECTOR_HEALTH_DIR="/run/smd-connector-health"
mkdir -p "${CONNECTOR_HEALTH_DIR}"
chown hermes:hermes "${CONNECTOR_HEALTH_DIR}"
chmod 0700 "${CONNECTOR_HEALTH_DIR}"

# ============================================================================
# ADR 0009 / SEC-22 — cross-machine isolation boot check (fail-closed)
# ============================================================================
# The last root gate before the privilege-drop to the hermes gateway. Verify
# that every Phase-1 storage binding — the per-customer R2 skill-bodies bucket,
# the shared config bucket, and the on-volume SQLite paths (SMD_D1_AUDIT_BINDING
# / SMD_D1_AGENT_STATE_BINDING) — resolves to THIS Machine's own customer
# namespace, derived from CUSTOMER_SLUG. A binding that names another customer's
# slug, escapes the volume root (ADR 0007), or is unbound is the cross-Machine
# isolation failure mode ADR 0009 exists to catch: refuse to serve.
#
# verify_at_boot() (the invariant_7 __main__ shim) reads the real env; on a
# violation it prints the offending binding to stderr AND emits an
# INVARIANT_BOOT_CHECK_FAILED row through the broker's append-only audit_append
# (SMD_AUDIT_BROKER_SOCKET is live at this point — the broker started above, and
# this root process is still the admitted SMD_GATEWAY_PID peer). The exit code
# is the load-bearing refusal; audit-emit is best-effort and never weakens it.
# On a clean boot verify_at_boot returns 0 without touching the broker socket.
#
# Runs BEFORE any skill loads, connector authenticates, or memory read — all of
# that is post-exec, in bootstrap/gateway. FAIL-CLOSED on a degraded substrate:
# a missing OR unimportable invariant module is itself a refusal (exit 3), never
# a silent skip — no stub/NoOp path reports a false pass (the standing
# fail-closed posture, e.g. bootstrap.sh's harness-less-gateway gates).
INVARIANT7_BOOT_CHECK="/app/safety-substrate/invariants/invariant_7.py"
if [ ! -f "${INVARIANT7_BOOT_CHECK}" ]; then
  log "FATAL: cross-machine isolation boot check module missing (${INVARIANT7_BOOT_CHECK}); refusing to boot (ADR 0009 / SEC-22)"
  exit 3
fi
if /opt/hermes/.venv/bin/python3 "${INVARIANT7_BOOT_CHECK}"; then
  log "Cross-machine isolation boot check PASSED (ADR 0009 / SEC-22)"
else
  log "FATAL: INVARIANT_BOOT_CHECK_FAILED — cross-machine isolation boot check refused boot (ADR 0009 / SEC-22); see stderr for the offending binding or a module import error (both fail closed)"
  exit 3
fi

# ============================================================================
# ss ADR 0083 / #2084 — authored-spec tree ownership boot check (fail-closed)
# ============================================================================
# The second root gate before the privilege drop. Verify that ${SMD_SPEC_DIR}
# and everything under it is NOT writable by the hermes uid, and that nothing in
# it symlinks out of the tree.
#
# WHY IT REFUSES BOOT rather than warning. An authored spec enters the drafting
# context by being READ, and `read_file` is READ-class: unfenced, always allowed,
# and it does not taint the session. A spec the agent can WRITE is therefore a
# persistent, untainted, self-authored instruction channel surviving restarts —
# worse than a tainted inbound email, which at least fences its turn. This is the
# same self-loopback shape the keystone comment at the top of this file records
# from hermes-smd-staging 2026-06-15, and it got the same answer: root ownership,
# structurally enforced, not a policy anyone has to remember.
#
# An ABSENT spec dir PASSES — no spec installed means nothing to author and
# nothing any consumer reads. Same fail-closed posture as the invariant_7 gate
# above: a missing or unimportable module is itself a refusal (exit 3), never a
# silent skip.
SPEC_OWNERSHIP_CHECK="/app/safety-substrate/invariants/spec_dir_ownership.py"
if [ ! -f "${SPEC_OWNERSHIP_CHECK}" ]; then
  log "FATAL: authored-spec ownership boot check module missing (${SPEC_OWNERSHIP_CHECK}); refusing to boot (ss ADR 0083)"
  exit 3
fi
if /opt/hermes/.venv/bin/python3 "${SPEC_OWNERSHIP_CHECK}"; then
  log "Authored-spec tree ownership check PASSED (ss ADR 0083)"
else
  log "FATAL: SPEC_DIR_OWNERSHIP_CHECK_FAILED — the authored-spec tree is writable by the agent uid, or reaches outside itself; refusing to boot (ss ADR 0083); see stderr for the offending paths"
  exit 3
fi

exec setpriv \
  --reuid=hermes \
  --regid=hermes \
  --init-groups \
  --no-new-privs \
  /app/bootstrap.sh
