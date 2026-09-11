"""Behavioural coverage for the root-side gateway liveness supervisor (P0 ss#2488).

These tests EXTRACT the supervisor's real text out of ``entrypoint.sh`` and run
it, rather than asserting that the file contains certain words. The distinction
is the point. The defect that opened ss#2488 was a recovery path that logged its
intent and never performed it; a test that greps the source for ``kill -KILL``
would have passed against exactly that. The static assertions in
``tests/operator-entrypoint.test.ts`` lock the SHAPE so it cannot be silently
removed; this file proves the STATE MACHINE, which is where the bugs live:

  * the arming guard (a stale heartbeat left on the persistent volume by the
    PREVIOUS boot must never be treated as a wedge, or every cold start dies),
  * loop survival under ``set -euo pipefail``, which a backgrounded subshell
    inherits — the failure mode that would kill the supervisor on its first
    vanished file and leave the seat with no recovery and no signal,
  * the recovery re-check after the diagnostic grace,
  * the kill ledger, which stops a flapping seat instead of restarting forever,
  * profile resolution from the gateway's own argv rather than by mtime.

The harness supplies ``log`` and a ``kill`` stub, and points the two seams
(``GATEWAY_LIVENESS_PROC_DIR``, ``GATEWAY_LIVENESS_PROFILES_DIR``) at a tmpdir,
so the suite runs on Linux CI and on a developer's macOS, which has no /proc.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "operator" / "templates" / "entrypoint.sh"

# The extracted region: from the argv-resolution helper through the `) &` that
# closes the backgrounded supervisor subshell. Anchored on comment text that is
# load-bearing prose, so a rewrite that moves the block fails here loudly rather
# than silently testing nothing.
_START = "# The supervisor's state machine, one word, rewritten on every transition"
_END = "\n) &\n"

GATEWAY_PID = "4242"


def _extract_supervisor() -> str:
    src = ENTRYPOINT.read_text()
    start = src.find(_START)
    assert start != -1, f"supervisor block start anchor not found in {ENTRYPOINT}"
    end = src.find(_END, start)
    assert end != -1, f"supervisor subshell end anchor not found in {ENTRYPOINT}"
    return src[start : end + len(_END)]


def test_extraction_anchors_still_match():
    """If this fails, every other test in this file is measuring nothing."""
    block = _extract_supervisor()
    assert "gateway_liveness_state()" in block
    assert "gateway_heartbeat_path()" in block
    assert "gateway_liveness_escalate()" in block
    assert "set +e" in block
    assert "while true; do" in block


class Harness:
    """A tmpdir world the extracted supervisor can run against."""

    def __init__(self, tmp_path: Path):
        self.root = tmp_path
        self.run_dir = tmp_path / "run"
        self.ledger_dir = tmp_path / "ledger"
        self.proc_dir = tmp_path / "proc"
        self.profiles_dir = tmp_path / "profiles"
        self.log = tmp_path / "log.txt"
        self.kills = tmp_path / "kills.txt"
        for d in (self.run_dir, self.ledger_dir, self.proc_dir, self.profiles_dir):
            d.mkdir(parents=True, exist_ok=True)
        # A pin that DOES register a SIGUSR2 faulthandler, so the dump gate is
        # exercised on its open branch unless a test says otherwise.
        self.hermes_run_py = tmp_path / "run.py"
        self.hermes_run_py.write_text("faulthandler.register(signal.SIGUSR2)\n")
        # A pin that DOES write the loop heartbeat. hermes-smd-staging runs one
        # that does not (0.18.0 / 7c1a029), which is why the capability gate
        # exists at all.
        self.heartbeat_writer = tmp_path / "shutdown_watchdog.py"
        self.heartbeat_writer.write_text("async def loop_heartbeat_forever():\n    pass\n")
        self.proc = None

    def set_argv(self, *argv: str) -> None:
        pid_dir = self.proc_dir / GATEWAY_PID
        pid_dir.mkdir(parents=True, exist_ok=True)
        (pid_dir / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")

    def heartbeat_path(self, profile: str) -> Path:
        return self.profiles_dir / profile / "state" / "gateway.heartbeat"

    def write_heartbeat(self, profile: str, age_seconds: float = 0.0, pid: str = GATEWAY_PID) -> Path:
        path = self.heartbeat_path(profile)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"pid": %s, "updated_at": "x", "monotonic": 1.0}' % pid)
        when = time.time() - age_seconds
        os.utime(path, (when, when))
        return path

    def script(self, extra_env_lines: str = "") -> str:
        block = _extract_supervisor()
        return f"""#!/usr/bin/env bash
