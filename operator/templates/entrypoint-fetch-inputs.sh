#!/usr/bin/env bash
# entrypoint-fetch-inputs.sh: the boot's vault fetches, SOURCED by
# entrypoint.sh (same shell, as root) before it reads the seat's config. It
# defines _fetch_lane_inputs, the one fetch every lane's firm inputs go
# through, and, at the end, the fetch of the seat's own customer.yaml
# (smd_boot_customer_yaml). It runs nothing itself.
#
# WHY (2026-10-14, A&P v141). The litigation inputs were in the vault
# (vaults/ashton-price/litigation/, 422 objects, uploaded 13:32Z) and the boot
# at 13:43Z did not land them: the single `aws s3 cp --recursive` failed, its
# stderr went to /dev/null, the image is fresh so there was no copy to keep,
# and the lane logged "No litigation firm inputs in the vault", which was
# false. Every litigation job then stopped at config_missing. A restart of the
# same image fetched them fine, so the failure was transient. A transient
# failure gets retries, and a failed fetch of inputs the vault DOES hold is
# logged as the error it is, never as "none authored".
#
# _fetch_lane_inputs <prefix> <dest> <marker-file>
#   Copies s3://<config bucket>/vaults/<slug>/<prefix>/ into <dest>.r2.tmp/,
#   up to three attempts, and succeeds only when <marker-file> arrived. The
#   caller keeps its own fail-static branches (swap in, keep the old copy,
#   defer jobs). On failure it sets _FETCH_INPUTS_STATE to "absent" when the
#   vault holds no <marker-file> and "unreachable" when it does or cannot be
#   listed, so the caller's log line says which.
#
# THE STATE FILE (2026-10-23). The same verdict is written, root-owned 0644,
# to ${SMD_LANE_INPUTS_STATE_DIR:-/run/smd-lanes}/<prefix>.inputs as one word:
# fetched | kept (fetch failed, the seat's earlier copy stays) | absent (a clean
# vault listing holds none) | unreachable (the vault holds them or could not be
# listed, and this seat has no copy). The in-image self-test (selftest.sh
# lane_authored) reads it: a lane whose vault is cleanly empty is NOT authored
# for the seat, so its presence check is skipped with a note, while an
# unreachable vault still counts as authored and fails the check. Without it,
# pilot-smokeball (demand-letter-drafter enabled, no demand inputs ever
# authored) failed demand-firm-inputs-present on every boot. The old file is
# removed first, so a write that fails can never leave an earlier boot's
# "absent" standing in for this one's verdict.
# shellcheck shell=bash
# shellcheck disable=SC2154 # _seed_endpoint, CUSTOMER_SLUG and the R2_* names are set by entrypoint.sh
# The seat's folder layout (operator/contracts/seat-layout.yaml, generated beside
# this file): the SMD_LAYOUT_* paths below. Sourced here as well as by
# entrypoint.sh, so this file reads it on every path that reaches it.
# shellcheck source=/dev/null
. "$(dirname "${BASH_SOURCE[0]}")/_seat-layout.generated.sh"

_record_lane_inputs_state() { # <prefix> <fetched|kept|absent|unreachable>
  local dir="${SMD_LANE_INPUTS_STATE_DIR:-/run/smd-lanes}" tmp
  install -d -o root -g root -m 0755 "${dir}" || return 1
  tmp="$(mktemp "${dir}/.$1.inputs.XXXXXX")" || return 1
  if printf '%s\n' "$2" > "${tmp}" && chmod 0644 "${tmp}" && mv -f "${tmp}" "${dir}/$1.inputs"; then
    return 0
  fi
  rm -f "${tmp}"
  return 1
}

