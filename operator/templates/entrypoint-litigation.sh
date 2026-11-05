#!/usr/bin/env bash
# entrypoint-litigation.sh: the litigation status lane's boot-time tree, SOURCED
# by entrypoint.sh right after entrypoint-drafting.sh (same shell, as root,
# before the broker and the jobs daemon launch). The drafting lane's file,
# its swaps made, plus two things that lane does not have: a persistent STATE
# dir (the last list's per-matter results and file manifests, so a run re-reads
# only the files that changed) and a broker-readable copy of the firm config
# (the broker enforces the monthly budget at submit and resolves attorney
# names, and its uid cannot open the root:jobs inputs). It reads
# JOBS_DATA_DIR, JOBS_RUN_DIR, CONFIG_DIR, CUSTOMER_SLUG, the R2_* env
# and _seed_endpoint from the entrypoint, and exports SMD_LITIGATION_QUEUE_DIR
# and SMD_LITIGATION_FIRM_CONFIG (the broker's env -i line passes both on),
# JOBS_LITIGATION_INPUTS, JOBS_LITIGATION_STATE_DIR and
# JOBS_LITIGATION_TEXTCACHE_DIR (the daemon
# inherits them and hands them to each litigation child).
# shellcheck shell=bash
# shellcheck disable=SC2154 # _seed_endpoint, CUSTOMER_SLUG and the R2_* names are set by entrypoint.sh, which sources this file

# The queue and job dirs, same owners and modes as the drafting lane's, for the
# same reasons: litigation-queue/ is written by the broker
# (litigation_job_submit, litigation_job_resume) and read by the root daemon;
# litigation-jobs/ is root, group jobs execute-only.
install -d -o root -g workspace-broker -m 0770 "${JOBS_DATA_DIR}/litigation-queue"
install -d -o root -g jobs -m 0710 "${JOBS_DATA_DIR}/litigation-jobs"
export SMD_LITIGATION_QUEUE_DIR="${JOBS_RUN_DIR}/litigation-queue"

# The state the job carries between runs, on the volume. The child (uid
# jobs) owns and writes it; nothing else needs it. It holds client facts
# (each matter's last results), so it is 0700 and never leaves the volume.
install -d -o root -g jobs -m 0750 "${JOBS_DATA_DIR}/litigation"
install -d -o jobs -g jobs -m 0700 "${JOBS_DATA_DIR}/litigation/state"
export JOBS_LITIGATION_STATE_DIR="${JOBS_RUN_DIR}/litigation/state"

# The text cache (2026-10-24, jobs/litigation/textcache.py): every page the
# lane paid to read, kept so a later list does not buy it again. Transcribed
# matter text, so child-only 0700 like the state; the parent above is
# root:jobs 0750, so the child could not make it itself (its writes would
# stop on the first one). Survives reprovision by design; a matter leaves it
# with `python -m jobs.litigation.textcache purge --matter <id>`.
install -d -o jobs -g jobs -m 0700 "${JOBS_DATA_DIR}/litigation/textcache"
export JOBS_LITIGATION_TEXTCACHE_DIR="${JOBS_RUN_DIR}/litigation/textcache"

# The firm inputs (litigation-firm.yaml, and once the seed baseline/), staged by
# provision-customer.sh through lib/stage-litigation-inputs.sh. Fail-static
# like the drafting inputs: refreshed from the vault every boot, the existing
# copy kept on a failed fetch, deferred litigation jobs when there is none.
export JOBS_LITIGATION_INPUTS="${CONFIG_DIR}/litigation"
# Versioned and validated before the swap (entrypoint-fetch-inputs.sh says how).
_lane_inputs_begin litigation "${JOBS_LITIGATION_INPUTS}"
if _fetch_lane_inputs litigation "${JOBS_LITIGATION_INPUTS}" litigation-firm.yaml; then
  _adopt_lane_inputs litigation "${JOBS_LITIGATION_INPUTS}" "${JOBS_LITIGATION_INPUTS}.r2.tmp" || true
elif [ -f "${JOBS_LITIGATION_INPUTS}/litigation-firm.yaml" ]; then
  log "WARN: R2 fetch of the litigation firm inputs failed (${_FETCH_INPUTS_STATE}); keeping the existing root-owned copy"
elif [ "${_FETCH_INPUTS_STATE}" = absent ]; then
  log "No litigation firm inputs in the vault for ${CUSTOMER_SLUG}; the litigation lane will defer litigation jobs"
else
  log "ERROR: the litigation firm inputs could not be fetched from the vault for ${CUSTOMER_SLUG} and this seat has no copy; the litigation lane will defer litigation jobs until a restart fetches them"
fi
if [ -d "${JOBS_LITIGATION_INPUTS}" ]; then
  _own_lane_inputs "${JOBS_LITIGATION_INPUTS}/"
fi

# The broker's copy of the firm config: the same bytes, group workspace-broker,
# read-only. Absent inputs remove it, so a broker never meters against a budget
# the runner no longer has (the submit then refuses: no budget authored).
export SMD_LITIGATION_FIRM_CONFIG="${CONFIG_DIR}/litigation-broker-firm.yaml"
if [ -f "${JOBS_LITIGATION_INPUTS}/litigation-firm.yaml" ]; then
  install -o root -g workspace-broker -m 0640 \
    "${JOBS_LITIGATION_INPUTS}/litigation-firm.yaml" "${SMD_LITIGATION_FIRM_CONFIG}"
else
  rm -f "${SMD_LITIGATION_FIRM_CONFIG}"
fi

# The seed baseline (the hand run's results in the state format), copied into
# the state dir ONCE: only when the vault staged one and the state holds no
# baseline yet. Never over the runner's own state, which is newer by definition.
_lit_seed="${JOBS_LITIGATION_INPUTS}/baseline"
_lit_state="${JOBS_DATA_DIR}/litigation/state"
if [ -f "${_lit_seed}/baseline.json" ] && [ ! -e "${_lit_state}/baseline.json" ]; then
  cp -R "${_lit_seed}/." "${_lit_state}/"
  chown -R jobs:jobs "${_lit_state}"
  find "${_lit_state}" -type d -exec chmod 0700 {} +
  find "${_lit_state}" -type f -exec chmod 0600 {} +
  log "litigation state seeded from the staged baseline"
fi
unset _lit_seed _lit_state
