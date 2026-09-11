#!/bin/bash
# SessionStart hook: keep the SIBLING hermes-smd-overlay checkout honest.
#
# Companion to sync-primary.sh, which does this for ss-console's own primary
# checkout. The overlay had no equivalent, and on 2026-08-22 it was found 84
# commits behind origin/main with four dirty paths.
#
# WHY THIS MATTERS, AND WHY IT IS *NOT* A BUILD RISK. The operator image installs
# the overlay from remote git at the pinned SHA:
#
#     pip install "git+${OVERLAY_REPO}@${OVERLAY_REF}"   (operator/templates/Dockerfile:907)
#
# so nothing that reaches a seat ever comes from this checkout. The hazard is
# READING. An agent that opens plugins/... on disk gets whatever revision that
# tree happens to hold, and answers confidently from it. On 2026-08-22 that tree
# was 84 commits stale while four of five live sessions were doing overlay work.
# The existing reflex ("read `git show origin/main:<path>`, never the sibling
# working tree") is an instruction, not a control. This hook is the control.
#
# It NEVER touches a dirty tree. A fast-forward would silently destroy whatever
# is uncommitted there, and uncommitted work in a shared checkout belongs to
# whoever left it. Dirty means: say so, loudly, every session, and stop.
#
# stdout is injected into the session's context, so speak only when something
# needs the agent's attention. Never blocks startup.
set -u

OVERLAY="${SS_OVERLAY_DIR:-$HOME/dev/hermes-smd-overlay}"

# Not cloned here is a normal state, not a problem worth a line of context.
[ -d "$OVERLAY/.git" ] || exit 0

git -C "$OVERLAY" rev-parse --git-dir >/dev/null 2>&1 || exit 0

# A dirty tree is the one case where we report and change nothing.
if [ -n "$(git -C "$OVERLAY" status --porcelain 2>/dev/null)" ]; then
  count=$(git -C "$OVERLAY" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
  echo "overlay-sync: the sibling overlay checkout ($OVERLAY) is DIRTY ($count paths) and was NOT synced. Uncommitted work there is not yours to discard. Read overlay files with 'git -C $OVERLAY show origin/main:<path>', never from that working tree."
  exit 0
fi

GIT_TERMINAL_PROMPT=0 git -C "$OVERLAY" fetch -q origin main 2>/dev/null || {
  echo "overlay-sync: fetch failed for $OVERLAY; treat that checkout as stale and read via 'git show origin/main:<path>'."
  exit 0
}

branch=$(git -C "$OVERLAY" branch --show-current 2>/dev/null)
behind=$(git -C "$OVERLAY" rev-list --count HEAD..origin/main 2>/dev/null || echo 0)

if [ "$branch" != "main" ]; then
  # Someone may be legitimately working on a branch there. Don't move it; do say
  # what a file read from that tree would actually give you.
  if [ "${behind:-0}" -gt 0 ]; then
    echo "overlay-sync: the sibling overlay checkout is on '$branch', $behind commit(s) behind origin/main; not syncing. Files read from that tree are stale by that much - prefer 'git -C $OVERLAY show origin/main:<path>'."
  fi
  exit 0
fi

# Already current: stay quiet.
git -C "$OVERLAY" merge-base --is-ancestor origin/main HEAD 2>/dev/null && exit 0

before=$(git -C "$OVERLAY" rev-parse --short HEAD 2>/dev/null)
if git -C "$OVERLAY" merge --ff-only -q origin/main 2>/dev/null; then
  echo "overlay-sync: sibling overlay checkout fast-forwarded $before -> $(git -C "$OVERLAY" rev-parse --short HEAD) (origin/main)."
else
  echo "overlay-sync: the sibling overlay checkout has local commits not on origin/main; not syncing. Reconcile manually."
fi
exit 0
