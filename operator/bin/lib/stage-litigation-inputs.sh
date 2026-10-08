#!/usr/bin/env bash
# stage-litigation-inputs.sh: the litigation status job's firm-inputs staging
# block, sourced by provision-customer.sh in the same shell right after the
# drafting block (it reads SLUG, REPO_ROOT, the R2_* operator env,
# SS_ENGAGEMENTS_DIR, and calls die / log from there). Its own file for the
# reason the drafting one is: the provisioner sits at its shell size ratchet.
# shellcheck shell=bash

# ---------- Step 2f: the litigation status job's firm inputs (2026-10-07) ----------
# The litigation job (runners/medchron/medchron/litigation/) reads the firm's
# litigation-firm.yaml (models, the per-job and monthly spend caps, the
# court-paper and discovery patterns, process server names, settlement terms,
# form hints, the attorney roster, the case-status vocabulary), private, in
# engagements operator/customers/<slug>/litigation/. Beside it, once, a
# baseline/ seed (baseline.json + matters/*.json + manifest/*.json: a hand
# run's results in the job's state format) that entrypoint-litigation.sh copies
# into the seat's state dir only when the state holds none. Validated against
# THIS checkout's runner (python -m medchron.litigation.firm), refused unless
# litigation-firm.yaml is engagements origin/main, and SYNCED with --delete to
# vaults/<slug>/litigation/ (gone means gone). A seat with no litigation inputs
# is skipped quietly here; its litigation lane defers litigation jobs and its
# broker refuses every submit (no budget authored).
# >>> litigation-inputs-stage
LITIGATION_INPUTS_DIR="${SS_ENGAGEMENTS_DIR:-${HOME}/dev/engagements}/operator/customers/${SLUG}/litigation"
if [ -f "${LITIGATION_INPUTS_DIR}/litigation-firm.yaml" ]; then
  _eng="${LITIGATION_INPUTS_DIR%/operator/customers/*}"
  _rel="operator/customers/${SLUG}/litigation/litigation-firm.yaml"
  if [ "${SS_ALLOW_DIVERGENT_SOURCE:-}" != "1" ] \
     && [ "$(git -C "${_eng}" show "origin/main:${_rel}" 2>/dev/null)" != "$(cat "${LITIGATION_INPUTS_DIR}/litigation-firm.yaml")" ]; then
    die "the litigation firm inputs at ${LITIGATION_INPUTS_DIR} are not engagements origin/main; merge them first (an unmerged file is an unreviewed one)"
  fi
  [ -f "${REPO_ROOT}/operator/runners/medchron/medchron/litigation/firm.py" ] \
    || die "this checkout's runner has no litigation firm validator (medchron/litigation/firm.py); not uploading unvalidated litigation inputs"
  PYTHONPATH="${REPO_ROOT}/operator/runners/medchron" \
    uv run --quiet --with pyyaml python3 -m medchron.litigation.firm "${LITIGATION_INPUTS_DIR}" \
    || die "the litigation firm inputs for ${SLUG} do not validate against this checkout's runner (see the line above); not uploading them"
  log "Syncing the litigation firm inputs to R2: s3://${R2_BUCKET_CONFIG}/vaults/${SLUG}/litigation/"
  AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID}" AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY}" \
    aws s3 sync "${LITIGATION_INPUTS_DIR}/" "s3://${R2_BUCKET_CONFIG}/vaults/${SLUG}/litigation/" --delete \
      --exclude "eval/*" --exclude "*.md" \
      --endpoint-url "${R2_ENDPOINT_URL}" --only-show-errors \
    || die "R2 sync of the litigation firm inputs failed"
  log "R2 upload OK (litigation firm inputs)"
fi
# <<< litigation-inputs-stage
