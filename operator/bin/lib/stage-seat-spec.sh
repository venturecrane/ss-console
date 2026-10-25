#!/usr/bin/env bash
# stage-seat-spec.sh: provisioning step 2, sourced by provision-customer.sh in
# the same shell (it reads SLUG, REPO_ROOT, CUSTOMER_YAML, R2_CONFIG_KEY, the
# R2_* operator env and SEAT_SPEC_SIGNING_KEY if set, and calls die / log from
# there). Its own file because the provisioner sits at its shell size ratchet.
# shellcheck shell=bash

# ---------- Step 2: customer.yaml to R2, through the seat-spec compiler ----------
# The upload goes through scripts/compile-seat-spec.ts, the same compiler the
# deploy job's publisher (the one writer, ADR 0094 as amended) runs, so the
# bytes a provisioned seat first boots from are the bytes a spec describes.
# --provisioning records the config's requirements without checking them
# against a running image: this run builds and deploys the image that carries
# them, which is why provisioning never ran the image guard.
#
# Signing needs SEAT_SPEC_SIGNING_KEY, which lives in the protected seat-spec
# CI environment and, for an operator who holds it, Infisical. Without it the
# customer.yaml uploads exactly as before and the spec waits for the next push
# to main, whose publisher converges every seat. bootstrap.sh fetches the
# object on first boot, so the upload precedes the Fly deploy.
# A seat authored before the client gave us everything (seat.yaml fly_app:
# null, or a PENDING marker in customer.yaml) is refused here, before a byte
# reaches R2: scripts/lib/pending-config.ts lists the markers.
( cd "${REPO_ROOT}" && npx --quiet tsx scripts/refuse-pending-config.ts --slug "${SLUG}" --config "${CUSTOMER_YAML}" ) \
  || die "${SLUG} is authored but not ready to provision (the lines above name each pending value); not uploading it"
_spec_out="$(mktemp -d)"
log "Compiling the seat spec for ${SLUG} (scripts/compile-seat-spec.ts)"
( cd "${REPO_ROOT}" && npx --quiet tsx scripts/compile-seat-spec.ts compile --slug "${SLUG}" \
    --config "${CUSTOMER_YAML}" --out "${_spec_out}" --provisioning --sign ) \
  || die "the seat-spec compiler refused ${CUSTOMER_YAML} (see the line above); not uploading it"
_r2_put() {
  AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID}" AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY}" \
    aws s3 cp "$1" "s3://${R2_BUCKET_CONFIG}/$2" --endpoint-url "${R2_ENDPOINT_URL}" --only-show-errors
}
log "Uploading customer.yaml to R2: s3://${R2_BUCKET_CONFIG}/${R2_CONFIG_KEY}"
_r2_put "${CUSTOMER_YAML}" "${R2_CONFIG_KEY}" \
  || die "R2 upload failed; bootstrap.sh would not be able to fetch customer.yaml"
log "R2 upload OK"
if [ -f "${_spec_out}/pointer.json" ]; then
  _spec_key="$(node -e "process.stdout.write(JSON.parse(require('fs').readFileSync(process.argv[1],'utf8')).spec_key)" "${_spec_out}/pointer.json")"
  [[ "${_spec_key}" =~ ^fleet/specs/${SLUG}/[0-9a-f]{64}\.json$ ]] || die "the compiler named a spec key outside fleet/specs/${SLUG}/: ${_spec_key}"
  _r2_put "${_spec_out}/spec.json" "${_spec_key}" || die "R2 upload of the seat spec failed"
  _r2_put "${_spec_out}/pointer.json" "vaults/${SLUG}/spec-pointer.json" || die "R2 upload of the spec pointer failed"
  log "Seat spec published and signed: ${_spec_key}"
else
  log "Seat spec compiled but NOT signed (SEAT_SPEC_SIGNING_KEY is not set here): the next push to main publishes it"
fi
rm -rf "${_spec_out}"
