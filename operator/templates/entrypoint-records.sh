#!/usr/bin/env bash
# entrypoint-records.sh: the matter records lane's boot-time tree (ADR 0102
# amendment 4), SOURCED by entrypoint.sh right after entrypoint-casefile.sh
# (same shell, as root, before the broker and the jobs daemon launch). The
# case file lane's file, its swaps made: a queue the broker writes and the root
# daemon reads, a root-only job dir the child can traverse, the lane's state
# dir (the parse cache), the page index the child reads and writes, the firm
# inputs (records-firm.yaml), and a broker-readable copy of them (the broker
# meters the month's budget at submit and its uid cannot open the root:jobs
# inputs). It reads JOBS_DATA_DIR, JOBS_RUN_DIR, CONFIG_DIR,
# CUSTOMER_SLUG, the R2_* env and _seed_endpoint from the entrypoint, and
# exports SMD_RECORDS_QUEUE_DIR and SMD_RECORDS_FIRM_CONFIG (the broker's env -i
# line passes both on), JOBS_RECORDS_INPUTS_DIR, JOBS_RECORDS_STATE_DIR
# and SMOKEBALL_PAGE_INDEX_DIR (the daemon inherits them and hands them to each
# records child).
# shellcheck shell=bash
# shellcheck disable=SC2154 # _seed_endpoint, CUSTOMER_SLUG and the R2_* names are set by entrypoint.sh, which sources this file
# The seat's folder layout (operator/contracts/seat-layout.yaml, generated beside
# this file): the SMD_LAYOUT_* paths below. Sourced here as well as by
# entrypoint.sh, so this file reads it on every path that reaches it.
# shellcheck source=/dev/null
. "$(dirname "${BASH_SOURCE[0]}")/_seat-layout.generated.sh"

# Only a seat that authors the lane builds its tree (ADR 0100). The gate is
# here, not in entrypoint.sh's loop line, which the ratchet holds.
lane_on records || return 0

# The queue and job dirs, the case file lane's owners and modes: records-queue/
# is written by the broker (records_job_submit, records_job_resume) and read by
# the root daemon; records-jobs/ is root, group jobs execute-only.
install -d -o root -g workspace-broker -m 0770 "${JOBS_DATA_DIR}/records-queue"
install -d -o root -g jobs -m 0710 "${JOBS_DATA_DIR}/records-jobs"
export SMD_RECORDS_QUEUE_DIR="${JOBS_RUN_DIR}/records-queue"

# The lane's state (the parse cache: each request's records, in its own words),
# owned and written by the child. Client facts: 0700, never off the volume.
install -d -o jobs -g jobs -m 0700 "${JOBS_DATA_DIR}/records-state"
export JOBS_RECORDS_STATE_DIR="${JOBS_RUN_DIR}/records-state"

