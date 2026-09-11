#!/usr/bin/env bash
# cycle-window-mutation-check.sh — prove the three window suites can actually FAIL.
#
# WHY THIS IS A SCRIPT AND NOT A ONE-TIME DEMONSTRATION. On 2026-09-10, in this
# repo, a tamper proof came back GREEN against stale code: the runner had been
# installed NON-editable, so pytest imported a site-packages snapshot and the
# mutation never reached the module under test. A tamper proof that cannot fail
# is worth exactly as much as the check it was meant to validate — nothing. So
# this runs on demand, asserts the import path FIRST, and refuses to report
# anything if the suites are reading a different copy than the one it mutates.
#
# Usage:  bash operator/bin/tests/cycle-window-mutation-check.sh
# Exit 0  every mutation was caught. Exit 1  a mutation survived (or the
#         instrument could not be trusted).

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "${REPO_ROOT}"

PY="${REPO_ROOT}/operator/.venv/bin/python"
SEAT_MODULE="operator/workspace_broker/cycle_window.py"
FIXTURE="operator/contracts/cycle_window_fixture.json"
LAPTOP_DIR="${SS_ENGAGEMENTS_DIR:-${HOME}/dev/engagements}/operator/customers/ashton-price/tools/medchron"

fail() { echo "MUTATION CHECK FAILED: $*" >&2; exit 1; }

[ -x "${PY}" ] || fail "no venv at ${PY} (create it: cd operator && uv venv .venv && uv pip install -e ./runners/medchron)"

