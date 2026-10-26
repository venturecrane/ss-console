#!/usr/bin/env bash
# stage-casefile-inputs.sh: the case file manager's firm-inputs staging block,
# sourced by provision-customer.sh in the same shell right after the litigation
# block (it reads SLUG, REPO_ROOT, the R2_* operator env, SS_ENGAGEMENTS_DIR,
# and calls die / log / assert_lane_not_on_main from there). Its own file for
# the reason the litigation one is: the provisioner sits at its shell size
# ratchet.
# shellcheck shell=bash

# ---------- Step 2f': the case file manager's firm inputs ----------
# The casefile job (runners/medchron/medchron/casefile/) reads the firm's
# casefile-firm.yaml (models, the per-job and monthly spend caps, the File
# Status shape, the fields a numbered change may write) and its prompts/
# (contract.md, reader.md, auditor.md), private, in engagements
# operator/customers/<slug>/casefile/. Validated against THIS checkout's runner
# (python -m medchron.casefile.firm: exit 0 OK, 1 problems, 2 usage), refused
# unless casefile-firm.yaml and every prompt are engagements origin/main, and
# SYNCED with --delete to vaults/<slug>/casefile/ (gone means gone). The prompts
# are markdown, so unlike the litigation sync only top-level notes are left out.
# A seat with no casefile inputs is skipped quietly here; its casefile lane
# defers casefile jobs and its broker refuses every submit (no budget authored).
# >>> casefile-inputs-stage
CASEFILE_INPUTS_DIR="${SS_ENGAGEMENTS_DIR:-${HOME}/dev/engagements}/operator/customers/${SLUG}/casefile"
if [ -f "${CASEFILE_INPUTS_DIR}/casefile-firm.yaml" ]; then
  _eng="${CASEFILE_INPUTS_DIR%/operator/customers/*}"
  if [ "${SS_ALLOW_DIVERGENT_SOURCE:-}" != "1" ]; then
    for _cf_rel in casefile-firm.yaml prompts/contract.md prompts/reader.md prompts/auditor.md; do
      [ -f "${CASEFILE_INPUTS_DIR}/${_cf_rel}" ] || continue
      [ "$(git -C "${_eng}" show "origin/main:operator/customers/${SLUG}/casefile/${_cf_rel}" 2>/dev/null)" = "$(cat "${CASEFILE_INPUTS_DIR}/${_cf_rel}")" ] \
        || die "the casefile firm input ${_cf_rel} at ${CASEFILE_INPUTS_DIR} is not engagements origin/main; merge it first (an unmerged file is an unreviewed one)"
    done
    unset _cf_rel
  fi
  [ -f "${REPO_ROOT}/operator/runners/medchron/medchron/casefile/firm.py" ] \
    || die "this checkout's runner has no casefile firm validator (medchron/casefile/firm.py); not uploading unvalidated casefile inputs"
  PYTHONPATH="${REPO_ROOT}/operator/runners/medchron" \
    uv run --quiet --with pyyaml python3 -m medchron.casefile.firm "${CASEFILE_INPUTS_DIR}" \
    || die "the casefile firm inputs for ${SLUG} do not validate against this checkout's runner (see the line above); not uploading them"
  log "Syncing the casefile firm inputs to R2: s3://${R2_BUCKET_CONFIG}/vaults/${SLUG}/casefile/"
  AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID}" AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY}" \
    aws s3 sync "${CASEFILE_INPUTS_DIR}/" "s3://${R2_BUCKET_CONFIG}/vaults/${SLUG}/casefile/" --delete \
      --exclude "eval/*" --exclude "*.md" --include "prompts/*.md" \
      --endpoint-url "${R2_ENDPOINT_URL}" --only-show-errors \
    || die "R2 sync of the casefile firm inputs failed"
  log "R2 upload OK (casefile firm inputs)"
else
  assert_lane_not_on_main "operator/customers/${SLUG}/casefile/casefile-firm.yaml"
fi
# <<< casefile-inputs-stage