_fetch_lane_inputs() {
  local prefix="$1" dest="$2" marker="$3" attempt err src state
  src="s3://${R2_BUCKET_CONFIG}/vaults/${CUSTOMER_SLUG}/${prefix}/"
  _FETCH_INPUTS_STATE=""
  rm -f "${SMD_LANE_INPUTS_STATE_DIR:-/run/smd-lanes}/${prefix}.inputs"
  for attempt in 1 2 3; do
    rm -rf "${dest}.r2.tmp"
    if err=$(AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID:?}" \
               AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY:?}" \
               aws s3 cp --endpoint-url "${_seed_endpoint}" --only-show-errors \
                 --recursive "${src}" "${dest}.r2.tmp/" 2>&1) \
       && [ -f "${dest}.r2.tmp/${marker}" ]; then
      _record_lane_inputs_state "${prefix}" fetched || log "WARN: could not record the ${prefix} inputs state"
      return 0
    fi
    log "WARN: ${prefix} inputs fetch attempt ${attempt}/3 did not land ${marker}: $(printf '%s' "${err}" | head -n 2 | tr '\n' ' ' | cut -c1-300)"
    [ "${attempt}" -lt 3 ] && sleep $((attempt * 5))
  done
  rm -rf "${dest}.r2.tmp"
  if AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID:?}" \
       AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY:?}" \
       aws s3 ls --endpoint-url "${_seed_endpoint}" "${src}${marker}" >/dev/null 2>&1; then
    _FETCH_INPUTS_STATE="unreachable"
  else
    # A failed list is not proof of absence either; only a clean empty list is.
    if AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID:?}" \
         AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY:?}" \
         aws s3 ls --endpoint-url "${_seed_endpoint}" "s3://${R2_BUCKET_CONFIG}/vaults/${CUSTOMER_SLUG}/" >/dev/null 2>&1; then
      _FETCH_INPUTS_STATE="absent"
    else
      _FETCH_INPUTS_STATE="unreachable"
    fi
  fi
  state="${_FETCH_INPUTS_STATE}"
  [ -f "${dest}/${marker}" ] && state="kept"
  _record_lane_inputs_state "${prefix}" "${state}" || log "WARN: could not record the ${prefix} inputs state"
  return 1
}

# ---------- versioned inputs, validated before an atomic swap (2026-11-04) ----------
# A lane's live path is a SYMLINK to a version named for the first 8 hex of its
# marker object's ETag: ${CONFIG_DIR}/records -> records.v1a2b3c4d/, and the
# chronology's ${CONFIG_DIR}/medchron-firm.yaml -> medchron-firm.v1a2b3c4d.yaml.
# The boot fetches into <live>.r2.tmp as before, then _adopt_lane_inputs owns the
# copy root:jobs, VALIDATES it with the runner's own validator from the jobs
# bundle venv, and only then renames it to its version and swaps the symlink with
# one rename. A refused copy is removed, the live symlink stays, and the verdict
# word is `rejected`. The root firm-inputs applier (overlay firm_inputs_applier)
# does the same live, every 15 s, so a published change reaches a running seat
# with no restart; a running job resolved the symlink once and keeps its version.
# Before this the swap was `rm -rf; mv` and validation ran after it, only logging.
_FIRM_VALIDATOR_PY="${SMD_FIRM_VALIDATOR_PYTHON:-${SMD_LAYOUT_JOBS_PYTHON}}"
_FIRM_LINK_PY="${SMD_FIRM_LINK_PYTHON:-${SMD_LAYOUT_HERMES_PYTHON}}"

# _lane_version_path <live> <label>
_lane_version_path() { case "$1" in *.yaml) printf '%s' "${1%.yaml}.v$2.yaml" ;; *) printf '%s' "$1.v$2" ;; esac; }

# _swap_lane_link <live> <version-path>: point <live> at the version in ONE rename.
_swap_lane_link() {
  local tmp
  tmp="$(dirname "$1")/.$(basename "$1").link-$$"
  rm -f "${tmp}"
  if ln -s "$(basename "$2")" "${tmp}" \
     && "${_FIRM_LINK_PY}" -I -c 'import os, sys; os.replace(sys.argv[1], sys.argv[2])' "${tmp}" "$1"; then
    return 0
  fi
  rm -f "${tmp}"
  return 1
}

