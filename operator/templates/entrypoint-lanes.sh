#!/usr/bin/env bash
# entrypoint-lanes.sh: the job lanes' shared boot work, SOURCED by entrypoint.sh
# (same shell, as root) after the volume sweep and before the per-lane fragments,
# the broker and the medchron daemon launch. It decides which lanes this seat
# runs (ADR 0100) and builds only their tree: the chronology runner's firm config
# and controls, the runner's run dir and bind mount, each authored lane's dirs.
# Exports SMD_AUTHORED_LANES and lane_on for the entrypoint and the lane files.
# Reads CONFIG_DIR, LIVE_CUSTOMER_YAML, MEDCHRON_DATA_DIR, CUSTOMER_SLUG,
# R2_* and _seed_endpoint from the entrypoint.
# shellcheck shell=bash
# shellcheck disable=SC2154 # _seed_endpoint, CUSTOMER_SLUG and the R2_* names are set by entrypoint.sh, which sources this file
# shellcheck disable=SC2034 # the SMD_*_QUEUE_DIR / *_FIRM_CONFIG names are read by entrypoint.sh's broker launch

# ADR 0100: what this seat authors decides what boots. A job lane is authored when a
# persona enables its skill (shared/capabilities.py: the one predicate the gateway,
# the self-test and the release controller ask too). A seat that authors no lane
# boots no runner, no lane dirs and no lane fetches: on a 1 GB seat that was 45 MB
# and a minute of fetch retries for work it never does (2026-10-31, captain-mark).
# If the predicate cannot run, every lane boots, as before ADR 0100: a client seat
# must never lose a lane to a broken read.
# A COMMA list, never spaces: it is exported (the runner reads it), so every child
# inherits it, the gateway included, and seat-probe rebuilds the gateway's env by
# word-splitting; a space-separated value broke every probe on smd-staging
# (2026-10-31). tests/operator-entrypoint.test.ts pins the separator.
_ALL_LANES="medchron,demand,drafting,litigation,casefile,negotiation,note"
if _lanes="$(/opt/hermes/.venv/bin/python -I -m shared.capabilities lanes "${LIVE_CUSTOMER_YAML}")"; then
  SMD_AUTHORED_LANES="$(printf '%s' "${_lanes}" | paste -sd, -)"
else
  SMD_AUTHORED_LANES="${_ALL_LANES}"
  log "WARN: could not read authored lanes; booting every lane (${_ALL_LANES})"
fi
export SMD_AUTHORED_LANES
log "authored job lanes: ${SMD_AUTHORED_LANES:-none}"
lane_on() { case ",${SMD_AUTHORED_LANES}," in *",$1,"*) return 0 ;; esac; return 1; }
# Every lane's broker env is passed on below whether or not the lane booted; an
# empty queue dir makes its verbs answer fail-closed (medchron_verbs.py build).
SMD_MEDCHRON_QUEUE_DIR="" SMD_DEMAND_QUEUE_DIR="" SMD_DRAFTING_QUEUE_DIR="" SMD_LITIGATION_QUEUE_DIR=""
SMD_LITIGATION_FIRM_CONFIG="" SMD_CASEFILE_QUEUE_DIR="" SMD_CASEFILE_FIRM_CONFIG="" SMD_NEGOTIATION_QUEUE_DIR=""

# ss#2614: the chronology runner's per-firm config rides the same vault prefix.
# Same fail-static shape as customer.yaml above, without the FATAL arm: a seat
# without it boots fine and the runner refuses every job with one loud line.
# Root-owned, group medchron read-only (the driver child reads it; the agent
# uid has no group membership and no need to read the firm's tables).
MEDCHRON_FIRM_CONFIG="${CONFIG_DIR}/medchron-firm.yaml"
# The fetch verdict is recorded like every other lane's inputs (fetched | kept |
# absent | unreachable in /run/smd-lanes/medchron.inputs; entrypoint-fetch-inputs.sh
# defines the recorder and why): a seat that enables the chronology skill with no
# firm config in its vault (a cleanly empty listing) passes the self-test with a
# note, as demand does, while an unreachable vault still fails it (2026-10-31,
# smd-staging halted ring 0 on five chronology input checks).
# shellcheck source=/dev/null
. /app/entrypoint-fetch-inputs.sh
_vault="s3://${R2_BUCKET_CONFIG}/vaults/${CUSTOMER_SLUG}/"
_r2() { AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID:?}" AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY:?}" aws "$@" --endpoint-url "${_seed_endpoint}"; }
rm -f "${SMD_LANE_INPUTS_STATE_DIR:-/run/smd-lanes}/medchron.inputs"
if ! lane_on medchron; then
  log "medchron lane not authored; medchron-firm.yaml not fetched"
elif _r2 s3 cp --only-show-errors "${_vault}medchron-firm.yaml" "${MEDCHRON_FIRM_CONFIG}.r2.tmp" 2>/dev/null; then
  mv -f "${MEDCHRON_FIRM_CONFIG}.r2.tmp" "${MEDCHRON_FIRM_CONFIG}"
  _record_lane_inputs_state medchron fetched || true
  log "medchron-firm.yaml refreshed from R2 into ${CONFIG_DIR}"
elif [ -f "${MEDCHRON_FIRM_CONFIG}" ]; then
  _record_lane_inputs_state medchron kept || true
  log "WARN: R2 fetch of medchron-firm.yaml failed; keeping the existing root-owned copy"
elif ! _r2 s3 ls "${_vault}medchron-firm.yaml" >/dev/null 2>&1 && _r2 s3 ls "${_vault}" >/dev/null 2>&1; then
  _record_lane_inputs_state medchron absent || true
  log "No medchron-firm.yaml in the vault for ${CUSTOMER_SLUG}; the chronology runner will refuse jobs"
