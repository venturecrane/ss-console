#!/usr/bin/env bash
# entrypoint-supervisor.sh: the Machine's stop sequence, SOURCED by
# entrypoint.sh (same shell, as root). Ships at
# /app/runners/supervisor/entrypoint-supervisor.sh through the image's existing
# `COPY operator/runners/ /app/runners/`, so it needs no Dockerfile line of its
# own; its own file because entrypoint.sh sits at its shell size ratchet.
# shellcheck shell=bash
# shellcheck disable=SC2154 # log, MEDCHRON_RUN_DIR and the R2_* names are set by entrypoint.sh, which sources this file
#
# WHY THE ENTRYPOINT NO LONGER EXECS INTO THE GATEWAY (fleet job-safe restarts).
# It used to end in `exec setpriv ... /app/bootstrap.sh`, and bootstrap execs
# the gateway, so the gateway was tini's only direct child. A Machine stop then
# had nowhere to drain: tini (`-g`) signalled the whole process group at once,
# every chronology child died mid-stage, and when the gateway exited, tini
# exited and the container went with it. Now the entrypoint stays tini's child,
# runs the gateway as ITS child in a session of its own, and on SIGTERM:
#   1. writes the quiesce file (until now+600 s, by "machine-stop") so no lane
#      starts new work;
#   2. signals the medchron daemon and waits up to 240 s for it to exit (it
#      asks each running job to checkpoint, waits up to 220 s, terminates what
#      is left, and exits 130);
#   3. sends SIGTERM to the gateway and waits up to 45 s (Hermes' own stop:
#      chat turns are interrupted and marked for resume, cron work gets its
#      30 s drain floor, the watchdog leash is 60 s); then SIGKILL;
#   4. exits with the gateway's code.
# 240 + 45 fits Fly's kill_timeout of 300 s. A gateway that exits on its own
# (a crash, the liveness watcher's kill) makes the supervisor exit with its
# code at once, exactly as the exec'd gateway's exit used to end the container.
#
# WHY THE GATEWAY IS FORKED EARLY AND HELD. The broker admits the gateway by
# pid (SO_PEERCRED: peer pid == SMD_GATEWAY_PID) and the liveness watcher kills
# that pid. When the entrypoint exec'd, that pid was `$$`. Now it must be the
# gateway's own pid, known before the broker launches, so smd_supervisor_boot
# forks the gateway's process right where SMD_GATEWAY_PID used to be exported
# and holds it blocked on a root-only FIFO. Everything the entrypoint does
# afterwards (credential unsets, the root daemons, the boot gates) happens in
# the parent; smd_supervise then streams the parent's FINAL exported
# environment through the FIFO, and the held process replaces its own
# environment with exactly that before it execs setsid -> setpriv ->
# bootstrap.sh -> hermes. Every step is an exec, so the pid never changes. The
# environment never rides an argv (argv is world-readable in /proc), and a
# release with no terminating sentinel (the parent died mid-boot) execs nothing.

SMD_SUPERVISOR_DAEMON_WAIT_SECONDS="${SMD_SUPERVISOR_DAEMON_WAIT_SECONDS:-240}"
SMD_SUPERVISOR_GATEWAY_WAIT_SECONDS="${SMD_SUPERVISOR_GATEWAY_WAIT_SECONDS:-45}"
SMD_SUPERVISOR_QUIESCE_SECONDS="${SMD_SUPERVISOR_QUIESCE_SECONDS:-600}"
_SMD_RELEASE_SENTINEL="__SMD_GATEWAY_RELEASE__"

# The quiesce file the daemon and every lane read (medchron/quiesce.py).
smd_quiesce_write() { # by reason seconds
  local _f="${MEDCHRON_RUN_DIR:-}/quiesce" _until
  [ -d "${MEDCHRON_RUN_DIR:-}" ] || return 0
  _until=$(( $(date +%s) + $3 ))
  if printf '{"until": %s, "by": "%s", "reason": "%s"}\n' "${_until}" "$1" "$2" > "${_f}.tmp" \
     && chmod 0644 "${_f}.tmp" && mv -f "${_f}.tmp" "${_f}"; then
    log "Supervisor: quiesce written (${_f}, until ${_until}, by $1)"
  else
    log "WARN: could not write the quiesce file ${_f}; the daemon's own stop flag still holds new work"
  fi
}