# Mirror the real entrypoint's shell options EXACTLY. Without this the test
# cannot detect the `set -e` hazard the supervisor's `set +e` exists to defuse.
set -euo pipefail

log() {{ echo "$*" >> "{self.log}"; }}

# The Machine is Debian, so the supervisor correctly calls GNU `stat -c %Y`.
# macOS ships BSD stat, which has no -c. Shim it HERE rather than weakening the
# production call to a lowest-common-denominator form the Machine never needs —
# and note the failure mode this papers over is silent: BSD stat writes usage to
# stderr and returns empty, the supervisor's `case` guard treats that as
# unreadable and continues, and every test would hang at "never armed" while the
# code under test was perfectly fine.
if ! stat -c %Y . >/dev/null 2>&1; then
  stat() {{
    if [ "$1" = "-c" ] && [ "$2" = "%Y" ]; then
      shift 2
      command stat -f %m "$@"
    else
      command stat "$@"
    fi
  }}
fi

# `kill` as a function shadows the builtin, so escalation is observable without
# signalling anything real. It also MODELS reality: a delivered SIGTERM/SIGKILL
# makes the process go away, so the fake /proc entry goes with it.
kill() {{
  echo "$*" >> "{self.kills}"
  case "$1" in
    -TERM|-KILL) rm -rf "{self.proc_dir}/{GATEWAY_PID}" ;;
  esac
  return 0
}}

SMD_GATEWAY_PID="{GATEWAY_PID}"
GATEWAY_LIVENESS_RUN_DIR="{self.run_dir}"
GATEWAY_LIVENESS_LEDGER_DIR="{self.ledger_dir}"
GATEWAY_LIVENESS_PROC_DIR="{self.proc_dir}"
GATEWAY_LIVENESS_PROFILES_DIR="{self.profiles_dir}"
SMD_GATEWAY_LIVENESS_POLL_SECONDS=1
SMD_GATEWAY_LIVENESS_STALE_SECONDS=3
SMD_GATEWAY_LIVENESS_DUMP_GRACE_SECONDS=1
SMD_GATEWAY_LIVENESS_TERM_GRACE_SECONDS=1
SMD_GATEWAY_LIVENESS_BOOT_DEADLINE_SECONDS=3
# The PRODUCTION default, not a shrunk one: most tests here need the startup
# grace to be effectively infinite so they exercise the branch they name rather
# than tripping into `starting` / `never-healthy` partway through. The tests that
# are ABOUT the grace pass their own value, which lands after this line and wins.
SMD_GATEWAY_LIVENESS_STARTUP_GRACE_SECONDS=900
SMD_GATEWAY_LIVENESS_MAX_KILLS=2
SMD_GATEWAY_LIVENESS_KILL_WINDOW_SECONDS=3600
SMD_GATEWAY_LIVENESS_KILL_VERIFY_SECONDS=1
GATEWAY_LIVENESS_HEARTBEAT_WRITER="{self.heartbeat_writer}"
{extra_env_lines}

