#!/bin/bash
# smd-ops entrypoint: make the volume a usable home for the ops user, start cron
# (the triage agent's schedule lives in the ops user's crontab, on the volume),
# then stay up. No network service is started: the Machine is reached only over
# Fly's private network (fly ssh console).
set -euo pipefail

HOME_DIR=/home/ops
chown ops:ops "$HOME_DIR"
chmod 0700 "$HOME_DIR"
for d in .ssh .config dev bin logs; do
  install -d -o ops -g ops -m 0700 "$HOME_DIR/$d"
done
if [ ! -f "$HOME_DIR/.bashrc" ]; then
  cat > "$HOME_DIR/.bashrc" <<'EOF'
export PATH="$HOME/bin:$HOME/.local/bin:$PATH"
export SS_ENGAGEMENTS_DIR="$HOME/dev/engagements"
export TZ=America/Phoenix
[ -f "$HOME/.ops-env" ] && . "$HOME/.ops-env"
EOF
  chown ops:ops "$HOME_DIR/.bashrc"
fi

# The ops user's crontab is kept on the volume and re-installed each boot.
if [ -f "$HOME_DIR/.crontab" ]; then
  crontab -u ops "$HOME_DIR/.crontab"
fi
service cron start >/dev/null

exec sleep infinity
