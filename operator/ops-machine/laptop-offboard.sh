#!/bin/bash
# laptop-offboard.sh [--execute]: take client operations off this laptop, after
# the move to smd-ops has been VERIFIED (move-to-ops.sh / move-by-folder.sh).
#
# Without --execute it only prints what it would delete and why (dry run). With
# --execute it deletes, then reads the filesystem back to prove each path is gone.
#
# What it removes:
#   1. client folders recorded as verified on smd-ops (the ~/.smd-ops-moved-*
#      ledgers, plus the single-shot moves listed in ~/.smd-ops-verified.txt);
#   2. engagements repo clones left in Claude temp folders, ONLY if each has no
#      uncommitted change and no commit missing from origin;
#   3. the laptop's Fly login and its WireGuard configs (seat access now lives on
#      smd-ops; `fly auth login` remains the Captain's break-glass);
#   4. the local encrypted cache of the production secrets (~/.infisical/secrets-backup).
# It never touches ~/dev (product repos), ~/.claude memory, or anything unverified.
set -uo pipefail
EXEC=0; [ "${1:-}" = "--execute" ] && EXEC=1
del=(); skipped=()

add_verified() { # <base> <ledger-file>
  local base="$1" ledger="$2"
  [ -f "$ledger" ] || return 0
  while IFS= read -r rel; do
    [ -n "$rel" ] && [ -e "$base/$rel" ] && del+=("$base/${rel#./}")
  done < "$ledger"
}
add_verified "$HOME/smd-medchron-data" "$HOME/.smd-ops-moved-mac23_smd-medchron-data.txt"
add_verified /private/tmp/claude-501 "$HOME/.smd-ops-moved-mac23_tmp.txt"
add_verified "$HOME/.claude/projects" "$HOME/.smd-ops-moved-mac23_transcripts.txt"
[ -f "$HOME/.smd-ops-verified.txt" ] && while IFS= read -r p; do [ -e "$p" ] && del+=("$p"); done < "$HOME/.smd-ops-verified.txt"

# Stale engagements clones in Claude temp folders.
while IFS= read -r g; do
  repo="${g%/.git}"
  url=$(git -C "$repo" remote get-url origin 2>/dev/null || true)
  case "$url" in *venturecrane/engagements*) ;; *) continue;; esac
  dirty=$(git -C "$repo" status --porcelain 2>/dev/null | head -1)
  git -C "$repo" fetch -q origin 2>/dev/null || true
  ahead=$(git -C "$repo" log --oneline --branches --not --remotes 2>/dev/null | head -1)
  if [ -z "$dirty" ] && [ -z "$ahead" ]; then del+=("$repo"); else skipped+=("$repo (unsaved work)"); fi
done < <(find /private/tmp/claude-501 -maxdepth 5 -name .git -type d 2>/dev/null)

[ -d "$HOME/.infisical/secrets-backup" ] && del+=("$HOME/.infisical/secrets-backup")

echo "Would delete ${#del[@]} paths; skipped ${#skipped[@]}:"
printf '  skip %s\n' "${skipped[@]:-}" | grep -v '^  skip $' || true
if [ $EXEC = 0 ]; then printf '  del  %s\n' "${del[@]:-}"; echo "dry run; re-run with --execute"; exit 0; fi

for p in "${del[@]}"; do rm -rf -- "$p"; done
left=0; for p in "${del[@]}"; do [ -e "$p" ] && { echo "STILL PRESENT $p"; left=$((left+1)); }; done
echo "deleted $(( ${#del[@]} - left )) of ${#del[@]}"

fly auth logout >/dev/null 2>&1 || true
rm -f "$HOME"/.fly/wireguard* 2>/dev/null || true
if fly auth whoami >/dev/null 2>&1; then echo "FLY STILL LOGGED IN"; else echo "fly: logged out"; fi
