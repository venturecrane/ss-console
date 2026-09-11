#!/usr/bin/env bash
# decommission-customer.sh: per-customer off-boarding (issue #820).
#
# Composes the existing memory + voice ``decommission_source`` hooks with
# substrate-deletion steps (R2 namespace, Vectorize indexes, AgentMail,
# Fly Machine), the compliance evidence packet archive, and the
# customers/<slug>/ tombstone.
#
# Usage:
#   operator/bin/decommission-customer.sh <slug> [--dry-run]
#   operator/bin/decommission-customer.sh <slug> --live [--confirm-slug <slug>]
#
# Default is --dry-run. Pass --live to execute deletions. A live run asks
# for the slug a second time: on a terminal this script prompts for it,
# and a non-interactive caller passes --confirm-slug <slug> explicitly.
# Live mode halts on any failure (exit code 3); re-run with the same slug
# to resume from the last completed step. Every step is idempotent.
#
# Every destructive backend arms itself only from a staged credential
# (CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID, AGENTMAIL_API_KEY,
# FLY_API_TOKEN, HEALTHCHECKS_API_KEY). A logged-in `fly` CLI does not
# count. With any backend unwired a --live run is refused (exit 5) and
# nothing is touched.
#
# Captain CLI integration (when bin/smd-cli lands):
#   smd-cli decommission <slug>
# delegates to this script with --live and --actor=$USER.

set -euo pipefail

SLUG="${1:-}"
[ -n "${SLUG}" ] || { echo "Usage: $0 <slug> [--dry-run|--live] [--confirm-slug <slug>]" >&2; exit 2; }

# Shift off the slug so the remaining args can be forwarded.
shift

# Default mode is dry-run; the Python CLI also defaults that way, but
# making it explicit here keeps the contract obvious in CI traces.
MODE_FLAG="--dry-run"
CONFIRM_SLUG=""
EXTRA_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) MODE_FLAG="--dry-run" ;;
    --live)    MODE_FLAG="--live" ;;
    --confirm-slug)
      [ $# -ge 2 ] || { echo "--confirm-slug needs a value" >&2; exit 2; }
      CONFIRM_SLUG="$2"
      shift
      ;;
    --confirm-slug=*) CONFIRM_SLUG="${1#--confirm-slug=}" ;;
    *)         EXTRA_ARGS+=("$1") ;;
  esac
  shift
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AIE_ROOT="${REPO_ROOT}/operator"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [decommission/${SLUG}] $*"; }

# A live run is confirmed by typing the slug a second time. The Python CLI
# enforces the match (exit 2); this is where the second typing happens when a
# person is at the keyboard. Without a terminal there is nobody to ask, so the
# caller must have passed --confirm-slug, and the CLI refuses otherwise.
if [ "${MODE_FLAG}" = "--live" ] && [ -z "${CONFIRM_SLUG}" ]; then
  if [ -t 0 ]; then
    printf 'This will DESTROY the seat %s: its Fly app and volume, R2 objects, inbox, and console rows.\n' "${SLUG}" >&2
    printf 'Type the slug to confirm: ' >&2
    IFS= read -r CONFIRM_SLUG
  else
    log "REFUSED: --live without --confirm-slug and no terminal to prompt on; nothing was touched"
    exit 2
  fi
fi

log "starting (${MODE_FLAG})"

# Run from inside operator/ so `bin.lib.decommission_cli` resolves as
# a package; this matches the layout the other adapter scripts use.
cd "${AIE_ROOT}"

CONFIRM_ARGS=()
if [ -n "${CONFIRM_SLUG}" ]; then
  CONFIRM_ARGS=(--confirm-slug "${CONFIRM_SLUG}")
fi

# uv + pyyaml is the same toolchain pause/provision/rollback use, so
# Captain does not need a separate venv.
set +e
uv run --quiet --with pyyaml python3 -m bin.lib.decommission_cli \
  "${SLUG}" \
  "${MODE_FLAG}" \
  "${CONFIRM_ARGS[@]}" \
  "${EXTRA_ARGS[@]}"
EXIT_CODE=$?
set -e

case "${EXIT_CODE}" in
  0)  log "complete (${MODE_FLAG})" ;;
  2)  log "preflight failed (exit 2): see stderr (a --live run also needs --confirm-slug equal to the slug)" ;;
  3)  log "decommission halted mid-sequence (exit 3): re-run with same slug to resume" ;;
  4)  log "unexpected error (exit 4): see stderr" ;;
  5)  log "REFUSED (exit 5): nothing was touched. Either a --live run found a destructive backend unwired (stage the missing credential named on stderr; see decommission_cli.py header), or --allow-unwired was given without a fixture --customers-root" ;;
  130) log "interrupted (exit 130)" ;;
  *)  log "unknown exit ${EXIT_CODE}" ;;
esac

exit "${EXIT_CODE}"
