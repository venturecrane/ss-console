"""The busy gate: the judgement (busy.decide) and the seat-side scripts, run for real.

The two remote scripts are executed here against a temporary directory laid
out like a seat (/run/smd-medchron, /opt/data/profiles/*/cron/jobs.json), with
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

from bin.lib.fleet import busy
from bin.lib.fleet.runner import Result, ToolingError, looks_like_transport_failure
from bin.tests.fleet_fakes import FakeFly

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
    assert busy.decide(_facts(errors=["/run/smd-medchron/heartbeat.json unreadable"])).busy


def _seat(tmp: Path) -> tuple[Path, Path]:
    run = tmp / "run" / "smd-medchron"
    data = tmp / "opt" / "data"
    run.mkdir(parents=True)
    (data / "profiles" / "operator" / "cron").mkdir(parents=True)
    return run, data


def _run_remote(name: str, tmp: Path, prefix: str = "") -> str:
    src = (HERE / name).read_text(encoding="utf-8")
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
