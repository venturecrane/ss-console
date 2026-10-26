#!/usr/bin/env bash
# entrypoint-casefile.sh: the case file manager lane's boot-time tree, SOURCED
# by entrypoint.sh right after entrypoint-litigation.sh (same shell, as root,
# before the broker and the medchron daemon launch). The litigation lane's
# file, its swaps made: a queue the broker writes and the root daemon reads, a
# root-only job dir the child can traverse, a persistent STATE dir (what each
# matter's last File Status said, so a recheck reads only what changed), the
# firm inputs (casefile-firm.yaml plus its prompts/), and a broker-readable copy
# of the firm config (the broker meters the budget at submit and its uid cannot
# open the root:medchron inputs). The text cache is the litigation lane's
# (MEDCHRON_LITIGATION_TEXTCACHE_DIR, exported by entrypoint-litigation.sh): a
# page either lane paid to read is never bought again by the other. It reads
# MEDCHRON_DATA_DIR, MEDCHRON_RUN_DIR, CONFIG_DIR, CUSTOMER_SLUG, the R2_* env
# and _seed_endpoint from the entrypoint, and exports SMD_CASEFILE_QUEUE_DIR
# and SMD_CASEFILE_FIRM_CONFIG (the broker's env -i line passes both on),
# MEDCHRON_CASEFILE_INPUTS_DIR and MEDCHRON_CASEFILE_STATE_DIR (the daemon
# inherits them and hands them to each casefile child).
# shellcheck shell=bash
# shellcheck disable=SC2154 # _seed_endpoint, CUSTOMER_SLUG and the R2_* names are set by entrypoint.sh, which sources this file

# The queue and job dirs, same owners and modes as the litigation lane's:
# casefile-queue/ is written by the broker (casefile_job_submit,
# casefile_apply_submit, casefile_job_resume) and read by the root daemon;
# casefile-jobs/ is root, group medchron execute-only.
install -d -o root -g workspace-broker -m 0770 "${MEDCHRON_DATA_DIR}/casefile-queue"
install -d -o root -g medchron -m 0710 "${MEDCHRON_DATA_DIR}/casefile-jobs"
export SMD_CASEFILE_QUEUE_DIR="${MEDCHRON_RUN_DIR}/casefile-queue"

# The state the job carries between runs, on the volume. The child (uid
# medchron) owns and writes it; nothing else needs it. It holds client facts
# (each matter's last File Status), so it is 0700 and never leaves the volume.
install -d -o root -g medchron -m 0750 "${MEDCHRON_DATA_DIR}/casefile"
install -d -o medchron -g medchron -m 0700 "${MEDCHRON_DATA_DIR}/casefile/state"
export MEDCHRON_CASEFILE_STATE_DIR="${MEDCHRON_RUN_DIR}/casefile/state"

# The firm inputs (casefile-firm.yaml and prompts/contract.md, reader.md,
# auditor.md), staged by provision-customer.sh through
# lib/stage-casefile-inputs.sh. Fail-static like the litigation inputs:
# refreshed from the vault every boot, the existing copy kept on a failed
# fetch, deferred casefile jobs when there is none.
export MEDCHRON_CASEFILE_INPUTS_DIR="${CONFIG_DIR}/casefile"
if _fetch_lane_inputs casefile "${MEDCHRON_CASEFILE_INPUTS_DIR}" casefile-firm.yaml; then
  rm -rf "${MEDCHRON_CASEFILE_INPUTS_DIR}"
  mv "${MEDCHRON_CASEFILE_INPUTS_DIR}.r2.tmp" "${MEDCHRON_CASEFILE_INPUTS_DIR}"
  log "casefile firm inputs refreshed from R2 into ${MEDCHRON_CASEFILE_INPUTS_DIR}"
elif [ -f "${MEDCHRON_CASEFILE_INPUTS_DIR}/casefile-firm.yaml" ]; then
  log "WARN: R2 fetch of the casefile firm inputs failed (${_FETCH_INPUTS_STATE}); keeping the existing root-owned copy"
elif [ "${_FETCH_INPUTS_STATE}" = absent ]; then
  log "No casefile firm inputs in the vault for ${CUSTOMER_SLUG}; the casefile lane will defer casefile jobs"
else
  log "ERROR: the casefile firm inputs could not be fetched from the vault for ${CUSTOMER_SLUG} and this seat has no copy; the casefile lane will defer casefile jobs until a restart fetches them"
fi
if [ -d "${MEDCHRON_CASEFILE_INPUTS_DIR}" ]; then
  chown -R root:medchron "${MEDCHRON_CASEFILE_INPUTS_DIR}"
  find "${MEDCHRON_CASEFILE_INPUTS_DIR}" -type d -exec chmod 0750 {} +
  find "${MEDCHRON_CASEFILE_INPUTS_DIR}" -type f -exec chmod 0640 {} +
  # The runner's own validator, the one staging ran: a copy that does not pass
  # it is said here at boot, not first discovered by a job at config_missing.
  # Exit 1 is a problem with the inputs (logged, the copy kept: the lane holds
  # each job and says why); anything else is a validator that could not run.
  _cf_out="$(/opt/medchron/.venv/bin/python -m medchron.casefile.firm "${MEDCHRON_CASEFILE_INPUTS_DIR}" 2>&1)" && _cf_rc=0 || _cf_rc=$?
  case "${_cf_rc}" in
    0) log "casefile firm inputs validate" ;;
    1) log "ERROR: the casefile firm inputs do not validate; casefile jobs will hold at config_missing: $(printf '%s' "${_cf_out}" | head -n 3 | tr '\n' ' ' | cut -c1-300)" ;;
    *) log "WARN: the casefile firm validator could not run (exit ${_cf_rc}); the inputs are unchecked at boot" ;;
  esac
  unset _cf_out _cf_rc
fi

# The broker's copy of the firm config: the same bytes, group workspace-broker,
# read-only. Absent inputs remove it, so a broker never meters against a budget
# the runner no longer has (the submit then refuses: no budget authored).
export SMD_CASEFILE_FIRM_CONFIG="${CONFIG_DIR}/casefile-broker-firm.yaml"
if [ -f "${MEDCHRON_CASEFILE_INPUTS_DIR}/casefile-firm.yaml" ]; then
  install -o root -g workspace-broker -m 0640 \
    "${MEDCHRON_CASEFILE_INPUTS_DIR}/casefile-firm.yaml" "${SMD_CASEFILE_FIRM_CONFIG}"
else
  rm -f "${SMD_CASEFILE_FIRM_CONFIG}"
fi