# _lane_inputs_legacy <live>: a copy from a boot before versioning (a real dir or
# file) becomes version 00000000, so a failed fetch still keeps it and the
# applier replaces it on its first tick.
_lane_inputs_legacy() {
  local v
  if [ ! -e "$1" ] || [ -L "$1" ]; then return 0; fi
  v="$(_lane_version_path "$1" 00000000)"
  rm -rf "${v}"
  mv "$1" "${v}" && _swap_lane_link "$1" "${v}"
}

# _lane_inputs_label <r2 key>: _LANE_LABEL = the key's ETag, first 8 hex, or "".
# Read BEFORE the copy: a publish landing mid-copy then labels the copy with the
# older ETag, and the applier's next tick sees the newer one and re-adopts.
_lane_inputs_label() {
  _LANE_LABEL="$(AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID:?}" AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY:?}" \
    aws s3api head-object --endpoint-url "${_seed_endpoint}" --bucket "${R2_BUCKET_CONFIG}" --key "$1" \
      --query ETag --output text 2>/dev/null | tr -d '"' | cut -c1-8 | tr 'A-F' 'a-f')" || _LANE_LABEL=""
  case "${_LANE_LABEL}" in
    [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;;
    *) _LANE_LABEL="" ;;
  esac
}

# _lane_inputs_begin <lane> <live>: before a lane's fetch, convert a legacy copy
# and read the version label of what is about to be fetched.
_lane_inputs_begin() {
  _lane_inputs_legacy "$2" || log "WARN: could not convert the $1 inputs at $2 to a versioned copy"
  _lane_inputs_label "vaults/${CUSTOMER_SLUG}/$1/$1-firm.yaml"
}

# _own_lane_inputs <path>: root:jobs, dirs 0750, files 0640 (a trailing / follows the link).
_own_lane_inputs() {
  chown -R root:jobs "$1"
  if [ -d "$1" ]; then
    find "$1" -type d -exec chmod 0750 {} +
    find "$1" -type f -exec chmod 0640 {} +
  else
    chmod 0640 "$1"
  fi
}

# _validate_lane_inputs <lane> <path>: 0 valid, 1 refused, 2 the validator could
# not run. Its last words go to _VALIDATE_OUT.
_validate_lane_inputs() {
  local rc=0
  _VALIDATE_OUT=""
  if [ ! -x "${_FIRM_VALIDATOR_PY}" ]; then
    _VALIDATE_OUT="${_FIRM_VALIDATOR_PY} is not executable"
    return 2
  fi
  if [ "$1" = medchron ]; then
    _VALIDATE_OUT="$("${_FIRM_VALIDATOR_PY}" -c 'import sys
from jobs.medchron.config import load
load(sys.argv[1])' "$2" 2>&1)" || rc=$?
  else
    _VALIDATE_OUT="$("${_FIRM_VALIDATOR_PY}" -m "jobs.$1.firm" "$2" 2>&1)" || rc=$?
  fi
  _VALIDATE_OUT="$(printf '%s\n' "${_VALIDATE_OUT}" | grep -v '^[[:space:]]*$' | tail -n 1 | cut -c1-300)" || true
  case "${rc}" in 0) return 0 ;; 1) return 1 ;; *) return 2 ;; esac
}

# _prune_lane_versions <live>: keep the newest three versions and the live one.
_prune_lane_versions() {
  local cur v n=0 pat
  cur="$(readlink "$1" 2>/dev/null)" || cur=""
  case "$1" in *.yaml) pat="${1%.yaml}.v*.yaml" ;; *) pat="$1.v*" ;; esac
  # shellcheck disable=SC2086,SC2012,SC2045 # the pattern is ours; version names are hex labels, sorted by mtime
  for v in $(ls -1dt $pat 2>/dev/null); do
    n=$((n + 1))
    if [ "${n}" -le 3 ] || [ "$(basename "${v}")" = "${cur}" ]; then continue; fi
    rm -rf "${v}"
  done
}

