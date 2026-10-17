#!/bin/bash
# Entrypoint for smd-ops and every client ops workspace (ops-<slug>): make the
# volume a usable home for the ops user, start cron, then stay up until the ops
# user asks the Machine to stop. No network service is started: the Machine is
# reached only over Fly's private network (fly ssh console).
set -euo pipefail

HOME_DIR=/home/ops
chown ops:ops "$HOME_DIR"
chmod 0700 "$HOME_DIR"
for d in .ssh .config dev bin logs state inbox locks; do
  install -d -o ops -g ops -m 0700 "$HOME_DIR/$d"
done
if [ ! -f "$HOME_DIR/.bashrc" ]; then
  cat > "$HOME_DIR/.bashrc" <<'EOF'
export PATH="$HOME/bin:/opt/ops/bin:$HOME/.local/bin:$PATH"
export SS_ENGAGEMENTS_DIR="$HOME/dev/engagements"
export TZ=America/Phoenix
[ -f "$HOME/.ops-env" ] && . "$HOME/.ops-env"
EOF
  chown ops:ops "$HOME_DIR/.bashrc"
fi
grep -q /opt/ops/bin "$HOME_DIR/.bashrc" || echo 'export PATH="$HOME/bin:/opt/ops/bin:$PATH"' >> "$HOME_DIR/.bashrc"

# A client workspace (OPS_WORKSPACE=<slug>, set by bin/ops-workspace).
#  * /etc/smd-ops-workspace is the marker the laptop tripwire
#    (client-data-guard.mjs) reads to know it is NOT on a laptop.
#  * The workspace's own secrets (Fly secrets, present only in this process's
#    environment) are written to ~/.ops-secrets for the ops user's shells. The
#    claude shim (/opt/ops/bin/claude) strips ANTHROPIC_API_KEY from Claude
#    itself, which runs on the Captain's Max login, and hands this file to
#    Claude's Bash tool (CLAUDE_ENV_FILE), so the chronology tools it runs get
#    the client's own Anthropic key and the seat token.
if [ -n "${OPS_WORKSPACE:-}" ]; then
  printf '%s\n' "$OPS_WORKSPACE" > /etc/smd-ops-workspace
  umask 077
  {
    for v in OPS_WORKSPACE OPS_SEAT_APP OPS_SEAT_TOKEN_EXPIRES FLY_API_TOKEN CRANE_CONTEXT_KEY; do
      [ -n "${!v:-}" ] && printf 'export %s=%q\n' "$v" "${!v}"
    done
    [ -n "${OPS_TOOL_ANTHROPIC_API_KEY:-}" ] && printf 'export ANTHROPIC_API_KEY=%q\n' "$OPS_TOOL_ANTHROPIC_API_KEY"
    printf 'export SMD_MC_DATA=%q SMD_FR_DATA=%q\n' "$HOME_DIR/work/medchron" "$HOME_DIR/work/filereview"
  } > "$HOME_DIR/.ops-secrets"
  umask 022
  chown ops:ops "$HOME_DIR/.ops-secrets"
  grep -q ops-secrets "$HOME_DIR/.bashrc" || echo '[ -f "$HOME/.ops-secrets" ] && . "$HOME/.ops-secrets"' >> "$HOME_DIR/.bashrc"
  install -d -o ops -g ops -m 0700 "$HOME_DIR/work" "$HOME_DIR/work/medchron" "$HOME_DIR/work/filereview" "$HOME_DIR/ledger"
  [ -f "$HOME_DIR/.crontab" ] || install -o ops -g ops -m 0600 /opt/ops/crontab-workspace "$HOME_DIR/.crontab"
fi

# The ops user's crontab is kept on the volume and re-installed each boot.
if [ -f "$HOME_DIR/.crontab" ]; then
  crontab -u ops "$HOME_DIR/.crontab"
fi
service cron start >/dev/null

# Stay up until ops-idle-stop (run by the ops user) asks for a stop. Exiting 0
# stops the Machine (restart policy on-failure); `ops <client>` or the daily
# sweep starts it again. The volume, and everything on it, is untouched.
rm -f "$HOME_DIR/state/stop-requested"
while [ ! -f "$HOME_DIR/state/stop-requested" ]; do sleep 20; done
rm -f "$HOME_DIR/state/stop-requested"
echo "stop requested by ops-idle-stop; exiting"
exit 0
