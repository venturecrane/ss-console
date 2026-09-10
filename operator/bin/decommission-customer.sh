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
#   operator/bin/decommission-customer.sh <slug> --live
#
# Default is --dry-run. Pass --live to execute deletions. Live mode halts
# on any failure (exit code 3); re-run with the same slug to resume from
# the last completed step. Every step is idempotent.
#
# Captain CLI integration (when bin/smd-cli lands):
#   smd-cli decommission <slug>
# delegates to this script with --live and --actor=$USER.
#
# Notes on stubs:
#   AgentMail and Fly Machine destruction are stubbed behind
#   protocols today; the stubs log "skipped (no client wired)" and
#   return a manifest the audit log records. Production wiring is a
#   constructor swap in bin/lib/decommission.py: no script rewrite.

set -euo pipefail

SLUG="${1:-}"
[ -n "${SLUG}" ] || { echo "Usage: $0 <slug> [--dry-run|--live]" >&2; exit 2; }

# Shift off the slug so the remaining args can be forwarded.
shift

# Default mode is dry-run; the Python CLI also defaults that way, but
# making it explicit here keeps the contract obvious in CI traces.
MODE_FLAG="--dry-run"
EXTRA_ARGS=()
for arg in "$@"; do
  case "${arg}" in
    --dry-run) MODE_FLAG="--dry-run" ;;
    --live)    MODE_FLAG="--live" ;;
    *)         EXTRA_ARGS+=("${arg}") ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AIE_ROOT="${REPO_ROOT}/operator"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [decommission/${SLUG}] $*"; }

log "starting (${MODE_FLAG})"

# Run from inside operator/ so `bin.lib.decommission_cli` resolves as
# a package; this matches the layout the other adapter scripts use.
cd "${AIE_ROOT}"

# uv + pyyaml is the same toolchain pause/provision/rollback use, so
# Captain does not need a separate venv.
set +e
uv run --quiet --with pyyaml python3 -m bin.lib.decommission_cli \
  "${SLUG}" \
  "${MODE_FLAG}" \
  "${EXTRA_ARGS[@]}"
EXIT_CODE=$?
set -e

case "${EXIT_CODE}" in
  0)  log "complete (${MODE_FLAG})" ;;
  2)  log "preflight failed (exit 2): see stderr" ;;
  3)  log "decommission halted mid-sequence (exit 3): re-run with same slug to resume" ;;
  4)  log "unexpected error (exit 4): see stderr" ;;
  5)  log "REFUSED (exit 5): a --live run was requested but one or more destructive backends are unwired; nothing was touched. Stage the missing credentials (see decommission_cli.py header) or pass --allow-unwired for a dev/fixture run" ;;
  130) log "interrupted (exit 130)" ;;
  *)  log "unknown exit ${EXIT_CODE}" ;;
esac

exit "${EXIT_CODE}"
