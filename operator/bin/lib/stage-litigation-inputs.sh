#!/usr/bin/env bash
# stage-litigation-inputs.sh: the litigation status job's firm-inputs staging
# block, sourced by provision-customer.sh in the same shell right after the
# drafting block (it reads SLUG, REPO_ROOT, the R2_* operator env,
# SS_ENGAGEMENTS_DIR, and calls die / log from there). Its own file for the
# reason the drafting one is: the provisioner sits at its shell size ratchet.
# shellcheck shell=bash

# ---------- Step 2f: the litigation status job's firm inputs (2026-10-07) ----------
# The litigation job (runners/jobs/jobs/litigation/) reads the firm's
# litigation-firm.yaml (models, the per-job and monthly spend caps, the
# court-paper and discovery patterns, process server names, settlement terms,
# form hints, the attorney roster, the case-status vocabulary), private, in
# engagements operator/customers/<slug>/litigation/. Beside it, once, a
# baseline/ seed (baseline.json + matters/*.json + manifest/*.json: a hand
# run's results in the job's state format) that entrypoint-litigation.sh copies
# into the seat's state dir only when the state holds none. Validated against
# THIS checkout's runner (python -m jobs.litigation.firm), refused unless
# litigation-firm.yaml is engagements origin/main, and SYNCED with --delete to
# vaults/<slug>/litigation/ (gone means gone). A seat with no litigation inputs
# is skipped quietly here; its litigation lane defers litigation jobs and its
# broker refuses every submit (no budget authored).
# The write itself (validation, the key guard, the --delete sync, the read-back)
# is lib/publish-firm-inputs.sh, the one writer, which the engagements repo's
# merge workflow also runs (2026-11-05): provisioning and a merge publish the
# same bytes the same way. This block keeps only the engagements-main gate.
# >>> litigation-inputs-stage
LITIGATION_INPUTS_DIR="${SS_ENGAGEMENTS_DIR:-${HOME}/dev/engagements}/operator/customers/${SLUG}/litigation"
if [ -f "${LITIGATION_INPUTS_DIR}/litigation-firm.yaml" ]; then
  _eng="${LITIGATION_INPUTS_DIR%/operator/customers/*}"
  _rel="operator/customers/${SLUG}/litigation/litigation-firm.yaml"
  if [ "${SS_ALLOW_DIVERGENT_SOURCE:-}" != "1" ] \
     && [ "$(git -C "${_eng}" show "origin/main:${_rel}" 2>/dev/null)" != "$(cat "${LITIGATION_INPUTS_DIR}/litigation-firm.yaml")" ]; then
    die "the litigation firm inputs at ${LITIGATION_INPUTS_DIR} are not engagements origin/main; merge them first (an unmerged file is an unreviewed one)"
  fi
  R2_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID}" R2_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY}" \
  R2_ENDPOINT_URL="${R2_ENDPOINT_URL}" R2_BUCKET_CONFIG="${R2_BUCKET_CONFIG}" \
    bash "${BIN_DIR}/lib/publish-firm-inputs.sh" "${SLUG}" litigation "${LITIGATION_INPUTS_DIR}" --runner-checkout "${REPO_ROOT}" \
    || die "the litigation firm inputs for ${SLUG} were not published (publish-firm-inputs exit $?: 1 refused by this checkout's runner, 3 R2, 4 the validator could not run; the lines above say which)"
  log "R2 upload OK (litigation firm inputs)"
else
  assert_lane_not_on_main "operator/customers/${SLUG}/litigation/litigation-firm.yaml"
fi
# <<< litigation-inputs-stage