# The page finder's index (what each page of a matter PDF says, per file
# sha256): the child's alone. Purge a matter with
# `python -m smokeball_connector.page_finder purge --matter <id>` under
# SMOKEBALL_PAGE_INDEX_DIR. ONE-TIME MOVE: the in-turn page finder (removed
# from every seat, ADR 0102 amendment 4) kept its index at
# /opt/data/smokeball-page-index, hermes-only. Each index there is moved here,
# never overwriting one already here (mv -n), and chowned to the child, so no
# page already read is paid for again. Idempotent: an empty or absent old dir
# moves nothing. Logged with counts only.
_RECORDS_INDEX_DIR="${JOBS_DATA_DIR}/page-index"
install -d -o jobs -g jobs -m 0700 "${_RECORDS_INDEX_DIR}"
_OLD_INDEX_DIR="/opt/data/smokeball-page-index"
if [ -d "${_OLD_INDEX_DIR}" ]; then
  _moved=0 _kept=0
  for _f in "${_OLD_INDEX_DIR}"/*.json; do
    [ -f "${_f}" ] || continue
    if [ -e "${_RECORDS_INDEX_DIR}/$(basename "${_f}")" ]; then
      _kept=$((_kept + 1))
    else
      mv -n "${_f}" "${_RECORDS_INDEX_DIR}/" && _moved=$((_moved + 1))
    fi
  done
  if [ "${_moved}" != 0 ] || [ "${_kept}" != 0 ]; then
    log "page index: moved ${_moved} index file(s) to ${_RECORDS_INDEX_DIR}; ${_kept} already there were kept"
  fi
  unset _f _moved _kept
fi
chown -R jobs:jobs "${_RECORDS_INDEX_DIR}"
find "${_RECORDS_INDEX_DIR}" -type f -name '*.json' -exec chmod 0600 {} +
export SMOKEBALL_PAGE_INDEX_DIR="${JOBS_RUN_DIR}/page-index"
unset _RECORDS_INDEX_DIR _OLD_INDEX_DIR

# The firm inputs (records-firm.yaml), staged in the vault under
# vaults/<slug>/records/. Fail-static like the case file inputs: refreshed every
# boot, the existing copy kept on a failed fetch, deferred jobs when there is none.
export JOBS_RECORDS_INPUTS_DIR="${CONFIG_DIR}/records"
# Versioned and validated before the swap (entrypoint-fetch-inputs.sh says how).
_lane_inputs_begin records "${JOBS_RECORDS_INPUTS_DIR}"
if _fetch_lane_inputs records "${JOBS_RECORDS_INPUTS_DIR}" records-firm.yaml; then
  _adopt_lane_inputs records "${JOBS_RECORDS_INPUTS_DIR}" "${JOBS_RECORDS_INPUTS_DIR}.r2.tmp" || true
elif [ -f "${JOBS_RECORDS_INPUTS_DIR}/records-firm.yaml" ]; then
  log "WARN: R2 fetch of the records firm inputs failed (${_FETCH_INPUTS_STATE}); keeping the existing root-owned copy"
elif [ "${_FETCH_INPUTS_STATE}" = absent ]; then
  log "No records firm inputs in the vault for ${CUSTOMER_SLUG}; the records lane will defer records jobs"
else
  log "ERROR: the records firm inputs could not be fetched from the vault for ${CUSTOMER_SLUG} and this seat has no copy; the records lane will defer records jobs until a restart fetches them"
fi
if [ -d "${JOBS_RECORDS_INPUTS_DIR}" ]; then
  _own_lane_inputs "${JOBS_RECORDS_INPUTS_DIR}/"
  # The runner's own validator: a copy that does not pass is said here at
  # boot. Exit 1 is a problem with the inputs; anything else is a validator
  # that could not run.
  _rf_out="$(${SMD_LAYOUT_JOBS_PYTHON} -m jobs.records.firm "${JOBS_RECORDS_INPUTS_DIR}" 2>&1)" && _rf_rc=0 || _rf_rc=$?
  case "${_rf_rc}" in
    0) log "records firm inputs validate" ;;
    1) log "ERROR: the records firm inputs do not validate; records jobs will fail at config_missing: $(printf '%s' "${_rf_out}" | head -n 3 | tr '\n' ' ' | cut -c1-300)" ;;
    *) log "WARN: the records firm validator could not run (exit ${_rf_rc}); the inputs are unchecked at boot" ;;
  esac
  unset _rf_out _rf_rc
fi

# The broker's copy of the firm config: the same bytes, group workspace-broker,
# read-only. Absent inputs remove it, so the broker never meters against a
# budget the runner no longer has (the submit then refuses: no budget authored).
export SMD_RECORDS_FIRM_CONFIG="${CONFIG_DIR}/records-broker-firm.yaml"
if [ -f "${JOBS_RECORDS_INPUTS_DIR}/records-firm.yaml" ]; then
  install -o root -g workspace-broker -m 0640 \
    "${JOBS_RECORDS_INPUTS_DIR}/records-firm.yaml" "${SMD_RECORDS_FIRM_CONFIG}"
else
  rm -f "${SMD_RECORDS_FIRM_CONFIG}"
fi
