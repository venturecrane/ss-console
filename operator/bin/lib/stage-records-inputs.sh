#!/usr/bin/env bash
# stage-records-inputs.sh: the matter records lane's firm-inputs staging block
# (ADR 0102 amendment 4), sourced by provision-customer.sh in the same shell
# right after the case file block (it reads SLUG, REPO_ROOT, the R2_* operator
# env, SS_ENGAGEMENTS_DIR, and calls die / log / assert_lane_not_on_main from
# there). Its own file for the reason the case file one is: the provisioner
# sits at its shell size ratchet.
# shellcheck shell=bash

# ---------- Step 2f'': the matter records lane's firm inputs ----------
# The records job (runners/medchron/medchron/records/) reads the firm's
# records-firm.yaml (per_job_cap_usd, monthly_budget_usd, usd_per_page),
# private, in engagements operator/customers/<slug>/records/. Validated against
# THIS checkout's runner (python -m medchron.records.firm: exit 0 OK, 1
# problems, 2 usage), refused unless it is engagements origin/main, and SYNCED
# with --delete to vaults/<slug>/records/ (gone means gone). Only the yaml goes:
# notes beside it stay in engagements. A seat with no records inputs is skipped
# quietly here; its records lane defers records jobs and its broker refuses
# every submit (no budget authored).
# >>> records-inputs-stage
RECORDS_INPUTS_DIR="${SS_ENGAGEMENTS_DIR:-${HOME}/dev/engagements}/operator/customers/${SLUG}/records"
if [ -f "${RECORDS_INPUTS_DIR}/records-firm.yaml" ]; then
  _eng="${RECORDS_INPUTS_DIR%/operator/customers/*}"
  if [ "${SS_ALLOW_DIVERGENT_SOURCE:-}" != "1" ]; then
    [ "$(git -C "${_eng}" show "origin/main:operator/customers/${SLUG}/records/records-firm.yaml" 2>/dev/null)" = "$(cat "${RECORDS_INPUTS_DIR}/records-firm.yaml")" ] \
      || die "the records firm input records-firm.yaml at ${RECORDS_INPUTS_DIR} is not engagements origin/main; merge it first (an unmerged file is an unreviewed one)"
  fi
  [ -f "${REPO_ROOT}/operator/runners/medchron/medchron/records/firm.py" ] \
    || die "this checkout's runner has no records firm validator (medchron/records/firm.py); not uploading unvalidated records inputs"
  PYTHONPATH="${REPO_ROOT}/operator/runners/medchron" \
    uv run --quiet --with pyyaml python3 -m medchron.records.firm "${RECORDS_INPUTS_DIR}" \
    || die "the records firm inputs for ${SLUG} do not validate against this checkout's runner (see the line above); not uploading them"
  log "Syncing the records firm inputs to R2: s3://${R2_BUCKET_CONFIG}/vaults/${SLUG}/records/"
  AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID}" AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY}" \
    aws s3 sync "${RECORDS_INPUTS_DIR}/" "s3://${R2_BUCKET_CONFIG}/vaults/${SLUG}/records/" --delete \
      --exclude "*" --include "records-firm.yaml" \
      --endpoint-url "${R2_ENDPOINT_URL}" --only-show-errors \
    || die "R2 sync of the records firm inputs failed"
  log "R2 upload OK (records firm inputs)"
else
  assert_lane_not_on_main "operator/customers/${SLUG}/records/records-firm.yaml"
fi
# <<< records-inputs-stage
