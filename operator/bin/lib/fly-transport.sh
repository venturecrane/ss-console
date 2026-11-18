#!/usr/bin/env bash
# fly-transport.sh: reach a seat's Machine when Fly refuses the ssh tunnel.
#
# Sourced by operator/bin/seat-probe.sh and operator/bin/boot-smoke-test.sh.
# Defines functions only; running it does nothing.
#
# WHY (2026-11-16, 2026-11-17). `fly ssh console` needs org-level calls (the
# WireGuard tunnel, the org's peers). On 2026-11-16 Fly refused those for about
# six hours ("can't build tunnel for personal: Not authorized to access this
# organization") while app-level calls with the same login kept working, so
# every seat probe failed; seat-probe.sh learned to fall back to `fly machine
# exec`, which is an HTTPS call to the Machines API and needs no tunnel. On
# 2026-11-17 the same refusal hit the boot smoke that re-verifies a rolled-back
# seat, so a client seat's rollback image went unverified. This file is the one
# copy of that fallback, so both callers take it the same way.

# fly_tunnel_refused [file]: true when the file (stdin without one) holds Fly's
# refusal of the org-level tunnel calls. Any other ssh failure is not this one.
fly_tunnel_refused() {
  grep -qE "can't build tunnel|Not authorized to access this organization|Not authorized to read this organization's WireGuard peers" "${1:--}"
}

# fly_started_machine <app>: the id of the app's started Machine, or nothing.
fly_started_machine() {
  fly machine list -a "$1" --json | python3 -c 'import json,sys
ids=[m["id"] for m in json.load(sys.stdin) if m.get("state")=="started"]
print(ids[0] if ids else "")'
}

# fly_exec_sh <app> <timeout_s> <command string>: run the command string on the
# app's started Machine through `fly machine exec`, as `fly ssh console
# --command` would run it. Prints its output (stdout and stderr) and returns its
# exit status. The status travels as a last line the Machine prints itself, so
# it never depends on what flyctl's own exit code means; no such line (exec
# refused, the Machine unreachable, no started Machine) returns 255, which a
# caller reads as no verdict. The command travels base64-encoded, so no quoting
# layer can touch it.
fly_exec_sh() {
  local app="$1" secs="$2" machine encoded out status
  machine="$(fly_started_machine "${app}" 2>/dev/null || true)"
  if [ -z "${machine}" ]; then
    echo "fly-transport: no started machine on ${app}"
    return 255
  fi
  encoded="$(printf '%s' "$3" | base64 | tr -d '\n')"
  out="$(fly machine exec "${machine}" -a "${app}" --timeout "${secs}" \
    "sh -c '( eval \"\$(echo ${encoded} | base64 -d)\" ); echo SMD_EXEC_STATUS=\$?'" 2>&1 || true)"
  status="$(printf '%s\n' "${out}" | sed -n 's/^SMD_EXEC_STATUS=\([0-9][0-9]*\)$/\1/p' | tail -1)"
  printf '%s\n' "${out}" | grep -v '^SMD_EXEC_STATUS=' || true
  [ -n "${status}" ] || return 255
  return "${status}"
}
