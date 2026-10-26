#!/usr/bin/env bash
# selftest.sh: the seat checks, run IN the image, at boot and every hour.
#
#   selftest.sh --supervise      what entrypoint.sh forks (root, env -i, nice):
#                                runs --loop and restarts it on a crash
#   selftest.sh --loop           boot run once the gateway supervisor arms, then
#                                one run per hour (or on the root-only trigger)
#   selftest.sh --once [boot|hourly]   one run, results written, then exit
#
# WHY. The boot smoke (operator/bin/boot-smoke-test.sh) was the only judge of a
# seat, and it ran only when a person ran it: a provision or a release. A seat
# that drifted between releases was found by whoever next looked (8 of 12
# incidents in the 2026-10-22 review were found by a person). The same check
# list (operator/templates/seat-checks.sh) now runs here as root, and the
# result reaches the heartbeat through /run/smd-selftest/results.json, which the
# gate (hermes uid) reads and ships as `selftest`. The fleet verifies a release
# from that, and the fleet-alerts worker pages `selftest_failing` from it.
#
# BOOT vs HOURLY. Two classes of check are judged differently by run kind:
# a lane-is-idle check FAILS at boot (nothing may be running on a fresh boot)
# and WARNS hourly (a job may legitimately be running), and a check that asks a
# vendor FAILS at boot and WARNS hourly (vendor weather must not page every seat
# every hour). Everything else is a failure whenever it fails.
#
# RESULTS. One JSON document, written to a temp file in the same directory and
# renamed over results.json (readers never see half a file), mode 0644 so the
# gate can read it: {schema_version, version, image_ref, hermes_sha,
# expected_hermes_sha, run_kind, started_at, finished_at, checks: [{id, ok,
# warn, detail}], failed, warned, total, failed_streak}. failed_streak is the
# number of consecutive runs, ending with this one, that had a failure. A
# detail is at most 300 characters and is the check's own last words; the
# probes it runs are written never to print a credential's value.
#
# THE TRIGGER. /run/smd-selftest/trigger/run-now asks the loop for a run now
# (fleet selftest --plant uses it, so every run has one writer). The trigger
# directory is root-only (0700); the loop honours the file only when it is a
# regular file owned by root inside a root-owned 0700 directory, and removes
# anything else it finds there without running.
#
# TEST SEAMS (unset on a seat, where this runs under env -i with the values in
# entrypoint.sh): SMD_SELFTEST_ROOT prefixes every seat path, SMD_SELFTEST_CHECKS_DIR
# is where seat-checks.sh lives, and the timing and uid knobs below.
set -uo pipefail

SEAT_ROOT="${SMD_SELFTEST_ROOT:-}"
case "${SEAT_ROOT}" in *[!A-Za-z0-9/._-]*) echo "selftest: unsafe SMD_SELFTEST_ROOT" >&2; exit 2 ;; esac
CHECKS_DIR="${SMD_SELFTEST_CHECKS_DIR:-/app}"
RUN_DIR="${SEAT_ROOT}/run/smd-selftest"
RESULTS="${RUN_DIR}/results.json"
TRIGGER_DIR="${RUN_DIR}/trigger"
TRIGGER="${TRIGGER_DIR}/run-now"
LIVENESS_STATE="${SEAT_ROOT}/run/smd-gateway-liveness/state"
PERIOD_S="${SMD_SELFTEST_PERIOD_S:-3600}"
POLL_S="${SMD_SELFTEST_POLL_S:-15}"
ARM_WAIT_S="${SMD_SELFTEST_ARM_WAIT_S:-900}"
CHECK_TIMEOUT_S="${SMD_SELFTEST_CHECK_TIMEOUT_S:-300}"
ROOT_UID="${SMD_SELFTEST_ROOT_UID:-0}"
BACKOFF_BASE_S="${SMD_SELFTEST_BACKOFF_BASE_S:-30}"
BACKOFF_MAX_S="${SMD_SELFTEST_BACKOFF_MAX_S:-1800}"
MAX_RESTARTS="${SMD_SELFTEST_MAX_RESTARTS:-0}"
PY="${SMD_SELFTEST_PYTHON:-python3}"

