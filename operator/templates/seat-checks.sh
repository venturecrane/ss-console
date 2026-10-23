# shellcheck shell=bash
# seat-checks.sh: the ONE list of checks a seat must pass, run by two transports.
#
#   operator/bin/boot-smoke-test.sh   the laptop: each check travels over
#                                     `fly ssh console` (unchanged behaviour)
#   /app/selftest.sh                  the image: each check runs in place, as
#                                     root, at boot and hourly, and the results
#                                     reach the heartbeat (selftest_json)
#
# WHY ONE LIST. The boot smoke used to be the only judge, and it ran only when a
# person ran it (a provision, a release). A seat that drifted between releases
# was found by whoever next looked. Two copies of the list would drift from each
# other the same way, so there is one list and each transport binds the verbs.
#
# THE VERBS a transport must define before calling `seat_checks`:
#   check <id> <cmd>              a shell command with NO single quotes
#   check_script <id> <program>   an sh program that may contain any quoting
#   check_idle <id> <cmd>         a lane-is-idle check: the image fails it at
#                                 boot and WARNS hourly (a job may be running)
#   check_vendor <id> <cmd>       asks a vendor: the image fails it at boot and
#                                 WARNS hourly (vendor weather must not page)
#   check_file <id> <interp> <f>  run probe operator/templates/<f> (= /app/<f>)
#   check_report <id> <program>   as check_script, and its stdout is the detail
#   check_lever <probe>           the identifier-gate lever verdict (warn-only)
#   check_note <id> <text>        a check that is skipped by design: a pass
#                                 carrying why
#   check_broken <id> <text>      a check that cannot be evaluated: a failure
#   lane_authored <lane>          does this seat author medchron | demand |
#                                 drafting | litigation (the expectation source)
# and the variables SEAT_ROOT (empty on a seat; a fixture tree in the tests),
# EXPECTED_HERMES_SHA and APP_NAME.
#
# A check's command is a STRING composed here and evaluated on the Machine, so
# `\$` defers expansion to the Machine and `${SEAT_ROOT}` is expanded where the
# list runs (empty on both transports in production).

# seat_prog: an sh program read from stdin, prefixed with R=<SEAT_ROOT> so a
# quoted heredoc can name seat paths as "$R/...".
seat_prog() {
  printf 'R=%s\n' "${SEAT_ROOT}"
  cat
}

# shellcheck disable=SC2016 # the check strings are evaluated on the Machine, not here
seat_checks() {
local _lane

# ---------- Steps 2-4: Postgres / Redis / Honcho — DEFERRED (Phase 2) ----------
# The Honcho data plane is deferred to Phase 2 (ADR 0016 revised); Phase 1 boots
# on Hermes' flat-file memory core, so there is no Postgres/Redis/Honcho to
# probe here. These checks return when Phase 2 vendors the real Honcho source.

# ---------- Step 5: customer.yaml present, root-owned, agent-read-only (keystone) ----------
# The live customer.yaml is the source every trust-ceiling / vertical-floor / scope
# decision resolves against, read fresh per action. The 2026-06-15 keystone moved
# it OFF the agent-writable /opt/data volume into the root-owned /var/lib/smd-config
# so the hermes uid can READ it (the trust gate must) but can NEITHER rewrite it
# (it owned the file before — proven exploitable: one sed flipped external_send
# draft_for_review->autonomous) NOR rename it (it owned the parent dir). These are
# the negative-fire conformance proof of that close (SEC-07/08/09/18/30, EFF-14).
check "customer-yaml-present" "test -s ${SEAT_ROOT}/var/lib/smd-config/customer.yaml"
check "customer-yaml-root-owned" "[ \"\$(stat -c %U ${SEAT_ROOT}/var/lib/smd-config/customer.yaml)\" = root ]"
check "customer-yaml-dir-root-owned" "[ \"\$(stat -c %U ${SEAT_ROOT}/var/lib/smd-config)\" = root ]"
check "customer-yaml-agent-readable" "setpriv --reuid=hermes --regid=hermes --init-groups test -r ${SEAT_ROOT}/var/lib/smd-config/customer.yaml"
check "customer-yaml-not-agent-writable" "setpriv --reuid=hermes --regid=hermes --init-groups sh -c \"! test -w ${SEAT_ROOT}/var/lib/smd-config/customer.yaml\""
check "customer-yaml-dir-not-agent-writable" "setpriv --reuid=hermes --regid=hermes --init-groups sh -c \"! test -w ${SEAT_ROOT}/var/lib/smd-config\""
check "customer-yaml-absent-from-agent-volume" "! test -e ${SEAT_ROOT}/opt/data/customer.yaml"

# ---------- Step 6: Hermes profiles directory exists ----------
# bootstrap.sh's `hermes-smd bootstrap` step materializes one profile per
# entry in customer.yaml.personas[]. v1 customers ship at length 1; we just
# verify the parent directory exists and is non-empty.
check "hermes-profiles-dir" "test -d ${SEAT_ROOT}/opt/data/profiles && [ -n \"\$(ls -A ${SEAT_ROOT}/opt/data/profiles)\" ]"

# ---------- Step 6b: no unauthored profile homes ----------
# The on-volume profile set must EQUAL the authored persona set — not merely
# contain it. A persona slug rename once left the retired slug's home (and
# its frozen cron store) on the volume for 12 days until the scheduler
# monitoring paged on a store nothing serves (#2009). translate now deletes
# orphans (overlay#185); this check proves it held on THIS boot, so any
# future orphan class in profiles/ fails the smoke test instead of lurking.
# Dot-prefixed entries and plain files are exempt, matching the reconciler.
# QUOTING CONSTRAINT: ssh_exec wraps the command in sh -c '...' — the check
# must contain NO single quotes and NO newlines (the first cut of this step
# used both and never parsed on the Machine; the volume was clean, the check
# was broken). Python strings below are double-quoted only, one line.
check "no-unauthored-profile-homes" "${SEAT_ROOT}/opt/hermes/.venv/bin/python3 -c \"import sys, yaml, pathlib; a = {p[\\\"slug\\\"] for p in (yaml.safe_load(open(\\\"${SEAT_ROOT}/var/lib/smd-config/customer.yaml\\\")) or {}).get(\\\"personas\\\", [])}; d = {e.name for e in pathlib.Path(\\\"${SEAT_ROOT}/opt/data/profiles\\\").iterdir() if e.is_dir() and not e.name.startswith(\\\".\\\")}; drift = sorted(d - a) + sorted(a - d); sys.stderr.write(f\\\"profile-home drift: orphans={sorted(d - a)} missing={sorted(a - d)}\\n\\\") if drift else None; sys.exit(1 if drift else 0)\""

# ---------- Step 6c: retired repo skills absent from the volume ----------
# The skill seed in bootstrap.sh is additive, so a skill deleted from the repo
# keeps its old copy on /opt/data/skills unless bootstrap prunes it by name.
# operator/templates/retired-skills.txt is the one home for those names, and the
# image carries it at /app/retired-skills.txt. The list is read ON THE MACHINE,
# so the laptop smoke and the in-image self-test judge one list, and an image
# whose list is missing or empty FAILS here instead of passing on an empty
# list. A dangling alias counts as present.
check_script "retired-skills-absent" "$(seat_prog <<'RETIRED_SKILLS_CHECK'
f="$R/app/retired-skills.txt"
names="$(grep -vE "^[[:space:]]*(#|$)" "$f" 2>/dev/null)"
[ -n "$names" ] || { echo "no retired skill name readable from $f" >&2; exit 1; }
for n in $names; do
  if test -e "$R/opt/data/skills/$n" || test -L "$R/opt/data/skills/$n"; then
    echo "retired skill still on volume: $n" >&2
    exit 1
  fi
done
RETIRED_SKILLS_CHECK
)"

# ---------- Step 6d: staged scripts current ----------
# Hermes' scheduler runs a cron pre_run only from <profile>/scripts/<skill>/,
# so the overlay stages a copy of the skill's script there. It stages only for
# skills with a live cron, so a skill whose cron was removed keeps its old
# staged copy, and a later cron (or a one-shot run) executes stale code while
# every skill copy is current. On 2026-09-29 pilot-smokeball ran a 749-line
# motion-calendar pre_run.py (pre-#3003) beside an 892-line skill copy, and six
# unscheduled trackers were stale: the wake line carried no facts_digest and no
# check noticed. Every staged script must be byte-identical (sha256) to its skill
# copy: <profile>/skills/<skill>/<file>, or /opt/data/skills/<skill>/<file> when
# the profile has none. A staged script with no skill copy at all fails too.
#
# The program runs through base64 (the ssh_exec_script shape) and its output is
# CAPTURED, not discarded, so a failure names every stale path.
# SMD_SMOKE_DATA_ROOT is unset on a seat; the tests point it at a fixture tree.
STAGED_SCRIPTS_CHECK="$(cat <<'STAGED_SCRIPTS_CHECK'
# staged-scripts-current
root="${SMD_SMOKE_DATA_ROOT:-/opt/data}"
if command -v sha256sum >/dev/null 2>&1; then
  digest() { sha256sum "$1" | cut -d " " -f 1; }
else
  digest() { shasum -a 256 "$1" | cut -d " " -f 1; }
fi
bad=0
checked=0
for staged in "$root"/profiles/*/scripts/*/*.py; do
  [ -f "$staged" ] || continue
  checked=$((checked + 1))
  skill_dir="$(dirname "$staged")"
  skill="$(basename "$skill_dir")"
  profile="$(dirname "$(dirname "$skill_dir")")"
  file="$(basename "$staged")"
  src="$profile/skills/$skill/$file"
  [ -f "$src" ] || src="$root/skills/$skill/$file"
  if [ ! -f "$src" ]; then
    echo "stale: $staged has no skill copy"
    bad=1
  elif [ "$(digest "$staged")" != "$(digest "$src")" ]; then
    echo "stale: $staged differs from $src"
    bad=1
  fi
done
echo "checked: $checked staged script(s)"
exit "$bad"
STAGED_SCRIPTS_CHECK
)"
check_report "staged-scripts-current" "${STAGED_SCRIPTS_CHECK}"

