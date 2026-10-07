#!/usr/bin/env bash
# stage-drafting-inputs.sh: the drafting job's firm-inputs staging block,
# sourced by provision-customer.sh in the same shell right after the demand
# block (it reads SLUG, REPO_ROOT, the R2_* operator env, SS_ENGAGEMENTS_DIR,
# and calls die / log from there). Its own file for the reason the demand one
# is: the provisioner sits at its shell size ratchet.
# shellcheck shell=bash

# ---------- Step 2e: the drafting job's firm inputs (2026-10-07) ----------
# The drafting job (runners/medchron/medchron/drafting/) reads the firm's house
# style, per-class skeletons, prompts and exemplars, and the served attachments,
# all private, all in engagements operator/customers/<slug>/drafting/, and
# drafting-firm.yaml pins every one by sha256. Validated against THIS
# checkout's runner (python -m medchron.drafting.firm: schema AND pins),
# refused unless drafting-firm.yaml is engagements origin/main, and SYNCED with
# --delete to vaults/<slug>/drafting/ (gone means gone). The entrypoint pulls
# the prefix into a root-owned tree on every boot. A seat with no drafting
# inputs is skipped quietly here; its drafting lane defers drafting jobs.
# >>> drafting-inputs-stage
DRAFTING_INPUTS_DIR="${SS_ENGAGEMENTS_DIR:-${HOME}/dev/engagements}/operator/customers/${SLUG}/drafting"
if [ -f "${DRAFTING_INPUTS_DIR}/drafting-firm.yaml" ]; then
  _eng="${DRAFTING_INPUTS_DIR%/operator/customers/*}"
  _rel="operator/customers/${SLUG}/drafting/drafting-firm.yaml"
  if [ "${SS_ALLOW_DIVERGENT_SOURCE:-}" != "1" ] \
     && [ "$(git -C "${_eng}" show "origin/main:${_rel}" 2>/dev/null)" != "$(cat "${DRAFTING_INPUTS_DIR}/drafting-firm.yaml")" ]; then
    die "the drafting firm inputs at ${DRAFTING_INPUTS_DIR} are not engagements origin/main; merge them first (an unmerged file is an unreviewed one)"
  fi
  PYTHONPATH="${REPO_ROOT}/operator/runners/medchron" \
    uv run --quiet --with pyyaml python3 -m medchron.drafting.firm "${DRAFTING_INPUTS_DIR}" \
    || die "the drafting firm inputs for ${SLUG} do not validate against this checkout's runner (see the line above); not uploading them"
  log "Syncing the drafting firm inputs to R2: s3://${R2_BUCKET_CONFIG}/vaults/${SLUG}/drafting/"
  AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID}" AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY}" \
    aws s3 sync "${DRAFTING_INPUTS_DIR}/" "s3://${R2_BUCKET_CONFIG}/vaults/${SLUG}/drafting/" --delete \
      --exclude "eval/*" --exclude "request-brief-*" \
      --endpoint-url "${R2_ENDPOINT_URL}" --only-show-errors \
    || die "R2 sync of the drafting firm inputs failed"
  log "R2 upload OK (drafting firm inputs)"
fi
# <<< drafting-inputs-stage