# shellcheck disable=SC2034 # read by seat-checks.sh (the AgentMail probe's hostname)
APP_NAME="${FLY_APP_NAME:-}"
EXPECTED_HERMES_SHA="${SMD_HERMES_UPSTREAM_SHA:-}"
RUN_KIND="boot"
RECORDS=""

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [selftest] $*" >&2; }

# ---------- recording ----------
# record <id> <ok 0|1> <warn 0|1> <detail>: one tab-separated line per check.
record() {
  local detail
  detail="$(printf '%s' "$4" | tr -d '\000-\010\013-\037' | tr '\t\n' '  ' | cut -c1-300)"
  printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "${detail}" >> "${RECORDS}"
}

# The last words of a check, for its detail: the final three non-empty lines.
last_words() { printf '%s\n' "$1" | grep -v '^[[:space:]]*$' | tail -3 | tr '\n' ';' | sed 's/;$//'; }

with_budget() {
  if command -v timeout >/dev/null 2>&1; then timeout -k 5 "${CHECK_TIMEOUT_S}" "$@"; else "$@"; fi
}

# judge <id> <class strict|soft> <rc> <output>
judge() {
  if [ "$3" -eq 0 ]; then
    record "$1" 1 0 ""
  elif [ "$2" = soft ] && [ "${RUN_KIND}" = hourly ]; then
    record "$1" 0 1 "$(last_words "$4")"
  else
    record "$1" 0 0 "exit $3: $(last_words "$4")"
  fi
}

run_cmd() { local out rc=0; out="$(cd / && with_budget sh -c "$3" 2>&1)" || rc=$?; judge "$1" "$2" "${rc}" "${out}"; }
run_prog() { local out rc=0; out="$(cd / && printf '%s' "$3" | with_budget sh 2>&1)" || rc=$?; judge "$1" "$2" "${rc}" "${out}"; }

# ---------- the verbs seat-checks.sh calls ----------
check() { run_cmd "$1" strict "$2"; }
check_script() { run_prog "$1" strict "$2"; }
check_idle() { run_cmd "$1" soft "$2"; }
check_vendor() { run_cmd "$1" soft "$2"; }
check_note() { record "$1" 1 0 "$2"; }
check_broken() { record "$1" 0 0 "$2"; }
check_file() {
  local out rc=0
  out="$(cd / && SMD_SEAT_ROOT="${SEAT_ROOT}" with_budget "$2" "${SEAT_ROOT}/app/$3" 2>&1)" || rc=$?
  judge "$1" strict "${rc}" "${out}"
}
check_report() {
  local out rc=0
  out="$(cd / && printf '%s' "$2" | with_budget sh 2>&1)" || rc=$?
  if [ "${rc}" -eq 0 ]; then record "$1" 1 0 "$(last_words "${out}" | sed 's/.*;//')"; else judge "$1" strict "${rc}" "${out}"; fi
}
check_lever() {
  local out
  out="$(cd / && with_budget sh -c "$1" 2>/dev/null)"
  case "${out}" in
    *SMD_LEVER=set*) record "identifier-gate-lever" 0 1 "SMD_IDENTIFIER_GATE_MODE is set: identifier blocking may be off (ADR 0093 incident rollback only)" ;;
    *SMD_LEVER=unset*) record "identifier-gate-lever" 1 0 "" ;;
    *) record "identifier-gate-lever" 0 1 "could not read whether SMD_IDENTIFIER_GATE_MODE is set" ;;
  esac
}

