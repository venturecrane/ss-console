#!/bin/bash
# install.sh <fly-token-file>: install the `crane ss` -> smd-ops redirect on this
# laptop. The token file holds a Fly deploy token for app smd-ops only; it is
# moved into ~/.smd-ops/fly-token (0600) and the source file removed.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/.smd-ops"; chmod 700 "$HOME/.smd-ops"
mv "$1" "$HOME/.smd-ops/fly-token"; chmod 600 "$HOME/.smd-ops/fly-token"
install -m 0755 "$here/ops-connect" "$HOME/.smd-ops/ops-connect"
install -m 0644 "$here/crane-ss.zsh" "$HOME/.smd-ops/crane-ss.zsh"
line='[ -f "$HOME/.smd-ops/crane-ss.zsh" ] && source "$HOME/.smd-ops/crane-ss.zsh"'
grep -qF '.smd-ops/crane-ss.zsh' "$HOME/.zshrc" 2>/dev/null || printf '\n# SMD client ops run on smd-ops\n%s\n' "$line" >> "$HOME/.zshrc"
FLY_API_TOKEN="$(cat "$HOME/.smd-ops/fly-token")" "$(command -v fly || echo /opt/homebrew/bin/fly)" \
  ssh console -a smd-ops -u ops -C "true" >/dev/null 2>&1 && echo "OK $(hostname -s): reaches smd-ops" || echo "FAIL $(hostname -s): cannot reach smd-ops"