# _adopt_lane_inputs <lane> <live> <staged>: own, validate, rename to its version
# (_LANE_LABEL, else the marker's sha256), swap. 0 = the live path now names the
# staged copy; 1 = the live copy stands (refused, or the validator could not run
# while a copy was in place: a broken validator never replaces a working copy;
# with NO copy the lane could not run anyway, so it adopts with a loud line and
# the runner's own gate still checks every job).
_adopt_lane_inputs() {
  local lane="$1" live="$2" staged="$3" label="${_LANE_LABEL:-}" ver marker rc=0
  if [ -z "${label}" ]; then
    marker="${staged}"
    if [ -d "${staged}" ]; then marker="${staged}/${lane}-firm.yaml"; fi
    label="$({ sha256sum "${marker}" 2>/dev/null || shasum -a 256 "${marker}"; } | cut -c1-8)" || label=""
  fi
  _own_lane_inputs "${staged}"
  _validate_lane_inputs "${lane}" "${staged}" || rc=$?
  if [ "${rc}" = 1 ]; then
    rm -rf "${staged}"
    _record_lane_inputs_state "${lane}" rejected || log "WARN: could not record the ${lane} inputs state"
    log "ERROR: the ${lane} firm inputs in the vault (version ${label}) do not validate, so they were NOT adopted; keeping $(readlink "${live}" 2>/dev/null || echo 'no copy'): ${_VALIDATE_OUT}"
    return 1
  fi
  if [ "${rc}" = 2 ] && [ -e "${live}" ]; then
    rm -rf "${staged}"
    _record_lane_inputs_state "${lane}" kept || log "WARN: could not record the ${lane} inputs state"
    log "WARN: the ${lane} firm validator could not run (${_VALIDATE_OUT}); keeping the existing copy, version ${label} not adopted"
    return 1
  fi
  if [ "${rc}" = 2 ]; then
    log "WARN: the ${lane} firm validator could not run (${_VALIDATE_OUT}) and this seat has no copy; adopting version ${label} UNCHECKED"
  fi
  ver="$(_lane_version_path "${live}" "${label}")"
  rm -rf "${ver}"
  if ! { mv "${staged}" "${ver}" && touch "${ver}" && _swap_lane_link "${live}" "${ver}"; }; then
    rm -rf "${staged}"
    log "ERROR: could not commit the ${lane} firm inputs version ${label} at ${live}"
    return 1
  fi
  _prune_lane_versions "${live}"
  log "${lane} firm inputs refreshed from R2 into ${live} (version ${label})"
  return 0
}

