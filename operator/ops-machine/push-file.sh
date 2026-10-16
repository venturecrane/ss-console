#!/bin/bash
# push-file.sh <local> <remote-path> [mode]: copy one file onto smd-ops as the
# ops user and prove it arrived intact (sha256 compare). `fly ssh sftp put` can
# leave an empty file; piping through tee with --pty=false does not.
set -euo pipefail
src="$1"; dst="$2"; mode="${3:-600}"
fly ssh console -a smd-ops -u ops --pty=false -C "tee $dst" < "$src" > /dev/null 2>&1
fly ssh console -a smd-ops -u ops -C "chmod $mode $dst" > /dev/null 2>&1
remote=$(fly ssh console -a smd-ops -u ops -C "sha256sum $dst" 2>/dev/null | tail -1 | cut -d' ' -f1)
local_sum=$(shasum -a 256 "$src" | cut -d' ' -f1)
if [ "$remote" = "$local_sum" ]; then echo "OK $dst"; else echo "MISMATCH $dst"; exit 1; fi
