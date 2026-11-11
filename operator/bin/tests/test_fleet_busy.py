"""The busy gate: the judgement (busy.decide) and the seat-side scripts, run for real.

The two remote scripts are executed here against a temporary directory laid
out like a seat (/run/smd-jobs, /opt/data/profiles/*/cron/jobs.json), with
only their path constants rewritten, so what is tested is the program that
ships, not a description of it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from bin.lib.fleet import busy, cli
from bin.lib.fleet.fly import Fly, FlyTokens
from bin.lib.fleet.runner import Result, ToolingError, looks_like_transport_failure
from bin.tests.fleet_fakes import FakeFly, FakeR2, FakeStore

HERE = Path(busy.__file__).resolve().parent


def _facts(**kw):
    base = {"lanes": [], "children": [], "crons": [], "errors": []}
    base.update(kw)
    return base


def test_idle_seat_is_idle() -> None:
    assert not busy.decide(_facts()).busy


def test_queued_work_is_not_busy() -> None:
    lane = {
        "file": "heartbeat.json",
        "age_s": 5,
        "running": None,
        "queued": 4,
        "wakes_pending": 0,
        "daemon_alive": True,
    }
    assert not busy.decide(_facts(lanes=[lane])).busy


def test_running_job_child_or_wakes_are_busy() -> None:
    assert busy.decide(_facts(children=[{"file": "demand-child.pid", "pid": "12", "alive": True}])).busy
    assert not busy.decide(_facts(children=[{"file": "demand-child.pid", "pid": "12", "alive": False}])).busy
    lane = {"file": "heartbeat.json", "age_s": 5, "running": "job-1", "wakes_pending": 0, "daemon_alive": True}
    assert busy.decide(_facts(lanes=[lane])).busy
    lane = {"file": "heartbeat.json", "age_s": 5, "running": None, "wakes_pending": 2, "daemon_alive": True}
    assert busy.decide(_facts(lanes=[lane])).busy


def test_stale_heartbeat_with_live_daemon_is_never_idle() -> None:
    # FALSIFIER: drop the staleness rule and a chronology that has not beaten in
    # an hour (old images have no beat thread) reads as idle and gets restarted.
    lane = {"file": "heartbeat.json", "age_s": 3600, "running": None, "wakes_pending": 0, "daemon_alive": True}
    verdict = busy.decide(_facts(lanes=[lane]))
    assert verdict.busy and "stale" in verdict.reasons[0]
    dead = dict(lane, daemon_alive=False)
    assert not busy.decide(_facts(lanes=[dead])).busy


def test_a_beat_the_live_daemon_never_wrote_is_not_busy() -> None:
    # 2026-10-31, pilot-smokeball: the W1 image stopped running drafting,
    # litigation and negotiation there; their old beats stayed on the volume,
    # named the live daemon's pid, and held four releases "busy" for 3 h.
    # FALSIFIER: drop the leftover rule and this lane is busy again.
    lane = {
        "file": "drafting-heartbeat.json",
        "age_s": 10532,
        "running": None,
        "wakes_pending": 0,
        "daemon_alive": True,
        "written_before_daemon": True,
    }
    assert not busy.decide(_facts(lanes=[lane])).busy
    # Unknown start time (an old probe, no /proc) keeps the stale rule.
    assert busy.decide(_facts(lanes=[dict(lane, written_before_daemon=None)])).busy
    assert busy.decide(_facts(lanes=[dict(lane, written_before_daemon=False)])).busy
    # A leftover that names a running job is still busy.
    assert busy.decide(_facts(lanes=[dict(lane, running="job-1")])).busy


def test_the_probe_marks_a_beat_older_than_its_daemon(tmp_path: Path) -> None:
    run, _ = _seat(tmp_path)
    now = time.time()
    (run / "drafting-heartbeat.json").write_text(json.dumps({"pid": os.getpid(), "last_poll_at": now - 10**7}))
    (run / "heartbeat.json").write_text(json.dumps({"pid": os.getpid(), "last_poll_at": now}))
    lanes = {lane["file"]: lane for lane in busy.parse_probe(_run_remote("remote_busy_probe.py", tmp_path))["lanes"]}
    if Path("/proc/self/stat").exists():  # Linux, as on a seat
        assert lanes["drafting-heartbeat.json"]["written_before_daemon"] is True
        assert lanes["heartbeat.json"]["written_before_daemon"] is False
    else:  # no /proc: unknown, never a guess
        assert lanes["drafting-heartbeat.json"]["written_before_daemon"] is None


def test_cron_window_and_recent_fire() -> None:
    soon = {"name": "op-managed:operator:inbox-triage", "next_in_s": 300, "last_ago_s": 3000}
    later = {"name": "op-managed:operator:inbox-triage", "next_in_s": 1200, "last_ago_s": 3000}
    just = {"name": "op-managed:operator:inbox-triage", "next_in_s": 3300, "last_ago_s": 60}
    assert busy.decide(_facts(crons=[soon])).busy
    assert not busy.decide(_facts(crons=[later])).busy
    assert busy.decide(_facts(crons=[just])).busy


def test_quarter_hour_slot_crons_leave_an_idle_window() -> None:
    """Couples slot crons tick every 15 min; somewhere in each quarter hour the gate must pass."""
    period = 900
    idle_seconds = [
        t
        for t in range(0, period, 30)
        if not busy.decide(
            _facts(
                crons=[{"name": "op-managed:cm:couples-checkin@person:p1", "next_in_s": period - t, "last_ago_s": t}]
            )
        ).busy
    ]
    assert idle_seconds, "no idle window inside a 15-minute cron period"
    assert len(idle_seconds) * 30 >= 300


def test_unreadable_store_is_busy() -> None:
    assert busy.decide(_facts(errors=["/run/smd-jobs/heartbeat.json unreadable"])).busy


def _seat(tmp: Path) -> tuple[Path, Path]:
    run = tmp / "run" / "smd-jobs"
    data = tmp / "opt" / "data"
    run.mkdir(parents=True)
    (data / "profiles" / "operator" / "cron").mkdir(parents=True)
    return run, data


def _run_remote(name: str, tmp: Path, prefix: str = "") -> str:
    src = (HERE / name).read_text(encoding="utf-8")
    src = src.replace('"/run/smd-jobs"', json.dumps(str(tmp / "run" / "smd-jobs")))
    src = src.replace('"/run/smd-medchron"', json.dumps(str(tmp / "run" / "smd-medchron")))
    src = src.replace('"/opt/data/', '"' + str(tmp / "opt" / "data") + "/")
    if name == "remote_quiesce.py":
        src = src.replace("os.chown(tmp, 0, 0)", "pass")  # not root in the test
    proc = subprocess.run(
        [sys.executable, "-I", "-"], input=prefix + src, capture_output=True, text=True, timeout=30, check=False
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_remote_probe_reads_a_seat_shaped_tree(tmp_path: Path) -> None:
    run, data = _seat(tmp_path)
    now = time.time()
    (run / "heartbeat.json").write_text(
        json.dumps({"pid": os.getpid(), "last_poll_at": now - 10, "running": None, "queued": 1, "wakes_pending": 0})
    )
    (run / "negotiation-heartbeat.json").write_text(
        json.dumps({"pid": 999999, "last_poll_at": now - 9000, "running": None})
    )
    (run / "demand-child.pid").write_text(str(os.getpid()))
    soon = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 240))
    jobs = {
        "jobs": [
            {"name": "op-managed:operator:negotiation-watch", "enabled": True, "next_run_at": soon},
            {"name": "hand-made", "next_run_at": soon},
        ]
    }
    (data / "profiles" / "operator" / "cron" / "jobs.json").write_text(json.dumps(jobs))
    out = _run_remote("remote_busy_probe.py", tmp_path)
    facts = busy.parse_probe(out)
    assert {lane["file"] for lane in facts["lanes"]} == {"heartbeat.json", "negotiation-heartbeat.json"}
    assert facts["children"][0]["alive"] is True
    assert [c["name"] for c in facts["crons"]] == ["op-managed:operator:negotiation-watch"]
    verdict = busy.decide(facts)
    assert verdict.busy
    reasons = " ".join(verdict.reasons)
    assert "demand-child.pid" in reasons and "negotiation-watch" in reasons
    # the dead daemon's stale heartbeat is NOT a reason (its daemon is gone)
    assert "negotiation-heartbeat.json" not in reasons


def test_a_seat_before_the_volume_move_is_read_and_quiesced_at_its_old_run_dir(tmp_path: Path) -> None:
    # ADR 0103 R2: the gate runs from the newest tooling against a seat whose
    # image still binds the tree at /run/smd-medchron only (the seat a rollout
    # is about to move, and every held seat until its turn).
    # FALSIFIER: read /run/smd-jobs alone and this seat has no lane, no child,
    # and the verdict is idle with a job running; the marker lands nowhere.
    _, data = _seat(tmp_path)
    (tmp_path / "run" / "smd-jobs").rmdir()
    old = tmp_path / "run" / "smd-medchron"
    old.mkdir()
    now = time.time()
    (old / "heartbeat.json").write_text(
        json.dumps({"pid": os.getpid(), "last_poll_at": now - 10, "running": "j1", "queued": 0, "wakes_pending": 0})
    )
    (old / "child.pid").write_text(str(os.getpid()))
    facts = busy.parse_probe(_run_remote("remote_busy_probe.py", tmp_path))
    assert [lane["file"] for lane in facts["lanes"]] == ["heartbeat.json"]
    verdict = busy.decide(facts)
    assert verdict.busy and "child.pid" in " ".join(verdict.reasons)
    args = {"action": "set", "until": now + 600, "by": "fleet:test", "reason": "release"}
    assert "SMD_QUIESCE set" in _run_remote("remote_quiesce.py", tmp_path, prefix=f"ARGS = {args!r}\n")
    assert json.loads((old / "quiesce").read_text())["by"] == "fleet:test"
    assert not (tmp_path / "run" / "smd-jobs").exists()


def test_a_moved_seat_is_read_at_the_new_run_dir_even_while_the_old_one_is_bound(tmp_path: Path) -> None:
    run, _ = _seat(tmp_path)
    (tmp_path / "run" / "smd-medchron").mkdir()
    (run / "child.pid").write_text(str(os.getpid()))
    facts = busy.parse_probe(_run_remote("remote_busy_probe.py", tmp_path))
    assert [c["file"] for c in facts["children"]] == ["child.pid"]


def test_a_deliver_turn_in_flight_is_busy() -> None:
    # 2026-11-02, A&P: seven offer-email turns started at 09:48:38, the lane
    # read idle (wakes consumed, child gone), the release restarted the Machine
    # at 09:48:48 and four emails never went.
    # FALSIFIER: drop the turn rule and this verdict is idle.
    turn = {"file": "negotiation-jobs/n1", "sent_ago_s": 10, "reply_open": True}
    verdict = busy.decide(_facts(turns=[turn]))
    assert verdict.busy and "DELIVER turn" in verdict.reasons[0]
    assert not busy.decide(_facts(turns=[dict(turn, reply_open=False)])).busy  # reply seen
    assert not busy.decide(_facts(turns=[dict(turn, sent_ago_s=busy.TURN_BUSY_S + 1)])).busy  # the watch's page now


def test_the_probe_reads_a_consumed_wake_and_its_reply_watch(tmp_path: Path) -> None:
    run, _ = _seat(tmp_path)
    now = time.time()
    jobs = run / "negotiation-jobs"
    for name, wake, watch in (
        ("open", {"pending": False, "sent_at": now - 10}, {"due": now + 1190}),
        ("proven", {"pending": False, "sent_at": now - 10}, {"due": now + 1190, "proven_at": now - 2}),
        ("pending", {"pending": True}, {}),
    ):
        (jobs / name).mkdir(parents=True)
        (jobs / name / "daemon.json").write_text(json.dumps({"wake": wake, "reply_watch": watch}))
    facts = busy.parse_probe(_run_remote("remote_busy_probe.py", tmp_path))
    by = {t["file"]: t for t in facts["turns"]}
    assert set(by) == {"negotiation-jobs/open", "negotiation-jobs/proven"}
    assert by["negotiation-jobs/open"]["reply_open"] is True
    assert by["negotiation-jobs/proven"]["reply_open"] is False
    verdict = busy.decide(facts)
    assert verdict.busy and "negotiation-jobs/open" in " ".join(verdict.reasons)


def test_a_gateway_with_turns_in_flight_is_busy() -> None:
    # 2026-11-02, A&P: "Gateway drain timed out after 0.0s with 7 active agent(s)".
    # FALSIFIER: drop the gateway rule and this verdict is idle.
    gw = {"file": "/opt/data/profiles/operator/gateway_state.json", "alive": True, "active_agents": 2}
    verdict = busy.decide(_facts(gateways=[gw]))
    assert verdict.busy and "2 turn(s)" in verdict.reasons[0]
    assert not busy.decide(_facts(gateways=[dict(gw, active_agents=0)])).busy
    assert not busy.decide(_facts(gateways=[dict(gw, alive=False)])).busy  # a dead gateway's leftover file


def test_a_turn_count_with_no_conversation_for_half_an_hour_is_not_busy() -> None:
    # captain-mark 2026-11-02: an interrupted reply left active_agents=2 on an
    # idle seat and the gate held a release for its whole 30-minute wait.
    # FALSIFIER: drop the quiet rule and the first verdict is busy forever.
    gw = {"file": "/opt/data/profiles/captain-mark/gateway_state.json", "alive": True, "active_agents": 2}
    assert not busy.decide(_facts(gateways=[dict(gw, quiet_s=busy.GATEWAY_QUIET_S + 60)])).busy
    # A turn that wrote recently is real work, and an unknown quiet time is never idle.
    assert busy.decide(_facts(gateways=[dict(gw, quiet_s=30.0)])).busy
    assert busy.decide(_facts(gateways=[dict(gw, quiet_s=None)])).busy


def test_the_probe_reads_how_long_the_conversation_store_is_quiet(tmp_path: Path) -> None:
    import sqlite3

    _, data = _seat(tmp_path)
    home = data / "profiles" / "operator"
    state = {"pid": os.getpid(), "gateway_state": "running", "active_agents": 2}
    (home / "gateway_state.json").write_text(json.dumps(state))
    db = home / "state.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, timestamp REAL)")
    con.execute("INSERT INTO messages (timestamp) VALUES (?)", (time.time() - 7200,))
    con.commit()
    before = sorted(p.name for p in home.iterdir())
    facts = busy.parse_probe(_run_remote("remote_busy_probe.py", tmp_path))
    # The read leaves nothing behind (it runs as root on a seat).
    assert sorted(p.name for p in home.iterdir()) == before
    (gw,) = facts["gateways"]
    assert gw["quiet_s"] is not None and gw["quiet_s"] > 7000
    assert not busy.decide(facts).busy
    con.execute("INSERT INTO messages (timestamp) VALUES (?)", (time.time(),))
    con.commit()
    facts = busy.parse_probe(_run_remote("remote_busy_probe.py", tmp_path))
    assert facts["gateways"][0]["quiet_s"] < 60
    assert busy.decide(facts).busy
    con.close()


def test_a_gateway_that_never_held_a_conversation_is_quiet_not_unknown(tmp_path: Path) -> None:
    # ap-staging 2026-11-10: the twin had never been emailed, its messages table was
    # empty, MAX(timestamp) was NULL, quiet_s read as unknown, and a stale
    # active_agents=1 held every release to the twin (and so to A&P) all night.
    # FALSIFIER: drop the empty-table branch and the first verdict is busy forever.
    import sqlite3

    _, data = _seat(tmp_path)
    home = data / "profiles" / "operator"
    state = {"pid": os.getpid(), "gateway_state": "running", "active_agents": 1}
    (home / "gateway_state.json").write_text(json.dumps(state))
    db = home / "state.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, timestamp REAL)")
    con.commit()
    con.close()
    old = time.time() - 7200
    os.utime(db, (old, old))
    facts = busy.parse_probe(_run_remote("remote_busy_probe.py", tmp_path))
    (gw,) = facts["gateways"]
    assert gw["quiet_s"] is not None and gw["quiet_s"] > 7000
    assert not busy.decide(facts).busy
    # A store written a moment ago is still recent, empty or not.
    os.utime(db, None)
    facts = busy.parse_probe(_run_remote("remote_busy_probe.py", tmp_path))
    assert facts["gateways"][0]["quiet_s"] < 60
    assert busy.decide(facts).busy


def test_the_probe_reads_the_gateway_turn_count(tmp_path: Path) -> None:
    _, data = _seat(tmp_path)
    state = {"pid": os.getpid(), "gateway_state": "running", "active_agents": 3}
    (data / "profiles" / "operator" / "gateway_state.json").write_text(json.dumps(state))
    facts = busy.parse_probe(_run_remote("remote_busy_probe.py", tmp_path))
    assert [(g["active_agents"], g["alive"]) for g in facts["gateways"]] == [(3, True)]
    assert busy.decide(facts).busy


def test_remote_probe_on_a_seat_without_runners_is_idle(tmp_path: Path) -> None:
    out = _run_remote("remote_busy_probe.py", tmp_path)
    facts = busy.parse_probe(out)
    assert facts["run_dir_present"] is False
    assert not busy.decide(facts).busy


def test_quiesce_set_and_clear_only_its_own_marker(tmp_path: Path) -> None:
    run, _ = _seat(tmp_path)
    src = busy.quiesce_source("set", "fleet:me", 2_000_000_000.0, "rollout")
    out = _run_remote("remote_quiesce.py", tmp_path, prefix=src.split("\n", 2)[0] + "\n" + src.split("\n", 2)[1] + "\n")
    assert "SMD_QUIESCE set" in out
    marker = json.loads((run / "quiesce").read_text())
    assert marker["by"] == "fleet:me" and marker["until"] == 2_000_000_000.0
    # another holder's clear keeps it; a supervisor marker survives a fleet-prefix clear
    other = busy.quiesce_source("clear", "fleet:other")
    out = _run_remote("remote_quiesce.py", tmp_path, prefix="\n".join(other.split("\n", 2)[:2]) + "\n")
    assert "kept" in out and (run / "quiesce").exists()
    (run / "quiesce").write_text(json.dumps({"by": "supervisor", "until": 1}))
    anyfleet = busy.quiesce_source("clear", "fleet:x", prefix=busy.MARKER_PREFIX)
    out = _run_remote("remote_quiesce.py", tmp_path, prefix="\n".join(anyfleet.split("\n", 2)[:2]) + "\n")
    assert "kept" in out
    (run / "quiesce").write_text(json.dumps({"by": "fleet:old-laptop", "until": 1}))
    out = _run_remote("remote_quiesce.py", tmp_path, prefix="\n".join(anyfleet.split("\n", 2)[:2]) + "\n")
    assert "cleared" in out and not (run / "quiesce").exists()


# --- transport failures while waiting (captain-mark, 2026-10-26) -------------------

FLY_API_EOF = 'Error: get app: Get "https://api.machines.dev/v1/apps/hermes-captain-mark": EOF'
_BUSY = {"lanes": [], "children": [], "crons": [{"name": "slot", "next_in_s": 60}], "errors": []}


class _DroppingFly(FakeFly):
    """A FakeFly whose busy probes lose their connection on the listed call numbers."""

    def __init__(self, drop_probe_calls: set[int], text: str = FLY_API_EOF) -> None:
        super().__init__()
        self.drop_probe_calls = drop_probe_calls
        self.text = text
        self.probe_calls = 0

    def run_python(self, app: str, source: str, timeout: float = 90) -> Result:
        if "SMD_QUIESCE" not in source:
            self.probe_calls += 1
            if self.probe_calls in self.drop_probe_calls:
                return Result(1, "", self.text)
        return super().run_python(app, source, timeout)


def _gate(fly: FakeFly) -> busy.BusyGate:
    clock = [0.0]

    def sleep(s: float) -> None:
        clock[0] += s

    return busy.BusyGate(fly, "test", sleep=sleep, clock=lambda: clock[0])


def test_a_bare_fly_api_eof_is_a_transport_failure() -> None:
    # FALSIFIER: drop the '": eof' marker and the exact error that halted ring 2
    # reads as a seat failure instead of a dropped connection.
    assert looks_like_transport_failure(Result(1, "", FLY_API_EOF))
    assert not looks_like_transport_failure(Result(1, "", "Error: the release did not come up"))


def test_a_dropped_connection_while_waiting_is_retried() -> None:
    # Busy, then the next poll loses its connection once, then idle.
    fly = _DroppingFly({2})
    fly.probe_facts["captain-mark"] = [_BUSY, _facts(), _facts()]
    log: list[str] = []
    verdict = _gate(fly).wait_idle("captain-mark", 3600, log.append)
    assert not verdict.busy
    assert any("lost its connection" in line for line in log)
    assert fly.probe_calls == 3


def test_a_dropped_connection_on_the_first_probe_is_retried() -> None:
    fly = _DroppingFly({1})
    assert not _gate(fly).wait_idle("captain-mark", 3600, lambda _: None).busy
    assert fly.probe_calls == 2


def test_a_connection_that_stays_down_still_halts() -> None:
    # FALSIFIER: retry forever (or swallow the error) and an unreachable seat is
    # waited on to the cap and then read as merely busy, instead of halting.
    fly = _DroppingFly(set(range(1, 100)))
    with pytest.raises(busy.TransportFailure, match="attempts in a row"):
        _gate(fly).wait_idle("captain-mark", 3600, lambda _: None)
    assert fly.probe_calls == busy.TRANSPORT_ATTEMPTS


def test_a_probe_that_answers_badly_halts_at_once() -> None:
    # Only transport failures are retried: a probe that ran and failed is an
    # answer about the seat.
    fly = _DroppingFly({1}, text="Traceback (most recent call last): KeyError")
    with pytest.raises(ToolingError) as caught:
        _gate(fly).wait_idle("captain-mark", 3600, lambda _: None)
    assert not isinstance(caught.value, busy.TransportFailure)
    assert fly.probe_calls == 1


# --- a seat with no Machine yet (a first provision, 2026-11-10) ---------------------
#
# Real Fly, real BusyGate, real `fleet busy` CLI: only the subprocess layer (the
# Runner protocol, i.e. the `fly` binary) is scripted, so what is tested is the
# listing as Fly.machines parses it and the verdict as the gate reaches it.

REPO = Path(__file__).resolve().parents[3]


class _FlyBinary:
    """Answers `fly status`, `fly machines list` and `fly machine exec` as scripted."""

    def __init__(self, machines: Result, probe_facts: dict | None = None) -> None:
        self.machines = machines
        self.probe_facts = probe_facts if probe_facts is not None else _facts()
        self.calls: list[list[str]] = []

    def run(self, argv, *, input_text=None, timeout=120, cwd=None, env=None) -> Result:
        argv = list(argv)
        self.calls.append(argv)
        if argv[1] == "status":
            return Result(0, "{}", "")
        if argv[1:3] == ["machines", "list"]:
            return self.machines
        if argv[1:3] == ["machine", "exec"]:
            return Result(0, "SMD_BUSY_PROBE " + json.dumps(self.probe_facts), "")
        raise AssertionError(f"unexpected fly call: {argv}")


def _real_fly(binary: _FlyBinary) -> Fly:
    return Fly(binary, str(REPO), FlyTokens({}))  # type: ignore[arg-type]


def _fleet_busy(binary: _FlyBinary, *extra: str) -> tuple[int, list[str]]:
    logs: list[str] = []
    ctx = cli.Ctx(REPO, binary, FakeStore(), FakeR2(), _real_fly(binary), sleep=lambda s: None, log=logs.append)  # type: ignore[arg-type]
    return cli.main(["busy", "smd-guide", *extra], ctx), logs


def test_zero_machines_is_idle_not_a_tooling_error(capsys: pytest.CaptureFixture[str]) -> None:
    # FALSIFIER: on the unfixed gate this raised "hermes-smd-guide has no Machine to
    # run a command on" (exit 3), and a first provision needed FLEET_FORCE_MID_JOB=1.
    binary = _FlyBinary(Result(0, "[]", ""))
    verdict = _gate(_real_fly(binary)).probe("smd-guide")  # type: ignore[arg-type]
    assert not verdict.busy and verdict.reasons == [busy.NO_MACHINE_REASON]
    assert not any(c[1:3] == ["machine", "exec"] for c in binary.calls)
    rc, _ = _fleet_busy(binary, "--wait")
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out == {"seat": "smd-guide", "busy": False, "reasons": [busy.NO_MACHINE_REASON]}


def test_a_failed_machine_listing_is_a_tooling_error_never_idle(capsys: pytest.CaptureFixture[str]) -> None:
    # FALSIFIER: read "could not list" as "no Machines" and a seat that cannot be
    # reached mid-job is restarted by a reprovision.
    for failure in (
        Result(1, "", "Error: unauthorized (Request ID: 01M5WVSBZ3EP8ENTTX85MA964K-lax)"),
        Result(1, "", FLY_API_EOF),
        Result(124, "", "timed out after 180s"),
    ):
        binary = _FlyBinary(failure)
        with pytest.raises(ToolingError):
            _gate(_real_fly(binary)).probe("smd-guide")  # type: ignore[arg-type]
        rc, _ = _fleet_busy(binary)
        captured = capsys.readouterr()
        assert rc == 3 and "TOOLING ERROR" in captured.out + captured.err
        assert '"busy": false' not in captured.out


def test_a_seat_with_a_machine_is_probed_as_before() -> None:
    one = Result(0, '[{"id": "6e8206d0f36d08", "state": "started"}]', "")
    binary = _FlyBinary(one)
    assert not _gate(_real_fly(binary)).probe("smd-guide").busy  # type: ignore[arg-type]
    assert any(c[1:4] == ["machine", "exec", "6e8206d0f36d08"] for c in binary.calls)
    binary = _FlyBinary(one, probe_facts=_BUSY)
    verdict = _gate(_real_fly(binary)).probe("smd-guide")  # type: ignore[arg-type]
    assert verdict.busy and verdict.reasons == ["cron slot fires in 60s"]
    rc, _ = _fleet_busy(binary)
    assert rc == 1


def test_unquiesce_on_a_seat_with_no_machine_says_so() -> None:
    binary = _FlyBinary(Result(0, "[]", ""))
    assert "no Machine" in _gate(_real_fly(binary)).clear_any_fleet("smd-guide")  # type: ignore[arg-type]
    assert not any(c[1:3] == ["machine", "exec"] for c in binary.calls)
