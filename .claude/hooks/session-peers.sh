#!/bin/bash
#
# Session Peers Hook (SessionStart) -- peripheral vision for concurrent work.
#
# Sessions on this repo cannot see each other. Handoffs are written at /eos,
# so a session in flight is invisible to every other one, and the SOS briefing
# shows the newest CLOSED session. On 2026-07-31 that meant five concurrent
# sessions, 22 merges in 36 hours, and every briefing describing a world from
# that morning: the newest handoff a session could see predated roughly ten
# merges that had already landed.
#
# This prints what a session cannot otherwise learn: who else is live, and
# what arrived on main ahead of it. Both are read straight off the filesystem
# and the local git refs -- no network, no `gh`, no credentials, no API.
#
# Registered with NO matcher, deliberately. The sibling sync-primary.sh uses
# matcher "startup" and additionally exits in worktrees, so copying either
# choice would silence this hook in the sessions that most need it: the
# worktree sessions, which are where all mutating work happens.
#
# Wire protocol:
#   stdin:  JSON with .cwd (Claude Code hook contract)
#   stdout: injected into the session's context
#   exit 0 always; never block startup on a diagnostic.

set -u

# Resolve the tree this session is in. Payload .cwd first: CLAUDE_PROJECT_DIR
# is pinned at launch and does not follow EnterWorktree, so preferring it
# would describe the primary to a worktree session.
PAYLOAD=$(cat 2>/dev/null) || exit 0
TREE=""
if command -v jq >/dev/null 2>&1; then
  TREE=$(printf '%s' "$PAYLOAD" | jq -r '.cwd // empty' 2>/dev/null)
fi
[ -n "$TREE" ] && [ -d "$TREE" ] || TREE="$PWD"
[ -d "$TREE" ] || TREE="${CLAUDE_PROJECT_DIR:-}"
[ -n "$TREE" ] && [ -d "$TREE" ] || exit 0

COMMON=$(git -C "$TREE" rev-parse --git-common-dir 2>/dev/null) || exit 0
case "$COMMON" in
  /*) ;;
  *) COMMON="$(cd "$TREE" && cd "$COMMON" 2>/dev/null && pwd)" || exit 0 ;;
esac
SELF=$(git -C "$TREE" rev-parse --show-toplevel 2>/dev/null) || exit 0

# --- Live peers -------------------------------------------------------------
#
# The lock files are written by Claude Code itself when a session enters a
# worktree; nothing in this repo writes them. They carry a pid, which is the
# only liveness signal available, and a dead pid means an abandoned worktree
# rather than a peer. Unlocked worktrees are reported without a pid because
# a session may hold one without the harness having locked it.
# Each worktree's path comes from its own `gitdir` file, which records the
# absolute path of its .git link. Deriving it from the current tree instead
# (as a sibling directory) is only correct when read FROM a worktree and
# silently finds nothing when read from the primary, where the worktrees live
# under .claude/worktrees/ rather than alongside.
PEERS=""
for d in "$COMMON"/worktrees/*/; do
  [ -d "$d" ] || continue
  name=$(basename "$d")
  [ -f "$d/gitdir" ] || continue
  wt=$(dirname "$(cat "$d/gitdir" 2>/dev/null)")
  [ -n "$wt" ] && [ -d "$wt" ] || continue
  [ "$wt" = "$SELF" ] && continue

  branch=$(git -C "$wt" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "?")
  if [ -f "$d/locked" ]; then
    pid=$(sed -n 's/.*(pid \([0-9][0-9]*\).*/\1/p' "$d/locked" 2>/dev/null)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      PEERS="${PEERS}  ${name} on ${branch} (pid ${pid}, live)
"
    else
      PEERS="${PEERS}  ${name} on ${branch} (locked by pid ${pid:-?}, NOT running -- abandoned)
"
    fi
  else
    PEERS="${PEERS}  ${name} on ${branch} (unlocked)
"
  fi
done

if [ -n "$PEERS" ]; then
  printf '[peers] Other worktrees on this repo. They cannot see you and you cannot see their work in progress:\n%s' "$PEERS"
fi

# --- Board stub --------------------------------------------------------------
#
# Register this session on the shared board (~/.claude/ss-board) at startup,
# before it has a mission: pid + worktree + session id. The reflex-primer
# refreshes `updated` every turn (liveness) and prints every peer's mission
# line; `.claude/bin/mission set` fills in the mission once the Captain
# states the session's focus. This hook's own pid parent IS the claude
# process, which is the one pid signal worth recording.
BOARD_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)/lib/board.mjs"
[ -f "$BOARD_LIB" ] || BOARD_LIB="$SELF/.claude/hooks/lib/board.mjs"
if command -v node >/dev/null 2>&1 && [ -f "$BOARD_LIB" ]; then
  SID=$(printf '%s' "$PAYLOAD" | jq -r '.session_id // empty' 2>/dev/null)
  if [ -n "$SID" ]; then
    node "$BOARD_LIB" set "$SELF" --pid "$PPID" --session "$SID" 2>/dev/null || true
  else
    node "$BOARD_LIB" set "$SELF" --pid "$PPID" 2>/dev/null || true
  fi
  echo "[board] Registered on the session board. Once the Captain states this session's focus, draft its winning line yourself and run: .claude/bin/mission set \"<outcome>, via <task>\" --focus <issue#|branch>. The outcome is what the business or a client experiences; product work never names a client."
fi

# --- What landed on main ahead of you ---------------------------------------
#
# Reads the local origin/main ref rather than fetching: this runs at startup
# and must not add a network round trip. Worktrees share the primary's refs,
# so any recent fetch by any session serves all of them.
BEHIND=$(git -C "$TREE" rev-list --count HEAD..origin/main 2>/dev/null) || BEHIND=""
if [ -n "$BEHIND" ] && [ "$BEHIND" -gt 0 ] 2>/dev/null; then
  echo "[peers] origin/main is ${BEHIND} commit(s) ahead of this checkout (as of the last fetch). Most recent:"
  git -C "$TREE" log --oneline --no-decorate -5 HEAD..origin/main 2>/dev/null | sed 's/^/  /'
fi

exit 0
