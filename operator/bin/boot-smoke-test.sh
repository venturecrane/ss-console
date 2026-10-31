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

# A WARNING is a state worth a human's attention that is not, by itself, a
# broken seat: it never changes the exit code, and the summary lists every one
# so it cannot scroll past unread. The first user is the identifier-gate
# rollback lever (ADR 0093): legitimate during an incident, wrong as a
# standing per-seat loosening.
WARNED_CHECKS=()
check_warn() {
  WARNED_CHECKS+=("$*")
  log "WARN: $*"
  return 0
}

# The Machine's state RIGHT NOW, or "error" if it cannot be read.
machine_state() {
  fly status -a "${APP_NAME}" --json 2>/dev/null \
    | python3 -c "import sys, json
try:
    d = json.load(sys.stdin)
    machines = d.get('Machines') or []
    print(machines[0]['state'] if machines else 'none')
except Exception:
    print('error')" 2>/dev/null || echo "error"
}

# How long a cycling Machine is given to come back before a failing check is
# recorded as it stands. Overridable so a slow seat can be given more, and so
# the tests can use a budget that does not dominate their runtime.
BOOT_SMOKE_SETTLE_S="${BOOT_SMOKE_SETTLE_S:-120}"

# Wait for the Machine to be back in state=started, up to ${1:-$BOOT_SMOKE_SETTLE_S}s.
# Returns 0 if it got there, 1 otherwise. Never fails a check by itself.
await_started() {
  local budget="${1:-${BOOT_SMOKE_SETTLE_S}}" waited=0 state
  while [ "${waited}" -lt "${budget}" ]; do
    state="$(machine_state)"
    [ "${state}" = "started" ] && return 0
    waited=$((waited + 3))
    sleep 3
  done
  return 1
}