else
  _record_lane_inputs_state medchron unreachable || true
  log "WARN: medchron-firm.yaml could not be fetched and the vault could not be read cleanly; the chronology runner will refuse jobs"
fi
rm -f "${MEDCHRON_FIRM_CONFIG}.r2.tmp" 2>/dev/null || true
if [ -f "${MEDCHRON_FIRM_CONFIG}" ]; then
  chown root:medchron "${MEDCHRON_FIRM_CONFIG}"
  chmod 0640 "${MEDCHRON_FIRM_CONFIG}"
fi

# ss#2614: the chronology runner's tree, converged on every boot with explicit
# owners and modes (never mkdir -p defaults; the establish-spool note below
# explains why). queue/ is written by the broker uid (submit) and read by root
# (the daemon); jobs/ is root only; both reach their users through the bind
# mount, exactly like the audit ledger. The Smokeball refresh token becomes
# group-shared (setgid dir, 0660 file) so the medchron uid can mint and rotate
# it alongside the connector; the connector preserves that mode on rotation.
MEDCHRON_RUN_DIR="/run/smd-medchron"
# ADR 0100: the lane tree exists only on a seat that authors a lane.
if [ -n "${SMD_AUTHORED_LANES//,/}" ]; then
install -d -o root -g root -m 0755 "${MEDCHRON_DATA_DIR}"
mkdir -p "${MEDCHRON_RUN_DIR}"
mountpoint -q "${MEDCHRON_RUN_DIR}" \
  || mount --bind "${MEDCHRON_DATA_DIR}" "${MEDCHRON_RUN_DIR}" \
  || { log "FATAL: could not bind-mount ${MEDCHRON_DATA_DIR} -> ${MEDCHRON_RUN_DIR}"; exit 1; }
fi
if lane_on medchron; then
install -d -o root -g workspace-broker -m 0770 "${MEDCHRON_DATA_DIR}/queue"
# jobs/: root-owned, group medchron EXECUTE-ONLY (0710) — the driver child
# must traverse it to reach its own job dir (live-caught 2026-08-31: 0700
# gave the child PermissionError on its job.yaml) but must not be able to
# list or open sibling jobs; each job dir is 0700 medchron.
install -d -o root -g medchron -m 0710 "${MEDCHRON_DATA_DIR}/jobs"
export SMD_MEDCHRON_QUEUE_DIR="${MEDCHRON_RUN_DIR}/queue"
fi
# The SMD note lane (2026-10-15, medchron/note_lane.py): no queue and no child.
# note-jobs/ is written by root's note_by_smd command and read by the root
# daemon's note lane, which sends each note's wake and watches its reply; no
# other uid touches it, so it is root-only.
if lane_on note; then install -d -o root -g root -m 0700 "${MEDCHRON_DATA_DIR}/note-jobs"; fi
# The chronology runner's INSTALL-level artifacts — the scanned-page
# classifier's authored control pages (`controls.json` + the PDFs it names) and
# the vendored ICD tables (`icd/`) — ride the same vault prefix as the firm
# config, under medchron-controls/. Every job gets a fresh data_root under
# jobs/<id>/, so no job can carry them; the runner resolves them against this
# tree (Job.install_root, which the daemon points at the run dir). Seeded from
# the vault on EVERY boot, so a volume recreate converges on the authored set
# instead of refusing every job until someone copies files in by hand. Same
# fail-static shape as medchron-firm.yaml, no FATAL arm: a seat with nothing in
# its vault boots fine and the runner refuses the classify stage of each job
# with one loud line. Root-owned, group medchron read-only: the child reads
# its falsifier and can never rewrite it (a classifier that can edit its own
# controls measures nothing).
MEDCHRON_CONTROLS_DIR="${MEDCHRON_DATA_DIR}/controls"
rm -rf "${MEDCHRON_CONTROLS_DIR}.r2.tmp"
if ! lane_on medchron; then
  :
elif AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID:?}" \
     AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY:?}" \
       aws s3 cp \
         --endpoint-url "${_seed_endpoint}" \
         --only-show-errors \
         --recursive \
         "s3://${R2_BUCKET_CONFIG}/vaults/${CUSTOMER_SLUG}/medchron-controls/" \
         "${MEDCHRON_CONTROLS_DIR}.r2.tmp/" 2>/dev/null \
   && [ -f "${MEDCHRON_CONTROLS_DIR}.r2.tmp/controls.json" ]; then
  rm -rf "${MEDCHRON_CONTROLS_DIR}"
  mv "${MEDCHRON_CONTROLS_DIR}.r2.tmp" "${MEDCHRON_CONTROLS_DIR}"
  log "medchron controls refreshed from R2 into ${MEDCHRON_CONTROLS_DIR}"
elif [ -f "${MEDCHRON_CONTROLS_DIR}/controls.json" ]; then
  log "WARN: R2 fetch of medchron-controls/ failed; keeping the existing root-owned copy"
else
  log "No medchron-controls/ in the vault for ${CUSTOMER_SLUG}; the chronology runner will refuse the classify stage of every job"
fi
rm -rf "${MEDCHRON_CONTROLS_DIR}.r2.tmp"
if [ -d "${MEDCHRON_CONTROLS_DIR}" ]; then
  chown -R root:medchron "${MEDCHRON_CONTROLS_DIR}"
  find "${MEDCHRON_CONTROLS_DIR}" -type d -exec chmod 0750 {} +
  find "${MEDCHRON_CONTROLS_DIR}" -type f -exec chmod 0640 {} +
fi
