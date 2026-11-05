#!/usr/bin/env bash
# stage-records-inputs.sh: the matter records lane's firm-inputs staging block
# (ADR 0102 amendment 4), sourced by provision-customer.sh in the same shell
# right after the case file block (it reads SLUG, REPO_ROOT, the R2_* operator
# env, SS_ENGAGEMENTS_DIR, and calls die / log / assert_lane_not_on_main from
# there). Its own file for the reason the case file one is: the provisioner
# sits at its shell size ratchet.
# shellcheck shell=bash

# ---------- Step 2f'': the matter records lane's firm inputs ----------
# The records job (runners/jobs/jobs/records/) reads the firm's
# records-firm.yaml (per_job_cap_usd, monthly_budget_usd, usd_per_page),
# private, in engagements operator/customers/<slug>/records/. Validated against
# THIS checkout's runner (python -m jobs.records.firm: exit 0 OK, 1
# problems, 2 usage), refused unless it is engagements origin/main, and SYNCED
# with --delete to vaults/<slug>/records/ (gone means gone). Only the yaml goes:
# notes beside it stay in engagements. A seat with no records inputs is skipped
# quietly here; its records lane defers records jobs and its broker refuses
# every submit (no budget authored).
# The write itself (validation, the key guard, the --delete sync, the read-back)
# is lib/publish-firm-inputs.sh, the one writer, which the engagements repo's
# merge workflow also runs (2026-11-05): provisioning and a merge publish the
# same bytes the same way. This block keeps only the engagements-main gate.
# >>> records-inputs-stage
RECORDS_INPUTS_DIR="${SS_ENGAGEMENTS_DIR:-${HOME}/dev/engagements}/operator/customers/${SLUG}/records"
if [ -f "${RECORDS_INPUTS_DIR}/records-firm.yaml" ]; then
  _eng="${RECORDS_INPUTS_DIR%/operator/customers/*}"
  if [ "${SS_ALLOW_DIVERGENT_SOURCE:-}" != "1" ]; then
    [ "$(git -C "${_eng}" show "origin/main:operator/customers/${SLUG}/records/records-firm.yaml" 2>/dev/null)" = "$(cat "${RECORDS_INPUTS_DIR}/records-firm.yaml")" ] \
      || die "the records firm input records-firm.yaml at ${RECORDS_INPUTS_DIR} is not engagements origin/main; merge it first (an unmerged file is an unreviewed one)"
  fi
  R2_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID}" R2_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY}" \
  R2_ENDPOINT_URL="${R2_ENDPOINT_URL}" R2_BUCKET_CONFIG="${R2_BUCKET_CONFIG}" \
    bash "${BIN_DIR}/lib/publish-firm-inputs.sh" "${SLUG}" records "${RECORDS_INPUTS_DIR}" --runner-checkout "${REPO_ROOT}" \
    || die "the records firm inputs for ${SLUG} were not published (publish-firm-inputs exit $?: 1 refused by this checkout's runner, 3 R2, 4 the validator could not run; the lines above say which)"
  log "R2 upload OK (records firm inputs)"
else
  assert_lane_not_on_main "operator/customers/${SLUG}/records/records-firm.yaml"
fi
# <<< records-inputs-stage