# The run dir is on the volume, so a quiesce outlives the reboot. The previous
# Machine stop's own one ended with that Machine; any other writer's (the fleet
# rollout) stands until it expires or its writer removes it.
smd_quiesce_clear_stale() {
  local _f="${MEDCHRON_RUN_DIR:-}/quiesce"
  [ -f "${_f}" ] || return 0
  if grep -q '"by": "machine-stop"' "${_f}"; then
    rm -f "${_f}" && log "Cleared the previous Machine stop's quiesce (${_f})"
  else
    log "A quiesce is in place (${_f}); the job lanes honour it until it expires: $(head -c 300 "${_f}")"
  fi
}

# Runs in the forked subshell whose pid IS SMD_GATEWAY_PID: blocks until the
# release, takes the released environment as its whole environment, and execs
# the privilege-dropped bootstrap in a session of its own (so tini's group
# signal no longer reaches it; the supervisor forwards the stop instead).
_smd_gateway_held() {
  local _fifo="$1" _item _name _released=0
  trap - TERM INT
  exec 3<"${_fifo}"
  for _name in $(compgen -e); do unset "${_name}" 2>/dev/null || true; done
  while IFS= read -r -d '' _item <&3; do
    if [ "${_item}" = "__SMD_GATEWAY_RELEASE__" ]; then _released=1; break; fi
    export "${_item?}" 2>/dev/null || true
  done
  exec 3<&-
  [ "${_released}" = 1 ] || exit 1
  exec setsid setpriv --reuid=hermes --regid=hermes --init-groups --no-new-privs /app/bootstrap.sh
}

# Root writers' audit-loss directory (overlay shared/audit_failure_counter.py
# _record_as_root, 2026-10-20). Made here, as root, before any agent-uid process
# exists this boot: /opt/data is the agent's, so anything it left at this name
# (its own directory, a symlink) is removed first and the real one created. The
# overlay refuses a .smd-root that is not root-owned and private to root, so a
# planted one costs counts, never ownership; this keeps the counts.
smd_root_loss_dir() {
  local _d="${HERMES_HOME:-/opt/data}/.smd-root"
  [ "$(id -u)" = 0 ] && [ -d "${HERMES_HOME:-/opt/data}" ] || return 0
  if [ -L "${_d}" ] || [ ! -d "${_d}" ] || [ "$(stat -c %u "${_d}")" != 0 ]; then
    rm -rf -- "${_d}"
  fi
  install -d -o root -g root -m 0755 "${_d}" \
    || log "WARN: could not create ${_d}; root appliers' audit losses go uncounted this boot"
}

smd_supervisor_boot() {
  smd_root_loss_dir
  smd_quiesce_clear_stale
  _SMD_GW_GATE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/smd-gateway-gate.XXXXXX")"
  chmod 0700 "${_SMD_GW_GATE_DIR}"
  mkfifo -m 0600 "${_SMD_GW_GATE_DIR}/release"
  ( _smd_gateway_held "${_SMD_GW_GATE_DIR}/release" ) &
  SMD_GATEWAY_PID=$!
  export SMD_GATEWAY_PID
  log "Gateway process forked and held (pid ${SMD_GATEWAY_PID}) until the boot gates pass"
}

# A root respawn loop for an overlay poller (config_applier, spec_applier,
# skills_applier). Exit 130 is the poller's own "stopped by the Machine stop":
# final, like the medchron daemon's, so nothing respawns inside a dying Machine.
smd_root_poller() { # module label
  ( _smd_stop=""
    trap '_smd_stop=1' TERM INT
    while true; do
      if /opt/hermes/.venv/bin/python -m "$1"; then _smd_rc=0; else _smd_rc=$?; fi
      if [ "${_smd_rc}" = 130 ] || [ -n "${_smd_stop}" ]; then
        log "$2 stopped by the Machine stop (${_smd_rc}); not restarting"; break
      fi
      log "$2 exited (${_smd_rc}); restarting in 5s"; sleep 5
    done ) &
}

# Skills delivered as a release (vaults/<slug>/skills-pointer.json): one
# synchronous verify-and-install before the drop, so bootstrap seeds from
# /var/lib/smd-skills/current, then the poll loop. Never fatal: an overlay
# without the module, or a fetch that fails, leaves bootstrap on /app/skills.
smd_skills_applier() {
  if [ -z "${R2_ACCESS_KEY_ID:-}" ] || [ -z "${R2_BUCKET_CONFIG:-}" ] \
     || ! /opt/hermes/.venv/bin/python -c "import skills_applier" 2>/dev/null; then
    log "Root skills applier NOT launched (R2 config creds absent, or skills_applier not in this overlay)"
    return 0
  fi
  if /opt/hermes/.venv/bin/python -m skills_applier --once; then
    log "skills release boot fetch complete (/var/lib/smd-skills/current)"
  else
    log "WARN: skills release boot fetch exited non-zero; bootstrap seeds from what is installed, else /app/skills"
  fi
  smd_root_poller skills_applier "skills applier"
  log "Root skills applier launched (uid 0; polls this seat's skills pointer)"
}