# ---------------------------------------------------------------------------
# THE SEAT'S customer.yaml (2026-11-16: moved here from entrypoint.sh, so the
# vault fetches live in one file). validate_candidate_config and
# smd_boot_customer_yaml <live path>; entrypoint.sh calls the second once,
# before anything reads the config.
#
# Validate a CANDIDATE customer.yaml before it becomes the live one, using the
# SAME on-box validator the ADR 0044 live applier uses. Until ss #2082 the boot
# fetch did a bare `cp` then `mv -f` with no check at all, and only the poller
# validated. That asymmetry was survivable while the R2 object was written by
# hand at provision time; auto-publish arms it. An object the poller REJECTS
# stays in R2, and the next restart (a Fly migration, an OOM, an unrelated
# redeploy) adopted it unvalidated, hours or days after the merge that put it
# there and with nothing tying the crash to that merge.
#
# Structural validation ONLY. The applier's safety layer (config_applier.safety)
# is deliberately NOT run here: it enforces live-apply transition rules, and the
# rebuild-class fields it refuses on the live path (persona tone, vertical,
# model) are precisely the ones a RESTART exists to deliver. Running it here
# would refuse the changes this path is for.
#
# Exit 2 means the validator itself is unimportable. That is treated exactly
# like an invalid config: a substrate we cannot check is not a substrate we
# adopt from (the standing fail-closed posture, same as the invariant_7 gate
# further down, which refuses boot on a missing module rather than skipping).
#
# AN R2 OUTAGE (2026-11-12). The live copy sits in the config directory on the image's
# root filesystem, which a Machine resets on every start, so after a restart the
# "keep the existing copy" branch below never had a copy to keep. One failed
# fetch meant FATAL, Fly's three quick restarts, and a client seat parked at
# "stopped" until someone started it by hand; during a regional outage that was
# several seats at once. Two changes:
#   * A fetch that fails for any reason but "not there" is retried, backing off
#     to 30s, for up to SMD_CONFIG_FETCH_WAIT_SECONDS (300) inside this one
#     boot: longer than Fly's whole restart budget (max_retries 3, seconds
#     apart), so a short outage is waited out instead of exhausting it.
#   * The seat keeps a SIGNED copy on its volume (overlay
#     config_applier/volume_copy.py: the root spec verifier writes it once it
#     proves the running yaml is the one an authentic seat spec names). With
#     one, the wait is SMD_CONFIG_FETCH_WAIT_WITH_COPY_SECONDS (60), and then
#     the seat boots on that copy with a loud WARN naming its spec. The copy is
#     verified again here (signature against the image's key, digest, seat, the
#     yaml's hash), never trusted for where it sits: the volume is the agent's.
#     A 404 is not an outage: a seat the vault no longer holds is not booted
#     from a copy (gone means gone), it stops as before.

validate_candidate_config() {
  ${SMD_CONFIG_VALIDATOR_PYTHON:-${SMD_LAYOUT_HERMES_PYTHON3}} - "$1" <<'PY'
import sys
from pathlib import Path

try:
    from bootstrap.validate import validate_customer_yaml
except Exception as exc:  # noqa: BLE001 - any import failure is a refusal
    print(f"candidate config validator unimportable: {exc}", file=sys.stderr)
    raise SystemExit(2)

errors = validate_customer_yaml(Path(sys.argv[1]))
for err in errors:
    print(f"  {err}", file=sys.stderr)
raise SystemExit(1 if errors else 0)
PY
}

# One fetch into $1. On failure _CY_FETCH is "absent"
# (the vault answered that the object is not there) or "unreachable".
_smd_fetch_customer_yaml() { # <into>
  local err
  rm -f "$1"
  if err=$(AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID:?}" \
             AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY:?}" \
             aws s3 cp --endpoint-url "${_seed_endpoint}" --only-show-errors \
               "s3://${R2_BUCKET_CONFIG}/vaults/${CUSTOMER_SLUG}/customer.yaml" \
               "$1" 2>&1); then
    _CY_FETCH="fetched"
    return 0
  fi
  case "${err}" in
    *"(404)"*|*NoSuchKey*|*"Not Found"*) _CY_FETCH="absent" ;;
    *) _CY_FETCH="unreachable" ;;
  esac
  _CY_FETCH_ERR="$(printf '%s' "${err}" | head -n 2 | tr '\n' ' ' | cut -c1-300)"
  return 1
}

# The signed volume copy, verified, into $1. Prints "<spec sha256> <commit>".
_smd_restore_volume_copy() {
  ${SMD_CONFIG_COPY_PYTHON:-${SMD_LAYOUT_HERMES_PYTHON3} -I} -m config_applier.volume_copy \
    restore "${CUSTOMER_SLUG}" "$1"
}

