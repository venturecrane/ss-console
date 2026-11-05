#!/usr/bin/env bash
# entrypoint-demand.sh: the demand lane's boot-time tree, SOURCED by
# entrypoint.sh (same shell, as root, before the broker and the jobs daemon
# launch). Its own file because entrypoint.sh sits at its shell size ratchet
# (tests/operator-module-size.test.ts says split, never raise). It reads
# JOBS_DATA_DIR, JOBS_RUN_DIR, CONFIG_DIR, CUSTOMER_SLUG, the R2_*
# env and _seed_endpoint from the entrypoint, and exports SMD_DEMAND_QUEUE_DIR
# (the broker's env -i line passes it on; the daemon inherits it) and
# JOBS_DEMAND_INPUTS (the daemon inherits it and hands it to each demand child).
# shellcheck shell=bash
# shellcheck disable=SC2154 # _seed_endpoint, CUSTOMER_SLUG and the R2_* names are set by entrypoint.sh, which sources this file

# The demand lane (2026-10-06): its own queue and job dirs beside the
# chronology's, same owners and modes for the same reasons. demand-queue/ is
# written by the broker (demand_job_submit, demand_job_resume) and read by the
# root daemon; demand-jobs/ is root, group jobs execute-only.
install -d -o root -g workspace-broker -m 0770 "${JOBS_DATA_DIR}/demand-queue"
install -d -o root -g jobs -m 0710 "${JOBS_DATA_DIR}/demand-jobs"
export SMD_DEMAND_QUEUE_DIR="${JOBS_RUN_DIR}/demand-queue"
# The demand firm inputs (voice, skeleton, prompts, house reference .docx,
# demand-firm.yaml pinning each by sha256), staged by provision-customer.sh
# Step 2d. Same fail-static shape as medchron-controls/: refreshed from the
# vault every boot, the existing copy kept on a failed fetch, a loud line and
# deferred demand jobs when there is none. Root-owned, group jobs
# read-only: the demand child reads its firm's voice and can never rewrite it.
export JOBS_DEMAND_INPUTS="${CONFIG_DIR}/demand"
# Versioned and validated before the swap (entrypoint-fetch-inputs.sh says how).
_lane_inputs_begin demand "${JOBS_DEMAND_INPUTS}"
if _fetch_lane_inputs demand "${JOBS_DEMAND_INPUTS}" demand-firm.yaml; then
  _adopt_lane_inputs demand "${JOBS_DEMAND_INPUTS}" "${JOBS_DEMAND_INPUTS}.r2.tmp" || true
elif [ -f "${JOBS_DEMAND_INPUTS}/demand-firm.yaml" ]; then
  log "WARN: R2 fetch of the demand firm inputs failed (${_FETCH_INPUTS_STATE}); keeping the existing root-owned copy"
elif [ "${_FETCH_INPUTS_STATE}" = absent ]; then
  log "No demand firm inputs in the vault for ${CUSTOMER_SLUG}; the demand lane will defer demand jobs"
else
  log "ERROR: the demand firm inputs could not be fetched from the vault for ${CUSTOMER_SLUG} and this seat has no copy; the demand lane will defer demand jobs until a restart fetches them"
fi
if [ -d "${JOBS_DEMAND_INPUTS}" ]; then
  _own_lane_inputs "${JOBS_DEMAND_INPUTS}/"
fi