{block}
wait
"""

    def start(self, extra_env_lines: str = "") -> None:
        path = self.root / "harness.sh"
        # The dump gate greps the installed Hermes source; point it at the fake.
        text = self.script(extra_env_lines).replace("/opt/hermes/gateway/run.py", str(self.hermes_run_py))
        path.write_text(text)
        path.chmod(0o755)
        self.proc = subprocess.Popen(["bash", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)  # noqa: S603 - test runs its own generated harness script under bash, no shell

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=10)

    def log_text(self) -> str:
        return self.log.read_text() if self.log.exists() else ""

    def kill_text(self) -> str:
        return self.kills.read_text() if self.kills.exists() else ""

    def wait_for_log(self, pattern: str, timeout: float = 20.0) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            text = self.log_text()
            if re.search(pattern, text):
                return text
            if self.proc and self.proc.poll() is not None:
                raise AssertionError(
                    f"supervisor EXITED before matching {pattern!r}.\n"
                    f"log:\n{text}\nstderr:\n{self.proc.stderr.read().decode()}"
                )
            time.sleep(0.2)
        raise AssertionError(f"timed out waiting for {pattern!r}; log:\n{self.log_text()}")

    def tick_mtime(self) -> float:
        tick = self.run_dir / "tick"
        return tick.stat().st_mtime if tick.exists() else 0.0

    def state(self) -> str | None:
        """The one-word state file the gate ships on the heartbeat (part 2)."""
        f = self.run_dir / "state"
        return f.read_text().strip() if f.exists() else None

    def wait_for_state(self, want: str, timeout: float = 20.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.state() == want:
                return
            time.sleep(0.2)
        raise AssertionError(f"state never reached {want!r}; last={self.state()!r}; log:\n{self.log_text()}")


@pytest.fixture
def harness(tmp_path):
    h = Harness(tmp_path)
    h.set_argv("python", "/opt/hermes/.venv/bin/hermes", "-p", "crane", "gateway", "run")
    yield h
    h.stop()


def test_arms_on_a_fresh_beat_then_restarts_the_seat_when_the_loop_wedges(harness):
    """The whole point: a wedge becomes a restart with nobody in the loop."""
    harness.write_heartbeat("crane", age_seconds=0)
    harness.start()
    harness.wait_for_log(r"ARMED")
    harness.wait_for_state("armed")
    # Stop refreshing the heartbeat — this is the wedge.
    harness.wait_for_log(r"GATEWAY WEDGE")
    harness.wait_for_log(r"SIGTERM to container main")
    assert "-TERM 4242" in harness.kill_text()


def test_never_arms_on_a_stale_beat_left_by_the_previous_boot(harness):
    """The forced negative control.

    /opt/data is a persistent volume, so a heartbeat from the LAST boot is on
    disk at every cold start. Arming on it would SIGKILL the container on every
    single boot. Asserting "nothing happened" would be a check that cannot fail,
    so this asserts the guard FIRED — the specific not-arming line — and that no
    signal was sent.
    """
    harness.write_heartbeat("crane", age_seconds=7200)
    harness.start()
    harness.wait_for_log(r"has never been seen fresh this boot; NOT arming")
    time.sleep(4)
    assert "GATEWAY WEDGE" not in harness.log_text()
    assert harness.kill_text() == ""
    assert harness.state() == "not-armed"


def test_loop_survives_a_vanished_heartbeat(harness):
    """The heartbeat can disappear under the supervisor; it must keep watching.

    The tick file is the proof of life: it is touched at the top of every
    iteration, so a stalled loop stops advancing it.

    Scope this honestly. A mutation run (2026-08-20, `set +e` deleted from the
    subshell) showed this test still PASSING, because bash exempts a failing
    non-final member of an `&&` list from errexit and the `[ -e ]` guard keeps
    `stat` off a missing file. The behavioural detector for a removed `set +e`
    is `test_is_inert_and_loud_when_argv_does_not_name_hermes` — there the
    `hb="$(gateway_heartbeat_path)"` assignment carries the function's non-zero
    status and errexit ends the subshell before it can log anything — plus the
    literal assertion in `test_extraction_anchors_still_match`. Both failed
    under that mutation. This one covers a different scenario and should not be
    read as guarding errexit.
    """
    harness.write_heartbeat("crane", age_seconds=0)
    harness.start()
    harness.wait_for_log(r"ARMED")
    harness.heartbeat_path("crane").unlink()
    (harness.profiles_dir / "crane" / "state").rmdir()
    before = harness.tick_mtime()
    time.sleep(3)
    assert harness.tick_mtime() > before, "supervisor stopped iterating after the heartbeat vanished"
    assert harness.proc.poll() is None, "supervisor process exited"


def test_does_not_kill_when_the_loop_recovers_during_the_dump_grace(harness):
    harness.write_heartbeat("crane", age_seconds=0)
    harness.start()
    harness.wait_for_log(r"ARMED")
    harness.wait_for_log(r"GATEWAY WEDGE")
    # Recover while the supervisor waits on the stack dump.
    harness.write_heartbeat("crane", age_seconds=0)
    harness.wait_for_log(r"loop recovered during the dump grace; NOT killing|loop recovered during the dump grace")
    assert "-TERM" not in harness.kill_text()


def test_kill_ledger_refuses_once_the_budget_is_spent(harness):
    """A seat flapping every few minutes is not better than one that is down."""
    ledger = harness.ledger_dir / "kills"
    now = int(time.time())
    ledger.write_text(f"{now - 10} iso loop-wedge\n{now - 5} iso loop-wedge\n")
    harness.write_heartbeat("crane", age_seconds=0)
    harness.start()
    harness.wait_for_log(r"ARMED")
    harness.wait_for_log(r"REFUSING to restart")
    assert harness.kill_text() == "", "refused escalation still signalled the process"
    harness.wait_for_state("refusing")


def test_profile_comes_from_argv_not_from_mtime(harness):
    """A seat may carry several persona homes; only one is the gateway's.

    The decoy is written LAST, so an mtime-ordered resolver would watch it. The
    argv-derived resolver must watch `crane` and therefore see a wedge.
    """
    harness.write_heartbeat("crane", age_seconds=0)
    time.sleep(0.05)
    harness.write_heartbeat("retired-persona", age_seconds=0)
    harness.start()
    harness.wait_for_log(r"ARMED")
    text = harness.wait_for_log(r"GATEWAY WEDGE")
    assert "profiles/crane/state/gateway.heartbeat" in text
    assert "retired-persona" not in text


def test_is_inert_and_loud_when_argv_does_not_name_hermes(harness):
    """ "Cannot evaluate" must never read as "healthy".

    Only AFTER the startup grace, now. Inside it the same argv means "bootstrap
    has not exec'd the gateway yet", which is every boot's first minutes — see
    the pair of tests below.
    """
    harness.set_argv("/bin/sh", "-c", "something-else")
    harness.write_heartbeat("crane", age_seconds=7200)
    harness.start("SMD_GATEWAY_LIVENESS_STARTUP_GRACE_SECONDS=0")
    harness.wait_for_log(r"supervisor is INERT")
    assert harness.kill_text() == ""
    harness.wait_for_state("inert")


# ---------------------------------------------------------------------------
# The 2026-09-01 pilot-smokeball crash loop. Two defects, both in what the
# supervisor SAID about a boot rather than in what it did to one.
# ---------------------------------------------------------------------------


def test_the_real_live_cmdline_bytes_resolve_the_profile(harness):
    """The exact NUL-separated argv captured from the wedged Machine.

    Written as a literal byte string, not via the harness helper, because the
    shape is the claim: the kernel puts the shebang INTERPRETER at argv[0], so
    the token that names hermes and the token that carries `-p` are different
    tokens, and any parse that reads only argv[0] or only argv[1] resolves
    nothing. Captured 2026-09-01 from /proc/<container-main>/cmdline on
    pilot-smokeball.
    """
    pid_dir = harness.proc_dir / GATEWAY_PID
    pid_dir.mkdir(parents=True, exist_ok=True)
    (pid_dir / "cmdline").write_bytes(
        b"/opt/hermes/.venv/bin/python\0/opt/hermes/.venv/bin/hermes\0-p\0operator\0gateway\0run\0"
    )
    harness.write_heartbeat("operator", age_seconds=0)
    harness.start()
    text = harness.wait_for_log(r"ARMED")
    assert "profiles/operator/state/gateway.heartbeat" in text
    assert "INERT" not in harness.log_text()


@pytest.mark.parametrize(
    "argv",
    [
        ("python", "/opt/hermes/.venv/bin/hermes", "-p", "operator", "gateway", "run"),
        ("python", "/opt/hermes/.venv/bin/hermes", "-p=operator", "gateway", "run"),
        ("python", "/opt/hermes/.venv/bin/hermes", "--profile", "operator", "gateway", "run"),
        ("python", "/opt/hermes/.venv/bin/hermes", "--profile=operator", "gateway", "run"),
    ],
    ids=["short-separated", "short-attached", "long-separated", "long-attached"],
)
def test_every_spelling_of_the_profile_flag_resolves(harness, argv):
    """bootstrap.sh writes one spelling today; this reads argv forever.

    A future invocation change must fail loudly at review, not silently un-watch
    the seat at runtime.
    """
    harness.set_argv(*argv)
    harness.write_heartbeat("operator", age_seconds=0)
    harness.start()
    text = harness.wait_for_log(r"ARMED")
    assert "profiles/operator/state/gateway.heartbeat" in text


def test_pre_exec_argv_is_starting_not_inert(harness):
    """The false page. Every healthy boot used to emit it.

    The supervisor is forked while still root, minutes before the gateway
    process exists: entrypoint execs bootstrap.sh, which stages skills and runs
    the appliers before its own exec at bootstrap.sh:916. For that whole window
    /proc/<container-main>/cmdline reads `bash /app/bootstrap.sh` — no hermes, no
    profile — and the old code called that INERT, a state fleet-alerts pages on
    with "it will never act".

    Observed live at 2026-09-01T02:30:37Z on /proc/652, two seconds after the
    bootstrap log line for seeding the skill catalog and five seconds before the
    next one. Asserting the calm line is not enough on its own, so this also
    asserts the paging word is ABSENT.
    """
    harness.set_argv("/bin/bash", "/app/bootstrap.sh")
    harness.write_heartbeat("crane", age_seconds=7200)
    harness.start()
    harness.wait_for_state("starting")
    harness.wait_for_log(r"does not name hermes yet")
    # The phrase, not the bare word: the calm line legitimately says "this
    # becomes an INERT page" when the grace runs out, and that forward reference
    # is the useful half of it.
    assert "supervisor is INERT" not in harness.log_text()
    assert harness.state() != "inert"
    assert harness.kill_text() == ""


def test_pre_exec_argv_becomes_inert_once_the_grace_expires(harness):
    """The other half: `starting` must not become a way to never page.

    Same argv as the test above, a grace it has already outlived. Without this
    pair, "do not page during startup" and "page when startup never ends" are
    indistinguishable.
    """
    harness.set_argv("/bin/bash", "/app/bootstrap.sh")
    harness.write_heartbeat("crane", age_seconds=7200)
    harness.start("SMD_GATEWAY_LIVENESS_STARTUP_GRACE_SECONDS=3")
    harness.wait_for_state("starting")
    harness.wait_for_state("inert")
    harness.wait_for_log(r"supervisor is INERT")
    assert harness.kill_text() == "", "an expired startup grace must never signal"


def test_a_boot_that_never_goes_fresh_pages_and_does_not_kill(harness):
    """The blind spot: a gateway that wedges DURING startup.

    It never writes a first beat, so it lands in the not-arming branch, which had
    no deadline of its own — the supervisor nagged into `fly logs` forever and
    the state stayed `not-armed`, which fleet-alerts does not page on. That is
    the shape pilot-smokeball was in at 02:21:54, 02:27:01 and 02:35:44 on
    2026-09-01 while restarting every ~15 minutes.

    The assertion that matters most is the negative one. Killing a slow-starting
    gateway is what sustained the loop, so this must page WITHOUT signalling.
    """
    harness.write_heartbeat("crane", age_seconds=7200)
    harness.start("SMD_GATEWAY_LIVENESS_STARTUP_GRACE_SECONDS=2")
    harness.wait_for_state("never-healthy")
    harness.wait_for_log(r"GATEWAY NEVER CAME UP")
    assert harness.kill_text() == "", "never-healthy must page, never kill"
    assert "GATEWAY WEDGE" not in harness.log_text()


def test_the_never_healthy_page_states_what_happens_and_when(harness):
    """A page that does not say what will happen next sends someone to read code.

    The line has to carry both halves: that nothing automatic follows, and that
    it clears itself. This asserts the text, and then proves the second half by
    producing a fresh beat and watching the state recover to `armed` — a claim in
    a log line that the code does not honour is worse than no line.
    """
    harness.write_heartbeat("crane", age_seconds=7200)
    harness.start("SMD_GATEWAY_LIVENESS_STARTUP_GRACE_SECONDS=2")
    text = harness.wait_for_log(r"GATEWAY NEVER CAME UP")
    assert "NOTHING AUTOMATIC WILL HAPPEN" in text
    assert "startup grace 2s" in text
    harness.write_heartbeat("crane", age_seconds=0)
    harness.wait_for_state("armed")


def test_refuses_to_watch_a_pin_that_has_no_loop_heartbeat(harness):
    """The capability gate, found by probing a second seat rather than reading code.

    hermes-smd-staging runs Hermes 0.18.0 (7c1a029) today. That pin predates the
    loop heartbeat entirely: the module is absent from its tree and no heartbeat
    file exists on its volume (vfy_01M0HBR1NZHSRMWSFPSQM32D1E). On such a pin
    "no heartbeat has ever appeared" means the build has none, not that the
    gateway is wedged — and without this gate the boot-deadline path would
    SIGKILL a healthy seat every 15 minutes until the ledger stopped it.

    Refusing must be LOUD. "Cannot evaluate" reading as "healthy" is the same
    failure the Law 2 engagement guard fails closed against.
    """
    harness.heartbeat_writer.write_text("# 0.18.0: no loop heartbeat in this pin\n")
    harness.write_heartbeat("crane", age_seconds=7200)
    harness.start()
    harness.wait_for_log(r"NOT watching")
    time.sleep(3)
    assert harness.kill_text() == ""
    assert "ARMED" not in harness.log_text()
    assert harness.state() == "not-watching"


def test_skips_the_dump_when_the_pin_registers_no_sigusr2_handler(harness):
    """SIGUSR2's default disposition is terminate.

    If a future Hermes pin drops `faulthandler.register`, an unguarded send stops
    being a diagnostic and becomes an unlogged kill that skips the recovery
    re-check and the kill ledger entirely.
    """
    harness.hermes_run_py.write_text("# no faulthandler registration in this pin\n")
    harness.write_heartbeat("crane", age_seconds=0)
    harness.start()
    harness.wait_for_log(r"ARMED")
    harness.wait_for_log(r"registers no SIGUSR2 faulthandler; skipping the stack dump")
    assert "-USR2" not in harness.kill_text()


def test_state_tick_and_ledger_are_world_readable_for_the_gate(harness):
    """Part 2 reads these from the webhook gate, which runs as the agent uid.

    A 0700 dir or a 0600 file would make every heartbeat field derived from them
    NULL forever -- and NULL is a hold, so the console would never page. The
    integrity property is unchanged: the harness cannot chmod-test root
    ownership, but the mode bits it CAN check are the ones that grant reading
    without granting writing.
    """
    import stat as st

    ledger = harness.ledger_dir / "kills"
    now = int(time.time())
    ledger.write_text(f"{now - 10} iso loop-wedge\n{now - 5} iso loop-wedge\n")
    harness.write_heartbeat("crane", age_seconds=0)
    harness.start()
    harness.wait_for_state("armed")
    harness.wait_for_state("refusing")  # drives the ledger path
    for name in ("state", "tick"):
        mode = (harness.run_dir / name).stat().st_mode
        assert mode & st.S_IROTH, f"{name} is not world-readable"
        assert not (mode & st.S_IWOTH), f"{name} is world-writable"
        assert not (mode & st.S_IWGRP), f"{name} is group-writable"