_smd_alive() {
  kill -0 "$1" 2>/dev/null || return 1
  case "$(ps -o stat= -p "$1" 2>/dev/null)" in Z*) return 1 ;; esac
  return 0
}

_smd_wait_for() { # pid seconds; 0 when it is gone in time
  local _i=0 _limit=$(( $2 * 5 ))
  while _smd_alive "$1"; do
    [ "${_i}" -ge "${_limit}" ] && return 1
    sleep 0.2
    _i=$(( _i + 1 ))
  done
  return 0
}

# The broker and its respawn loop ignore TERM (entrypoint.sh) so they outlive
# the gateway and the daemon drain; once the gateway has exited nothing is left
# to record, so they are KILLed here and add no time to the stop. Without this
# they would still end with the Machine, but only when Fly tears it down.
_smd_stop_broker() {
  [ -n "${SUPERVISOR_PID:-}" ] || return 0
  local _brk
  _brk="$(pgrep -P "${SUPERVISOR_PID}" 2>/dev/null | tr '\n' ' ')" || true
  # shellcheck disable=SC2086 # _brk is a space-separated pid list on purpose
  kill -KILL "${SUPERVISOR_PID}" ${_brk} 2>/dev/null || true
}

_smd_supervisor_stop() {
  trap '' TERM INT
  local _rc=0
  log "Supervisor: ${1} received; quiescing the lanes, draining the medchron daemon (<=${SMD_SUPERVISOR_DAEMON_WAIT_SECONDS}s), then the gateway (<=${SMD_SUPERVISOR_GATEWAY_WAIT_SECONDS}s)"
  smd_quiesce_write machine-stop "Machine stop (${1})" "${SMD_SUPERVISOR_QUIESCE_SECONDS}"
  if [ -n "${SMD_MEDCHRON_LOOP_PID:-}" ]; then
    kill -TERM "${SMD_MEDCHRON_LOOP_PID}" 2>/dev/null || true
    pkill -TERM -P "${SMD_MEDCHRON_LOOP_PID}" 2>/dev/null || true
    if _smd_wait_for "${SMD_MEDCHRON_LOOP_PID}" "${SMD_SUPERVISOR_DAEMON_WAIT_SECONDS}"; then
      log "Supervisor: medchron daemon drained"
    else
      log "WARN: medchron daemon still running after ${SMD_SUPERVISOR_DAEMON_WAIT_SECONDS}s; stopping the gateway anyway (claimed jobs resume on the next boot)"
    fi
  fi
  kill -TERM "${SMD_GATEWAY_PID}" 2>/dev/null || true
  if ! _smd_wait_for "${SMD_GATEWAY_PID}" "${SMD_SUPERVISOR_GATEWAY_WAIT_SECONDS}"; then
    log "WARN: gateway still running ${SMD_SUPERVISOR_GATEWAY_WAIT_SECONDS}s after SIGTERM; SIGKILL"
    kill -KILL "${SMD_GATEWAY_PID}" 2>/dev/null || true
  fi
  wait "${SMD_GATEWAY_PID}" 2>/dev/null || _rc=$?
  # Whatever the gateway left in its session (MCP stdio servers).
  kill -TERM -- "-${SMD_GATEWAY_PID}" 2>/dev/null || true
  _smd_stop_broker
  log "Supervisor: gateway exited (${_rc}); exiting with its code"
  exit "${_rc}"
}

# The release and the wait: the last thing the entrypoint does.
smd_supervise() {
  local _n _rc=0
  trap '_smd_supervisor_stop TERM' TERM
  trap '_smd_supervisor_stop INT' INT
  exec 9>"${_SMD_GW_GATE_DIR}/release"
  for _n in $(compgen -e); do printf '%s=%s\0' "${_n}" "${!_n}" >&9; done
  printf '%s\0' "${_SMD_RELEASE_SENTINEL}" >&9
  exec 9>&-
  rm -rf "${_SMD_GW_GATE_DIR}"
  log "Supervisor: gateway released (pid ${SMD_GATEWAY_PID}); the entrypoint stays as its supervisor"
  wait "${SMD_GATEWAY_PID}" || _rc=$?
  _smd_stop_broker
  log "Supervisor: gateway exited on its own (${_rc}); exiting with its code"
  exit "${_rc}"
}
