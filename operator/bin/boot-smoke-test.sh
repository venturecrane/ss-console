#!/usr/bin/env bash
# boot-smoke-test.sh — verify the dependency chain inside a customer Machine
#
# Usage:
#   operator/bin/boot-smoke-test.sh <customer-slug>
#
# Scope: this is a SMOKE TEST, not an end-to-end test. It verifies that
# bootstrap.sh's sequenced startup (customer.yaml fetched from R2 → Hermes
# profiles materialized → overlay plugins installed → curator disabled) came
# up cleanly. The Postgres/Redis/Honcho checks were removed when the Honcho
# data plane was deferred to Phase 2 (ADR 0016 revised). It does NOT exercise
# a real agent turn, a real LLM call,
# or a real D1 write through a connector. Those require live external
# credentials (MCP servers, Anthropic API, customer's OAuth tokens) and are
# the job of the per-connector prod smoke test in run_prod_smoke_test.py and,
# at higher fidelity, the boot-time end-to-end test described in §6 of the
# build plan.
#
# Each check logs PASS/FAIL with the slug + step name. EVERY check runs: a red
# one is recorded and the run continues, and the summary at the end lists all of
# them. Exit code: 0 only if every check passed. A failed PRECONDITION (Machine
# never started, this checkout cannot parse the pin) still aborts, because every
# check after one of those is noise rather than evidence. See ss#2487.

set -euo pipefail

SLUG="${1:-}"
[ -n "${SLUG}" ] || { echo "Usage: $0 <customer-slug>" >&2; exit 1; }

APP_NAME="hermes-${SLUG}"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [smoke/${SLUG}] $*"; }

# CHECK RESULTS ARE COLLECTED, NOT FATAL (ss#2487).
#
# This script used to exit on the first red. On hermes-ashton-price -- the
# paying client's seat -- check 6 sampled `! test -w /var/lib/smd-config` while
# the provisioner was still materializing `specs/` into that directory. A race,
# not a permission bug: a manual re-run four minutes later passed every check.
# But the abort meant checks 7 through 42 never ran, and those include
# `matter-mixing-fence`, the three credential-stripping assertions, and the four
# medchron gate-refusal probes. The operator saw "FATAL: dependency chain is
# unhealthy", which reads exactly the same whether the seat is fine or genuinely
# compromised, and the only way to tell was to re-run the script by hand.
#
# It recurred: a later run logged 39 PASS then FATALed and skipped the rest.
#
# A gate that stops at the first red hides the state of everything after it, and
# the entire value of 42 checks is seeing WHICH ones failed together. So a failed
# check is recorded and the run continues; the summary at the end prints every
# failure and the exit code is non-zero if there was any.
PASS_COUNT=0
FAILED_CHECKS=()

pass() {
  PASS_COUNT=$((PASS_COUNT + 1))
  log "PASS: $*"
}

# A CHECK failed. Record it, keep going, and return 0 so `set -e` and the
# `|| check_fail ...` callers below do not abort the run.
check_fail() {
  FAILED_CHECKS+=("$*")
  log "FAIL: $*"
  return 0
}

# A PRECONDITION failed, and every check after it would be noise rather than
# evidence: the Machine never started, or this checkout cannot parse the pin it
# is supposed to compare against. Distinct from check_fail on purpose. Softening
# these would produce 40 cascading failures whose real cause is one line, which
# is a different way of hiding the answer.
fail() { log "FATAL: $*"; exit 1; }