# The in-image expectation source: the live customer.yaml, narrowed by what the
# boot's fetch found in the vault. A lane is authored when the skill that
# drives it is enabled for any persona, UNLESS (demand, drafting, litigation, casefile)
# this boot's fetch recorded a clean `absent`: the vault was listed and holds no
# inputs for the lane, which is how a seat that enables a lane's skill but
# authors no inputs for it looks (pilot-smokeball's demand lane: the skill is
# enabled, vaults/pilot-smokeball/demand/ has been empty since 2026-07-28 by
# design). That is the same verdict the laptop smoke reaches from the
# engagements checkout (no <lane>-firm.yaml there means none staged into the
# vault), so the two bindings agree on it. Before 2026-10-23 the self-test
# asked the skill alone, and pilot failed demand-firm-inputs-present on every
# boot with "stat: cannot statx '/var/lib/smd-config/demand/demand-firm.yaml'".
#
# Only `absent` narrows. `unreachable` (the vault holds the inputs, or could
# not be listed, and the seat has no copy), a missing state file, an unreadable
# one, or one root did not write all count as authored, so a failed fetch still
# fails the presence check: the 2026-10-14 A&P v141 falsifier, where the
# litigation inputs were in the vault and the boot's fetch failed silently.
# A customer.yaml that cannot be read is "cannot evaluate", which must not read
# as "not authored", so every lane then counts as authored and its presence
# checks run and say why. The bindings still differ where the engagements
# checkout and the vault differ (inputs authored but never staged, or staged
# then deleted from the checkout); the release that records both verdicts
# shows it.
ENABLED_SKILLS=""
ENABLED_READ=0
read_enabled_skills() {
  ENABLED_SKILLS="$("${SEAT_ROOT}/opt/hermes/.venv/bin/python3" -I - "${SEAT_ROOT}/var/lib/smd-config/customer.yaml" <<'PY' 2>/dev/null
import sys, yaml
doc = yaml.safe_load(open(sys.argv[1])) or {}
for p in doc.get("personas") or []:
    for s in (p or {}).get("skills") or []:
        if isinstance(s, dict) and s.get("enabled") is not False and isinstance(s.get("name"), str):
            print(s["name"])
PY
)" && ENABLED_READ=1
}
lane_skill() {
  case "$1" in
    medchron) echo medical-chronology-maintainer ;;
    demand) echo demand-letter-drafter ;;
    drafting) echo document-drafter ;;
    litigation) echo litigation-status ;;
    casefile) echo case-file-manager ;;
    marketing) echo marketing-request-router ;;
    *) return 1 ;;
  esac
}
# lane_inputs_absent <lane>: this boot's fetch recorded a clean `absent` in a
# regular file root owns, inside a directory root owns that nobody else can
# write. Anything else (no file, a symlink, another owner, any other word) is
# false, which keeps the lane authored.
lane_inputs_absent() {
  "${PY}" -I -B - "${SEAT_ROOT}/run/smd-lanes" "$1.inputs" "${ROOT_UID}" <<'PY' 2>/dev/null
import os, stat, sys
d, name, uid = sys.argv[1], sys.argv[2], int(sys.argv[3])
try:
    ds = os.lstat(d)
    fs = os.lstat(os.path.join(d, name))
    with open(os.path.join(d, name), encoding="ascii") as fh:
        word = fh.read(64).strip()
except (OSError, ValueError):
    sys.exit(1)
ok = (stat.S_ISDIR(ds.st_mode) and ds.st_uid == uid and not stat.S_IMODE(ds.st_mode) & 0o022
      and stat.S_ISREG(fs.st_mode) and fs.st_uid == uid and word == "absent")
sys.exit(0 if ok else 1)
PY
}
lane_authored() {
  [ "${ENABLED_READ}" = 1 ] || return 0
  printf '%s\n' "${ENABLED_SKILLS}" | grep -qxF "$(lane_skill "$1")" || return 1
  case "$1" in demand|drafting|litigation|casefile|marketing) ! lane_inputs_absent "$1" ;; *) return 0 ;; esac
}

# ---------- one run ----------
now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }

