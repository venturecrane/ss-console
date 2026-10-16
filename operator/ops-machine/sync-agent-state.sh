#!/bin/bash
# sync-agent-state.sh: put the agent operating state on smd-ops where Claude
# Code there will read it: the ss-console project memory (keyed by the repo's
# path, /home/ops/dev/ss-console -> -home-ops-dev-ss-console), and the session
# coordination files (board, obligation journal, read log). Verified by sha256.
set -euo pipefail
mem_src="$HOME/.claude/projects/-Users-scottdurgan-dev-ss-console"
mem_dst="/home/ops/.claude/projects/-home-ops-dev-ss-console"
manifest=$(mktemp)

ship() { # <base> <dest> <paths...>
  local base="$1" dest="$2"; shift 2
  (cd "$base" && find "$@" -type f -print0 | xargs -0 shasum -a 256) > "$manifest"
  fly ssh console -a smd-ops -u ops -C "mkdir -p $dest" > /dev/null 2>&1
  (cd "$base" && tar -cf - "$@") | fly ssh console -a smd-ops -u ops --pty=false -C "tar -xf - -C $dest" > /dev/null 2>&1
  fly ssh console -a smd-ops -u ops --pty=false -C "tee $dest/.sync.sha256" < "$manifest" > /dev/null 2>&1
  bad=$(fly ssh console -a smd-ops -u ops -C "sh -c 'cd $dest && sha256sum -c --quiet .sync.sha256 2>&1 | wc -l'" 2>/dev/null | tail -1 | tr -d ' \r')
  echo "$dest: $(wc -l < "$manifest" | tr -d ' ') files, mismatches=${bad:-unknown}"
}

ship "$mem_src" "$mem_dst" memory
ship "$HOME/.claude" /home/ops/.claude ss-board ss-obligation-journal
rm -f "$manifest"
