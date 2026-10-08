#!/usr/bin/env bash
# entrypoint-litigation.sh: the litigation status lane's boot-time tree, SOURCED
# by entrypoint.sh right after entrypoint-drafting.sh (same shell, as root,
# before the broker and the medchron daemon launch). The drafting lane's file,
# its swaps made, plus two things that lane does not have: a persistent STATE
# dir (the last list's per-matter results and file manifests, so a run re-reads
# only the files that changed) and a broker-readable copy of the firm config
# (the broker enforces the monthly budget at submit and resolves attorney
# names, and its uid cannot open the root:medchron inputs). It reads
# MEDCHRON_DATA_DIR, MEDCHRON_RUN_DIR, CONFIG_DIR, CUSTOMER_SLUG, the R2_* env
# and _seed_endpoint from the entrypoint, and exports SMD_LITIGATION_QUEUE_DIR
# and SMD_LITIGATION_FIRM_CONFIG (the broker's env -i line passes both on),
# MEDCHRON_LITIGATION_INPUTS and MEDCHRON_LITIGATION_STATE_DIR (the daemon
# inherits both and hands them to each litigation child).
# shellcheck shell=bash
# shellcheck disable=SC2154 # _seed_endpoint, CUSTOMER_SLUG and the R2_* names are set by entrypoint.sh, which sources this file

# The queue and job dirs, same owners and modes as the drafting lane's, for the
# same reasons: litigation-queue/ is written by the broker
# (litigation_job_submit, litigation_job_resume) and read by the root daemon;
# litigation-jobs/ is root, group medchron execute-only.
install -d -o root -g workspace-broker -m 0770 "${MEDCHRON_DATA_DIR}/litigation-queue"
install -d -o root -g medchron -m 0710 "${MEDCHRON_DATA_DIR}/litigation-jobs"
export SMD_LITIGATION_QUEUE_DIR="${MEDCHRON_RUN_DIR}/litigation-queue"

# The state the job carries between runs, on the volume. The child (uid
# medchron) owns and writes it; nothing else needs it. It holds client facts
# (each matter's last results), so it is 0700 and never leaves the volume.
install -d -o root -g medchron -m 0750 "${MEDCHRON_DATA_DIR}/litigation"
install -d -o medchron -g medchron -m 0700 "${MEDCHRON_DATA_DIR}/litigation/state"
export MEDCHRON_LITIGATION_STATE_DIR="${MEDCHRON_RUN_DIR}/litigation/state"

# The firm inputs (litigation-firm.yaml, and once the seed baseline/), staged by
# provision-customer.sh through lib/stage-litigation-inputs.sh. Fail-static
# like the drafting inputs: refreshed from the vault every boot, the existing
# copy kept on a failed fetch, deferred litigation jobs when there is none.
export MEDCHRON_LITIGATION_INPUTS="${CONFIG_DIR}/litigation"
rm -rf "${MEDCHRON_LITIGATION_INPUTS}.r2.tmp"
if AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID:?}" \
     AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY:?}" \
       aws s3 cp \
         --endpoint-url "${_seed_endpoint}" \
         --only-show-errors \
         --recursive \
         "s3://${R2_BUCKET_CONFIG}/vaults/${CUSTOMER_SLUG}/litigation/" \
         "${MEDCHRON_LITIGATION_INPUTS}.r2.tmp/" 2>/dev/null \
   && [ -f "${MEDCHRON_LITIGATION_INPUTS}.r2.tmp/litigation-firm.yaml" ]; then
  rm -rf "${MEDCHRON_LITIGATION_INPUTS}"
  mv "${MEDCHRON_LITIGATION_INPUTS}.r2.tmp" "${MEDCHRON_LITIGATION_INPUTS}"
  log "litigation firm inputs refreshed from R2 into ${MEDCHRON_LITIGATION_INPUTS}"
elif [ -f "${MEDCHRON_LITIGATION_INPUTS}/litigation-firm.yaml" ]; then
  rm -rf "${MEDCHRON_LITIGATION_INPUTS}.r2.tmp"
  log "WARN: R2 fetch of the litigation firm inputs failed; keeping the existing root-owned copy"
else
  rm -rf "${MEDCHRON_LITIGATION_INPUTS}.r2.tmp"
  log "No litigation firm inputs in the vault for ${CUSTOMER_SLUG}; the litigation lane will defer litigation jobs"
fi
if [ -d "${MEDCHRON_LITIGATION_INPUTS}" ]; then
  chown -R root:medchron "${MEDCHRON_LITIGATION_INPUTS}"
  find "${MEDCHRON_LITIGATION_INPUTS}" -type d -exec chmod 0750 {} +
  find "${MEDCHRON_LITIGATION_INPUTS}" -type f -exec chmod 0640 {} +
fi

# The broker's copy of the firm config: the same bytes, group workspace-broker,
# read-only. Absent inputs remove it, so a broker never meters against a budget
# the runner no longer has (the submit then refuses: no budget authored).
export SMD_LITIGATION_FIRM_CONFIG="${CONFIG_DIR}/litigation-broker-firm.yaml"
if [ -f "${MEDCHRON_LITIGATION_INPUTS}/litigation-firm.yaml" ]; then
  install -o root -g workspace-broker -m 0640 \
    "${MEDCHRON_LITIGATION_INPUTS}/litigation-firm.yaml" "${SMD_LITIGATION_FIRM_CONFIG}"
else
  rm -f "${SMD_LITIGATION_FIRM_CONFIG}"
fi

# The seed baseline (the hand run's results in the state format), copied into
# the state dir ONCE: only when the vault staged one and the state holds no
# baseline yet. Never over the runner's own state, which is newer by definition.
_lit_seed="${MEDCHRON_LITIGATION_INPUTS}/baseline"
_lit_state="${MEDCHRON_DATA_DIR}/litigation/state"
if [ -f "${_lit_seed}/baseline.json" ] && [ ! -e "${_lit_state}/baseline.json" ]; then
  cp -R "${_lit_seed}/." "${_lit_state}/"
  chown -R medchron:medchron "${_lit_state}"
  find "${_lit_state}" -type d -exec chmod 0700 {} +
  find "${_lit_state}" -type f -exec chmod 0600 {} +
  log "litigation state seeded from the staged baseline"
fi
unset _lit_seed _lit_state
