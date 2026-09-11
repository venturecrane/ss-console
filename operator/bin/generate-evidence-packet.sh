#!/usr/bin/env bash
# generate-evidence-packet.sh: compose a compliance evidence packet
# (issue #894) for one customer + period. Output is a digest-verified
# tar.gz (per-artifact SHA-256 + a manifest SHA-256 recorded in the
# append-only COMPLIANCE_PACKET_EXPORTED audit row) containing a PDF,
# JSON manifest, and per-spec evidence files. The manifest is NOT yet
# cryptographically signed -- it self-discloses signature="unsigned-stub";
# detached Ed25519 signing is a tracked follow-on gated on /captain/signing-key.
#
# Usage:
#   operator/bin/generate-evidence-packet.sh \
#     --customer <slug> \
#     --matter <id-or-all> \
#     --from <ISO> \
#     --to <ISO> \
#     --output <path> \
#     --actor <name> \
#     [--actor-role captain|compliance] \
#     [--acknowledge-unattributed-gap] \
#     [--pinned-head <sha256 hex>]
#
# Completeness of the log itself (ss#2500):
#   The hash chain proves that a row altered, removed, or inserted BEFORE the
#   end of the log breaks it at a verifiable point. It proves nothing about rows
#   cut off the END, because what survives such a cut is itself a valid chain.
#   --pinned-head takes a chain head recorded off the Machine before the export
#   (the newest audit_head_history row for this seat on the control plane); the
#   ledger must still contain it. If it does not, the build HALTS (exit 3) and
#   there is no acknowledge flag, because a compliance packet asserting a
#   complete record over a ledger that lost rows is the artifact the whole
#   mechanism exists to prevent. Without the flag the packet states on its face
#   that its audit section was not checked for truncation.
#
# Captain CLI integration (when bin/smd-cli lands):
#   smd-cli evidence <slug> --matter <m> --from <a> --to <b>
# delegates to this script with --actor=$USER and --actor-role=captain.
#
# Matter scoping and coverage:
#   matter_ref was added to the audit schema after seats had begun writing
#   rows, so rows written before that fix carry matter_ref = NULL forever.
#   A --matter <id> export that matches zero rows while such rows exist in
#   the period HALTS (exit 3) rather than ship an empty audit section that
#   an auditor would read as "nothing happened on this matter". Re-run with
#   --matter all, narrow the period, or pass --acknowledge-unattributed-gap
#   to emit the packet with the gap stated on its face. Every packet carries
#   its coverage boundary in 00-README.md, 01-summary.pdf, and manifest.json.
#
# Exit codes (forwarded from bin/lib/evidence.py):
#   0  packet generated
#   2  preflight failed (missing customer.yaml, bad arg)
#   3  build halted (EvidencePacketError; e.g. secret leak, role gate fail,
#      unanswerable matter-scoped empty)
#   4  unexpected error

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AIE_ROOT="${REPO_ROOT}/operator"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [evidence] $*"; }

if [ "$#" -lt 1 ]; then
  cat >&2 <<'USAGE'
Usage: generate-evidence-packet.sh --customer <slug> --matter <id|all> \
                                   --from <ISO> --to <ISO> \
                                   --output <path> --actor <name> \
                                   [--actor-role captain|compliance] \
                                   [--acknowledge-unattributed-gap] \
                                   [--pinned-head <sha256 hex>]
USAGE
  exit 2
fi

log "starting"

# Run from inside operator/ so `bin.lib.evidence` resolves as a
# package; this matches the layout other adapter scripts use.
cd "${AIE_ROOT}"

# uv + pyyaml match the toolchain pause / provision / rollback /
# decommission use, so Captain does not need a separate venv.
set +e
uv run --quiet --with pyyaml python3 -m bin.lib.evidence "$@"
EXIT_CODE=$?
set -e

case "${EXIT_CODE}" in
  0) log "packet generated" ;;
  2) log "preflight failed (exit 2): see stderr" ;;
  3) log "build halted (exit 3): see stderr" ;;
  4) log "unexpected error (exit 4): see stderr" ;;
  130) log "interrupted (exit 130)" ;;
  *) log "unknown exit ${EXIT_CODE}" ;;
esac

exit "${EXIT_CODE}"