smd_boot_customer_yaml() { # <live path>
  local live="$1"
  local tmp="${live}.r2.tmp" vtmp="${live}.volume.tmp"
  local waited=0 nap=5 budget copy="" tried_copy=0 r2_usable=0
  rm -f "${vtmp}"
  _CY_FETCH="" _CY_FETCH_ERR=""
  until _smd_fetch_customer_yaml "${tmp}"; do
    [ "${_CY_FETCH}" = "absent" ] && break
    if [ "${tried_copy}" -eq 0 ]; then
      tried_copy=1
      copy="$(_smd_restore_volume_copy "${vtmp}")" || copy=""
    fi
    budget="${SMD_CONFIG_FETCH_WAIT_SECONDS:-300}"
    if [ -n "${copy}" ] || [ -f "${live}" ]; then
      budget="${SMD_CONFIG_FETCH_WAIT_WITH_COPY_SECONDS:-60}"
    fi
    [ "${waited}" -ge "${budget}" ] && break
    log "WARN: R2 customer.yaml fetch failed (${_CY_FETCH_ERR}); retrying in ${nap}s (${waited}s of ${budget}s waited)"
    sleep "${nap}"
    waited=$((waited + nap))
    nap=$((nap * 2 > 30 ? 30 : nap * 2))
  done

  if [ "${_CY_FETCH}" = "fetched" ]; then
    if validate_candidate_config "${tmp}"; then
      r2_usable=1
    elif [ $? -eq 2 ]; then
      log "WARN: cannot validate the R2 customer.yaml (validator unimportable); refusing to adopt it"
    else
      log "WARN: R2 customer.yaml REFUSED by the on-box validator (errors above); refusing to adopt it"
    fi
  fi

  if [ "${r2_usable}" -eq 1 ]; then
    rm -f "${vtmp}"
    mv -f "${tmp}" "${live}"
    log "customer.yaml refreshed from R2 (source of truth) into ${live%/*}"
    return 0
  fi
  rm -f "${tmp}" 2>/dev/null || true
  if [ -f "${live}" ]; then
    # Fail-static: the Machine comes up on the config it was already serving. A
    # bad publish costs the seat its update, never its uptime.
    rm -f "${vtmp}"
    if [ "${_CY_FETCH}" = "fetched" ]; then
      log "WARN: keeping the existing root-owned customer.yaml (the R2 object was not adopted)"
    else
      log "WARN: R2 fetch failed; using existing root-owned customer.yaml"
    fi
    return 0
  fi
  if [ "${_CY_FETCH}" != "absent" ]; then
    if [ "${tried_copy}" -eq 0 ]; then
      copy="$(_smd_restore_volume_copy "${vtmp}")" || copy=""
    fi
    if [ -n "${copy}" ] && validate_candidate_config "${vtmp}"; then
      mv -f "${vtmp}" "${live}"
      if [ "${_CY_FETCH}" = "fetched" ]; then
        log "WARN: BOOTING FROM THE VOLUME COPY of customer.yaml: the R2 object was refused. Signed spec ${copy% *}, compiled from ${copy#* }. The seat runs its last verified config until a valid object is published"
      else
        log "WARN: BOOTING FROM THE VOLUME COPY of customer.yaml: R2 did not answer for ${waited}s (${_CY_FETCH_ERR}). Signed spec ${copy% *}, compiled from ${copy#* }. The config applier adopts R2's copy when it answers"
      fi
      return 0
    fi
    rm -f "${vtmp}"
  fi
  if [ "${_CY_FETCH}" = "fetched" ]; then
    log "FATAL: no local customer.yaml and the R2 object was refused (${R2_BUCKET_CONFIG}/vaults/${CUSTOMER_SLUG}); nothing valid to boot on"
  elif [ "${_CY_FETCH}" = "absent" ]; then
    log "FATAL: customer.yaml is not in R2 (${R2_BUCKET_CONFIG}/vaults/${CUSTOMER_SLUG}, 404); a seat the vault does not hold is not booted from a copy"
  else
    log "FATAL: customer.yaml not present and R2 fetch failed for ${waited}s with no verified volume copy (${R2_BUCKET_CONFIG}/vaults/${CUSTOMER_SLUG})"
  fi
  return 1
}
