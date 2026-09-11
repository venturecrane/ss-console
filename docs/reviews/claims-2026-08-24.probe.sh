#!/usr/bin/env bash
# Re-derive every FACT claim in claims-2026-08-24.md.
#
# The point: a review finding you cannot re-run is an opinion wearing a severity
# tag. Run this and the findings prove themselves, or they do not and the review
# was wrong. Either outcome is progress; a narrative you have to trust is not.
#
# Usage:  bash docs/reviews/claims-2026-08-24.probe.sh
# Exit:   0 = every claim still holds, 1 = at least one drifted
#
# F16 hits production D1 and is skipped unless you pass --remote.

cd "$(dirname "$0")/../.." || exit 2

pass=0
fail=0

check() {
  local id="$1" desc="$2" expected="$3" actual="$4"
  if [ "$actual" = "$expected" ]; then
    printf '  \033[32mHOLDS\033[0m  %-4s %s  (= %s)\n' "$id" "$desc" "$actual"
    pass=$((pass + 1))
  else
    printf '  \033[31mDRIFT\033[0m  %-4s %s  expected %s, got %s\n' "$id" "$desc" "$expected" "$actual"
    fail=$((fail + 1))
  fi
}

echo "Re-deriving the FACT claims from claims-2026-08-24.md"
echo

# --- Operator safety substrate ---
check F1 "sticky_stop non-code refs only" 2 \
  "$(grep -rn 'sticky_stop\|record_tool_failure\|record_refusal' operator/ --include='*.py' 2>/dev/null | grep -v test | grep -v control-probes | grep -v 'sticky_stop.py:' | wc -l | tr -d ' ')"

check F2 "broker auth gate line" 545 \
  "$(grep -n 'peer_pid != self.gateway_pid' operator/workspace_broker/server.py 2>/dev/null | cut -d: -f1)"

check F3 "no verb-set test" 0 \
  "$(grep -rln '_VERBS' operator/workspace_broker/tests/ 2>/dev/null | wc -l | tr -d ' ')"

check F4 "trust_ceiling absent from parity manifest" 0 \
  "$(grep -o '"[a-z_/]*\.py"' operator/contracts/overlay-pairs.json 2>/dev/null | sort -u | grep -c trust_ceiling | tr -d ' ')"

check F5 "trust_ceiling claims parity" 1 \
  "$(grep -c 'here for parity' operator/adapter/trust_ceiling.py 2>/dev/null | tr -d ' ')"

# --- Web perimeter ---
check F6 "no security response headers in shipped code" 0 \
  "$(grep -rniE 'content-security-policy|x-frame-options|strict-transport-security|frame-ancestors' src/ workers/ public/ wrangler.toml 2>/dev/null | wc -l | tr -d ' ')"

# --- Skill duplication ---
check F7 "BrokerSuppressedWakeWriter copies" 4 \
  "$(grep -rln 'class BrokerSuppressedWakeWriter' operator/skills/ 2>/dev/null | wc -l | tr -d ' ')"

check F8 "no sync test covers it" 0 \
  "$(grep -rln 'BrokerSuppressedWakeWriter' operator/tests/ 2>/dev/null | wc -l | tr -d ' ')"

# --- Packaging ---
check F9 "non-test sys.path.insert calls" 28 \
  "$(grep -rn 'sys.path.insert' operator/ --include='*.py' 2>/dev/null | grep -v test | wc -l | tr -d ' ')"

check F10 "noqa E402 suppressions" 102 \
  "$(grep -rn 'noqa: E402' operator/ --include='*.py' 2>/dev/null | wc -l | tr -d ' ')"

# --- CI gates ---
check F11 "gitleaks runs --no-git" 1 \
  "$(grep -c 'no-git' .github/workflows/security.yml 2>/dev/null | tr -d ' ')"

check F12 "no workflow runs coverage" 0 \
  "$(grep -rn 'test:coverage\|--coverage' .github/workflows/ 2>/dev/null | wc -l | tr -d ' ')"

check F13 "events prefers client session_id" 1 \
  "$(grep -c 'body.session_id' src/pages/api/events.ts 2>/dev/null | tr -d ' ')"

# --- The fix ---
# 3 = the definition plus BOTH return paths of scrubPath. This check was written
# expecting 2 and DRIFTED on its first run — the author's expectation was wrong,
# not the code. Left documented rather than quietly corrected, because it is the
# cheapest possible demonstration of why this file exists.
check F15 "manage-token redaction is wired (def + both returns)" 3 \
  "$(grep -c 'redactOpaqueSegments' src/pages/api/events.ts 2>/dev/null | tr -d ' ')"

# --- Live state (opt-in) ---
if [ "$1" = "--remote" ]; then
  rows=$(npx wrangler d1 execute ss-console-db --remote \
    --command "SELECT COUNT(*) AS n FROM events WHERE path LIKE '/book/manage/%'" --json 2>/dev/null \
    | python3 -c "import json,sys; raw=sys.stdin.read(); print(json.loads(raw[raw.index('['):])[0]['results'][0]['n'])" 2>/dev/null)
  check F16 "zero manage tokens in prod events" 0 "$rows"
else
  printf '  \033[33mSKIP \033[0m  F16  zero manage tokens in prod events  (pass --remote to run)\n'
fi

echo
echo "  $pass held, $fail drifted"
[ "$fail" -eq 0 ] || exit 1