# Did this check fail because the MACHINE was cycling rather than because the
# thing it measures is broken?
#
# ss 2026-09-23: Step 1 waits for state=started, but that gate passes once, at
# the top, and the run that follows takes ~14 minutes. On a pilot rebuild the
# Machine went to `replacing` at 15:27:32 and two medchron gate probes ran at
# 15:27:39 — seven seconds later, against a Machine that was being replaced.
# Both were reported as failed gates and the provision exited FATAL on a seat
# that was healthy: re-run on a settled Machine, both returned REFUSED, which is
# exactly what they are supposed to return.
#
# A boot smoke that cries wolf is worse than one that is merely slow, because
# the next FATAL is the one nobody reads. So a failing check gets ONE retry, and
# ONLY when the Machine is not in state=started at the moment it failed.
#
# This deliberately cannot turn a real failure green: the retry runs the SAME
# command, and a second failure is recorded as a failure. What it removes is the
# case where the command never had a Machine to run on.
retry_if_machine_cycling() {
  local state
  state="$(machine_state)"
  if [ "${state}" = "started" ]; then
    return 1  # the Machine was fine; the check's failure is the check's own
  fi
  log "  (Machine state=${state}, not started — waiting for it to settle, then retrying once)"
  # The budget is passed explicitly rather than left to the default: with no
  # caller supplying an argument, CI's shellcheck reads the optional ${1} as an
  # unused parameter (SC2120) and fails the substrate job. Local shellcheck
  # 0.11.0 does not flag it, which is how this reached a PR.
  await_started "${BOOT_SMOKE_SETTLE_S}" || {
    log "  (Machine did not return to started; recording the failure as-is)"
    return 1
  }
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
  local w=${#WARNED_CHECKS[@]}
  log "warnings: ${w}"
  if [ "${w}" -gt 0 ]; then
    local x
    for x in "${WARNED_CHECKS[@]}"; do log "  WARNING: ${x}"; done
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
# The marker a check prints when it RAN and returned false. Its absence on a
# failed attempt means no verdict came back at all: the ssh session failed, not
# the check. 2026-10-17: `customer-yaml-root-owned` failed a pilot rebuild in 11 s
# (its neighbours took 2 s) on a file that was root:root; the re-run was 80/0.
CHECK_FAILED_MARK="SMD_CHECK_FAILED"

# Wall-clock budget for one `fly ssh console`. 2026-10-18: one check's ssh on a
# brokery rebuild HUNG for 38 minutes on a healthy Machine and never exited, so
# the retry below (which fires only when ssh exits) never ran and the whole smoke
# stalled. A check takes ~2 s; a slow ssh handshake ~15 s.
SSH_TIMEOUT_S="${BOOT_SMOKE_SSH_TIMEOUT_S:-90}"
SSH_TIMEOUT_MARK="SMD_SSH_TIMEOUT"

with_timeout() { # <seconds> <command...> -- exit 124 and print the timeout mark if it overruns
  # macOS has no `timeout` binary. The watcher's output goes to /dev/null so a
  # caller's $(...) is not held open by it after the command finishes.
  # The watcher touches a flag before it kills, so "it fired" is read from the
  # flag and never inferred from an exit code fly could also produce.
  local secs="$1" pid watcher rc=0 fired
  shift
  fired="$(mktemp)"
  rm -f "${fired}"
  "$@" &
  pid=$!
  ( sleep "${secs}"; touch "${fired}"; kill -TERM "${pid}" 2>/dev/null; sleep 5; kill -KILL "${pid}" 2>/dev/null ) >/dev/null 2>&1 &
  watcher=$!
  wait "${pid}" || rc=$?
  kill "${watcher}" 2>/dev/null || true
  wait "${watcher}" 2>/dev/null || true
  if [ -e "${fired}" ]; then
    rm -f "${fired}"
    echo "${SSH_TIMEOUT_MARK} (no answer in ${secs}s)"
    return 124
  fi
  return "${rc}"
}

ssh_run() {
  # `fly ssh console --command` execs the string directly (no shell), so compound
  # commands (&&, |, $(...), [ ]) fail unless wrapped in an explicit shell. Wrap
  # every check in `sh -c` so shell constructs evaluate ON THE MACHINE. (Check
  # commands contain no single quotes, so the single-quoted wrapper is safe.) The
  # subshell keeps a check's own `exit` from skipping the marker.
  # A timeout prints no CHECK_FAILED mark, so it takes the no-verdict retry path.
  with_timeout "${SSH_TIMEOUT_S}" fly ssh console -a "${APP_NAME}" --command "sh -c '( ${1} ) || { echo ${CHECK_FAILED_MARK}; exit 1; }'" 2>&1
}

ssh_exec() {
  local step="$1"
  shift
  ssh_check "${step}" "$*" "$*"
}

ssh_check() { # <step> <command as reported> <command as run on the Machine>
  local step="$1" cmd="$2" remote="$3" out
  if out="$(ssh_run "${remote}")"; then
    pass "${step}"
    return
  fi
  if [[ "${out}" == *"${CHECK_FAILED_MARK}"* ]]; then
    # The check ran and said no. Retry only if the Machine was cycling under it.
    if retry_if_machine_cycling && ssh_run "${remote}" >/dev/null; then
      pass "${step} (passed on retry; the Machine was cycling on the first attempt)"
    else
      check_fail "${step} — command failed: ${cmd}"
    fi
    return
  fi
  # No verdict: one retry, after letting a cycling Machine settle. A second
  # attempt with no verdict is recorded as a failure, never as a pass.
  if [[ "${out}" == *"${SSH_TIMEOUT_MARK}"* ]]; then
    log "  (no verdict from the Machine for ${step}; ssh hung past ${SSH_TIMEOUT_S}s and was killed, retrying once)"
  else
    log "  (no verdict from the Machine for ${step}; the ssh session failed, retrying once)"
  fi
  retry_if_machine_cycling || true
  if out="$(ssh_run "${remote}")"; then
    pass "${step} (passed on retry; the first attempt returned no verdict)"
  elif [[ "${out}" == *"${CHECK_FAILED_MARK}"* ]]; then
    check_fail "${step} — command failed: ${cmd}"
  else
    check_fail "${step} — no verdict from the Machine on two attempts (ssh failed): ${cmd}"
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
  ssh_check "${step}" "${cmd}" "echo ${encoded} | base64 -d | sh"
}

# ---------- Step 1: wait for Machine state=started ----------
log "Waiting for Machine state=started (up to 60s)..."
ATTEMPT=0
while [ "${ATTEMPT}" -lt 60 ]; do
  STATE="$(machine_state)"
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


# ---------- Steps 5-14b: the shared seat check list, over ssh ----------
# The checks themselves live in operator/templates/seat-checks.sh, which the
# image also runs in place every hour (/app/selftest.sh). This file binds the
# list's verbs to the ssh transport above; behaviour per check is what it was
# when the checks lived here. Read seat-checks.sh for what each check proves.
TEMPLATES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../templates" && pwd)"
CAPABILITIES_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")/../hermes-smd-overlay/shared" && pwd)/capabilities.py"
# shellcheck disable=SC2034 # read by seat-checks.sh: seat paths are the Machine's own
SEAT_ROOT=""

check() { ssh_exec "$@"; }
check_script() { ssh_exec_script "$@"; }
# Over ssh a busy lane and a vendor probe are judged like any check: the laptop
# smoke runs at provision and release, which are boot runs.
check_idle() { ssh_exec "$@"; }
check_vendor() { ssh_exec "$@"; }
check_note() { pass "$1 ($2)"; }
check_broken() { check_fail "$1 — $2"; }

# check_file <id> <interpreter> <probe>: the probe's text from THIS checkout,
# run on the Machine by the named interpreter reading the program from stdin.
# The checkout's copy, not /app's, so a rollback re-smoke of an older image
# still runs the probe this checkout means.
check_file() {
  local encoded
  encoded="$(base64 < "${TEMPLATES_DIR}/$3" | tr -d '\n')"
  ssh_exec_script "$1" "echo ${encoded} | base64 -d | $2 -"
}

# check_report <id> <program>: a check whose stdout is the evidence, so a
# failure names what it found (the staged-scripts step names every stale path).
# Sets REPORT_OUT and returns the remote exit status.
report_run() {
  REPORT_OUT="$(with_timeout "${SSH_TIMEOUT_S}" fly ssh console -a "${APP_NAME}" --command "sh -c 'echo ${1} | base64 -d | sh'" 2>&1)"
}
check_report() {
  local encoded
  encoded="$(printf '%s' "$2" | base64 | tr -d '\n')"
  REPORT_OUT=""
  if report_run "${encoded}"; then
    pass "$1 ($(printf '%s\n' "${REPORT_OUT}" | grep -v '^[[:space:]]*$' | tail -1 || true))"
  elif retry_if_machine_cycling && report_run "${encoded}"; then
    pass "$1 (passed on retry; the Machine was cycling on the first attempt)"
  else
    check_fail "$1: $(printf '%s\n' "${REPORT_OUT}" | grep -v '^[[:space:]]*$' | tr '\n' ';' || true)"
  fi
}

# check_lever <probe>: the probe prints a verdict; set is a WARNING (never a
# failure), unset a pass, and no verdict is never read as unset.
check_lever() {
  local out
  out="$(ssh_run "$1" || true)"
  case "${out}" in
    *SMD_LEVER=set*)
      check_warn "identifier-gate-lever-set: SMD_IDENTIFIER_GATE_MODE is set on ${APP_NAME}; identifier blocking may be off. Incident rollback only (ADR 0093): unset it once the incident is over" ;;
    *SMD_LEVER=unset*)
      pass "identifier-gate-lever-unset" ;;
    *)
      check_warn "identifier-gate-lever-unknown: could not read whether SMD_IDENTIFIER_GATE_MODE is set on ${APP_NAME}" ;;
  esac
}

