#!/bin/bash
# move-to-ops.sh <base-dir> <dest-subdir> <path>...
#
# Copy paths (relative to base-dir) onto smd-ops under
# /home/ops/archive/<dest-subdir>/, then PROVE the copy: a sha256 manifest is
# built locally, shipped with the files, and checked on the Machine. Prints
# counts only. Exit 0 only when every file verifies. Nothing is deleted here;
# deleting the laptop copy is a separate step that requires this to have passed.
set -euo pipefail
base="$1"; sub="$2"; shift 2
dest="/home/ops/archive/$sub"
tag=$(date +%s)
manifest=$(mktemp)

cd "$base"
find "$@" -type f -print0 | xargs -0 shasum -a 256 > "$manifest"
files=$(wc -l < "$manifest" | tr -d ' ')
echo "local files: $files"

fly ssh console -a smd-ops -u ops -C "mkdir -p $dest" > /dev/null 2>&1
tar -cf - "$@" | fly ssh console -a smd-ops -u ops --pty=false -C "tar -xf - -C $dest" > /dev/null 2>&1
fly ssh console -a smd-ops -u ops --pty=false -C "tee $dest/.manifest-$tag.sha256" < "$manifest" > /dev/null 2>&1
bad=$(fly ssh console -a smd-ops -u ops -C "sh -c 'cd $dest && sha256sum -c --quiet .manifest-$tag.sha256 2>&1 | wc -l'" 2>/dev/null | tail -1 | tr -d ' \r')
rm -f "$manifest"
echo "remote mismatches: ${bad:-unknown}"
if [ "${bad:-x}" = "0" ]; then echo "VERIFIED $sub ($files files)"; else echo "NOT VERIFIED $sub"; exit 1; fi
