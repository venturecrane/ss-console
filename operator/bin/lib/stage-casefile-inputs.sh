#!/usr/bin/env bash
# stage-casefile-inputs.sh: the case file manager's firm-inputs staging block,
# sourced by provision-customer.sh in the same shell right after the litigation
# block (it reads SLUG, REPO_ROOT, the R2_* operator env, SS_ENGAGEMENTS_DIR,
# and calls die / log / assert_lane_not_on_main from there). Its own file for
# the reason the litigation one is: the provisioner sits at its shell size
# ratchet.
# shellcheck shell=bash

# ---------- Step 2f': the case file manager's firm inputs ----------
# The casefile job (runners/jobs/jobs/casefile/) reads the firm's
# casefile-firm.yaml (models, the per-job and monthly spend caps, the File
# Status shape, the fields a numbered change may write) and its prompts/
# (contract.md, reader.md, auditor.md), private, in engagements
# operator/customers/<slug>/casefile/. Validated against THIS checkout's runner
# (python -m jobs.casefile.firm: exit 0 OK, 1 problems, 2 usage), refused
# unless casefile-firm.yaml and every prompt are engagements origin/main, and
# SYNCED with --delete to vaults/<slug>/casefile/ (gone means gone). The prompts
# are markdown, so unlike the litigation sync only top-level notes are left out.
# A seat with no casefile inputs is skipped quietly here; its casefile lane
# defers casefile jobs and its broker refuses every submit (no budget authored).
# The write itself (validation, the key guard, the --delete sync, the read-back)
# is lib/publish-firm-inputs.sh, the one writer, which the engagements repo's
# merge workflow also runs (2026-11-05): provisioning and a merge publish the
# same bytes the same way. This block keeps only the engagements-main gate.
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
  R2_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID}" R2_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY}" \
  R2_ENDPOINT_URL="${R2_ENDPOINT_URL}" R2_BUCKET_CONFIG="${R2_BUCKET_CONFIG}" \
    bash "${BIN_DIR}/lib/publish-firm-inputs.sh" "${SLUG}" casefile "${CASEFILE_INPUTS_DIR}" --runner-checkout "${REPO_ROOT}" \
    || die "the casefile firm inputs for ${SLUG} were not published (publish-firm-inputs exit $?: 1 refused by this checkout's runner, 3 R2, 4 the validator could not run; the lines above say which)"
  log "R2 upload OK (casefile firm inputs)"
else
  assert_lane_not_on_main "operator/customers/${SLUG}/casefile/casefile-firm.yaml"
fi
# <<< casefile-inputs-stage