# The Hermes pin the running Hermes must equal: the release's
# (BOOT_SMOKE_EXPECTED_HERMES_SHA, passed by `fleet release`, whose image may be
# built from another commit), else this checkout's Dockerfile default. Not
# readable is a precondition: every verdict on "the right Hermes" would be noise.
DOCKERFILE_FOR_PIN="${TEMPLATES_DIR}/Dockerfile"
EXPECTED_HERMES_SHA="${BOOT_SMOKE_EXPECTED_HERMES_SHA:-}"
if [ -z "${EXPECTED_HERMES_SHA}" ] && [ -f "${DOCKERFILE_FOR_PIN}" ]; then
  EXPECTED_HERMES_SHA="$(grep -m1 -E '^ARG HERMES_UPSTREAM_SHA=' "${DOCKERFILE_FOR_PIN}" | cut -d= -f2 | tr -d '"[:space:]')"
fi
printf '%s' "${EXPECTED_HERMES_SHA}" | grep -qE '^[0-9a-f]{40}$' \
  || fail "hermes-sha-matches-pin — could not read a 40-hex ARG HERMES_UPSTREAM_SHA from ${DOCKERFILE_FOR_PIN} (or BOOT_SMOKE_EXPECTED_HERMES_SHA)"

# The lane expectation source over ssh: the PRIVATE engagements checkout, where
# each seat's firm configs are authored and which provision-customer.sh stages
# from. A missing checkout is "cannot evaluate", which must never read as "not
# expected" (Law 2), so it is a precondition.
#
# A lane is authored when the working tree OR engagements origin/main carries
# its firm file. The default checkout (~/dev/engagements) is shared and
# routinely stale; on 2026-10-15 it predated the A&P demand/drafting/litigation
# dirs, so this smoke reported all three lanes "absent, by design" and skipped
# the presence checks on a seat that authors them. origin/main is what
# provision-customer.sh checks the firm config against, so it is the
# expectation source a stale tree cannot hide. The union errs toward running a
# check (a failure is loud; a skip is silent). FALSIFIER: point
# SS_ENGAGEMENTS_DIR at a checkout whose tree lacks a lane dir that origin/main
# has; the presence check must run, not pass-skip.
ENGAGEMENTS_DIR="${SS_ENGAGEMENTS_DIR:-${HOME}/dev/engagements}"
[ -d "${ENGAGEMENTS_DIR}" ] \
  || fail "medchron-firm-config-expected — engagements checkout missing at ${ENGAGEMENTS_DIR} (clone venturecrane/engagements or set SS_ENGAGEMENTS_DIR); cannot evaluate whether ${SLUG} authors a medchron firm config, and cannot-evaluate must not read as not-expected"
