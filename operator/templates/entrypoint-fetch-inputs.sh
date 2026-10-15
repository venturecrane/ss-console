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
# shellcheck shell=bash
# shellcheck disable=SC2154 # _seed_endpoint, CUSTOMER_SLUG and the R2_* names are set by entrypoint.sh

_fetch_lane_inputs() {
  local prefix="$1" dest="$2" marker="$3" attempt err src
  src="s3://${R2_BUCKET_CONFIG}/vaults/${CUSTOMER_SLUG}/${prefix}/"
  _FETCH_INPUTS_STATE=""
  for attempt in 1 2 3; do
    rm -rf "${dest}.r2.tmp"
    if err=$(AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID:?}" \
               AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY:?}" \
               aws s3 cp --endpoint-url "${_seed_endpoint}" --only-show-errors \
                 --recursive "${src}" "${dest}.r2.tmp/" 2>&1) \
       && [ -f "${dest}.r2.tmp/${marker}" ]; then
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
  return 1
}
