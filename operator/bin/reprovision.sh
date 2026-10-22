#!/usr/bin/env bash
# reprovision.sh — one command, no credential hunt, to (re)provision a customer's
# Operator Machine.
#
# WHY THIS EXISTS: the operator-local R2 credentials that provision-customer.sh
# needs (R2_ENDPOINT_URL / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY /
# R2_BUCKET_CONFIG) were, for months, not stored anywhere discoverable — every
# agent re-derived them and burned ~2h. They now live in Infisical `/ss` (prod)
# and this wrapper injects them, so the recurring trap is closed for good.
#
# (The R2 S3 creds are derivable from CLOUDFLARE_API_TOKEN — id + sha256(value) —
# which is why they need not be minted; see docs/runbooks/operator/first-boot.md
# "R2 credentials".)
#
# Usage:
#   operator/bin/reprovision.sh <slug> [--build]  # interactive (you answer secret prompts);
#                                                 # --build builds this checkout instead of
#                                                 # deploying the fleet release image
#   yes s | operator/bin/reprovision.sh <slug>    # non-interactive: skip all secret
#                                                 # prompts (Machine secrets persist
#                                                 # across deploy; nothing to re-enter)
#
# Run from the repo root. Equivalent to:
#   infisical run --env=prod --path=/ss --silent -- operator/bin/provision-customer.sh <slug>
set -euo pipefail
SLUG="${1:-}"
[ -n "${SLUG}" ] || { echo "Usage: $0 <customer-slug>" >&2; exit 1; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Never restart a seat in the middle of a job (fleet release, 2026-10-19). The
# same live busy gate `fleet release` uses: a running lane job, pending wakes,
# a managed cron firing within 10 minutes or a stale heartbeat all mean busy;
# busy sets the quiesce marker (no new claims) and waits up to 30 minutes for
# idle. Still busy, or the probe could not tell, refuses. A seat with no app yet
# is idle by definition. FLEET_FORCE_MID_JOB=1 overrides, loudly, on purpose.
if [ "${FLEET_FORCE_MID_JOB:-}" = "1" ]; then
  echo "reprovision: WARNING FLEET_FORCE_MID_JOB=1: skipping the busy gate; a running job will be stopped" >&2
elif ! "${HERE}/fleet" busy "${SLUG}" --wait; then
  echo "reprovision: ${SLUG} is busy (or the busy probe could not tell); refusing to restart it mid-job. Re-run when idle, or set FLEET_FORCE_MID_JOB=1." >&2
  exit 1
fi

# The busy gate above may have left a quiesce marker, and it lives on the volume
# (/run/smd-medchron is a bind mount), so the new boot would claim no new work
# until it expired. Clear it once provisioning is done, whatever the outcome.
#
# Provisioning holds this seat's lease (fleet with-seat-lease), the one a
# `fleet release` takes for each seat it deploys: a reprovision and a rollout
# never deploy the same seat at once, and neither waits on any OTHER seat. A
# seat a rollout is deploying right now refuses with exit 4; re-run after it.
rc=0
infisical run --env=prod --path=/ss --silent -- \
  "${HERE}/fleet" with-seat-lease "${SLUG}" -- "${HERE}/provision-customer.sh" "$@" || rc=$?
"${HERE}/fleet" unquiesce "${SLUG}" || true
exit "${rc}"