git -C "${ENGAGEMENTS_DIR}" fetch -q origin main 2>/dev/null || true
authored() {
  [ -f "${ENGAGEMENTS_DIR}/$1" ] \
    || git -C "${ENGAGEMENTS_DIR}" cat-file -e "origin/main:$1" 2>/dev/null
}
# A lane is ALSO authored when this checkout's customer.yaml enables the skill
# that drives it, the expectation the in-image self-test uses (selftest.sh
# lane_skill). Without it this smoke pass-skipped a seat that offered demand
# letters with no demand inputs (pilot-smokeball, 2026-10-23); the image failed it.
lane_authored() {
  if [ "$1" = medchron ]; then
    authored "operator/customers/${SLUG}/medchron/firm.yaml" && return 0
  else
    authored "operator/customers/${SLUG}/$1/$1-firm.yaml" && return 0
  fi
  lane_skill_enabled "$1"
}
# lane_runs <lane> (ADR 0100): this checkout's customer.yaml authors the lane, so the
# image booted its thread. The same predicate the entrypoint uses. Cannot evaluate
# reads as runs.
running_lanes() {
  uv run --quiet --no-project --with pyyaml python3 -I "${CAPABILITIES_PY}" lanes "${CUSTOMER_YAML}" 2>/dev/null
}
lane_runs() {
  [ -f "${CUSTOMER_YAML}" ] || return 0
  local out
  out="$(running_lanes)" || return 0
  printf '%s\n' "${out}" | grep -qxF "$1"
}
any_lane_runs() {
  [ -f "${CUSTOMER_YAML}" ] || return 0
  local out
  out="$(running_lanes)" || return 0
  [ -n "${out}" ]
}
lane_skill_enabled() {
  local skill
  case "$1" in medchron) skill=medical-chronology-maintainer ;; demand) skill=demand-letter-drafter ;; drafting) skill=document-drafter ;; litigation) skill=litigation-status ;; casefile) skill=case-file-manager ;; marketing) skill=marketing-request-router ;; *) return 1 ;; esac
  # Cannot evaluate (no file, unparseable, no uv) reads as authored, never as
  # absent: exit 1 is the only "not enabled" answer.
  [ -f "${CUSTOMER_YAML}" ] || return 0
  local rc=0
  uv run --quiet --with pyyaml python3 -c 'import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
on = {s["name"] for p in d.get("personas") or [] for s in (p or {}).get("skills") or [] if isinstance(s, dict) and s.get("enabled") is not False and isinstance(s.get("name"), str)}
sys.exit(0 if sys.argv[2] in on else 1)' "${CUSTOMER_YAML}" "${skill}" 2>/dev/null || rc=$?
  [ "${rc}" -ne 1 ]
}

# shellcheck source=../templates/seat-checks.sh
source "${TEMPLATES_DIR}/seat-checks.sh"
seat_checks

# The summarize EXIT trap prints the tally and owns the exit code. Nothing is
# claimed here: on the old fail-fast script this line was only ever reached on a
# clean run, so it doubled as the verdict. Now the run reaches the end whether
# checks were red or green, and a "passed" line here would be a lie on a red run.
log "All checks executed for ${APP_NAME}"
