#!/bin/bash
# The mac23 client-data move, one verified set at a time (2026-10-16).
# $1 = scratch dir holding desktop-client-items.txt and tmp-matter-dirs.txt.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
lists="$1"
move() { /bin/bash "$here/move-to-ops.sh" "$@" 2>&1 | tail -1; }

mapfile -t desk < "$lists/desktop-client-items.txt" 2>/dev/null || IFS=$'\n' read -r -d '' -a desk < "$lists/desktop-client-items.txt"
mapfile -t tmpd < "$lists/tmp-matter-dirs.txt" 2>/dev/null || IFS=$'\n' read -r -d '' -a tmpd < "$lists/tmp-matter-dirs.txt"

move "$HOME/Desktop" mac23/desktop "${desk[@]}"
move "$HOME" mac23/home Downloads
move /private/tmp/claude-501 mac23/tmp "${tmpd[@]}"
move "$HOME/.claude/projects" mac23/transcripts $(cd "$HOME/.claude/projects" && ls -d -- -Users-scottdurgan-dev-ss-console*)
move "$HOME" mac23/home smd-medchron-data
echo "BATCH DONE"
