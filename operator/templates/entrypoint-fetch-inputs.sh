#!/usr/bin/env bash
# entrypoint-fetch-inputs.sh: the one fetch every lane's firm inputs go
# through, SOURCED by entrypoint.sh before the lane files (same shell, as
# root). It defines _fetch_lane_inputs and nothing else.
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