# ---------- Step 6e: omitted repo skills absent from the volume ----------
# bootstrap.sh prunes every repo skill (one under /app/skills) that the live
# customer.yaml does not enable from /opt/data/skills, so an omitted lane cannot
# be run by reading its files (2026-10-06). This asserts the convergence held:
# no omitted repo skill is on the volume. A config that enables NO skill fails
# here rather than passing on an empty set.
check_script "omitted-skills-absent" "${SEAT_ROOT}/opt/hermes/.venv/bin/python3 -c '
import os, sys, yaml
p = os.environ.get(\"SMD_CUSTOMER_YAML_PATH\") or next((c for c in (\"${SEAT_ROOT}/var/lib/smd-config/customer.yaml\", \"${SEAT_ROOT}/opt/data/customer.yaml\") if os.path.exists(c)), \"\")
doc = yaml.safe_load(open(p)) or {}
on = {s[\"name\"] for q in doc.get(\"personas\") or [] for s in (q or {}).get(\"skills\") or [] if isinstance(s, dict) and s.get(\"enabled\") is not False and isinstance(s.get(\"name\"), str)}
if not on:
    sys.exit(\"no enabled skill read from customer.yaml\")
left = sorted(n for n in os.listdir(\"${SEAT_ROOT}/app/skills\") if n not in on and os.path.lexists(os.path.join(\"${SEAT_ROOT}/opt/data/skills\", n)))
sys.exit(\"omitted skills on the volume: \" + \" \".join(left) if left else 0)
'"

# ---------- Step 7: overlay plugins installed ----------
# bootstrap.sh copies the image's overlay pack into the root Hermes home and
# the active profile's home and enables it in both. This reads that result back
# from the volume: every overlay copy carries the fan-out __init__.py and a
# plugin.yaml that parses to a mapping, and at least one profile carries one.
# It does NOT run `hermes plugins list`: that starts a second Hermes CLI beside
# the live gateway, which the one-shot rule (operator/CLAUDE.md) forbids, and
# the in-image self-test runs this list every hour.
check_script "hermes-plugins-installed" "$(seat_prog <<'PLUGINS_CHECK'
"$R/opt/hermes/.venv/bin/python3" - "$R/opt/data" <<'PY'
import glob, sys, yaml
root = sys.argv[1]
dirs = [root + "/plugins/hermes-smd-overlay"] + sorted(glob.glob(root + "/profiles/*/plugins/hermes-smd-overlay"))
if len(dirs) < 2:
    sys.exit("no profile carries the overlay plugin")
for d in dirs:
    try:
        open(d + "/__init__.py").close()
        doc = yaml.safe_load(open(d + "/plugin.yaml"))
    except (OSError, yaml.YAMLError) as exc:
        sys.exit(f"{d}: {exc.__class__.__name__}")
    if not isinstance(doc, dict):
        sys.exit(f"{d}/plugin.yaml is not a mapping")
PY
PLUGINS_CHECK
)"

# ---------- Step 7b: the running Hermes IS the pinned Hermes ----------
# Hermes v0.18.0 -> v0.20.4 promotion (ss-console#2444). Every check above passes
# identically on 0.18 and 0.20, so until now boot-smoke could not tell a promoted
# seat from one a peer quietly rebuilt at the old pin from origin/main. The
# image bakes the cloned upstream commit at /opt/hermes/HERMES_SHA (Dockerfile
# hermes_source stage). The pin it is compared to is the Dockerfile's own
# `ARG HERMES_UPSTREAM_SHA` default (ADR 0024, amended 2026-10-19: one image for
# every seat, so customer.yaml no longer carries a per-seat hermes_ref), read
# by the binding as EXPECTED_HERMES_SHA: the laptop smoke reads the release's
# pin (BOOT_SMOKE_EXPECTED_HERMES_SHA) or this checkout's Dockerfile, and the
# in-image self-test reads the image's own SMD_HERMES_UPSTREAM_SHA (the fleet
# then compares the self-test's reported hermes_sha with the release's). A
# mismatch is the one failure that means "the wrong Hermes is running", so it
# is a FAIL, not a log.
if printf '%s' "${EXPECTED_HERMES_SHA:-}" | grep -qE '^[0-9a-f]{40}$'; then
  check "hermes-sha-matches-pin" "[ \"\$(tr -d \"[:space:]\" < ${SEAT_ROOT}/opt/hermes/HERMES_SHA)\" = ${EXPECTED_HERMES_SHA} ]"
else
  check_broken "hermes-sha-matches-pin" "no 40-hex expected Hermes pin to compare against"
fi

# ---------- Step 8: Hermes curator disabled (ADR 0017) ----------
# The autonomous curator is turned off per-customer (it rewrites agent-authored
# skills out of band — see docs/adr/0017). bootstrap.sh step 7b enforces
# curator.enabled:false in every profile config; --check re-verifies it held.
check "curator-disabled" "${SEAT_ROOT}/opt/hermes/.venv/bin/python3 ${SEAT_ROOT}/app/ensure-curator-disabled.py --check ${SEAT_ROOT}/opt/data"

# ---------- Step 8b: customer-disabled bundled skills stayed off the menu (#1198) ----------
# customer.yaml personas[].skills_disabled is the per-customer authority over
# Hermes' bundled catalog (e.g. google-workspace + himalaya, which the DWD-broker
# model replaces — ADR 0045). bootstrap step 7b.1 prunes them from the profile
# skill tree + prompt snapshot, and a startup reconciler re-prunes after Hermes'
# gateway sync. This --check FAILS the boot if any disabled skill reappeared —
# the fail-closed guard against a Hermes-upgrade rehydration regression (a
# re-exposed google-workspace skill is a governance-bypass path back to the raw
# credential, exactly what ADR 0045 closes). No-op when no skills_disabled authored.
check "disabled-skills-pruned" "${SEAT_ROOT}/opt/hermes/.venv/bin/python3 ${SEAT_ROOT}/app/ensure-disabled-skills.py --check ${SEAT_ROOT}/var/lib/smd-config/customer.yaml ${SEAT_ROOT}/opt/data"

# ---------- Step 8c: the matter-mixing READ fence discriminates (ss#2167) ----------
# The fence refuses a session holding one matter's substance from reading a
# second matter's. It is what stops a draft containing two clients' facts from
# ever being COMPOSED — every other matter control fires when a send is
# attempted, by which point that draft exists and is in a paralegal's queue, and
# the firm finding it there is the event the engagement does not survive whether
# or not it was sent.
#
# The probe asserts BOTH directions on purpose: a second matter is refused, AND
# the same matter is still readable. Asserting only the refusal would pass
# against a fence that refused every content read — not a safe fence, a bricked
# Operator the firm switches off. It also fails the boot if the overlay pin
# predates ss#2167, which is the cross-repo drift this catches.
check "matter-mixing-fence" "${SEAT_ROOT}/opt/hermes/.venv/bin/python3 ${SEAT_ROOT}/app/matter-mixing-fence-probe.py"

# ---------- Step 9: audit ledger is broker-owned and NOT agent-writable (OP-P1-4) ----------
# The immutable ledger must be owned by the broker uid (workspace-broker), the
# dir setgid 2750, and the agent uid (hermes) must be physically unable to write
# it. The probe (run as hermes) exits 0 only when both DELETE and INSERT are
# refused — the affirmative tamper-resistance proof.
check "audit-db-owner-is-broker" "[ \"\$(stat -c %U ${SEAT_ROOT}/opt/data/audit/audit.db)\" = workspace-broker ]"
check "audit-dir-setgid-2750" "[ \"\$(stat -c %a ${SEAT_ROOT}/opt/data/audit)\" = 2750 ]"
check "audit-db-not-agent-writable" "setpriv --reuid=hermes --regid=hermes --init-groups ${SEAT_ROOT}/opt/hermes/.venv/bin/python3 ${SEAT_ROOT}/app/audit-write-fail-probe.py ${SEAT_ROOT}/opt/data/audit/audit.db"

# ---------- Step 10: mutable agent state stayed hermes-writable (regression for the split) ----------
# agent_skills_inventory moved off the ledger onto a hermes-owned file so locking
# the ledger does not break skill capture. The overlay's register() creates it.
check "agent-state-owner-is-hermes" "[ \"\$(stat -c %U ${SEAT_ROOT}/opt/data/agent-state.db)\" = hermes ]"

# ---------- Step 11: the broker is supervised by a ROOT respawner (OP-P1-4 follow-up) ----------
# The broker must be respawnable on mid-run death, which requires a root parent
# (only root can re-setpriv to uid workspace-broker). With the supervisor, the
# broker's parent is the root subshell loop; without it the broker is a direct
# child of PID 1 (the hermes gateway after the exec-drop). So "broker's parent
# proc dir is root-owned" is a non-destructive proof the supervisor is in place.
# NB: read PPid from /proc/<pid>/status with grep+tr (NO single quotes) — ssh_exec
# wraps this whole string in `sh -c '...'`, so a single-quoted `awk '{print $4}'`
# would collide with the outer quote and mangle the command.
check "broker-respawn-supervised" "pid=\$(pgrep -f workspace_broker.server | head -1); [ -n \"\$pid\" ] && ppid=\$(grep -m1 PPid /proc/\$pid/status | tr -dc 0-9) && [ \"\$(stat -c %U /proc/\$ppid)\" = root ]"

# The check above proves the broker can be RESTARTED. It never opens the socket,
# so it cannot tell a working broker from one whose socket is gone, whose parent
# directory lost its setgid bit, or which is refusing every connection. A seat in
# that state has no document surface at all and reports healthy on every signal
# we watch — the same shape as the 2026-07-16 scheduler outage, which ran eight
# days green because the check confirmed a process EXISTED rather than that it
# WORKED.
#
# `health` is the right call to make: it sits ABOVE the broker's gateway-PID gate
# (workspace_broker/server.py:282 vs :291), so any process may issue it, and it
# reports whether the credential, customer, jobs and audit stores actually
# loaded. Run as the AGENT uid, because "hermes can reach this socket" is the
# property that matters — root reaching it proves nothing about the caller that
# needs it.
#
# SCOPE IT HONESTLY: this proves the socket answers and the stores loaded. It
# CANNOT prove the gateway can authorize a privileged verb, because only the
# gateway process may make that call and this is not it. Claiming otherwise
# would rebuild the blind spot one layer up.
# The socket path is taken from the canonical default rather than the env, and
# that is not laziness: a fresh `fly ssh console` session inherits NONE of the
# app's environment (verified — SMD_WORKSPACE_BROKER_SOCKET is unset there),
# while the entrypoint sets both the env var and this path from the same
# constant. Reading the env here would make the check fail on every seat for a
# reason that has nothing to do with the broker, which is worse than a path that
# can drift. Same idiom as every other absolute path in this file. If the
# entrypoint's SOCKET_PATH ever moves, this line moves with it.
check_script "broker-socket-answers-health" "setpriv --reuid=hermes --regid=hermes --init-groups ${SEAT_ROOT}/opt/hermes/.venv/bin/python3 -c \"import socket,os,json,sys; p=os.environ.get('SMD_WORKSPACE_BROKER_SOCKET') or '${SEAT_ROOT}/run/smd-workspace-broker/broker.sock'; s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); s.settimeout(5); s.connect(p); s.sendall(b'{\\\"action\\\":\\\"health\\\"}'+bytes([10])); r=json.loads(s.recv(65536).decode()); sys.exit(0 if r.get('ok') and r.get('credential_ready') and r.get('customer_ready') else 1)\""

# ---------- Step 11c: the GATEWAY is supervised, and its loop is beating (P0 ss#2488) ----------
# On 2026-08-20 the paying client's seat wedged for 33 minutes: Hermes' own
# loop-liveness watchdog logged that it was exiting so a supervisor could restart
# it, then did not exit, and nothing at any layer noticed. entrypoint.sh EXECS the
# gateway as the container's main process, the respawner above covers the BROKER,
# and Fly does not restart a Machine on a failing health check.
#
# Deliberately NOT a pid check. The broker note above already says why: a check
# that "confirmed a process EXISTED rather than that it WORKED" is how the
# 2026-07-16 scheduler outage ran eight days green. The supervisor touches
# ${RUN_DIR}/tick at the top of EVERY iteration, so tick freshness fails if the
# loop stops turning for any reason — including the one that would otherwise be
# invisible, an inherited `set -e` killing the subshell on its first failed probe.
check "gateway-liveness-supervisor-ticking" "t=${SEAT_ROOT}/run/smd-gateway-liveness/tick; [ -f \$t ] && [ \$(( \$(date -u +%s) - \$(stat -c %Y \$t) )) -lt 90 ]"

# Ticking proves the loop turns; it does not prove the loop ever found the
# heartbeat and ARMED. A supervisor stuck on `inert` (argv unresolvable) or
# `not-armed` (it has never seen a fresh beat) ticks identically to a healthy
# one and would kill nothing. The state file is one word per transition, written
# for exactly this question and for the gate to ship on the heartbeat (part 2).
# Bounded wait: arming happens on the first poll AFTER the gateway's first beat,
# which on 1 vCPU can land a minute or more into boot. The failing branch prints
# the state it did see, so a red run says WHICH wrong state, not just "failed".
check "gateway-liveness-supervisor-armed" "n=0; while [ \$n -lt 36 ]; do s=\$(head -c 16 ${SEAT_ROOT}/run/smd-gateway-liveness/state 2>/dev/null); [ \"\$s\" = armed ] && exit 0; n=\$((n+1)); sleep 5; done; echo supervisor-state=\$s >&2; exit 1"

# Part 2 ships these three files on the heartbeat FROM THE GATE, which is the
# hermes uid. Run the read AS hermes, because "root can read it" proves nothing
# about the only process that needs to. Found live on hermes-scott 2026-08-21: a
# ledger written under the first cut's umask was 0640, the gate got Permission
# denied, and the field went absent (a hold) with every root-side check green.
# An absent ledger FILE passes (first boot, no kills yet); a present-but-unreadable
# one fails. tick and state must exist by this point (the two checks above).
check "gateway-liveness-files-readable-by-gate" "setpriv --reuid=hermes --regid=hermes --init-groups sh -c \"head -c 1 ${SEAT_ROOT}/run/smd-gateway-liveness/state >/dev/null && head -c 0 ${SEAT_ROOT}/run/smd-gateway-liveness/tick && { [ ! -e ${SEAT_ROOT}/opt/data/.smd-root/gateway-liveness/kills ] || head -c 0 ${SEAT_ROOT}/opt/data/.smd-root/gateway-liveness/kills; }\""

# The supervisor is only as good as the signal it reads, and that signal is
# UPSTREAM's: an asyncio task on the gateway loop rewrites this file every 30s.
# This check is the tripwire for the whole mechanism rotting silently. The path
# is not where the Hermes source implies — _process_hermes_home() reads
# HERMES_HOME (/opt/data), but the file lands under the PROFILE home, which is
# why the supervisor derives it from the gateway's argv. If a future pin drops
# the heartbeat, moves it again, or disables it via `gateway.loop_watchdog`, the
# supervisor would go quietly inert; this fails the provision instead.
#
# Bounded wait rather than a single shot: boot smoke is fail-fast and on a 1 vCPU
# box the first beat can land well after this step runs. Same idea as the
# --wait-gateway-s flag the R2 strip probe below uses.
check "gateway-loop-heartbeat-fresh" "n=0; while [ \$n -lt 36 ]; do for f in ${SEAT_ROOT}/opt/data/profiles/*/state/gateway.heartbeat; do [ -f \$f ] || continue; [ \$(( \$(date -u +%s) - \$(stat -c %Y \$f) )) -lt 120 ] && exit 0; done; n=\$((n+1)); sleep 5; done; exit 1"

# ---------- Step 11d: the loop-watchdog budget is sized to this seat (#2789) ----------
# Hermes arms its event-loop liveness watchdog before the startup plugin crawl
# and exits 75 after max_strikes missed probes. At the shipped defaults that is
# about two minutes; a 1 GB seat's synchronous imports take about four, which is
# the 2026-09-01 pilot crash loop (docs/runbooks/operator/incidents/). From Hermes
# v2026.9.14 the budget is config, and the overlay's translate writes
# gateway.loop_watchdog_* into every profile's config.yaml from machine.memory_mb
# (<= 1024 MB: 12 strikes; larger: 6). This reads the rendered file back against
# the authored size, so a seat that would crash-loop on a cold start fails the
# provision here instead of paging at 02:00. Inert on an older Hermes pin, which
# ignores the keys; the read-back is still what proves translate wrote them.
# (No single quotes: ssh_exec wraps the command in sh -c '...'.)
check "gateway-loop-watchdog-budget-authored" "${SEAT_ROOT}/opt/hermes/.venv/bin/python3 -c \"import sys, yaml, pathlib; c = yaml.safe_load(open(\\\"${SEAT_ROOT}/var/lib/smd-config/customer.yaml\\\")) or {}; mb = int((c.get(\\\"machine\\\") or {}).get(\\\"memory_mb\\\") or 0); want = 12 if mb <= 1024 else 6; got = {str(p): ((yaml.safe_load(open(p)) or {}).get(\\\"gateway\\\") or {}).get(\\\"loop_watchdog_max_strikes\\\") for p in pathlib.Path(\\\"${SEAT_ROOT}/opt/data/profiles\\\").glob(\\\"*/config.yaml\\\")}; bad = {k: v for k, v in got.items() if v != want}; sys.stderr.write(f\\\"loop-watchdog budget drift: want max_strikes={want} for {mb} MB, got {bad}\\n\\\") if (bad or not got) else None; sys.exit(1 if (bad or not got) else 0)\""

# ---------- Step 12: /app governance artifacts are root-owned, not agent-writable (SEC-31) ----------
# The activation-gate source the gateway:startup hook force-loads
# (/app/overlay-pack, incl. hooks/smd-overlay-activation/handler.py) must be owned
# by root so a code-executing agent cannot rewrite its own governance self-check.
# bootstrap only READS it (copies to the volume; the gateway loads from there), so
# root ownership is functionally inert. /app is image-backed and resets each boot,
# but the agent's write path is removed regardless — defense in depth.
check "overlay-pack-root-owned" "[ \"\$(stat -c %U ${SEAT_ROOT}/app/overlay-pack)\" = root ]"
check "overlay-pack-not-agent-writable" "setpriv --reuid=hermes --regid=hermes --init-groups sh -c \"! test -w ${SEAT_ROOT}/app/overlay-pack\""
check "activation-handler-not-agent-writable" "setpriv --reuid=hermes --regid=hermes --init-groups sh -c \"! test -w ${SEAT_ROOT}/app/overlay-pack/hooks/smd-overlay-activation/handler.py\""

# ---------- Step 13: the account-wide R2 key is stripped from the LIVE agent (OP-P2-1) ----------
# bootstrap.sh unsets R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY before forking any
# same-uid child or exec'ing the gateway, so a code-executing agent cannot read
# the account-wide R2 key (from its own env or a sibling's /proc/<pid>/environ)
# and rewrite the R2 config object — the loopback that would walk around the
# keystone's filesystem lock (ADR 0044 Decision 8). test_deploy_ordering.py
# proves the SOURCE strips it in the right order; this proves it MATERIALIZED on
# the running Machine. Runs as root (reads agent-uid /proc/environ); the probe
# excludes root processes (PID 1 + the config applier legitimately keep the key)
# and never echoes the value.
# --wait-gateway-s: on a cold boot this step lands ~75s after boot, before the
# gateway has spawned, and the probe's vacuous-zero fail-closed rule (exit 3)
# FATALed a healthy deploy (ss#2420, seen on both 2026-08-18 reprovisions). The
# wait retries ONLY the no-agent-process verdict; a real offender still fails
# the instant a scan sees it. Later strip probes need no wait — once this one
# passes, the gateway is up.
check "r2-account-key-stripped-from-agent" "${SEAT_ROOT}/opt/hermes/.venv/bin/python3 ${SEAT_ROOT}/app/r2-account-key-strip-probe.py --wait-gateway-s 120"

# ss#2258, the same proof for the AgentMail SEND credential. This is the one
# check that would have caught the incident class before a client ever saw it:
# four fabricated messages reached a real principal from a seat whose agent
# process held an org-wide, all-permission send key, and no static test can tell
# you what is in a running process's environ. The probe takes the var names as
# arguments, so this reuses it verbatim — no second implementation to drift.
#
# What makes this able to FAIL: if entrypoint.sh's `unset AGENTMAIL_SEND_API_KEY`
# is removed, reordered after the exec-drop, or never runs because the strip
# block moved, the gateway inherits the send key and this exits non-zero naming
# the pid. The value is never printed.
check "agentmail-send-key-stripped-from-agent" \
  "${SEAT_ROOT}/opt/hermes/.venv/bin/python3 ${SEAT_ROOT}/app/r2-account-key-strip-probe.py hermes AGENTMAIL_SEND_API_KEY"

# The webhook-READ key is the one AgentMail credential on the seat with ORG scope
# (2026-09-17). It exists so boot smoke can ask the vendor about this seat's
# webhook; it must never be reachable from the agent, where org scope would mean
# reach beyond this inbox. Same instrument, same failure mode: remove the unset in
# entrypoint.sh and this exits non-zero naming the pid, printing no value.
check "agentmail-webhook-read-key-stripped-from-agent" \
  "${SEAT_ROOT}/opt/hermes/.venv/bin/python3 ${SEAT_ROOT}/app/r2-account-key-strip-probe.py hermes AGENTMAIL_WEBHOOK_READ_API_KEY"

# The staged AgentMail webhook secret is the VENDOR's, for THIS seat's webhook.
#
# WHY (scott, 2026-09-15, vfy_01M2HXT17Q32RX6TCV5NVZA9D6): with no per-seat
# Infisical key the provisioner staged the global WEBHOOK_SECRET_AGENTMAIL, which
# was not the secret of the seat's own webhook. Every inbound email was rejected
# 401 at warning level while every check here passed. The probe reads the
# gateway's environ as root, asks the vendor for the webhook naming this seat's
# hostname, and compares signing secrets by value, printing hash prefixes only.
#
# The vendor lookup uses the ORG-scoped webhook_read key from PID 1's environ,
# NOT the agent's AgentMail key (2026-09-17, vfy_01M2RD3EH4GDBH5SN2K16B0RRH).
# The first version asked with the agent's key, which is inbox-scoped by design;
# AgentMail webhooks are org-level objects, so that key returns 403, and even a
# replacement minted on the same inbox WITH webhook_read lists zero webhooks.
# The check could not pass on a correctly-scoped seat — it reported a credential
# defect where none existed. Scope, not permission, was the missing thing.
#
# What makes it able to FAIL: stage the wrong secret and it exits 1 naming both
# hash prefixes; no vendor webhook for the host, or two, also exits 1; a seat
# carrying the secret with no key file exits 1 naming the path. A seat with no
# AgentMail channel passes vacuously and says so.
check_vendor "agentmail-webhook-secret-matches-vendor" \
  "${SEAT_ROOT}/opt/hermes/.venv/bin/python3 ${SEAT_ROOT}/app/agentmail-webhook-secret-probe.py ${APP_NAME}.fly.dev hermes"

# The same proof for the Graph SEND app credential (ss#2258 msgraph wave).
#
# READ WHAT THIS CHECK DOES AND DOES NOT COVER. It proves the BROKER'S copy never
# reaches the agent. It does NOT itself prove the agent cannot send —
# MSGRAPH_CLIENT_SECRET deliberately stays in the gateway (the delta poller and
# the msgraph-mail MCP server need it), and this probe never asks Microsoft what
# that credential is allowed to do.
#
# What makes the agent unable to send is upstream, at provisioning: since
# 2026-08-13 provision-customer.sh REFUSES a seat whose MSGRAPH_SEND_* is not a
# distinct app registration from the gateway's MSGRAPH_* (tests/msgraph-two-app
# -fence.test.ts drives the refusal arms). So on any seat that booted, the
# gateway's credential belongs to a read-only registration that Microsoft refuses
# at /sendMail with 403 ErrorAccessDenied — proven on the sandbox seat 2026-08-13,
# vfy_01KZXX523V6JNWEETG4PSZDQY3. This probe guards the other half: the one
# credential in the tenant that DOES hold Mail.Send is a credential the agent
# genuinely never has.
#
# What makes it able to FAIL: remove or reorder the `unset MSGRAPH_SEND_*` in
# entrypoint.sh on a seat where those are staged, and the gateway inherits them —
# non-zero, naming the pid, never the value. On a seat with no msgraph connector
# nothing is staged and this passes vacuously, which is the honest outcome for a
# credential that does not exist.
check "msgraph-send-credential-stripped-from-agent" \
  "${SEAT_ROOT}/opt/hermes/.venv/bin/python3 ${SEAT_ROOT}/app/r2-account-key-strip-probe.py hermes MSGRAPH_SEND_CLIENT_SECRET MSGRAPH_SEND_CLIENT_ID MSGRAPH_SEND_TENANT_ID"

# ADR 0089: the half the check above says it does NOT cover, asked of Microsoft,
# on the seats where it matters. A seat authoring scope.staff_send_as has had the
# firm grant its mailbox Send As on real people, so the agent's own Graph app
# (the read app) holding Mail.Send would let agent-run code send as a person with
# no approval and no row. The probe mints that app's token and reads its roles
# claim; Mail.Send present fails, no credential to ask with fails, and a seat
# with no staff_send_as passes vacuously and says so. Role names only in output.
check_vendor "msgraph-read-app-cannot-send-on-send-as-seat" \
  "${SEAT_ROOT}/opt/hermes/.venv/bin/python3 ${SEAT_ROOT}/app/msgraph-read-app-cannot-send-probe.py ${SEAT_ROOT}/var/lib/smd-config/customer.yaml hermes"

# ADR 0089 amendment 5a. The approve buttons in an approval email are signed
# one-decision links, and the broker keeps no Sent Items copy of that email so
# the only mailbox holding them is the approver's. That is worth nothing if the
# agent's read app can read the approver's mailbox, which it can unless the
# tenant scopes it with an ApplicationAccessPolicy. Ask Microsoft, about EVERY
# approver: the scope group may hold staff mailboxes the firm authored for
# reading, so one approver being out of it says nothing about the next.
check_vendor "msgraph-read-app-cannot-read-staff-mailbox" \
  "${SEAT_ROOT}/opt/hermes/.venv/bin/python3 ${SEAT_ROOT}/app/msgraph-read-app-cannot-read-staff-probe.py ${SEAT_ROOT}/var/lib/smd-config/customer.yaml hermes"

# ---------- Step 13b: the identifier-gate rollback lever is not set (ADR 0093) ----------
# SMD_IDENTIFIER_GATE_MODE=report turns OFF all identifier blocking on a seat,
# SSNs and case numbers included. ADR 0093 keeps it only as an incident-time,
# fleet-wide rollback; what a seat may send is authored as a role policy in
# operator/roles/<role>/role.yaml and reviewed as the seat x gate matrix. A
# lever left set on one seat is a loosening nobody reviewed (captain-mark,
# 2026-10), so the smoke names it on every run.
#
# WARN, not FAIL: during an incident the lever is the right tool and a red
# smoke would block the very reprovision that rolls back. The summary still
# prints it, so it cannot be forgotten once the incident is over.
#
# Reads every process environ as root and prints only whether the NAME is
# present, never the value (`grep -q`). A fresh ssh session, and the self-test
# (forked under env -i), inherit none of the app env (see step 11), so a match
# is the gateway or a sibling, not the checker. The probe prints a VERDICT
# (SMD_LEVER=set|unset); no verdict is never read as unset.
check_lever "for e in ${SEAT_ROOT}/proc/[0-9]*/environ; do if tr \"\\000\" \"\\n\" 2>/dev/null < \$e | grep -q ^SMD_IDENTIFIER_GATE_MODE=; then echo SMD_LEVER=set; exit 0; fi; done; echo SMD_LEVER=unset"

# ---------- Step 14: the chronology runner is present, idle, capped, and fenced (ss#2614) ----------
# The runner daemon is a root process that spends the firm's model budget on
# its own schedule, so the checks here are the ones that would otherwise be
# invisible: that its loop turns (tick freshness, the 11c idiom — not a pid),
# that it is NOT running a job on a fresh boot (the child pidfile the daemon
# writes for exactly this question; a `pgrep -f` would match its own sh -c
# wrapper), that the memory controller the cap depends on is really there
# (the heartbeat says which; `none` means jobs would run uncapped beside the
# gateway on a 4 GB guest, the #2465 shape), that the queue and job dirs
# carry the modes the trust argument rests on, and that the medchron uid can
# read the firm config but not write it.
#
# What makes these able to FAIL: stop the daemon and the tick ages past 90 s;
# run the image on a guest without cgroup v2 memory and memory_cap reads
# `none`; loosen an install -d mode in entrypoint.sh and the stat reads it;
# drop the chown of the firm config and the write test passes.
check "medchron-uid-exists" "id -u medchron >/dev/null"
check "medchron-daemon-ticking" "t=${SEAT_ROOT}/run/smd-medchron/tick; [ -f \$t ] && [ \$(( \$(date -u +%s) - \$(stat -c %Y \$t) )) -lt 90 ]"
check_idle "medchron-daemon-idle" "! test -f ${SEAT_ROOT}/run/smd-medchron/child.pid"
# cgroup2 on a unified guest, cgroup1 on Fly's hybrid layout (live-probed
# 2026-08-31: v2 is mounted bare at /sys/fs/cgroup/unified with no
# controllers; memory is on the v1 mount). `none` is the failure.
check "medchron-memory-cap-present" "grep -q memory_cap.:..cgroup ${SEAT_ROOT}/run/smd-medchron/heartbeat.json"
check "medchron-queue-root-owned" "[ \"\$(stat -c %U:%G:%a ${SEAT_ROOT}/run/smd-medchron/queue)\" = root:workspace-broker:770 ]"
check "medchron-jobs-dir-root-owned-child-traversable" "[ \"\$(stat -c %U:%G:%a ${SEAT_ROOT}/run/smd-medchron/jobs)\" = root:medchron:710 ]"
# The demand lane (2026-10-06): its own thread in the same daemon, its own tick,
# queue and job dirs. A lane thread that died leaves the chronology ticking and
# this tick aging, which is the only way that death would be seen.
check "demand-lane-ticking" "t=${SEAT_ROOT}/run/smd-medchron/demand-tick; [ -f \$t ] && [ \$(( \$(date -u +%s) - \$(stat -c %Y \$t) )) -lt 90 ]"
check_idle "demand-lane-idle" "! test -f ${SEAT_ROOT}/run/smd-medchron/demand-child.pid"
check "demand-queue-root-owned" "[ \"\$(stat -c %U:%G:%a ${SEAT_ROOT}/run/smd-medchron/demand-queue)\" = root:workspace-broker:770 ]"
check "demand-jobs-dir-root-owned-child-traversable" "[ \"\$(stat -c %U:%G:%a ${SEAT_ROOT}/run/smd-medchron/demand-jobs)\" = root:medchron:710 ]"
# The drafting lane (2026-10-07): the daemon's third thread, the same four checks.
# Every seat runs it (entrypoint-drafting.sh always makes the queue); a seat with
# no drafting inputs ticks and defers, so a silent tick is still a dead thread.
check "drafting-lane-ticking" "t=${SEAT_ROOT}/run/smd-medchron/drafting-tick; [ -f \$t ] && [ \$(( \$(date -u +%s) - \$(stat -c %Y \$t) )) -lt 90 ]"
check_idle "drafting-lane-idle" "! test -f ${SEAT_ROOT}/run/smd-medchron/drafting-child.pid"
check "drafting-queue-root-owned" "[ \"\$(stat -c %U:%G:%a ${SEAT_ROOT}/run/smd-medchron/drafting-queue)\" = root:workspace-broker:770 ]"
check "drafting-jobs-dir-root-owned-child-traversable" "[ \"\$(stat -c %U:%G:%a ${SEAT_ROOT}/run/smd-medchron/drafting-jobs)\" = root:medchron:710 ]"
# The litigation status lane (2026-10-07): the daemon's fourth thread, the same
# four checks plus its state dir (client facts: the child's alone, 0700) and the
# broker's copy of the firm config (present exactly when the runner's is, and
# group workspace-broker read-only, never writable by it).
check "litigation-lane-ticking" "t=${SEAT_ROOT}/run/smd-medchron/litigation-tick; [ -f \$t ] && [ \$(( \$(date -u +%s) - \$(stat -c %Y \$t) )) -lt 90 ]"
check_idle "litigation-lane-idle" "! test -f ${SEAT_ROOT}/run/smd-medchron/litigation-child.pid"
check "litigation-queue-root-owned" "[ \"\$(stat -c %U:%G:%a ${SEAT_ROOT}/run/smd-medchron/litigation-queue)\" = root:workspace-broker:770 ]"
check "litigation-jobs-dir-root-owned-child-traversable" "[ \"\$(stat -c %U:%G:%a ${SEAT_ROOT}/run/smd-medchron/litigation-jobs)\" = root:medchron:710 ]"
check "litigation-state-dir-child-only" "[ \"\$(stat -c %U:%G:%a ${SEAT_ROOT}/run/smd-medchron/litigation/state)\" = medchron:medchron:700 ]"
check "litigation-broker-config-matches-runner" "r=${SEAT_ROOT}/var/lib/smd-config/litigation/litigation-firm.yaml; b=${SEAT_ROOT}/var/lib/smd-config/litigation-broker-firm.yaml; if [ -f \$r ]; then [ \"\$(stat -c %U:%G:%a \$b)\" = root:workspace-broker:640 ] && cmp -s \$r \$b; else ! test -e \$b; fi"
# MEMORY BUDGET (2026-10-07). Every lane child runs under a memory cgroup cap,
# and the gateway runs under none, so the caps must leave the gateway room: the
# sum of every lane cgroup's limit must not exceed the authored machine memory
# minus a 512 MiB gateway reserve (the #2465 shape: a 1 GB guest went
# health-critical when a job and the gateway shared it). A cgroup a lane has
# not made yet (no job since boot) counts at the cap the daemon WILL write: the
# authored machine.lane_memory_mb value when the customer.yaml authors one
# (2026-10-13; the entrypoint exports it to the daemon through
# medchron.lane_memory, the same function counted here), else the code default
# read from the runner itself, so a bigger default fails here before the first
# job. An image without medchron.lane_memory exports nothing, so its lanes
# count at their defaults, which is what that daemon would write. The
# litigation and drafting lanes share the demand cgroup, so it counts once.
# A lane that CANNOT run a job on this seat counts zero: with no firm inputs
# staged it refuses or defers every job (provision-customer.sh steps 2b-2f), so
# its cgroup is never filled. Counting it anyway demanded a 4 GB guest of every
# seat, including a hosted agent that will never run a chronology (2026-10-13,
# the brokery seat failed this at 1 and 2 GB with no lane inputs at all). A
# cgroup that already exists has run a job and always counts at its live limit.
# FALSIFIER: raise a lane default (or an authored lane_memory_mb value) or
# shrink machine.memory_mb on a seat with that lane's inputs staged and this
# fails.
check_file "lane-memory-caps-leave-gateway-reserve" "${SEAT_ROOT}/opt/medchron/.venv/bin/python" "lane-memory-caps-probe.py"
# The negotiation watch (2026-10-09): its own daemon thread, the same checks,
# and its state dir (file cursors: the child's alone, 0700).
check "negotiation-lane-ticking" "t=${SEAT_ROOT}/run/smd-medchron/negotiation-tick; [ -f \$t ] && [ \$(( \$(date -u +%s) - \$(stat -c %Y \$t) )) -lt 90 ]"
check "negotiation-queue-root-owned" "[ \"\$(stat -c %U:%G:%a ${SEAT_ROOT}/run/smd-medchron/negotiation-queue)\" = root:workspace-broker:770 ]"
check "negotiation-jobs-dir-root-owned-child-traversable" "[ \"\$(stat -c %U:%G:%a ${SEAT_ROOT}/run/smd-medchron/negotiation-jobs)\" = root:medchron:710 ]"
check "negotiation-state-dir-child-only" "[ \"\$(stat -c %U:%G:%a ${SEAT_ROOT}/run/smd-medchron/negotiation/state)\" = medchron:medchron:700 ]"
# The SMD note lane (2026-10-15): its own daemon thread (wakes and reply
# watches only, no child) and its root-only job dir.
check "note-lane-ticking" "t=${SEAT_ROOT}/run/smd-medchron/note-tick; [ -f \$t ] && [ \$(( \$(date -u +%s) - \$(stat -c %Y \$t) )) -lt 90 ]"
check "note-jobs-dir-root-only" "[ \"\$(stat -c %U:%G:%a ${SEAT_ROOT}/run/smd-medchron/note-jobs)\" = root:root:700 ]"

# The firm config is authored per-seat in the PRIVATE engagements repo, and
# provision-customer.sh step 2b reads the SAME path and continues without it
# ("the chronology runner will refuse jobs" — the authored state for a seat
# that runs no chronology routine). Whether the config is EXPECTED here is
# decided by that same source of expectation, never by whether the boot
# happened to materialize it: this check landed unconditional in the
# 2026-08-31 medchron slices, and that same evening pilot-smokeball (no
# medchron authored) ran 39 PASS then FATALed on it, skipping every check
# after — the instrument failed, not the seat. Law 2's fail-closed rule
# applies to the expectation source itself: "cannot evaluate" must never read
# as "not expected".
#
# The expectation source is the binding's `lane_authored <lane>` (lane is one
# of medchron, demand, drafting, litigation). The laptop smoke asks the
# engagements checkout (working tree or origin/main; boot-smoke-test.sh says
# why both). The in-image self-test has no engagements checkout, so it asks
# the live customer.yaml whether the lane's skill is enabled. Where the two
# disagree on a seat, the release that records both verdicts shows it.
MEDCHRON_AUTHORED=0
lane_authored medchron && MEDCHRON_AUTHORED=1
if [ "${MEDCHRON_AUTHORED}" = 1 ]; then
  check "medchron-firm-config-present" "[ \"\$(stat -c %U:%G:%a ${SEAT_ROOT}/var/lib/smd-config/medchron-firm.yaml)\" = root:medchron:640 ]"
  check "medchron-uid-cannot-write-config" "setpriv --reuid=medchron --regid=medchron --init-groups sh -c \"! test -w ${SEAT_ROOT}/var/lib/smd-config/medchron-firm.yaml\""
  check "medchron-token-shared" "[ \"\$(stat -c %G:%a ${SEAT_ROOT}/run/smd-smokeball-token/refresh_token)\" = smokeball-token:660 ]"
  # A seat that authors a firm config must also carry the
  # install-level tree the entrypoint seeds from vaults/<slug>/medchron-controls/
  # — the scanned-page classifier's falsifier (controls.json + its PDFs) and
  # the ICD tables, both staged into that vault by provision-customer.sh step
  # 2c (the tables vendored on the console: this tree is read-only to the
  # child, so a seat can never fetch its own). Without it every job refuses
  # at classify_scanned or fails at icd_tables, the seat path the 2026-09-04
  # review found nobody had walked. The child can READ the controls (it runs
  # the classifier) and never write them (a classifier that can edit its own
  # controls measures nothing). What makes these able to FAIL: empty the vault
  # prefix and reboot; drop the chown/chmod in entrypoint.sh and the stat or
  # the write test reads it.
  check "medchron-controls-present" "[ \"\$(stat -c %U:%G:%a ${SEAT_ROOT}/run/smd-medchron/controls)\" = root:medchron:750 ] && test -f ${SEAT_ROOT}/run/smd-medchron/controls/controls.json"
  check "medchron-icd-tables-present" "test -f ${SEAT_ROOT}/run/smd-medchron/controls/icd/VERSION.json"
  check "medchron-uid-reads-controls-cannot-write" "setpriv --reuid=medchron --regid=medchron --init-groups sh -c \"test -r ${SEAT_ROOT}/run/smd-medchron/controls/controls.json && ! test -w ${SEAT_ROOT}/run/smd-medchron/controls/controls.json && ! test -w ${SEAT_ROOT}/run/smd-medchron/controls\""
else
  check_note "medchron-firm-config-absent" "not authored for this seat; runner refuses jobs by design, so the config-perm, token, controls, and gate-refusal checks are skipped"
fi

# The demand, drafting and litigation lanes' firm inputs: present on the seat
# exactly when the engagements checkout authors them (the same expectation
# source as the medchron firm config above; provision-customer.sh stages each
# from that path). 2026-10-14, A&P v141: the litigation inputs were in the
# vault and the boot's fetch failed silently; this smoke ran 75 PASS and the
# next litigation job stopped at config_missing. A fetch that fails leaves the
# lane "defer every job", which no earlier check could see: every lane check
# above holds on an inputs-less seat BY DESIGN. FALSIFIER: empty the vault
# prefix (or break the fetch) and reboot a seat that authors the lane.
for _lane in demand drafting litigation; do
  if lane_authored "${_lane}"; then
    check "${_lane}-firm-inputs-present" "[ \"\$(stat -c %U:%G:%a ${SEAT_ROOT}/var/lib/smd-config/${_lane}/${_lane}-firm.yaml)\" = root:medchron:640 ]"
  else
    check_note "${_lane}-firm-inputs-absent" "not authored for this seat; the ${_lane} lane defers jobs by design"
  fi
done

# ---------- Step 14b: the four registered runner gates refuse their planted violations (ss#2614, ADR 0087) ----------
# `medchron probe <gate>` builds a throwaway synthetic matter, plants exactly
# the violation the registry names, runs the gate module the registry points
# at, and prints REFUSED only when the gate held. $0 and offline. These back
# the `enforced` status of the four medchron_* rows in runtime-controls.yaml
# (probe_surface: prod-boot): the status is re-earned on every provision, not
# asserted once. What makes these able to FAIL: neuter a gate module (return 0,
# drop the planted check) and its probe prints UNEXPECTED_PASS, non-zero.
# Gated on the same authored expectation as the firm-config checks above:
# on a seat with no medchron authored the runner refuses every job, and the
# pass-annotated skip is emitted by the firm-config branch.
if [ "${MEDCHRON_AUTHORED}" = 1 ]; then
  check "medchron-gate-claim-audit-refuses" "${SEAT_ROOT}/opt/medchron/.venv/bin/medchron probe claim_audit | grep -q REFUSED"
  check "medchron-gate-extractive-refuses" "${SEAT_ROOT}/opt/medchron/.venv/bin/medchron probe extractive | grep -q REFUSED"
  check "medchron-gate-cross-client-refuses" "${SEAT_ROOT}/opt/medchron/.venv/bin/medchron probe cross_client | grep -q REFUSED"
  check "medchron-gate-provenance-refuses" "${SEAT_ROOT}/opt/medchron/.venv/bin/medchron probe provenance | grep -q REFUSED"
fi

}
