#!/usr/bin/env bash
# entrypoint-drafting.sh: the drafting lane's boot-time tree, SOURCED by
# entrypoint.sh right after entrypoint-demand.sh (same shell, as root, before
# the broker and the medchron daemon launch). The demand lane's file, its swaps
# made: it reads MEDCHRON_DATA_DIR, MEDCHRON_RUN_DIR, CONFIG_DIR, CUSTOMER_SLUG,
# the R2_* env and _seed_endpoint from the entrypoint, and exports
# SMD_DRAFTING_QUEUE_DIR (the broker's env -i line passes it on; the daemon
# inherits it) and MEDCHRON_DRAFTING_INPUTS (the daemon inherits it and hands it
# to each drafting child).
# shellcheck shell=bash
# shellcheck disable=SC2154 # _seed_endpoint, CUSTOMER_SLUG and the R2_* names are set by entrypoint.sh, which sources this file

# The drafting lane (2026-10-07): its own queue and job dirs beside the demand
# lane's, same owners and modes for the same reasons. drafting-queue/ is
# written by the broker (drafting_job_submit, drafting_job_resume) and read by
# the root daemon; drafting-jobs/ is root, group medchron execute-only.
install -d -o root -g workspace-broker -m 0770 "${MEDCHRON_DATA_DIR}/drafting-queue"
install -d -o root -g medchron -m 0710 "${MEDCHRON_DATA_DIR}/drafting-jobs"
export SMD_DRAFTING_QUEUE_DIR="${MEDCHRON_RUN_DIR}/drafting-queue"
# The drafting firm inputs (house style, per-class skeletons, prompts and
# exemplars, the served attachments, drafting-firm.yaml pinning each by
# sha256), staged by provision-customer.sh Step 2e. Fail-static like the demand
# inputs: refreshed from the vault every boot, the existing copy kept on a
# failed fetch, deferred drafting jobs when there is none. Root-owned, group
# medchron read-only.
export MEDCHRON_DRAFTING_INPUTS="${CONFIG_DIR}/drafting"
rm -rf "${MEDCHRON_DRAFTING_INPUTS}.r2.tmp"
if AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID:?}" \
     AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY:?}" \
       aws s3 cp \
         --endpoint-url "${_seed_endpoint}" \
         --only-show-errors \
         --recursive \
         "s3://${R2_BUCKET_CONFIG}/vaults/${CUSTOMER_SLUG}/drafting/" \
         "${MEDCHRON_DRAFTING_INPUTS}.r2.tmp/" 2>/dev/null \
   && [ -f "${MEDCHRON_DRAFTING_INPUTS}.r2.tmp/drafting-firm.yaml" ]; then
  rm -rf "${MEDCHRON_DRAFTING_INPUTS}"
  mv "${MEDCHRON_DRAFTING_INPUTS}.r2.tmp" "${MEDCHRON_DRAFTING_INPUTS}"
  log "drafting firm inputs refreshed from R2 into ${MEDCHRON_DRAFTING_INPUTS}"
elif [ -f "${MEDCHRON_DRAFTING_INPUTS}/drafting-firm.yaml" ]; then
  rm -rf "${MEDCHRON_DRAFTING_INPUTS}.r2.tmp"
  log "WARN: R2 fetch of the drafting firm inputs failed; keeping the existing root-owned copy"
else
  rm -rf "${MEDCHRON_DRAFTING_INPUTS}.r2.tmp"
  log "No drafting firm inputs in the vault for ${CUSTOMER_SLUG}; the drafting lane will defer drafting jobs"
fi
if [ -d "${MEDCHRON_DRAFTING_INPUTS}" ]; then
  chown -R root:medchron "${MEDCHRON_DRAFTING_INPUTS}"
  find "${MEDCHRON_DRAFTING_INPUTS}" -type d -exec chmod 0750 {} +
  find "${MEDCHRON_DRAFTING_INPUTS}" -type f -exec chmod 0640 {} +
fi
