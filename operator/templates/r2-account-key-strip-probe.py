#!/usr/bin/env python3
"""Affirmative OP-P2-1 proof: NO agent-uid process holds the account-wide R2 key.

bootstrap.sh unsets R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY before forking any
same-uid child or exec'ing the gateway (operator/templates/bootstrap.sh, the
``unset`` at the "Strip the account-wide R2 credential NOW" block). Two unit
tests already assert the *source* contains that strip in the right order
(operator/bin/tests/test_deploy_ordering.py) — but source-level proof is exactly
what this whole hardening effort distrusts: a control that passes a static test
can still fail to materialize on the running Machine. This probe closes that gap
by reading the LIVE process table.

It runs as ROOT (boot-smoke's SSH session) and scans every process owned by the
agent uid (``hermes`` by default) — the gateway plus any same-uid children such
as the webhook gate — reading each ``/proc/<pid>/environ``. It exits 0 only when
NONE of them carry the account-wide R2 credential. Root processes (PID 1, the
config applier) legitimately keep the key and are deliberately excluded: the
loopback risk is a *hermes*-uid process holding a credential that can rewrite the
R2 config object.

The key VALUE is never printed — only the offending pid + comm, so the probe
output is safe in a CI transcript.

Usage:  r2-account-key-strip-probe.py [--wait-gateway-s N] [agent-username] [VAR ...]
        defaults: agent-username=hermes  VARs=R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY
Intended to be invoked by boot-smoke-test.sh via:
        /opt/hermes/.venv/bin/python3 /app/r2-account-key-strip-probe.py

``--wait-gateway-s N`` retries ONLY the "no agent-uid process yet" verdict for up
to N seconds (ss#2420: on a cold boot, smoke reaches this probe ~75s after boot,
before the gateway has spawned, and the vacuous-zero fail-closed rule fired a
false FATAL on a healthy deploy). The wait never applies to a real finding: a
scan that sees an offender exits 1 the moment it sees it, and a missing user or
unreadable /proc stays an immediate failure — those are configuration defects,
not races.
"""

from __future__ import annotations

import os
import pwd
import sys
import time

_DEFAULT_USER = "hermes"
_DEFAULT_VARS = ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")

# Re-scan cadence while waiting for the gateway to come up.
_WAIT_POLL_S = 3.0


def _proc_uid(proc_root: str, pid: str) -> int | None:
    """Real UID of a process, or None if it vanished / is unreadable."""
    try:
        st = os.stat(os.path.join(proc_root, pid))
    except OSError:
        return None
    return st.st_uid


def _env_var_names(proc_root: str, pid: str) -> set[str] | None:
    """The NAMES present in a process's environ (values are never returned).

    Returns None when the environ cannot be read (process exited, or — when the
    probe is not root — a foreign-uid process we are not permitted to inspect).
    """
    try:
        with open(os.path.join(proc_root, pid, "environ"), "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    names: set[str] = set()
    for entry in raw.split(b"\x00"):
        if not entry:
            continue
        name, _, _ = entry.partition(b"=")
        names.add(name.decode("utf-8", "replace"))
    return names


def _comm(proc_root: str, pid: str) -> str:
    try:
        with open(os.path.join(proc_root, pid, "comm")) as fh:
            return fh.read().strip()
    except OSError:
        return "?"


def scan(
    proc_root: str,
    target_uid: int,
    var_names: list[str],
    uid_of=_proc_uid,
) -> tuple[int, list[str]]:
    """Return (agent_proc_count, offenders).

    ``offenders`` is a list of human-readable "<pid> (<comm>): <VAR>" strings —
    pids of target-uid processes whose environ still carries one of ``var_names``.
    The credential value is never included. ``uid_of`` is injectable so a unit
    test can drive ownership without being root (the live caller uses the default,
    which stats /proc/<pid>).
    """
    wanted = set(var_names)
    agent_procs = 0
    offenders: list[str] = []
    for pid in os.listdir(proc_root):
        if not pid.isdigit():
            continue
        if uid_of(proc_root, pid) != target_uid:
            continue
        names = _env_var_names(proc_root, pid)
        if names is None:
            continue
        agent_procs += 1
        leaked = sorted(wanted & names)
        for var in leaked:
            offenders.append(f"{pid} ({_comm(proc_root, pid)}): {var}")
    return agent_procs, offenders


def wait_scan(
    proc_root: str,
    target_uid: int,
    var_names: list[str],
    *,
    wait_s: float = 0.0,
    uid_of=_proc_uid,
    sleep=time.sleep,
    monotonic=time.monotonic,
) -> tuple[int, list[str]]:
    """``scan``, retrying ONLY the zero-agent-process verdict for up to ``wait_s``.

    An offender ends the wait the instant any scan sees it (the retry must never
    swallow the real signal — ss#2420 AC), and a scan that finds a clean agent
    process ends it too. Only "nothing to scan yet" keeps polling; when the
    deadline passes it is returned as-is for main() to fail loud (exit 3).
    ``sleep``/``monotonic`` are injectable so tests drive the loop without
    real time passing.
    """
    deadline = monotonic() + wait_s
    while True:
        agent_procs, offenders = scan(proc_root, target_uid, var_names, uid_of=uid_of)
        if offenders or agent_procs > 0 or monotonic() >= deadline:
            return agent_procs, offenders
        sleep(_WAIT_POLL_S)


def main(argv: list[str]) -> int:
    args = argv[1:]
    wait_s = 0.0
    if args and args[0] == "--wait-gateway-s":
        if len(args) < 2:
            print("FAIL: --wait-gateway-s requires a seconds value", file=sys.stderr)
            return 2
        try:
            wait_s = float(args[1])
        except ValueError:
            print(f"FAIL: --wait-gateway-s value {args[1]!r} is not a number", file=sys.stderr)
            return 2
        args = args[2:]
    username = args[0] if args else _DEFAULT_USER
    var_names = list(args[1:]) if len(args) > 1 else list(_DEFAULT_VARS)

    try:
        target_uid = pwd.getpwnam(username).pw_uid
    except KeyError:
        print(f"FAIL: agent user {username!r} does not exist on this Machine", file=sys.stderr)
        return 2

    # Fail CLOSED, not with a traceback, if the process table is unreadable: a
    # guard that cannot inspect /proc has proved nothing. (Also makes this a clean
    # failure on a host without procfs, e.g. a dev macOS box, instead of a crash.)
    try:
        agent_procs, offenders = wait_scan("/proc", target_uid, var_names, wait_s=wait_s)
    except OSError as exc:
        print(f"FAIL: cannot enumerate the process table at /proc ({exc.strerror})", file=sys.stderr)
        return 4

    if offenders:
        for line in offenders:
            print(f"FAIL: agent-uid process still holds account-wide R2 key — {line}", file=sys.stderr)
        return 1

    if agent_procs == 0:
        # Vacuous "no offenders" is not a pass: if no agent process is running,
        # the gateway never started and the guard proved nothing. Fail loud.
        print(
            f"FAIL: no processes found for uid {target_uid} ({username}) — gateway not up?",
            file=sys.stderr,
        )
        return 3

    print(f"OK: {agent_procs} agent-uid process(es) scanned; none carry {', '.join(var_names)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
