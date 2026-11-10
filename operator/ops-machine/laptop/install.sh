#!/bin/bash
# laptop/install.sh: set up a Captain laptop (mac23, m16) for client ops
# workspaces. Idempotent; re-run after workspaces.json or the guard changes.
#   * `ops`, `ops-handoff` and `ops-requests` (with its requests_digest.py)
#     into ~/.local/bin (first on PATH in an interactive shell);
#   * the workspace list to ~/.claude/smd-ops-workspaces.json;
#   * client-data-guard.mjs registered as a USER-level PreToolUse hook, so
#     every Claude session on this laptop, in any repo, is stopped before it
#     pulls client documents and told to hand the task over;
#   * cleanupPeriodDays 30, so session transcripts (which can quote client
#     text) are not kept on the laptop for longer than a month;
#   * removes the 2026-10-16 smd-ops-only launcher (~/.smd-ops and its .zshrc
#     line), superseded by this. Its Fly token is revoked separately.
#
# install.sh --check: install nothing; print each installed file that is
# missing or differs from this tree and exit 1, or print nothing and exit 0.
# .claude/hooks/ops-tools-sync.sh runs it at every session start against
# origin/main and reinstalls on a difference. Why (2026-11-10): a fix to
# ops-handoff merged on 11-09 and this laptop still ran the 10-27 copy the
# next morning, because a merge does not reach ~/.local/bin and nothing
# compared the two. The file list below is the only one; add a tool here and
# both the install and the check cover it.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# source (relative to this folder) | installed path | mode
files="ops|$HOME/.local/bin/ops|0755
ops-handoff|$HOME/.local/bin/ops-handoff|0755
ops-requests|$HOME/.local/bin/ops-requests|0755
requests_digest.py|$HOME/.local/bin/requests_digest.py|0644
../workspaces.json|$HOME/.claude/smd-ops-workspaces.json|0644
client-data-guard.mjs|$HOME/.claude/hooks/smd-client-data-guard.mjs|0644"
if [ "${1:-}" = "--check" ]; then
  stale=""
  while IFS='|' read -r src dst _; do
    cmp -s "$here/$src" "$dst" || stale="$stale $(basename "$dst")"
  done <<<"$files"
  [ -z "$stale" ] && exit 0
  echo "${stale# }"
  exit 1
fi
mkdir -p "$HOME/.local/bin" "$HOME/.claude/hooks"
while IFS='|' read -r src dst mode; do
  install -m "$mode" "$here/$src" "$dst"
done <<<"$files"

node - "$HOME/.claude/settings.json" "$HOME/.claude/hooks/smd-client-data-guard.mjs" <<'JS'
const fs = require('fs')
const [file, hook] = process.argv.slice(2)
const s = fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, 'utf8')) : {}
s.cleanupPeriodDays = 30
s.hooks ??= {}
s.hooks.PreToolUse ??= []
const cmd = `node ${hook}`
s.hooks.PreToolUse = s.hooks.PreToolUse.filter((e) => !(e.hooks || []).some((h) => (h.command || '').includes('smd-client-data-guard')))
s.hooks.PreToolUse.push({ matcher: 'Bash|Edit|Write|NotebookEdit', hooks: [{ type: 'command', command: cmd }] })
fs.writeFileSync(file + '.tmp', JSON.stringify(s, null, 2) + '\n')
fs.renameSync(file + '.tmp', file)
console.log('settings: guard registered, cleanupPeriodDays 30')
JS

if [ -f "$HOME/.zshrc" ] && grep -q '\.smd-ops/' "$HOME/.zshrc"; then
  sed -i '' -e '/^# SMD client ops run on smd-ops$/d' -e '/\.smd-ops\/crane-ss\.zsh/d' "$HOME/.zshrc"
  echo "removed the old smd-ops launcher line from ~/.zshrc"
fi
rm -rf "$HOME/.smd-ops"

# Read back what was installed, by its installed path: an agent may run this
# over ssh, where the shell never reads ~/.zshrc and ~/.local/bin is not on
# PATH (mac23, 2026-10-22: "ops: command not found" after a good install).
echo "ops: $HOME/.local/bin/ops"
echo '{"tool_input":{"command":"python3 download.py 1"}}' | node "$HOME/.claude/hooks/smd-client-data-guard.mjs" 2>/dev/null \
  && { echo "FAIL guard did not block a test command"; exit 1; } || echo "guard: blocks a document pull (exit $?)"
"$HOME/.local/bin/ops"