# Print every result and exit non-zero if any check failed. Registered as an EXIT
# trap so a precondition abort still gets a summary of what had run.
summarize() {
  local code=$?
  local n=${#FAILED_CHECKS[@]}
  log "----- boot smoke summary for ${APP_NAME} -----"
  log "checks passed: ${PASS_COUNT}"
  log "checks failed: ${n}"
  if [ "${n}" -gt 0 ]; then
    local c
    for c in "${FAILED_CHECKS[@]}"; do log "  FAILED: ${c}"; done
  fi
  # A precondition abort already carries its own non-zero code; do not mask it.
  # A non-zero code with NOTHING recorded is the third case and the confusing
  # one: an unguarded command between checks died under `set -e`, so the run
  # stopped without a FAIL or a FATAL. On the fail-fast script that was a silent
  # death; printing "checks failed: 0" beside a non-zero exit would be worse
  # than silence, so name it.
  if [ "${code}" -ne 0 ]; then
    if [ "${n}" -eq 0 ]; then
      log "Run ENDED EARLY at an unguarded error (exit ${code}) after ${PASS_COUNT} check(s)."
      log "No check failed. The checks after that point did not run and their state is UNKNOWN."
    fi
    exit "${code}"
  fi
  if [ "${n}" -gt 0 ]; then
    log "Boot smoke FAILED for ${APP_NAME}: ${n} check(s) red"
    exit 1
  fi
}
trap summarize EXIT

# ssh_exec <step-name> <command>
# Run a command inside the Machine via `fly ssh console`. The command is
# passed as a single string; non-zero exit fails the test with the step name.
ssh_exec() {
  local step="$1"
  shift
  local cmd="$*"
  # `fly ssh console --command` execs the string directly (no shell), so compound
  # commands (&&, |, $(...), [ ]) fail unless wrapped in an explicit shell. Wrap
  # every check in `sh -c` so shell constructs evaluate ON THE MACHINE. (Check
  # commands contain no single quotes, so the single-quoted wrapper is safe.)
  if fly ssh console -a "${APP_NAME}" --command "sh -c '${cmd}'" >/dev/null 2>&1; then
    pass "${step}"
  else
    check_fail "${step} — command failed: ${cmd}"
  fi
}

ssh_exec_script() {
  # As ssh_exec, but for a check that CONTAINS SINGLE QUOTES.
  #
  # ssh_exec wraps its command in `sh -c '...'`, so a check carrying a single
  # quote closes the wrapper early and the remainder is mangled — silently, and
  # presenting as a failed check rather than a broken one. The note above has
  # kept every existing check quote-free, which works right up until a check
  # genuinely needs a quoted string (an inline python program, say), at which
  # point the constraint costs more than it saves.
  #
  # Base64 carries the program through both shell layers untouched: the encoded
  # text contains no quotes at all, so nothing can collide. Decoded and run ON
  # THE MACHINE, exactly as written here.
  local step="$1"
  shift
  local cmd="$*"
  local encoded
  encoded="$(printf '%s' "${cmd}" | base64 | tr -d '\n')"
  if fly ssh console -a "${APP_NAME}" \
    --command "sh -c 'echo ${encoded} | base64 -d | sh'" >/dev/null 2>&1; then
    pass "${step}"
  else
    check_fail "${step} — command failed: ${cmd}"
  fi
}

# ---------- Step 1: wait for Machine state=started ----------
log "Waiting for Machine state=started (up to 60s)..."
ATTEMPT=0
while [ "${ATTEMPT}" -lt 60 ]; do
  STATE="$(fly status -a "${APP_NAME}" --json 2>/dev/null \
    | python3 -c "import sys, json
try:
    d = json.load(sys.stdin)
    machines = d.get('Machines') or []
    print(machines[0]['state'] if machines else 'none')
except Exception:
    print('error')" 2>/dev/null || echo "error")"
  if [ "${STATE}" = "started" ]; then
    pass "machine-state-started (after ${ATTEMPT}s)"
    break
  fi
  ATTEMPT=$((ATTEMPT + 1))
  sleep 1
done
[ "${STATE}" = "started" ] || fail "machine-state-started — state=${STATE} after 60s"

# ---------- Step 1b: the guest is the size customer.yaml authored ----------
# ss#2612: the template hardcoded `cpus = 1`, so an authored shared-cpu-2x
# rendered a 2x label on a one-vCPU guest and nothing noticed. The guest Fly
# reports is compared to the authored size and memory, and the cpu count is
# derived by the same function provisioning used (operator/bin/lib/machine-size.sh).
CUSTOMER_YAML="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/operator/customers/${SLUG}/customer.yaml"
if [ -f "${CUSTOMER_YAML}" ]; then
  # shellcheck source=lib/machine-size.sh
  source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/machine-size.sh"
  # Parse with the same interpreter shape provisioning and step 7b use (uv +
  # pyyaml): the workstation python3 has no yaml module (step 7b's comment,
  # re-proven here 2026-08-31), and the first cut's bare
  # `python3 -c "import yaml" ... 2>/dev/null || echo ""` swallowed that
  # ImportError into EMPTY authored values, so a successful pilot reprovision
  # FATALed with the nonsense "authored /MB, Fly reports shared-1-1024" —
  # the instrument failed, not the seat (live 2026-08-31). A check that cannot
  # fail as itself measured nothing: the dependency is probed loudly first,
  # and a genuine parse error now fails the step naming itself instead of
  # feeding an empty value into the comparison.
  command -v uv >/dev/null 2>&1 \
    || fail "guest-matches-authored-size — uv not on PATH ($(command -v python3 || echo python3) has no pyyaml; this script parses customer.yaml via 'uv run --with pyyaml' — install uv or run from a shell where it resolves)"
  AUTHORED_SIZE="$(uv run --quiet --with pyyaml python3 -c 'import sys, yaml; print((yaml.safe_load(open(sys.argv[1])) or {})["machine"]["size"])' "${CUSTOMER_YAML}")" \
    || fail "guest-matches-authored-size — could not parse machine.size from ${CUSTOMER_YAML}"
  AUTHORED_MEM="$(uv run --quiet --with pyyaml python3 -c 'import sys, yaml; print((yaml.safe_load(open(sys.argv[1])) or {})["machine"]["memory_mb"])' "${CUSTOMER_YAML}")" \
    || fail "guest-matches-authored-size — could not parse machine.memory_mb from ${CUSTOMER_YAML}"
  # Same silent-empty pattern, same comparison: machine_cpus already refuses
  # an unrecognised size loudly on stderr — let that surface instead of
  # muting it into the empty string the [ -n ... ] guard below turns into
  # the same misleading FATAL.
  AUTHORED_CPUS="$(machine_cpus "${AUTHORED_SIZE}")" \
    || fail "guest-matches-authored-size — machine_cpus has no cpu count for authored machine.size '${AUTHORED_SIZE}'"
  GUEST="$(fly status -a "${APP_NAME}" --json 2>/dev/null \
    | python3 -c "import sys, json
try:
    d = json.load(sys.stdin)
    g = (d.get('Machines') or [{}])[0].get('config', {}).get('guest', {})
    print(f\"{g.get('cpu_kind')}-{g.get('cpus')}-{g.get('memory_mb')}\")
except Exception:
    print('error')" 2>/dev/null || echo "error")"
  if [ -n "${AUTHORED_CPUS}" ] && [ "${GUEST}" = "$(machine_cpu_kind "${AUTHORED_SIZE}")-${AUTHORED_CPUS}-${AUTHORED_MEM}" ]; then
    pass "guest-matches-authored-size (${AUTHORED_SIZE}, ${AUTHORED_MEM} MB, ${AUTHORED_CPUS} vCPU)"
  else
    check_fail "guest-matches-authored-size — authored ${AUTHORED_SIZE}/${AUTHORED_MEM}MB, Fly reports ${GUEST}"
  fi
fi

# ---------- Steps 2-4: Postgres / Redis / Honcho — DEFERRED (Phase 2) ----------
# The Honcho data plane is deferred to Phase 2 (ADR 0016 revised); Phase 1 boots
# on Hermes' flat-file memory core, so there is no Postgres/Redis/Honcho to
# probe here. These checks return when Phase 2 vendors the real Honcho source.

# ---------- Step 5: customer.yaml present, root-owned, agent-read-only (keystone) ----------
# The live customer.yaml is the source every trust-ceiling / vertical-floor / scope
# decision resolves against, read fresh per action. The 2026-06-15 keystone moved
# it OFF the agent-writable /opt/data volume into the root-owned /var/lib/smd-config
# so the hermes uid can READ it (the trust gate must) but can NEITHER rewrite it
# (it owned the file before — proven exploitable: one sed flipped external_send
# draft_for_review->autonomous) NOR rename it (it owned the parent dir). These are
# the negative-fire conformance proof of that close (SEC-07/08/09/18/30, EFF-14).
ssh_exec "customer-yaml-present" "test -s /var/lib/smd-config/customer.yaml"
ssh_exec "customer-yaml-root-owned" "[ \"\$(stat -c %U /var/lib/smd-config/customer.yaml)\" = root ]"
ssh_exec "customer-yaml-dir-root-owned" "[ \"\$(stat -c %U /var/lib/smd-config)\" = root ]"
ssh_exec "customer-yaml-agent-readable" "setpriv --reuid=hermes --regid=hermes --init-groups test -r /var/lib/smd-config/customer.yaml"
ssh_exec "customer-yaml-not-agent-writable" "setpriv --reuid=hermes --regid=hermes --init-groups sh -c \"! test -w /var/lib/smd-config/customer.yaml\""
ssh_exec "customer-yaml-dir-not-agent-writable" "setpriv --reuid=hermes --regid=hermes --init-groups sh -c \"! test -w /var/lib/smd-config\""
ssh_exec "customer-yaml-absent-from-agent-volume" "! test -e /opt/data/customer.yaml"

# ---------- Step 6: Hermes profiles directory exists ----------
# bootstrap.sh's `hermes-smd bootstrap` step materializes one profile per
# entry in customer.yaml.personas[]. v1 customers ship at length 1; we just
# verify the parent directory exists and is non-empty.
ssh_exec "hermes-profiles-dir" "test -d /opt/data/profiles && [ -n \"\$(ls -A /opt/data/profiles)\" ]"

# ---------- Step 6b: no unauthored profile homes ----------
# The on-volume profile set must EQUAL the authored persona set — not merely
# contain it. A persona slug rename once left the retired slug's home (and
# its frozen cron store) on the volume for 12 days until the scheduler
# monitoring paged on a store nothing serves (#2009). translate now deletes
# orphans (overlay#185); this check proves it held on THIS boot, so any
# future orphan class in profiles/ fails the smoke test instead of lurking.
# Dot-prefixed entries and plain files are exempt, matching the reconciler.
# QUOTING CONSTRAINT: ssh_exec wraps the command in sh -c '...' — the check
# must contain NO single quotes and NO newlines (the first cut of this step
# used both and never parsed on the Machine; the volume was clean, the check
# was broken). Python strings below are double-quoted only, one line.
ssh_exec "no-unauthored-profile-homes" "/opt/hermes/.venv/bin/python3 -c \"import sys, yaml, pathlib; a = {p[\\\"slug\\\"] for p in (yaml.safe_load(open(\\\"/var/lib/smd-config/customer.yaml\\\")) or {}).get(\\\"personas\\\", [])}; d = {e.name for e in pathlib.Path(\\\"/opt/data/profiles\\\").iterdir() if e.is_dir() and not e.name.startswith(\\\".\\\")}; drift = sorted(d - a) + sorted(a - d); sys.stderr.write(f\\\"profile-home drift: orphans={sorted(d - a)} missing={sorted(a - d)}\\n\\\") if drift else None; sys.exit(1 if drift else 0)\""

# ---------- Step 7: overlay plugins installed ----------
# `hermes plugins list` should include the four hermes-smd-* plugins
# installed at image-build time via `hermes plugins install venturecrane/hermes-smd-overlay`.
ssh_exec "hermes-plugins-installed" "/opt/hermes/.venv/bin/hermes plugins list | grep -q hermes-smd-"

# ---------- Step 7b: the running Hermes IS the pinned Hermes ----------
# Hermes v0.18.0 -> v0.20.4 promotion (ss-console#2444). Every check above passes
# identically on 0.18 and 0.20, so until now boot-smoke could not tell a promoted
# seat from one a peer quietly rebuilt at the old pin from origin/main. The
# image bakes the cloned upstream commit at /opt/hermes/HERMES_SHA (Dockerfile
# hermes_source stage); the authored pin is customer.yaml `hermes_ref`
# (<tag>@<40-hex sha>), read from THIS checkout's operator/customers/<slug>/ —
# the same file provision-customer.sh rendered the build from. A mismatch is the
# one failure that means "the wrong Hermes is running", so it is a FAIL, not a log.
REPO_ROOT_FOR_PIN="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CUSTOMER_YAML_FOR_PIN="${REPO_ROOT_FOR_PIN}/operator/customers/${SLUG}/customer.yaml"
if [ -f "${CUSTOMER_YAML_FOR_PIN}" ]; then
  # Same interpreter shape provision-customer.sh uses (uv + pyyaml; no yq, and
  # the host python3 has no yaml module).
  EXPECTED_HERMES_SHA="$(uv run --quiet --with pyyaml python3 -c 'import sys, yaml; ref = str((yaml.safe_load(open(sys.argv[1])) or {}).get("hermes_ref", "")); print(ref.split("@", 1)[1] if "@" in ref else "")' "${CUSTOMER_YAML_FOR_PIN}")"
  if printf '%s' "${EXPECTED_HERMES_SHA}" | grep -qE '^[0-9a-f]{40}$'; then
    ssh_exec "hermes-sha-matches-pin" "[ \"\$(tr -d \"[:space:]\" < /opt/hermes/HERMES_SHA)\" = ${EXPECTED_HERMES_SHA} ]"
  else
    fail "hermes-sha-matches-pin — could not read a 40-hex sha from hermes_ref in ${CUSTOMER_YAML_FOR_PIN}"
  fi
else
  fail "hermes-sha-matches-pin — no customer.yaml at ${CUSTOMER_YAML_FOR_PIN} (run from the checkout that provisioned ${SLUG})"
fi

# ---------- Step 8: Hermes curator disabled (ADR 0017) ----------
# The autonomous curator is turned off per-customer (it rewrites agent-authored
# skills out of band — see docs/adr/0017). bootstrap.sh step 7b enforces
# curator.enabled:false in every profile config; --check re-verifies it held.
ssh_exec "curator-disabled" "/opt/hermes/.venv/bin/python3 /app/ensure-curator-disabled.py --check /opt/data"

# ---------- Step 8b: customer-disabled bundled skills stayed off the menu (#1198) ----------
# customer.yaml personas[].skills_disabled is the per-customer authority over
# Hermes' bundled catalog (e.g. google-workspace + himalaya, which the DWD-broker
# model replaces — ADR 0045). bootstrap step 7b.1 prunes them from the profile
# skill tree + prompt snapshot, and a startup reconciler re-prunes after Hermes'
# gateway sync. This --check FAILS the boot if any disabled skill reappeared —
# the fail-closed guard against a Hermes-upgrade rehydration regression (a
# re-exposed google-workspace skill is a governance-bypass path back to the raw
# credential, exactly what ADR 0045 closes). No-op when no skills_disabled authored.
ssh_exec "disabled-skills-pruned" "/opt/hermes/.venv/bin/python3 /app/ensure-disabled-skills.py --check /var/lib/smd-config/customer.yaml /opt/data"

# ---------- Step 8c: the matter-mixing READ fence discriminates (ss#2167) ----------
# The fence refuses a session holding one matter's substance from reading a
# second matter's. It is what stops a draft containing two clients' facts from
# ever being COMPOSED — every other matter control fires when a send is
# attempted, by which point that draft exists and is in a paralegal's queue, and
# the firm finding it there is the event the engagement does not survive whether
# or not it was sent.
#
# The probe asserts BOTH directions on purpose: a second matter is refused, AND
# the same matter is still readable. Asserting only the refusal would pass
# against a fence that refused every content read — not a safe fence, a bricked
# Operator the firm switches off. It also fails the boot if the overlay pin
# predates ss#2167, which is the cross-repo drift this catches.
ssh_exec "matter-mixing-fence" "/opt/hermes/.venv/bin/python3 /app/matter-mixing-fence-probe.py"

# ---------- Step 9: audit ledger is broker-owned and NOT agent-writable (OP-P1-4) ----------
# The immutable ledger must be owned by the broker uid (workspace-broker), the
# dir setgid 2750, and the agent uid (hermes) must be physically unable to write
# it. The probe (run as hermes) exits 0 only when both DELETE and INSERT are
# refused — the affirmative tamper-resistance proof.
ssh_exec "audit-db-owner-is-broker" "[ \"\$(stat -c %U /opt/data/audit/audit.db)\" = workspace-broker ]"
ssh_exec "audit-dir-setgid-2750" "[ \"\$(stat -c %a /opt/data/audit)\" = 2750 ]"
ssh_exec "audit-db-not-agent-writable" "setpriv --reuid=hermes --regid=hermes --init-groups /opt/hermes/.venv/bin/python3 /app/audit-write-fail-probe.py /opt/data/audit/audit.db"

# ---------- Step 10: mutable agent state stayed hermes-writable (regression for the split) ----------
# agent_skills_inventory moved off the ledger onto a hermes-owned file so locking
# the ledger does not break skill capture. The overlay's register() creates it.
ssh_exec "agent-state-owner-is-hermes" "[ \"\$(stat -c %U /opt/data/agent-state.db)\" = hermes ]"

# ---------- Step 11: the broker is supervised by a ROOT respawner (OP-P1-4 follow-up) ----------
# The broker must be respawnable on mid-run death, which requires a root parent
# (only root can re-setpriv to uid workspace-broker). With the supervisor, the
# broker's parent is the root subshell loop; without it the broker is a direct
# child of PID 1 (the hermes gateway after the exec-drop). So "broker's parent
# proc dir is root-owned" is a non-destructive proof the supervisor is in place.
# NB: read PPid from /proc/<pid>/status with grep+tr (NO single quotes) — ssh_exec
# wraps this whole string in `sh -c '...'`, so a single-quoted `awk '{print $4}'`
# would collide with the outer quote and mangle the command.
ssh_exec "broker-respawn-supervised" "pid=\$(pgrep -f workspace_broker.server | head -1); [ -n \"\$pid\" ] && ppid=\$(grep -m1 PPid /proc/\$pid/status | tr -dc 0-9) && [ \"\$(stat -c %U /proc/\$ppid)\" = root ]"

# The check above proves the broker can be RESTARTED. It never opens the socket,
# so it cannot tell a working broker from one whose socket is gone, whose parent
# directory lost its setgid bit, or which is refusing every connection. A seat in
# that state has no document surface at all and reports healthy on every signal
# we watch — the same shape as the 2026-07-16 scheduler outage, which ran eight
# days green because the check confirmed a process EXISTED rather than that it
# WORKED.
#
# `health` is the right call to make: it sits ABOVE the broker's gateway-PID gate
# (workspace_broker/server.py:282 vs :291), so any process may issue it, and it
# reports whether the credential, customer, jobs and audit stores actually
# loaded. Run as the AGENT uid, because "hermes can reach this socket" is the
# property that matters — root reaching it proves nothing about the caller that
# needs it.
#
# SCOPE IT HONESTLY: this proves the socket answers and the stores loaded. It
# CANNOT prove the gateway can authorize a privileged verb, because only the
# gateway process may make that call and this is not it. Claiming otherwise
# would rebuild the blind spot one layer up.
# The socket path is taken from the canonical default rather than the env, and
# that is not laziness: a fresh `fly ssh console` session inherits NONE of the
# app's environment (verified — SMD_WORKSPACE_BROKER_SOCKET is unset there),
# while the entrypoint sets both the env var and this path from the same
# constant. Reading the env here would make the check fail on every seat for a
# reason that has nothing to do with the broker, which is worse than a path that
# can drift. Same idiom as every other absolute path in this file. If the
# entrypoint's SOCKET_PATH ever moves, this line moves with it.
ssh_exec_script "broker-socket-answers-health" "setpriv --reuid=hermes --regid=hermes --init-groups /opt/hermes/.venv/bin/python3 -c \"import socket,os,json,sys; p=os.environ.get('SMD_WORKSPACE_BROKER_SOCKET') or '/run/smd-workspace-broker/broker.sock'; s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); s.settimeout(5); s.connect(p); s.sendall(b'{\\\"action\\\":\\\"health\\\"}'+bytes([10])); r=json.loads(s.recv(65536).decode()); sys.exit(0 if r.get('ok') and r.get('credential_ready') and r.get('customer_ready') else 1)\""

# ---------- Step 11c: the GATEWAY is supervised, and its loop is beating (P0 ss#2488) ----------
# On 2026-08-20 the paying client's seat wedged for 33 minutes: Hermes' own
# loop-liveness watchdog logged that it was exiting so a supervisor could restart
# it, then did not exit, and nothing at any layer noticed. entrypoint.sh EXECS the
# gateway as the container's main process, the respawner above covers the BROKER,
# and Fly does not restart a Machine on a failing health check.
#
# Deliberately NOT a pid check. The broker note above already says why: a check
# that "confirmed a process EXISTED rather than that it WORKED" is how the
# 2026-07-16 scheduler outage ran eight days green. The supervisor touches
# ${RUN_DIR}/tick at the top of EVERY iteration, so tick freshness fails if the
# loop stops turning for any reason — including the one that would otherwise be
# invisible, an inherited `set -e` killing the subshell on its first failed probe.
ssh_exec "gateway-liveness-supervisor-ticking" "t=/run/smd-gateway-liveness/tick; [ -f \$t ] && [ \$(( \$(date -u +%s) - \$(stat -c %Y \$t) )) -lt 90 ]"

# Ticking proves the loop turns; it does not prove the loop ever found the
# heartbeat and ARMED. A supervisor stuck on `inert` (argv unresolvable) or
# `not-armed` (it has never seen a fresh beat) ticks identically to a healthy
# one and would kill nothing. The state file is one word per transition, written
# for exactly this question and for the gate to ship on the heartbeat (part 2).
# Bounded wait: arming happens on the first poll AFTER the gateway's first beat,
# which on 1 vCPU can land a minute or more into boot. The failing branch prints
# the state it did see, so a red run says WHICH wrong state, not just "failed".
ssh_exec "gateway-liveness-supervisor-armed" "n=0; while [ \$n -lt 36 ]; do s=\$(head -c 16 /run/smd-gateway-liveness/state 2>/dev/null); [ \"\$s\" = armed ] && exit 0; n=\$((n+1)); sleep 5; done; echo supervisor-state=\$s >&2; exit 1"

# Part 2 ships these three files on the heartbeat FROM THE GATE, which is the
# hermes uid. Run the read AS hermes, because "root can read it" proves nothing
# about the only process that needs to. Found live on hermes-scott 2026-08-21: a
# ledger written under the first cut's umask was 0640, the gate got Permission
# denied, and the field went absent (a hold) with every root-side check green.
# An absent ledger FILE passes (first boot, no kills yet); a present-but-unreadable
# one fails. tick and state must exist by this point (the two checks above).
ssh_exec "gateway-liveness-files-readable-by-gate" "setpriv --reuid=hermes --regid=hermes --init-groups sh -c \"head -c 1 /run/smd-gateway-liveness/state >/dev/null && head -c 0 /run/smd-gateway-liveness/tick && { [ ! -e /opt/data/gateway-liveness/kills ] || head -c 0 /opt/data/gateway-liveness/kills; }\""

# The supervisor is only as good as the signal it reads, and that signal is
# UPSTREAM's: an asyncio task on the gateway loop rewrites this file every 30s.
# This check is the tripwire for the whole mechanism rotting silently. The path
# is not where the Hermes source implies — _process_hermes_home() reads
# HERMES_HOME (/opt/data), but the file lands under the PROFILE home, which is
# why the supervisor derives it from the gateway's argv. If a future pin drops
# the heartbeat, moves it again, or disables it via `gateway.loop_watchdog`, the
# supervisor would go quietly inert; this fails the provision instead.
#
# Bounded wait rather than a single shot: boot smoke is fail-fast and on a 1 vCPU
# box the first beat can land well after this step runs. Same idea as the
# --wait-gateway-s flag the R2 strip probe below uses.
ssh_exec "gateway-loop-heartbeat-fresh" "n=0; while [ \$n -lt 36 ]; do for f in /opt/data/profiles/*/state/gateway.heartbeat; do [ -f \$f ] || continue; [ \$(( \$(date -u +%s) - \$(stat -c %Y \$f) )) -lt 120 ] && exit 0; done; n=\$((n+1)); sleep 5; done; exit 1"

# ---------- Step 12: /app governance artifacts are root-owned, not agent-writable (SEC-31) ----------
# The activation-gate source the gateway:startup hook force-loads
# (/app/overlay-pack, incl. hooks/smd-overlay-activation/handler.py) must be owned
# by root so a code-executing agent cannot rewrite its own governance self-check.
# bootstrap only READS it (copies to the volume; the gateway loads from there), so
# root ownership is functionally inert. /app is image-backed and resets each boot,
# but the agent's write path is removed regardless — defense in depth.
ssh_exec "overlay-pack-root-owned" "[ \"\$(stat -c %U /app/overlay-pack)\" = root ]"
ssh_exec "overlay-pack-not-agent-writable" "setpriv --reuid=hermes --regid=hermes --init-groups sh -c \"! test -w /app/overlay-pack\""
ssh_exec "activation-handler-not-agent-writable" "setpriv --reuid=hermes --regid=hermes --init-groups sh -c \"! test -w /app/overlay-pack/hooks/smd-overlay-activation/handler.py\""

# ---------- Step 13: the account-wide R2 key is stripped from the LIVE agent (OP-P2-1) ----------
# bootstrap.sh unsets R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY before forking any
# same-uid child or exec'ing the gateway, so a code-executing agent cannot read
# the account-wide R2 key (from its own env or a sibling's /proc/<pid>/environ)
# and rewrite the R2 config object — the loopback that would walk around the
# keystone's filesystem lock (ADR 0044 Decision 8). test_deploy_ordering.py
# proves the SOURCE strips it in the right order; this proves it MATERIALIZED on
# the running Machine. Runs as root (reads agent-uid /proc/environ); the probe
# excludes root processes (PID 1 + the config applier legitimately keep the key)
# and never echoes the value.
# --wait-gateway-s: on a cold boot this step lands ~75s after boot, before the
# gateway has spawned, and the probe's vacuous-zero fail-closed rule (exit 3)
# FATALed a healthy deploy (ss#2420, seen on both 2026-08-18 reprovisions). The
# wait retries ONLY the no-agent-process verdict; a real offender still fails
# the instant a scan sees it. Later strip probes need no wait — once this one
# passes, the gateway is up.
ssh_exec "r2-account-key-stripped-from-agent" "/opt/hermes/.venv/bin/python3 /app/r2-account-key-strip-probe.py --wait-gateway-s 120"

# ss#2258, the same proof for the AgentMail SEND credential. This is the one
# check that would have caught the incident class before a client ever saw it:
# four fabricated messages reached a real principal from a seat whose agent
# process held an org-wide, all-permission send key, and no static test can tell
# you what is in a running process's environ. The probe takes the var names as
# arguments, so this reuses it verbatim — no second implementation to drift.
#
# What makes this able to FAIL: if entrypoint.sh's `unset AGENTMAIL_SEND_API_KEY`
# is removed, reordered after the exec-drop, or never runs because the strip
# block moved, the gateway inherits the send key and this exits non-zero naming
# the pid. The value is never printed.
ssh_exec "agentmail-send-key-stripped-from-agent" \
  "/opt/hermes/.venv/bin/python3 /app/r2-account-key-strip-probe.py hermes AGENTMAIL_SEND_API_KEY"

# The same proof for the Graph SEND app credential (ss#2258 msgraph wave).
#
# READ WHAT THIS CHECK DOES AND DOES NOT COVER. It proves the BROKER'S copy never
# reaches the agent. It does NOT itself prove the agent cannot send —
# MSGRAPH_CLIENT_SECRET deliberately stays in the gateway (the delta poller and
# the msgraph-mail MCP server need it), and this probe never asks Microsoft what
# that credential is allowed to do.
#
# What makes the agent unable to send is upstream, at provisioning: since
# 2026-08-13 provision-customer.sh REFUSES a seat whose MSGRAPH_SEND_* is not a
# distinct app registration from the gateway's MSGRAPH_* (tests/msgraph-two-app
# -fence.test.ts drives the refusal arms). So on any seat that booted, the
# gateway's credential belongs to a read-only registration that Microsoft refuses
# at /sendMail with 403 ErrorAccessDenied — proven on the sandbox seat 2026-08-13,
# vfy_01KZXX523V6JNWEETG4PSZDQY3. This probe guards the other half: the one
# credential in the tenant that DOES hold Mail.Send is a credential the agent
# genuinely never has.
#
# What makes it able to FAIL: remove or reorder the `unset MSGRAPH_SEND_*` in
# entrypoint.sh on a seat where those are staged, and the gateway inherits them —
# non-zero, naming the pid, never the value. On a seat with no msgraph connector
# nothing is staged and this passes vacuously, which is the honest outcome for a
# credential that does not exist.
ssh_exec "msgraph-send-credential-stripped-from-agent" \
  "/opt/hermes/.venv/bin/python3 /app/r2-account-key-strip-probe.py hermes MSGRAPH_SEND_CLIENT_SECRET MSGRAPH_SEND_CLIENT_ID MSGRAPH_SEND_TENANT_ID"

# ---------- Step 14: the chronology runner is present, idle, capped, and fenced (ss#2614) ----------
# The runner daemon is a root process that spends the firm's model budget on
# its own schedule, so the checks here are the ones that would otherwise be
# invisible: that its loop turns (tick freshness, the 11c idiom — not a pid),
# that it is NOT running a job on a fresh boot (the child pidfile the daemon
# writes for exactly this question; a `pgrep -f` would match its own sh -c
# wrapper), that the memory controller the cap depends on is really there
# (the heartbeat says which; `none` means jobs would run uncapped beside the
# gateway on a 4 GB guest, the #2465 shape), that the queue and job dirs
# carry the modes the trust argument rests on, and that the medchron uid can
# read the firm config but not write it.
#
# What makes these able to FAIL: stop the daemon and the tick ages past 90 s;
# run the image on a guest without cgroup v2 memory and memory_cap reads
# `none`; loosen an install -d mode in entrypoint.sh and the stat reads it;
# drop the chown of the firm config and the write test passes.
ssh_exec "medchron-uid-exists" "id -u medchron >/dev/null"
ssh_exec "medchron-daemon-ticking" "t=/run/smd-medchron/tick; [ -f \$t ] && [ \$(( \$(date -u +%s) - \$(stat -c %Y \$t) )) -lt 90 ]"
ssh_exec "medchron-daemon-idle" "! test -f /run/smd-medchron/child.pid"
# cgroup2 on a unified guest, cgroup1 on Fly's hybrid layout (live-probed
# 2026-08-31: v2 is mounted bare at /sys/fs/cgroup/unified with no
# controllers; memory is on the v1 mount). `none` is the failure.
ssh_exec "medchron-memory-cap-present" "grep -q memory_cap.:..cgroup /run/smd-medchron/heartbeat.json"
ssh_exec "medchron-queue-root-owned" "[ \"\$(stat -c %U:%G:%a /run/smd-medchron/queue)\" = root:workspace-broker:770 ]"
ssh_exec "medchron-jobs-dir-root-owned-child-traversable" "[ \"\$(stat -c %U:%G:%a /run/smd-medchron/jobs)\" = root:medchron:710 ]"

# The firm config is authored per-seat in the PRIVATE engagements repo, and
# provision-customer.sh step 2b reads the SAME path and continues without it
# ("the chronology runner will refuse jobs" — the authored state for a seat
# that runs no chronology routine). Whether the config is EXPECTED here is
# decided by that same source of expectation, never by whether the boot
# happened to materialize it: this check landed unconditional in the
# 2026-08-31 medchron slices, and that same evening pilot-smokeball (no
# medchron authored) ran 39 PASS then FATALed on it, skipping every check
# after — the instrument failed, not the seat. Law 2's fail-closed rule
# applies to the expectation source itself: a missing engagements checkout is
# "cannot evaluate", which must never read as "not expected".
ENGAGEMENTS_DIR="${SS_ENGAGEMENTS_DIR:-${HOME}/dev/engagements}"
[ -d "${ENGAGEMENTS_DIR}" ] \
  || fail "medchron-firm-config-expected — engagements checkout missing at ${ENGAGEMENTS_DIR} (clone venturecrane/engagements or set SS_ENGAGEMENTS_DIR); cannot evaluate whether ${SLUG} authors a medchron firm config, and cannot-evaluate must not read as not-expected"
MEDCHRON_FIRM_YAML_AUTHORED="${ENGAGEMENTS_DIR}/operator/customers/${SLUG}/medchron/firm.yaml"
if [ -f "${MEDCHRON_FIRM_YAML_AUTHORED}" ]; then
  ssh_exec "medchron-firm-config-present" "[ \"\$(stat -c %U:%G:%a /var/lib/smd-config/medchron-firm.yaml)\" = root:medchron:640 ]"
  ssh_exec "medchron-uid-cannot-write-config" "setpriv --reuid=medchron --regid=medchron --init-groups sh -c \"! test -w /var/lib/smd-config/medchron-firm.yaml\""
  ssh_exec "medchron-token-shared" "[ \"\$(stat -c %G:%a /run/smd-smokeball-token/refresh_token)\" = smokeball-token:660 ]"
  # 2026-09-04: a seat that authors a firm config must also carry the
  # install-level tree the entrypoint seeds from vaults/<slug>/medchron-controls/
  # — the scanned-page classifier's falsifier (controls.json + its PDFs) and
  # the ICD tables, both staged into that vault by provision-customer.sh step
  # 2c (the tables vendored on the console: this tree is read-only to the
  # child, so a seat can never fetch its own). Without it every job refuses
  # at classify_scanned or fails at icd_tables, the seat path the 2026-09-04
  # review found nobody had walked. The child can READ the controls (it runs
  # the classifier) and never write them (a classifier that can edit its own
  # controls measures nothing). What makes these able to FAIL: empty the vault
  # prefix and reboot; drop the chown/chmod in entrypoint.sh and the stat or
  # the write test reads it.
  ssh_exec "medchron-controls-present" "[ \"\$(stat -c %U:%G:%a /run/smd-medchron/controls)\" = root:medchron:750 ] && test -f /run/smd-medchron/controls/controls.json"
  ssh_exec "medchron-icd-tables-present" "test -f /run/smd-medchron/controls/icd/VERSION.json"
  ssh_exec "medchron-uid-reads-controls-cannot-write" "setpriv --reuid=medchron --regid=medchron --init-groups sh -c \"test -r /run/smd-medchron/controls/controls.json && ! test -w /run/smd-medchron/controls/controls.json && ! test -w /run/smd-medchron/controls\""
else
  pass "medchron-firm-config-absent (not authored for this seat at ${MEDCHRON_FIRM_YAML_AUTHORED}; runner refuses jobs by design — skipping config-perm, token, controls, and gate-refusal checks)"
fi

# ---------- Step 14b: the four registered runner gates refuse their planted violations (ss#2614, ADR 0087) ----------
# `medchron probe <gate>` builds a throwaway synthetic matter, plants exactly
# the violation the registry names, runs the gate module the registry points
# at, and prints REFUSED only when the gate held. $0 and offline. These back
# the `enforced` status of the four medchron_* rows in runtime-controls.yaml
# (probe_surface: prod-boot): the status is re-earned on every provision, not
# asserted once. What makes these able to FAIL: neuter a gate module (return 0,
# drop the planted check) and its probe prints UNEXPECTED_PASS, non-zero.
# Gated on the same authored expectation as the firm-config checks above:
# on a seat with no medchron authored the runner refuses every job, and the
# pass-annotated skip is emitted by the firm-config branch.
if [ -f "${MEDCHRON_FIRM_YAML_AUTHORED}" ]; then
  ssh_exec "medchron-gate-claim-audit-refuses" "/opt/medchron/.venv/bin/medchron probe claim_audit | grep -q REFUSED"
  ssh_exec "medchron-gate-extractive-refuses" "/opt/medchron/.venv/bin/medchron probe extractive | grep -q REFUSED"
  ssh_exec "medchron-gate-cross-client-refuses" "/opt/medchron/.venv/bin/medchron probe cross_client | grep -q REFUSED"
  ssh_exec "medchron-gate-provenance-refuses" "/opt/medchron/.venv/bin/medchron probe provenance | grep -q REFUSED"
fi

# The summarize EXIT trap prints the tally and owns the exit code. Nothing is
# claimed here: on the old fail-fast script this line was only ever reached on a
# clean run, so it doubled as the verdict. Now the run reaches the end whether
# checks were red or green, and a "passed" line here would be a lie on a red run.
log "All checks executed for ${APP_NAME}"