write_results() { # <started_at> <finished_at>
  local tmp
  tmp="$(mktemp "${RUN_DIR}/.results.XXXXXX")" || return 1
  if SELFTEST_RECORDS="${RECORDS}" SELFTEST_PREVIOUS="${RESULTS}" SELFTEST_RUN_KIND="${RUN_KIND}" \
     SELFTEST_STARTED="$1" SELFTEST_FINISHED="$2" SELFTEST_HERMES_FILE="${SEAT_ROOT}/opt/hermes/HERMES_SHA" \
     SELFTEST_VERSION="${SMD_OVERLAY_REF:-}" SELFTEST_IMAGE_REF="${FLY_IMAGE_REF:-}" \
     SELFTEST_EXPECTED_HERMES="${EXPECTED_HERMES_SHA}" "${PY}" -I -B - > "${tmp}" <<'PY'
import json, os, re
env = os.environ
checks = []
with open(env["SELFTEST_RECORDS"], encoding="utf-8", errors="replace") as fh:
    for line in fh:
        cid, ok, warn, detail = (line.rstrip("\n").split("\t", 3) + ["", "", ""])[:4]
        checks.append({"id": cid, "ok": ok == "1", "warn": warn == "1", "detail": detail[:300]})
failed = sum(1 for c in checks if not c["ok"] and not c["warn"])
streak = 0
if failed:
    try:
        with open(env["SELFTEST_PREVIOUS"], encoding="utf-8") as fh:
            prev = json.load(fh)
        streak = int(prev.get("failed_streak") or 0) if int(prev.get("failed") or 0) > 0 else 0
    except (OSError, ValueError, TypeError, AttributeError):
        streak = 0
    streak += 1
def sha(v):
    v = (v or "").strip()
    return v if re.fullmatch(r"[0-9a-f]{40}", v) else None
try:
    hermes = sha(open(env["SELFTEST_HERMES_FILE"], encoding="utf-8").read())
except OSError:
    hermes = None
doc = {
    "schema_version": 1,
    "version": env.get("SELFTEST_VERSION") or None,
    "image_ref": env.get("SELFTEST_IMAGE_REF") or None,
    "hermes_sha": hermes,
    "expected_hermes_sha": sha(env.get("SELFTEST_EXPECTED_HERMES")),
    "run_kind": env["SELFTEST_RUN_KIND"],
    "started_at": env["SELFTEST_STARTED"],
    "finished_at": env["SELFTEST_FINISHED"],
    "checks": checks,
    "failed": failed,
    "warned": sum(1 for c in checks if c["warn"]),
    "total": len(checks),
    "failed_streak": streak,
}
print(json.dumps(doc, sort_keys=True))
PY
  then
    chmod 0644 "${tmp}" && mv -f "${tmp}" "${RESULTS}"
  else
    rm -f "${tmp}"
    return 1
  fi
}

run_once() { # <boot|hourly>
  local started rc=0
  RUN_KIND="$1"
  install -d -m 0755 "${RUN_DIR}" || return 1
  RECORDS="$(mktemp "${RUN_DIR}/.records.XXXXXX")" || return 1
  started="$(now_iso)"
  read_enabled_skills || true
  # The staged-scripts program reads its data root from this variable.
  if [ -n "${SEAT_ROOT}" ]; then export SMD_SMOKE_DATA_ROOT="${SEAT_ROOT}/opt/data"; fi
  # shellcheck source=seat-checks.sh
  source "${CHECKS_DIR}/seat-checks.sh"
  seat_checks
  write_results "${started}" "$(now_iso)" || rc=1
  rm -f "${RECORDS}"
  log "${RUN_KIND} run finished (results $([ "${rc}" -eq 0 ] && echo written || echo NOT written))"
  return "${rc}"
}

