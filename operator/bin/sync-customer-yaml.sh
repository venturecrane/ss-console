#!/usr/bin/env bash
#
# sync-customer-yaml.sh <slug>
#
# Push operator/customers/<slug>/customer.yaml to the R2 object the seat boots
# from (s3://${R2_BUCKET_CONFIG}/vaults/<slug>/customer.yaml), so it propagates
# to the running Machine on its next restart.
#
# The merge-triggered publisher (scripts/ci-publish-customer-configs.sh) and the
# nightly reconciler (scripts/ci-reconcile-r2-customer-configs.sh) are the normal
# writers of this object. This is the operator-run path for the same write, and
# it applies the same guards they do, because whichever process writes the key
# decides what the seat runs at its next restart:
#
#   SLUG. The canonical pattern (#2285), the shape the seat demands at boot.
#   VALIDATE. The canonical TS validator (ADR 0019); an invalid object is a seat
#     kill on the next restart.
#   NEVER CREATE. A 404 on the object refuses: the first publish binds a config
#     to a Machine, a volume and a secret set, and is provisioning's job
#     (provision-customer.sh). Any other head-object failure also refuses:
#     "could not tell" must not read as "no object here".
#   IMAGE GUARD (ADR 0093, the 2026-10-13 A&P outage). A config naming a skill
#     or pre_run the seat's running image (plus its skills release) lacks is
#     refused, from the one shared implementation
#     (scripts/lib/seat-image-guard.sh). Reprovision the seat to ship it.
#   READ BACK. The object is read back and compared byte for byte.
#
# R2 auth: R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_ENDPOINT_URL if set
# (e.g. `infisical run --env prod --path /ss -- ...`), otherwise derived from
# CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID (scripts/lib/r2-creds.sh).
# Optional:
#   R2_BUCKET_CONFIG    config bucket name (default: smd-customer-config)
#   FLEET_IMAGE_LOOKUP  1 to resolve the seat's image by its heartbeat image_ref
#                       (needs wrangler + CLOUDFLARE_API_TOKEN); otherwise the
#                       image manifest the seat uploads at boot is read.
#
# This script does NOT touch Fly, secrets, or git. It guards + uploads, nothing else.

set -euo pipefail

die() { echo "ERROR: $*" >&2; exit 1; }
log() { echo "[sync-customer-yaml] $*"; }

SLUG="${1:-}"
[ -n "${SLUG}" ] || die "usage: sync-customer-yaml.sh <slug>"
# Canonical slug pattern (#2285), identical to the publisher's and reconciler's:
# the slug becomes part of the R2 key, so this guard must never be the loose one.
[[ "${SLUG}" =~ ^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$ ]] || die "invalid slug: ${SLUG}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CUSTOMER_YAML="${REPO_ROOT}/operator/customers/${SLUG}/customer.yaml"
[ -f "${CUSTOMER_YAML}" ] || die "not found: ${CUSTOMER_YAML}"

R2_BUCKET_CONFIG="${R2_BUCKET_CONFIG:-smd-customer-config}"
R2_CONFIG_KEY="vaults/${SLUG}/customer.yaml"
[[ "${R2_CONFIG_KEY}" =~ ^vaults/[a-z0-9][a-z0-9-]{0,38}[a-z0-9]/customer\.yaml$ ]] \
  || die "refusing to write R2 key outside the customer.yaml key space: ${R2_CONFIG_KEY}"

command -v aws >/dev/null 2>&1 || die "aws CLI not found (required for R2 upload)"

# shellcheck source=../../scripts/lib/r2-creds.sh
source "${REPO_ROOT}/scripts/lib/r2-creds.sh"
derive_r2_creds

r2() {
  AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID}" \
  AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY}" \
    aws "$@" --endpoint-url "${R2_ENDPOINT_URL}"
}

# shellcheck source=../../scripts/lib/seat-image-guard.sh
source "${REPO_ROOT}/scripts/lib/seat-image-guard.sh"

work_dir=$(mktemp -d)
trap 'rm -rf "${work_dir}"' EXIT

log "Validating ${CUSTOMER_YAML}..."
( cd "${REPO_ROOT}" && npx --quiet tsx scripts/validate-customer-yaml.ts "${CUSTOMER_YAML}" ) \
  || die "customer.yaml validation failed; not uploading"
log "customer.yaml OK"

# NEVER CREATE. Only an existing object is ever overwritten here.
head_err="${work_dir}/head.err"
if r2 s3api head-object --bucket "${R2_BUCKET_CONFIG}" --key "${R2_CONFIG_KEY}" \
     >/dev/null 2>"${head_err}"; then
  :
elif grep -q '(404)' "${head_err}"; then
  die "${SLUG} has no object at s3://${R2_BUCKET_CONFIG}/${R2_CONFIG_KEY}; the first publish is Captain-gated provisioning: run operator/bin/provision-customer.sh"
else
  sed 's/^/    /' "${head_err}" >&2
  die "${SLUG}: could not read s3://${R2_BUCKET_CONFIG}/${R2_CONFIG_KEY} (not a 404); refusing to guess whether the seat is provisioned"
fi

log "Checking the config against the seat's running image..."
seat_image_guard "${SLUG}" "${CUSTOMER_YAML}" "${work_dir}" \
  || die "${SLUG}: not uploading; the seat's running image cannot run this config (above). Reprovision the seat to ship it."

log "Uploading to R2: s3://${R2_BUCKET_CONFIG}/${R2_CONFIG_KEY}"
r2 s3 cp "${CUSTOMER_YAML}" "s3://${R2_BUCKET_CONFIG}/${R2_CONFIG_KEY}" --only-show-errors \
  || die "R2 upload failed"

readback="${work_dir}/readback.yaml"
r2 s3 cp "s3://${R2_BUCKET_CONFIG}/${R2_CONFIG_KEY}" "${readback}" --only-show-errors \
  || die "uploaded but could not read the object back to verify"
cmp -s "${CUSTOMER_YAML}" "${readback}" \
  || die "R2 object does not match the file that was uploaded"
log "R2 upload OK (read back byte-identical)"
log "Next: restart the customer's Machine to apply (fly machine restart <id> -a hermes-${SLUG}), or redeploy."
