#!/usr/bin/env bash
# entrypoint-drafting.sh: the drafting lane's boot-time tree, SOURCED by
# entrypoint.sh right after entrypoint-demand.sh (same shell, as root, before
# the broker and the jobs daemon launch). The demand lane's file, its swaps
# made: it reads JOBS_DATA_DIR, JOBS_RUN_DIR, CONFIG_DIR, CUSTOMER_SLUG,
# the R2_* env and _seed_endpoint from the entrypoint, and exports
# SMD_DRAFTING_QUEUE_DIR (the broker's env -i line passes it on; the daemon
# inherits it) and JOBS_DRAFTING_INPUTS (the daemon inherits it and hands it
# to each drafting child).
# shellcheck shell=bash
# shellcheck disable=SC2154 # _seed_endpoint, CUSTOMER_SLUG and the R2_* names are set by entrypoint.sh, which sources this file

# The drafting lane (2026-10-07): its own queue and job dirs beside the demand
# lane's, same owners and modes for the same reasons. drafting-queue/ is
# written by the broker (drafting_job_submit, drafting_job_resume) and read by
# the root daemon; drafting-jobs/ is root, group jobs execute-only.
install -d -o root -g workspace-broker -m 0770 "${JOBS_DATA_DIR}/drafting-queue"
install -d -o root -g jobs -m 0710 "${JOBS_DATA_DIR}/drafting-jobs"
export SMD_DRAFTING_QUEUE_DIR="${JOBS_RUN_DIR}/drafting-queue"
# The drafting firm inputs (house style, per-class skeletons, prompts and
# exemplars, the served attachments, drafting-firm.yaml pinning each by
# sha256), staged by provision-customer.sh Step 2e. Fail-static like the demand
# inputs: refreshed from the vault every boot, the existing copy kept on a
# failed fetch, deferred drafting jobs when there is none. Root-owned, group
# jobs read-only.
export JOBS_DRAFTING_INPUTS="${CONFIG_DIR}/drafting"
# Versioned and validated before the swap (entrypoint-fetch-inputs.sh says how).
_lane_inputs_begin drafting "${JOBS_DRAFTING_INPUTS}"
if _fetch_lane_inputs drafting "${JOBS_DRAFTING_INPUTS}" drafting-firm.yaml; then
  _adopt_lane_inputs drafting "${JOBS_DRAFTING_INPUTS}" "${JOBS_DRAFTING_INPUTS}.r2.tmp" || true
elif [ -f "${JOBS_DRAFTING_INPUTS}/drafting-firm.yaml" ]; then
  log "WARN: R2 fetch of the drafting firm inputs failed (${_FETCH_INPUTS_STATE}); keeping the existing root-owned copy"
elif [ "${_FETCH_INPUTS_STATE}" = absent ]; then
  log "No drafting firm inputs in the vault for ${CUSTOMER_SLUG}; the drafting lane will defer drafting jobs"
else
  log "ERROR: the drafting firm inputs could not be fetched from the vault for ${CUSTOMER_SLUG} and this seat has no copy; the drafting lane will defer drafting jobs until a restart fetches them"
fi
if [ -d "${JOBS_DRAFTING_INPUTS}" ]; then
  _own_lane_inputs "${JOBS_DRAFTING_INPUTS}/"
fi