# ---------- the loop ----------
# A trigger is honoured only when root put it there: a regular file owned by
# ROOT_UID in a ROOT_UID-owned 0700 directory. Anything else is removed unrun.
take_trigger() {
  [ -e "${TRIGGER}" ] || [ -L "${TRIGGER}" ] || return 1
  if "${PY}" -I -B - "${TRIGGER_DIR}" "${TRIGGER}" "${ROOT_UID}" <<'PY'
import os, stat, sys
d, f, uid = sys.argv[1], sys.argv[2], int(sys.argv[3])
ds, fs = os.lstat(d), os.lstat(f)
ok = (stat.S_ISDIR(ds.st_mode) and ds.st_uid == uid and stat.S_IMODE(ds.st_mode) == 0o700
      and stat.S_ISREG(fs.st_mode) and fs.st_uid == uid)
sys.exit(0 if ok else 1)
PY
  then
    rm -f "${TRIGGER}"
    return 0
  fi
  log "ignored a trigger that root did not place (${TRIGGER}); removed it"
  rm -rf "${TRIGGER}"
  return 1
}

nap() { # sleep up to PERIOD_S, ending early on an honoured trigger
  local waited=0
  while [ "${waited}" -lt "${PERIOD_S}" ]; do
    take_trigger && return 0
    sleep "${POLL_S}"
    waited=$((waited + POLL_S))
  done
}

await_armed() {
  local waited=0
  while [ "${waited}" -lt "${ARM_WAIT_S}" ]; do
    [ "$(head -c 16 "${LIVENESS_STATE}" 2>/dev/null)" = armed ] && return 0
    sleep "${POLL_S}"
    waited=$((waited + POLL_S))
  done
  log "the gateway supervisor did not arm in ${ARM_WAIT_S}s; running the boot checks anyway (they will say why)"
}

loop() {
  install -d -m 0755 "${RUN_DIR}" && install -d -m 0700 "${TRIGGER_DIR}" || exit 1
  await_armed
  run_once boot || log "boot run did not write results"
  while true; do
    nap
    run_once hourly || log "hourly run did not write results"
  done
}

# The crash path: the loop died. A synthetic WARN reaches the results file so
# a crash-looping self-test is seen, and the restart backs off exponentially.
record_crash() { # <restarts> <exit code>
  install -d -m 0755 "${RUN_DIR}" || return 1
  RECORDS="$(mktemp "${RUN_DIR}/.records.XXXXXX")" || return 1
  RUN_KIND="hourly"
  record "selftest-crashlooping" 0 1 "the self-test loop exited $2; restart $1"
  write_results "$(now_iso)" "$(now_iso)"
  rm -f "${RECORDS}"
}

supervise() {
  local restarts=0 rc delay
  log "Root self-test started (uid $(id -u); boot run once the gateway supervisor arms, then every ${PERIOD_S}s; ${RESULTS})"
  while true; do
    rc=0
    "$0" --loop || rc=$?
    [ "${rc}" -eq 130 ] && { log "loop ended (exit 130)"; exit 0; }
    restarts=$((restarts + 1))
    log "loop exited ${rc} (restart ${restarts})"
    record_crash "${restarts}" "${rc}" || log "could not record the crash"
    [ "${MAX_RESTARTS}" -gt 0 ] && [ "${restarts}" -ge "${MAX_RESTARTS}" ] && exit 1
    delay=$((BACKOFF_BASE_S << (restarts > 6 ? 6 : restarts - 1)))
    [ "${delay}" -gt "${BACKOFF_MAX_S}" ] && delay="${BACKOFF_MAX_S}"
    sleep "${delay}"
  done
}

case "${1:-}" in
  --supervise) supervise ;;
  --loop) loop ;;
  --once)
    case "${2:-hourly}" in boot|hourly) run_once "${2:-hourly}" ;; *) echo "usage: $0 --once [boot|hourly]" >&2; exit 2 ;; esac ;;
  *) echo "usage: $0 --supervise | --loop | --once [boot|hourly]" >&2; exit 2 ;;
esac
