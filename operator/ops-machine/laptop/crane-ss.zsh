# SMD client operations run on smd-ops, not this laptop (2026-10-16).
# `crane ss`          -> opens or rejoins the session on the ops machine
# `crane ss <name>`   -> a second, separately named session (e.g. crane ss brokery)
# `crane ss --local`  -> the old behaviour, here on the laptop (product work)
# Every other venture is untouched. Sourced from ~/.zshrc.
crane() {
  if [ "${1:-}" = "ss" ]; then
    if [ "${2:-}" = "--local" ]; then
      shift 2; command crane ss "$@"; return
    fi
    if [ -x "$HOME/.smd-ops/ops-connect" ]; then
      shift; "$HOME/.smd-ops/ops-connect" "${1:-main}"; return
    fi
  fi
  command crane "$@"
}