# ---- 0. the instrument itself -------------------------------------------------
# The 2026-09-10 trap: assert every module the suites import resolves INSIDE this
# worktree before trusting a single result below.
for mod in medchron; do
  where="$("${PY}" -c "import ${mod}; print(${mod}.__file__)" 2>/dev/null)" \
    || fail "cannot import ${mod}"
  case "${where}" in
    "${REPO_ROOT}"/*) : ;;
    *) fail "${mod} resolves to ${where}, OUTSIDE this worktree — the suites would
       pass against a stale snapshot. Reinstall editable: uv pip install -e ./runners/medchron" ;;
  esac
done
echo "ok  instrument: medchron imports from this worktree"

# Restore from a SNAPSHOT, not from git: these files may be uncommitted on the
# branch under development, and `git checkout --` silently does nothing for an
# untracked path. The first version of this script used git, left the module
# mutated after the run, and then copied the MUTATED fixture over the pristine
# vendored copy — destroying the very artifact the contract compares against.
SNAP="$(mktemp -d)"
cp "${SEAT_MODULE}" "${SNAP}/cycle_window.py"
cp "${FIXTURE}" "${SNAP}/fixture.json"
cp "${LAPTOP_DIR}/cycle_window_fixture.json" "${SNAP}/laptop_fixture.json"

restore() {
  cp "${SNAP}/cycle_window.py" "${SEAT_MODULE}"
  cp "${SNAP}/fixture.json" "${FIXTURE}"
  cp "${SNAP}/laptop_fixture.json" "${LAPTOP_DIR}/cycle_window_fixture.json"
}
cleanup() { restore; rm -rf "${SNAP}"; }
trap cleanup EXIT

export LAPTOP_DIR
seat_suite()   { "${PY}" -m pytest operator/workspace_broker/tests/test_cycle_window.py -q >/dev/null 2>&1; }
console_suite() { npx vitest run tests/cycle-window.test.ts >/dev/null 2>&1; }
laptop_suite() { ( cd "${LAPTOP_DIR}" && python3 test_cycle_window.py >/dev/null 2>&1 ); }

expect_red() { # <label> <suite-fn>
  if "$2"; then
    echo "FAIL $1: the suite PASSED against mutated code — it cannot fail, so it measures nothing" >&2
    return 1
  fi
  echo "ok  $1: caught"
}

rc=0

# ---- 1. chaining instead of returning to the anchor ---------------------------
# Jan 31 -> Feb 28 -> Mar 28 (wrong) rather than -> Mar 31. This mutation TILES
# PERFECTLY, so the property test cannot see it; only the hand-authored
# return-to-anchor vectors can. That is why both exist.
"${PY}" - <<'MUT'
import pathlib
p = pathlib.Path("operator/workspace_broker/cycle_window.py")
s = p.read_text()
s = s.replace("        end = _clamp(ey, em, anchor_day)",
              "        end = _clamp(ey, em, min(anchor_day, start.day))")
p.write_text(s)
MUT
expect_red "mutation 1 (chained, not return-to-anchor): seat" seat_suite || rc=1
restore

# ---- 2. an off-by-one that opens gaps between windows -------------------------
# The inverse: this one the PROPERTY test catches and the vectors might not.
"${PY}" - <<'MUT'
import pathlib
p = pathlib.Path("operator/workspace_broker/cycle_window.py")
s = p.read_text()
s = s.replace("    return date(year, month, min(day, calendar.monthrange(year, month)[1]))",
              "    return date(year, month, max(1, min(day, calendar.monthrange(year, month)[1]) - 1))")
p.write_text(s)
MUT
expect_red "mutation 2 (off-by-one clamp, opens gaps): seat" seat_suite || rc=1
restore

# ---- 3. the reader stops refusing a quoted anchor -----------------------------
# The divergence the reader table exists for: "15" must not read as 15.
"${PY}" - <<'MUT'
import pathlib
p = pathlib.Path("operator/workspace_broker/cycle_window.py")
s = p.read_text()
s = s.replace("    if isinstance(value, bool) or not isinstance(value, int):",
              "    if isinstance(value, str) and value.isdigit():\n        value = int(value)\n    if isinstance(value, bool) or not isinstance(value, int):")
p.write_text(s)
MUT
expect_red "mutation 3 (reader coerces a quoted anchor): seat" seat_suite || rc=1
restore

# ---- 4. the fixture moves in ONE repo only ------------------------------------
# The cross-repo contract: every side must notice, not just the one that changed.
"${PY}" - <<'MUT'
import json, pathlib
p = pathlib.Path("operator/contracts/cycle_window_fixture.json")
d = json.loads(p.read_text())
d["window"][1]["start"] = "2026-09-16T00:00:00.000Z"   # off by one day
p.write_text(json.dumps(d, indent=2) + "\n")
MUT
# The side that CHANGED goes red. The laptop keeps its own byte-identical copy
# and its own pin, so it stays green here — correctly: its copy did not move.
# That asymmetry is what makes a one-sided edit unmergeable, because the repo
# that changed cannot go green without carrying the change across.
expect_red "mutation 4a (fixture edited in ss-console only): seat"    seat_suite    || rc=1
expect_red "mutation 4a (fixture edited in ss-console only): console" console_suite || rc=1
if laptop_suite; then
  echo "ok  mutation 4a: laptop stays green — its own copy is untouched"
else
  echo "FAIL mutation 4a: the laptop went red on a file it does not read" >&2
  rc=1
fi
restore

# ---- 4b. the mirror: the LAPTOP's copy drifts instead -------------------------
"${PY}" - <<'MUT'
import json, os, pathlib
p = pathlib.Path(os.environ["LAPTOP_DIR"]) / "cycle_window_fixture.json"
d = json.loads(p.read_text())
d["window"][1]["start"] = "2026-09-16T00:00:00.000Z"
p.write_text(json.dumps(d, indent=2) + "\n")
MUT
expect_red "mutation 4b (fixture edited in the laptop copy only): laptop" laptop_suite || rc=1
restore

# ---- 5. the control: everything green when nothing is mutated -----------------
# A check that reports "caught" on an unmutated tree is reporting noise.
seat_suite    || fail "control: the seat suite is red on an unmutated tree"
console_suite || fail "control: the console suite is red on an unmutated tree"
laptop_suite  || fail "control: the laptop suite is red on an unmutated tree"
echo "ok  control: all three suites green on an unmutated tree"

[ "${rc}" -eq 0 ] && echo && echo "every mutation was caught" || echo >&2
exit "${rc}"
