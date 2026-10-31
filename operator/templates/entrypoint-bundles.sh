#!/usr/bin/env bash
# entrypoint-bundles.sh: capability bundles (ADR 0100 W3), SOURCED by
# entrypoint-lanes.sh (same shell, as root) once the authored lanes are known and
# BEFORE anything runs the job code: the lane fragments (casefile checks its firm
# inputs with /opt/medchron's python), the medchron daemon launch, and the
# gateway, which spawns the Smokeball MCP from /opt/connectors/smokeball.
#
# The jobs bundle is the job runner's venv (/opt/medchron) and the Smokeball
# connector's (/opt/connectors/smokeball), published on its own so a change to
# the runner ships only to the seats that run jobs. A seat that authors no lane
# installs nothing and mounts nothing. A seat that does runs the root installer
# (overlay bundle_installer: pointer, sha-verified manifest and tarball, root-owned
# extract under /opt/data/.smd-root/bundles/jobs/, the image's own import check,
# the tree check) and bind-mounts what it prints over the image's two paths.
# Bind mounts, not a path change: every caller names /opt/medchron and
# /opt/connectors/smokeball, and the mount presents the tree there without its
# ancestors, so the medchron and hermes uids reach it whatever /opt/data's mode
# (the gateway chmods it 0700 mid-boot).
#
# Anything failing leaves the image's copy in place with one loud line. What runs
# is written to /run/smd-bundles/<name> ("<version>" or "image"; fresh tmpfs each
# boot) for the heartbeat's `bundles` field (overlay shared/fleet_facts.py).
# shellcheck shell=bash

SMD_BUNDLE_STATE_DIR="${SMD_BUNDLE_STATE_DIR:-/run/smd-bundles}"
SMD_BUNDLE_PYTHON="${SMD_BUNDLE_PYTHON:-/opt/hermes/.venv/bin/python}"
# Test seam only (tests/operator-entrypoint.test.ts), like seat-checks.sh's
# SEAT_ROOT: empty on a seat, so the targets are the image's own paths.
_SMD_BT="${SMD_BUNDLE_TARGET_ROOT:-}"

# Undo whatever of a bundle got mounted (targets in mount order).
_smd_bundle_unmount() {
  local _t
  for _t in "${_SMD_BT}/opt/connectors/smokeball" "${_SMD_BT}/opt/medchron"; do
    if mountpoint -q "${_t}"; then umount "${_t}" || log "WARN: could not unmount ${_t}"; fi
  done
}

# The uids that run the jobs bundle must exec it from the mount points.
_smd_bundle_reachable() {
  setpriv --reuid=medchron --regid=medchron --init-groups \
    env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
    /opt/medchron/.venv/bin/python -I -c 'import medchron.daemon, smokeball_connector' \
  && setpriv --reuid=hermes --regid=hermes --init-groups \
    env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
    /opt/connectors/smokeball/.venv/bin/python -I -c 'import smokeball_connector'
}

# Mount <version dir>'s two trees over the image's; 0 only when both are
# mounted and reachable. Any failure unmounts both: never half a bundle.
_smd_bundle_mount_jobs() {
  local _dir="$1" _src _dst _pair
  for _pair in "medchron:/opt/medchron" "connectors/smokeball:/opt/connectors/smokeball"; do
    _src="${_dir}/${_pair%%:*}" _dst="${_SMD_BT}${_pair#*:}"
    if [ ! -d "${_dst}" ] || [ ! -d "${_src}" ] || ! mount --bind "${_src}" "${_dst}"; then
      log "bundle: could not bind-mount ${_src} -> ${_dst}"; _smd_bundle_unmount; return 1
    fi
    mount -o remount,bind,ro "${_dst}" || log "WARN: bundle: ${_dst} stays read-write (root-owned) after a failed read-only remount"
  done
  if ! _smd_bundle_reachable; then
    log "bundle: the mounted runner is not executable by the medchron or hermes uid"; _smd_bundle_unmount; return 1
  fi
}

rm -rf "${SMD_BUNDLE_STATE_DIR}"
if [ -z "${SMD_AUTHORED_LANES// /}" ]; then
  log "jobs bundle: this seat authors no job lane; nothing installed or mounted"
else
  install -d -o root -g root -m 0755 "${SMD_BUNDLE_STATE_DIR}"
  _smd_bundle_jobs="image"
  _smd_bundle_out="$("${SMD_BUNDLE_PYTHON}" -I -m bundle_installer jobs)" || _smd_bundle_out=""
  if [ -n "${_smd_bundle_out}" ]; then
    _smd_bundle_version="${_smd_bundle_out%%$'\t'*}"
    if _smd_bundle_mount_jobs "${_smd_bundle_out#*$'\t'}"; then
      _smd_bundle_jobs="${_smd_bundle_version}"
      log "jobs bundle ${_smd_bundle_version} mounted over /opt/medchron and /opt/connectors/smokeball"
    fi
  fi
  if [ "${_smd_bundle_jobs}" = "image" ]; then
    log "JOBS BUNDLE NOT MOUNTED: the image's job runner and Smokeball connector run (reasons above); heartbeat bundles.jobs=image"
  fi
  printf '%s\n' "${_smd_bundle_jobs}" > "${SMD_BUNDLE_STATE_DIR}/jobs"
  chmod 0644 "${SMD_BUNDLE_STATE_DIR}/jobs"
fi
