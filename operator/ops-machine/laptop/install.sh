#!/bin/bash
# laptop/install.sh: set up a Captain laptop (mac23, m16) for client ops
# workspaces. Idempotent; re-run after workspaces.json or the guard changes.
#   * `ops` and `ops-handoff` into ~/.local/bin (already first on PATH);
#   * the workspace list to ~/.claude/smd-ops-workspaces.json;
#   * client-data-guard.mjs registered as a USER-level PreToolUse hook, so
#     every Claude session on this laptop, in any repo, is stopped before it
#     pulls client documents and told to hand the task over;
#   * cleanupPeriodDays 30, so session transcripts (which can quote client
#     text) are not kept on the laptop for longer than a month;
#   * removes the 2026-10-16 smd-ops-only launcher (~/.smd-ops and its .zshrc
#     line), superseded by this. Its Fly token is revoked separately.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/.local/bin" "$HOME/.claude/hooks"
install -m 0755 "$here/ops" "$here/ops-handoff" "$HOME/.local/bin/"
install -m 0644 "$here/../workspaces.json" "$HOME/.claude/smd-ops-workspaces.json"
install -m 0644 "$here/client-data-guard.mjs" "$HOME/.claude/hooks/smd-client-data-guard.mjs"

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

# Read back what was installed.
command -v ops >/dev/null && echo "ops: $(command -v ops)"
echo '{"tool_input":{"command":"python3 download.py 1"}}' | node "$HOME/.claude/hooks/smd-client-data-guard.mjs" 2>/dev/null \
  && { echo "FAIL guard did not block a test command"; exit 1; } || echo "guard: blocks a document pull (exit $?)"
ops
