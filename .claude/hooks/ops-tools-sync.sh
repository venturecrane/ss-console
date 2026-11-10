#!/bin/bash
# SessionStart hook: keep the laptop's installed ops tools equal to origin/main.
#
# The ops tools (ops, ops-handoff, ops-requests, the client-data guard, the
# workspace list) are tracked under operator/ops-machine/laptop/ but RUN from
# copies in ~/.local/bin and ~/.claude, put there by laptop/install.sh. A merge
# does not move those copies. On 2026-11-10 the Captain's daily request check
# took 20 minutes instead of one because this laptop still ran the 10-27
# ops-handoff: the fix for "a handoff after a restart starts nothing" had
# merged the afternoon before (#3896) and was never installed. Its tests ran
# the repo's file, so nothing could see the stale copy.
#
# So: at every session start, in the primary checkout or a worktree, compare
# the installed copies with origin/main (install.sh --check owns the file
# list) and reinstall on a difference. origin/main, never this tree: a
# worktree can be days behind, and a branch editing these tools must not
# install its work in progress.
#
# Quiet when current. Speaks when it reinstalled or could not. Never blocks
# startup. A laptop that was never set up for ops (no ~/.local/bin/ops) is
# left alone: installing registers a user-level hook, which is a setup act.
# Tests: tests/ops-tools-sync.test.ts.
set -u

[ -x "$HOME/.local/bin/ops" ] || exit 0
# The checkout this hook file sits in (a worktree or the primary): any of them
# can read origin/main, which is all this needs.
cd "${OPS_TOOLS_SYNC_REPO:-$(dirname "$0")/../..}" 2>/dev/null || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0
[ "${OPS_TOOLS_SYNC_NO_FETCH:-}" = 1 ] || GIT_TERMINAL_PROMPT=0 git fetch -q origin main 2>/dev/null || true
sha=$(git rev-parse --short -q --verify origin/main 2>/dev/null) || exit 0

tmp=$(mktemp -d) || exit 0
trap 'rm -rf "$tmp"' EXIT
if ! git archive origin/main operator/ops-machine/laptop operator/ops-machine/workspaces.json 2>/dev/null | tar -x -C "$tmp" 2>/dev/null; then
  echo "ops-tools: could not read the laptop ops tools from origin/main @ $sha, so the installed copies are UNCHECKED this session."
  exit 0
fi
inst="$tmp/operator/ops-machine/laptop/install.sh"
[ -f "$inst" ] || exit 0
# An install.sh from before --check existed ignores the flag and installs,
# which is the right outcome for it too.
stale=$(bash "$inst" --check 2>/dev/null) && exit 0

bash "$inst" >/dev/null 2>&1
if left=$(bash "$inst" --check 2>/dev/null); then
  echo "ops-tools: the installed laptop ops tools were stale (${stale:-unknown files}); reinstalled from origin/main @ $sha and read back equal."
else
  echo "ops-tools: THE INSTALLED LAPTOP OPS TOOLS ARE STALE (${left:-${stale:-unknown files}}) and the reinstall from origin/main @ $sha did not fix it. ops-handoff and ops-requests on this laptop may be old. Run: bash operator/ops-machine/laptop/install.sh from a checkout at origin/main, and read its output."
fi
exit 0
