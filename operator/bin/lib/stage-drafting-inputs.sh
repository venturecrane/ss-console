#!/usr/bin/env bash
# stage-drafting-inputs.sh: the drafting job's firm-inputs staging block,
# sourced by provision-customer.sh in the same shell right after the demand
# block (it reads SLUG, REPO_ROOT, the R2_* operator env, SS_ENGAGEMENTS_DIR,
# and calls die / log from there). Its own file for the reason the demand one
# is: the provisioner sits at its shell size ratchet.
# shellcheck shell=bash

# ---------- Step 2e: the drafting job's firm inputs (2026-10-07) ----------
# The drafting job (runners/jobs/jobs/drafting/) reads the firm's house
# style, per-class skeletons, prompts and exemplars, and the served attachments,
# all private, all in engagements operator/customers/<slug>/drafting/, and
# drafting-firm.yaml pins every one by sha256. Validated against THIS
# checkout's runner (python -m jobs.drafting.firm: schema AND pins),
# refused unless drafting-firm.yaml is engagements origin/main, and SYNCED with
# --delete to vaults/<slug>/drafting/ (gone means gone). The entrypoint pulls
# the prefix into a root-owned tree on every boot. A seat with no drafting
# inputs is skipped quietly here; its drafting lane defers drafting jobs.
# The R2 backfill of pinned client exemplars (Law 15: they live only in R2)
# runs inside the publisher, before its --delete sync.
# The write itself (validation, the key guard, the --delete sync, the read-back)
# is lib/publish-firm-inputs.sh, the one writer, which the engagements repo's
# merge workflow also runs (2026-11-05): provisioning and a merge publish the
# same bytes the same way. This block keeps only the engagements-main gate.
# >>> drafting-inputs-stage
DRAFTING_INPUTS_DIR="${SS_ENGAGEMENTS_DIR:-${HOME}/dev/engagements}/operator/customers/${SLUG}/drafting"
if [ -f "${DRAFTING_INPUTS_DIR}/drafting-firm.yaml" ]; then
  _eng="${DRAFTING_INPUTS_DIR%/operator/customers/*}"
  _rel="operator/customers/${SLUG}/drafting/drafting-firm.yaml"
  if [ "${SS_ALLOW_DIVERGENT_SOURCE:-}" != "1" ] \
     && [ "$(git -C "${_eng}" show "origin/main:${_rel}" 2>/dev/null)" != "$(cat "${DRAFTING_INPUTS_DIR}/drafting-firm.yaml")" ]; then
    die "the drafting firm inputs at ${DRAFTING_INPUTS_DIR} are not engagements origin/main; merge them first (an unmerged file is an unreviewed one)"
  fi
  R2_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID}" R2_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY}" \
  R2_ENDPOINT_URL="${R2_ENDPOINT_URL}" R2_BUCKET_CONFIG="${R2_BUCKET_CONFIG}" \
    bash "${BIN_DIR}/lib/publish-firm-inputs.sh" "${SLUG}" drafting "${DRAFTING_INPUTS_DIR}" --runner-checkout "${REPO_ROOT}" \
    || die "the drafting firm inputs for ${SLUG} were not published (publish-firm-inputs exit $?: 1 refused by this checkout's runner, 3 R2, 4 the validator could not run; the lines above say which)"
  log "R2 upload OK (drafting firm inputs)"
else
  assert_lane_not_on_main "operator/customers/${SLUG}/drafting/drafting-firm.yaml"
fi
# <<< drafting-inputs-stage
