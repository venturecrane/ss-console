#!/usr/bin/env bash
# entrypoint-demand.sh: the demand lane's boot-time tree, SOURCED by
# entrypoint.sh (same shell, as root, before the broker and the medchron daemon
# launch). Its own file because entrypoint.sh sits at its shell size ratchet
# (tests/operator-module-size.test.ts says split, never raise). It reads
# MEDCHRON_DATA_DIR, MEDCHRON_RUN_DIR, CONFIG_DIR, CUSTOMER_SLUG, the R2_*
# env and _seed_endpoint from the entrypoint, and exports SMD_DEMAND_QUEUE_DIR
# (the broker's env -i line passes it on; the daemon inherits it) and
# MEDCHRON_DEMAND_INPUTS (the daemon inherits it and hands it to each demand child).
# shellcheck shell=bash
# shellcheck disable=SC2154 # _seed_endpoint, CUSTOMER_SLUG and the R2_* names are set by entrypoint.sh, which sources this file

# The demand lane (2026-10-06): its own queue and job dirs beside the
# chronology's, same owners and modes for the same reasons. demand-queue/ is
# written by the broker (demand_job_submit, demand_job_resume) and read by the
# root daemon; demand-jobs/ is root, group medchron execute-only.
install -d -o root -g workspace-broker -m 0770 "${MEDCHRON_DATA_DIR}/demand-queue"
install -d -o root -g medchron -m 0710 "${MEDCHRON_DATA_DIR}/demand-jobs"
export SMD_DEMAND_QUEUE_DIR="${MEDCHRON_RUN_DIR}/demand-queue"
# The demand firm inputs (voice, skeleton, prompts, house reference .docx,
# demand-firm.yaml pinning each by sha256), staged by provision-customer.sh
# Step 2d. Same fail-static shape as medchron-controls/: refreshed from the
# vault every boot, the existing copy kept on a failed fetch, a loud line and
# deferred demand jobs when there is none. Root-owned, group medchron
# read-only: the demand child reads its firm's voice and can never rewrite it.
export MEDCHRON_DEMAND_INPUTS="${CONFIG_DIR}/demand"
rm -rf "${MEDCHRON_DEMAND_INPUTS}.r2.tmp"
if AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID:?}" \
     AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY:?}" \
       aws s3 cp \
         --endpoint-url "${_seed_endpoint}" \
         --only-show-errors \
         --recursive \
         "s3://${R2_BUCKET_CONFIG}/vaults/${CUSTOMER_SLUG}/demand/" \
         "${MEDCHRON_DEMAND_INPUTS}.r2.tmp/" 2>/dev/null \
   && [ -f "${MEDCHRON_DEMAND_INPUTS}.r2.tmp/demand-firm.yaml" ]; then
  rm -rf "${MEDCHRON_DEMAND_INPUTS}"
  mv "${MEDCHRON_DEMAND_INPUTS}.r2.tmp" "${MEDCHRON_DEMAND_INPUTS}"
  log "demand firm inputs refreshed from R2 into ${MEDCHRON_DEMAND_INPUTS}"
elif [ -f "${MEDCHRON_DEMAND_INPUTS}/demand-firm.yaml" ]; then
  rm -rf "${MEDCHRON_DEMAND_INPUTS}.r2.tmp"
  log "WARN: R2 fetch of the demand firm inputs failed; keeping the existing root-owned copy"
else
  rm -rf "${MEDCHRON_DEMAND_INPUTS}.r2.tmp"
  log "No demand firm inputs in the vault for ${CUSTOMER_SLUG}; the demand lane will defer demand jobs"
fi
if [ -d "${MEDCHRON_DEMAND_INPUTS}" ]; then
  chown -R root:medchron "${MEDCHRON_DEMAND_INPUTS}"
  find "${MEDCHRON_DEMAND_INPUTS}" -type d -exec chmod 0750 {} +
  find "${MEDCHRON_DEMAND_INPUTS}" -type f -exec chmod 0640 {} +
fi
