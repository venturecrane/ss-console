#!/usr/bin/env bash
# seat-probe.sh — the blessed way to run a probe command on an Operator seat.
#
# WHY THIS EXISTS (2026-07-24 incident): `fly ssh console` lands you as ROOT.
# A probe `hermes cron run` executed as root on 2026-07-16 rewrote
# profiles/operator/cron/jobs.json root-owned 0600 — the hermes-uid scheduler
# could not read its own job DB and NOTHING fired for 8 days while the machine
# stayed green. Two traps, both closed here:
#
#   1. NEVER run file-mutating commands as root in the container. This wrapper
#      always drops to the hermes uid via runuser.
#   2. `hermes cron run` (and most gateway-adjacent commands) need the GATEWAY
#      process env (persona resolution, keys). The gateway pid MUST be resolved
#      inline in the same remote shell — a pre-resolved/stale pid yields an
#      empty env and a refused turn (persona=(none)).
#
# Usage:
#   operator/bin/seat-probe.sh <slug> <command...>
#
# Examples:
#   operator/bin/seat-probe.sh pilot-smokeball hermes -p operator cron list
#   operator/bin/seat-probe.sh pilot-smokeball hermes -p operator cron create "2m" "<prompt>" --repeat 1 --name <name>
#
# The second example is the sanctioned way to get a turn out of a seat: it
# schedules the turn on the gateway that is already running. Never `hermes
# -p <profile> -z ...`, `hermes chat`, or `hermes cron run <id>` through
# here: each starts a SECOND hermes runtime beside the live gateway on a
# 1 vCPU / 1GB Machine (operator/CLAUDE.md, the one-shot rule; the
# 2026-09-01 crash-loop incident). `-p operator` is load-bearing on cron
# commands: without it the job lands in a store the gateway never reads.
#
# The command runs as the hermes user with the live gateway env. Read-only
# inspection needs no env and also works fine through here — there is no
# reason to ever use a bare `fly ssh console -C` for seat probes.
set -euo pipefail

SLUG="${1:-}"
shift || true
[ -n "${SLUG}" ] && [ "$#" -ge 1 ] || {
  echo "Usage: $0 <customer-slug> <command...>" >&2
  exit 1
}
if [[ ! "${SLUG}" =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]]; then
  echo "invalid slug '${SLUG}' (must match ^[a-z0-9][a-z0-9-]{0,31}$)" >&2
  exit 1
fi
APP_NAME="hermes-${SLUG}"

# Quote the argv safely for transport through the remote sh -c.
QUOTED=""
for arg in "$@"; do
  QUOTED+=" $(printf '%q' "${arg}")"
done

# Resolve the gateway pid INLINE on the seat (never pre-resolved — stale pid =
# empty env = refused turn), export its env, drop to hermes, run the command.
#
# NEVER run `pgrep -a`, `pgrep -af`, `ps e`, or `ps auxe` THROUGH this wrapper.
# ss#2218 (P1, 2026-08-10): a probe used `pgrep -af establish_intake`; because
# the line below re-execs the command as `runuser -- env ${ENVV} ...`, the
# wrapper's OWN process matched the pattern, and `-a` printed its full argv —
# which is the gateway environment, secret VALUES included — into a session
# transcript. The exposure was ANTHROPIC_API_KEY, the Smokeball client id and
# secret, and more.
#
# The env is on this process's command line BY DESIGN; that is how the probe
# reaches the seat with the gateway's credentials. So any flag that prints a
# command line is an exfiltration primitive here, not a debugging convenience.
# Match on a pattern that cannot match this wrapper (as the gateway resolve
# below does), and print pids only — never `-a`, never `-f` with output.
#
# The SEND credentials are stripped TWICE below, because they arrive by two
# independent paths and removing either one alone leaves the leak intact:
#
#   1. `grep -vE` on the ENVV build, so the gateway's copy of them never lands
#      on this wrapper's argv (where any `ps`-shaped command would print them).
#   2. `env -u` on the exec, because `fly ssh` inherits the Machine's PID 1
#      environment and `env` starts from the CALLER's environment. The hallpass
#      session is already holding them before ENVV is assembled at all.
#
# Measured on ashton-price 2026-09-22: with only the grep in place, a probe
# launched through this wrapper still reported all three MSGRAPH_SEND_* present
# in its own environ. The `-u` flags must come BEFORE ${ENVV}, since a later
# assignment would win over an earlier -u.
#
# entrypoint.sh unsets them before its own exec-drop, but an unset cannot reach
# a process launched later by a different path, and this wrapper is that path.
# The result was the one credential holding Mail.Send on a seat with Send As for
# real staff, sitting in an environ any same-uid sibling can read (ADR 0044
# Decision 8). Boot smoke checks for exactly this
# (`msgraph-send-credential-stripped-from-agent`), and a probe that needs to
# send should run as a gateway turn, not as a hermes-uid one-shot.
exec fly ssh console -a "${APP_NAME}" -C "sh -c '
GPID=\$(pgrep -f \"hermes.*gateway run\" | head -1)
if [ -z \"\${GPID}\" ]; then
  echo \"seat-probe: no gateway process found on ${APP_NAME}\" >&2
  exit 1
fi
ENVV=\$(tr \"\\0\" \"\\n\" < /proc/\${GPID}/environ | grep -vE \"^(PWD|SHLVL|_|MSGRAPH_SEND_TENANT_ID|MSGRAPH_SEND_CLIENT_ID|MSGRAPH_SEND_CLIENT_SECRET|AGENTMAIL_SEND_API_KEY|AGENTMAIL_WEBHOOK_READ_API_KEY)=\" | tr \"\\n\" \" \")
exec runuser -u hermes -- env -u MSGRAPH_SEND_TENANT_ID -u MSGRAPH_SEND_CLIENT_ID -u MSGRAPH_SEND_CLIENT_SECRET -u AGENTMAIL_SEND_API_KEY -u AGENTMAIL_WEBHOOK_READ_API_KEY \${ENVV} PATH=/opt/hermes/.venv/bin:/usr/local/bin:/usr/bin:/bin ${QUOTED}
'"
