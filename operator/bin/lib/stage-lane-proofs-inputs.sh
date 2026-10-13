#!/usr/bin/env bash
# stage-lane-proofs-inputs.sh: the lane-proof gate's authored seed, sourced by
# provision-customer.sh in the same shell right after the litigation block (it
# reads SLUG, REPO_ROOT, the R2_* operator env, and calls die / log from
# there). Its own file for the reason the others are: the provisioner sits at
# its shell size ratchet, and its stage loop names this file.
# shellcheck shell=bash

# ---------- Step 2g: the kinds of job proven on this seat (2026-10-13) ----------
# operator/customers/<slug>/lane-proofs.yaml in THIS repo (lane keys, dates and
# evidence ids only; reviewed in a PR like any config) is validated against
# this checkout's runner (python -m medchron.lane_proof) and copied to
# vaults/<slug>/lane-proofs.yaml; entrypoint-lane-proofs.sh fetches it into the
# root-owned medchron tree every boot. No file: the vault copy is REMOVED (gone
# means gone); a seat keeps the copy it last fetched (fail-static), so a lane
# is un-proven by a revocation, never by deleting this file.
# >>> lane-proofs-stage
LANE_PROOFS_FILE="${REPO_ROOT}/operator/customers/${SLUG}/lane-proofs.yaml"
if [ -f "${LANE_PROOFS_FILE}" ]; then
  PYTHONPATH="${REPO_ROOT}/operator/runners/medchron" \
    uv run --quiet --with pyyaml python3 -m medchron.lane_proof "${LANE_PROOFS_FILE}" \
    || die "${LANE_PROOFS_FILE} does not validate (see the line above); not uploading it"
  AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID}" AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY}" \
    aws s3 cp "${LANE_PROOFS_FILE}" "s3://${R2_BUCKET_CONFIG}/vaults/${SLUG}/lane-proofs.yaml" \
      --endpoint-url "${R2_ENDPOINT_URL}" --only-show-errors \
    || die "R2 upload of lane-proofs.yaml failed"
  log "R2 upload OK (lane-proofs.yaml)"
else
  AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID}" AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY}" \
    aws s3 rm "s3://${R2_BUCKET_CONFIG}/vaults/${SLUG}/lane-proofs.yaml" \
      --endpoint-url "${R2_ENDPOINT_URL}" --only-show-errors 2>/dev/null || true
  log "No lane-proofs.yaml for ${SLUG}: every job on this seat waits for a proof run"
fi
# <<< lane-proofs-stage
