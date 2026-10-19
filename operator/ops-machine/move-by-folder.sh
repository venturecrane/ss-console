#!/bin/bash
# move-by-folder.sh <base-dir> <dest-subdir> [list-file]
#
# Resumable, verified move of a large tree: each top-level entry of base-dir (or
# each line of list-file, relative to base-dir) goes through move-to-ops.sh on
# its own, up to 3 tries, and a verified entry is recorded in a local ledger so
# a re-run skips it. One dropped connection costs one folder, not 81 GB.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
base="$1"; sub="$2"; list="${3:-}"
ledger="$HOME/.${OPS_APP:-smd-ops}-moved-$(echo "$sub" | tr '/' '_').txt"
touch "$ledger"
if [ -n "$list" ]; then entries=$(cat "$list"); else entries=$(cd "$base" && ls -A | sed 's|^|./|'); fi
ok=0; failed=0
while IFS= read -r -u 3 e; do
  [ -z "$e" ] && continue
  if grep -Fxq -- "$e" "$ledger"; then ok=$((ok+1)); continue; fi
  for try in 1 2 3; do
    if out=$(/bin/bash "$here/move-to-ops.sh" "$base" "$sub" "$e" 2>&1) && echo "$out" | grep -q '^VERIFIED'; then
      echo "$e" >> "$ledger"; ok=$((ok+1)); echo "ok   $e"; break
    fi
    [ "$try" = 3 ] && { failed=$((failed+1)); echo "FAIL $e: $(echo "$out" | tail -1)"; }
  done
done 3<<< "$entries"
echo "SUMMARY $sub: verified=$ok failed=$failed"
