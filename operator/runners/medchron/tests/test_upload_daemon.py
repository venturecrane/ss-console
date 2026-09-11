"""The upload stage (idempotent through the recorded folder id) and the
on-Machine daemon (queue, one at a time, quarantine, pause-once, resume,
wipe, heartbeat)."""

from __future__ import annotations

import json
import os
import stat
import sqlite3
from pathlib import Path

import pytest
import yaml

from medchron import config as config_mod, job as job_mod
from medchron.daemon import BrokerError, Daemon, memory_cap_mode, sticky_level
from medchron.stages import upload
from medchron.stages.base import StageRun
from medchron_testkit import FIRM_CONFIG, FakeSeat


# ---- upload ------------------------------------------------------------------


def _upload_sr(job_dir: Path, firm: Path, data_root: Path, seat: FakeSeat, log: list[str]) -> StageRun:
    job = job_mod.load(job_dir)
    cfg = config_mod.load(str(firm))
    sd = data_root / "example-matter"
    out = sd / "out" / "alpha"
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, body in (
        ("Alpha Example - Medical Chronology 08-29-26.docx", b"docx-bytes"),
        ("Exhibit 1 - Example Clinic - 01-20-2026 (Medical Records).pdf", b"%PDF-1"),
    ):
        (out / name).write_bytes(body)
        import hashlib

        rows.append(
            {
                "name": name,
                "folder": "MEDICAL CHRONOLOGY - Alpha Example 08-29-26",
                "local_path": str(out / name),
                "sha256": hashlib.sha256(body).hexdigest(),
                "bytes": len(body),
            }
        )
    (out / "upload_manifest.json").write_text(json.dumps(rows))
    (sd / "runs" / "alpha").mkdir(parents=True, exist_ok=True)
    return StageRun(
        job=job,
        cfg=cfg,
        unit=job.units[0],
        slug_dir=sd,
        decided={},
        log=log.append,
        seat_factory=lambda: seat,
        date_stamp="08-29-26",
    )


def test_upload_creates_the_folder_records_it_first_and_reads_back(job_dir, firm_config_path, data_root):
    seat = FakeSeat([], [], {})
    seat.lag = 2  # the vendor's index lag: two lists before it shows
    log: list[str] = []
    sr = _upload_sr(job_dir, firm_config_path, data_root, seat, log)
    assert upload.run(sr, pause=0, tries=5) == 0
    assert [c["name"] for c in seat.created] == ["MEDICAL CHRONOLOGY - Alpha Example 08-29-26"]
    assert [s["name"][:9] for s in seat.sent] == ["Alpha Exa", "Exhibit 1"]
    d = json.loads((sr.slug_dir / "runs" / "alpha" / "delivery.json").read_text())
    assert d["folder_id"] == "folder-1" and all(f["confirmed"] for f in d["files"]) and len(d["files"]) == 2
    assert any("read-back complete" in line for line in log)


def test_upload_refuses_a_folder_it_did_not_create(job_dir, firm_config_path, data_root):
    seat = FakeSeat(
        [], [{"id": "theirs", "name": "MEDICAL CHRONOLOGY - Alpha Example 08-29-26", "parentId": None, "path": "x"}], {}
    )
    log: list[str] = []
    sr = _upload_sr(job_dir, firm_config_path, data_root, seat, log)
    assert upload.run(sr, pause=0, tries=2) == 1
    assert not seat.created and not seat.sent and any("did not create it" in line for line in log)


def test_upload_resumes_after_a_crash_between_create_and_add(job_dir, firm_config_path, data_root):
    seat = FakeSeat([], [], {})
    seat.crash_after = 1  # the second add_file dies
    log: list[str] = []
    sr = _upload_sr(job_dir, firm_config_path, data_root, seat, log)
    with pytest.raises(RuntimeError):
        upload.run(sr, pause=0, tries=2)
    d = json.loads((sr.slug_dir / "runs" / "alpha" / "delivery.json").read_text())
    assert d["folder_id"] == "folder-1"  # recorded before the first add_file
    seat.crash_after = None
    assert upload.run(sr, pause=0, tries=3) == 0
    assert len(seat.created) == 1 and [s["name"][:9] for s in seat.sent] == ["Alpha Exa", "Exhibit 1"]
    assert any("already present" in line and "1 file(s) sent" in line for line in log)


def test_upload_holds_when_the_read_back_stays_short(job_dir, firm_config_path, data_root):
    seat = FakeSeat([], [], {})
    seat.lag = 50
    log: list[str] = []
    sr = _upload_sr(job_dir, firm_config_path, data_root, seat, log)
    assert upload.run(sr, pause=0, tries=3) == 2
    d = json.loads((sr.slug_dir / "runs" / "alpha" / "delivery.json").read_text())
    assert not any(f["confirmed"] for f in d["files"])


def test_upload_refuses_when_local_bytes_changed_since_the_manifest(job_dir, firm_config_path, data_root):
    seat = FakeSeat([], [], {})
    log: list[str] = []
    sr = _upload_sr(job_dir, firm_config_path, data_root, seat, log)
    (sr.slug_dir / "out" / "alpha" / "Alpha Example - Medical Chronology 08-29-26.docx").write_bytes(b"tampered")
    assert upload.run(sr, pause=0, tries=1) == 1 and not seat.sent


# ---- daemon -------------------------------------------------------------------


class FakeBroker:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.records: list[tuple[str, str, dict]] = []
        self.down = False
        # The month's state the daemon re-reads before every run. `month` is a
        # dict the test can mutate between ticks, which is how "fresh, not the
        # envelope's stale copy" is made observable.
        self.month = {
            "month": "2026-09",
            "allowance": 40,
            "used": 0,
            "remaining": 40,
            "authored": True,
            "unit": "pages",
            "pages_used": 0,
            "pages_remaining": 40,
            "documents_used": 0,
            "cents_used": 0,
        }
        self.allowance_calls: list[str | None] = []

    def status(self, job_id):
        if self.down:
            raise BrokerError("down")
        return self.rows.get(job_id)

    def allowance(self, exclude_job_id=None):
        self.allowance_calls.append(exclude_job_id)
        if self.down:
            raise BrokerError("down")
        return dict(self.month)

    def record(self, job_id, state, fields):
        if self.down:
            raise BrokerError("down")
        self.records.append((job_id, state, fields))
        self.rows.setdefault(job_id, {})["state"] = state
        return {"ok": True}


def _fake_runner(tmp_path: Path, script: str) -> list[str]:
    p = tmp_path / "fake-medchron.py"
    p.write_text(script)
    return [os.environ.get("PYTHON", "python3"), str(p)]


OK_RUNNER = """
import json, sys, pathlib
jd = pathlib.Path(sys.argv[1])
assert jd.joinpath('job.yaml').is_file()
marker = jd / 'ran'
n = int(marker.read_text()) + 1 if marker.exists() else 1
marker.write_text(str(n))
if n == 1 and (jd / 'crash-once').exists():
    sys.exit(137)
print(json.dumps([{"unit": "alpha", "outcome": "delivered", "reason": None, "stage": None, "dollars": 12.34,
                   "pages": 300, "documents": 40, "folder_id": "folder-9",
                   "files": [{"name": "A.docx", "sha256": "ab", "bytes": 10, "confirmed": True}]}]))
"""


# Captured VERBATIM from the broker's own MedchronLedger.submit() (2026-09-09,
# `validate_envelope(...)` then `submit(env, remaining=40)`, job_id and
# submitted_at edited to fixed values). This is the W1/W2 wire: if either side
# renames a key, the round-trip test below goes red rather than a seat
# discovering it.
BROKER_SUBMIT_ENVELOPE = {
    "allowance_remaining_documents": 40,
    "allowance_remaining_pages": 40,
    "cap_usd": 25.0,
    "incident": {"date": "2026-01-15", "source": "administrator_request"},
    "job_id": "01Q",
    "matter": {"id": "m-1", "number": "2026-PI-102", "title": "Example v. Example"},
    "requested_by": "admin@example.test",
    "submitted_at": "2026-09-09T17:11:01.062Z",
    "units": [
        {
            "client_name": "Alpha Example",
            "dob": "01/02/1980",
            "name_token": "Example",
            "surname": "Example",
            "unit": "alpha",
        }
    ],
}


def _envelope(job_id: str) -> dict:
    return {
        "job_id": job_id,
        "matter": {"id": "m-1", "number": "2026-PI-102", "title": ""},
        "units": [
            {
                "unit": "alpha",
                "client_name": "Alpha Example",
                "name_token": "Example",
                "surname": "Example",
                "dob": "01/02/1980",
            }
        ],
        "incident": {"date": "2026-01-15", "source": "administrator_request"},
        "allowance_remaining_documents": 100,
        "cap_usd": 25,
    }


def _firm_yaml(tmp_path: Path) -> Path:
    """The daemon refuses to start a job while the firm config does not
    validate, so every daemon test needs one on disk."""
    import yaml as _yaml

    p = tmp_path / "firm.yaml"
    if not p.is_file():
        p.write_text(_yaml.safe_dump(FIRM_CONFIG, sort_keys=False), encoding="utf-8")
    return p


def _daemon(tmp_path: Path, script: str = OK_RUNNER, **kw) -> tuple[Daemon, FakeBroker]:
    run_dir = tmp_path / "run"
    (run_dir / "queue").mkdir(parents=True, exist_ok=True)
    broker = FakeBroker()
    now = {"t": 1_000_000.0}
    kw.setdefault("firm_config", str(_firm_yaml(tmp_path)))
    d = Daemon(
        run_dir=run_dir,
        broker=broker,
        runner_cmd=_fake_runner(tmp_path, script),
        customer_slug="example",
        sticky_db=str(tmp_path / "sticky.db"),
        cgroup_root=tmp_path / "cgroup",
        child_uid=None,
        clock=lambda: now["t"],
        **kw,
    )
    d._now = now  # type: ignore[attr-defined]
    return d, broker


def _submit(d: Daemon, broker: FakeBroker, job_id: str) -> None:
    broker.rows[job_id] = {"state": "submitted"}
    (d.queue / f"{job_id}.json").write_text(json.dumps(_envelope(job_id)))


def test_daemon_runs_the_oldest_job_reports_transitions_and_ticks(tmp_path):
    d, broker = _daemon(tmp_path)
    _submit(d, broker, "01B")
    _submit(d, broker, "01A")
    assert d.tick() == "delivered"
    assert [(j, s) for j, s, _ in broker.records] == [("01A", "running"), ("01A", "delivered")]
    fields = broker.records[-1][2]
    assert (fields["documents"], fields["pages"], fields["cents"], fields["folder_id"]) == (40, 300, 1234, "folder-9")
    assert fields["delivery"]["files"][0]["name"] == "A.docx"
    job_yaml = (d.jobs / "01A" / "job.yaml").read_text()
    assert "slug: example" in job_yaml and "allowance_remaining_documents: 100" in job_yaml and "data_root:" in job_yaml
    # The envelope the child parses: a fresh per-job data_root, and the
    # install root pointed at the run dir the entrypoint seeds with the
    # classifier's controls and the ICD tables (a job's own data_root can
    # never carry them, 2026-09-04).
    job = job_mod.load(d.jobs / "01A")
    assert job.data_root == d.jobs / "01A" / "data" and job.install_root == d.run_dir
    hb = json.loads((d.run_dir / "heartbeat.json").read_text())
    assert hb["memory_cap"] == "none" and hb["queued"] == 1 and (d.run_dir / "tick").exists()
    assert not (d.run_dir / "child.pid").exists()
    assert d.tick() == "delivered" and broker.records[-1][0] == "01B"
    assert d.tick() is None


def test_daemon_quarantines_an_envelope_without_a_ledger_row(tmp_path):
    d, broker = _daemon(tmp_path)
    (d.queue / "01Z.json").write_text(json.dumps(_envelope("01Z")))
    assert d.tick() is None
    assert (d.queue / "quarantine" / "01Z.json").exists() and not broker.records


def test_daemon_holds_once_at_hard_stop_and_releases(tmp_path):
    d, broker = _daemon(tmp_path)
    conn = sqlite3.connect(d.sticky_db)
    conn.execute("CREATE TABLE sticky_stop_state (customer TEXT, persona TEXT, level TEXT, updated_at TEXT)")
    conn.execute("INSERT INTO sticky_stop_state VALUES ('example', '_machine', 'HARD_STOP', 'now')")
    conn.commit()
    _submit(d, broker, "01A")
    assert d.tick() == "paused" and d.tick() == "paused"
    assert [(j, s) for j, s, _ in broker.records] == [("01A", "held")]  # once
    conn.execute("UPDATE sticky_stop_state SET level='OK'")
    conn.commit()
    conn.close()
    assert d.tick() == "delivered"
    assert [s for _, s, _ in broker.records] == ["held", "running", "delivered"]


def test_sticky_level_reads_the_worst_row_and_fails_toward_unknown(tmp_path):
    assert sticky_level(str(tmp_path / "none.db")) is None
    p = tmp_path / "s.db"
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE sticky_stop_state (customer TEXT, persona TEXT, level TEXT)")
    conn.executemany("INSERT INTO sticky_stop_state VALUES (?,?,?)", [("x", "a", "OK"), ("x", "b", "SOFT_STOP")])
    conn.commit()
    conn.close()
    assert sticky_level(str(p)) == "SOFT_STOP"
    p.write_bytes(b"not a database")
    assert sticky_level(str(p)) == "unknown"


def test_daemon_resumes_a_job_after_a_crash(tmp_path):
    d, broker = _daemon(tmp_path)
    _submit(d, broker, "01A")
    jd = d.jobs / "01A"
    jd.mkdir(parents=True)
    (jd / "crash-once").write_text("")
    assert d.tick() == "failed"  # exit 137, no verdict
    assert broker.records[-1][1] == "failed" and "exited 137" in broker.records[-1][2]["reason"]
    # A kill mid-run leaves no terminal record at all: the daemon restarts and finds the claimed job.
    d2, broker2 = _daemon(tmp_path)
    broker2.rows["01A"] = {"state": "running"}
    d2._write_state("01A", claimed=True, state="running", attempts=1)
    assert d2.tick() == "delivered"
    assert d2._daemon_state("01A")["attempts"] == 2


def test_daemon_wipes_terminal_jobs_after_the_hold_and_never_live_ones(tmp_path):
    d, broker = _daemon(tmp_path, wipe_hours=72)
    _submit(d, broker, "01A")
    d.tick()
    assert (d.jobs / "01A").exists()
    d._now["t"] += 71 * 3600
    assert d.wipe_expired() == [] and (d.jobs / "01A").exists()
    d._now["t"] += 2 * 3600
    assert d.wipe_expired() == ["01A"] and not (d.jobs / "01A").exists()
    _submit(d, broker, "01B")
    d.claim_next()
    d._now["t"] += 1000 * 3600
    assert d.wipe_expired() == [] and (d.jobs / "01B").exists()


def test_daemon_defers_when_the_broker_is_down_and_child_env_is_allow_listed(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "never")
    script = """
import json, os, sys
assert os.environ.get("ANTHROPIC_API_KEY") == "sk-test"
assert "R2_SECRET_ACCESS_KEY" not in os.environ
print(json.dumps([{"unit": "alpha", "outcome": "refused", "reason": "cap", "dollars": 0.0, "pages": 5,
                   "documents": 3}]))
"""
    d, broker = _daemon(tmp_path, script)
    _submit(d, broker, "01A")
    broker.down = True
    assert d.tick() is None and not broker.records  # cannot even claim
    broker.down = False
    assert d.tick() == "held"
    assert broker.records[-1][1] == "held" and broker.records[-1][2]["reason"] == "refused: cap"
    # A runner-held job is PARKED, not retried (live-caught 2026-08-31: a
    # cap-refused job re-ran every tick, one RUNNING/HELD audit pair per
    # cycle). Only the sticky-stop pause hold resumes by itself.
    before = len(broker.records)
    assert d.tick() is None and d.tick() is None
    assert len(broker.records) == before


def test_memory_cap_mode_detects_v2_v1_and_none(tmp_path):
    assert memory_cap_mode(tmp_path) == "none"
    # The Fly hybrid layout: no root cgroup.controllers, memory on the v1 mount.
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "cgroup.procs").write_text("")
    assert memory_cap_mode(tmp_path) == "cgroup1"
    (tmp_path / "cgroup.controllers").write_text("cpuset cpu io memory pids\n")
    assert memory_cap_mode(tmp_path) == "cgroup2"


def test_cgroup1_preexec_writes_the_v1_limit(tmp_path):
    d, broker = _daemon(tmp_path)
    (d.cgroup_root / "memory").mkdir(parents=True)
    (d.cgroup_root / "memory" / "cgroup.procs").write_text("")
    assert d._cgroup_preexec() is not None
    assert (d.cgroup_root / "memory" / "medchron" / "memory.limit_in_bytes").read_text() == str(d.memory_max)
    _submit(d, broker, "01A")
    d.tick()
    assert json.loads((d.run_dir / "heartbeat.json").read_text())["memory_cap"] == "cgroup1"


def test_daemon_heartbeat_is_world_readable(tmp_path):
    d, _ = _daemon(tmp_path)
    d.heartbeat(running=None)
    assert stat.S_IMODE((d.run_dir / "heartbeat.json").stat().st_mode) == 0o644


def test_cli_json_stdout_is_only_the_verdict(job_dir, firm_config_path, pricing_path, capsys, monkeypatch):
    # Live-caught 2026-08-31: the driver's default log=print interleaved [run]
    # lines with the --json report and the daemon recorded a real refusal as
    # "exited 4 without a verdict". With --json, stdout must parse whole.
    from medchron.__main__ import main

    monkeypatch.delenv("MEDCHRON_PIPELINE_DIR", raising=False)
    code = main(
        [
            "run",
            str(job_dir),
            "--dry-run",
            "--json",
            "--firm-config",
            str(firm_config_path),
            "--pricing",
            str(pricing_path),
        ]
    )
    captured = capsys.readouterr()
    outcomes = json.loads(captured.out)
    assert code == 0 and isinstance(outcomes, list) and outcomes[0]["outcome"] == "dry_run"


# ---- the deliver wake (ss#2616) -----------------------------------------------


class _FakeGate:
    """A local HTTP gate the daemon POSTs its wake to."""

    def __init__(self, status=202, body=b'{"accepted": true}'):
        import http.server
        import threading

        self.requests: list[dict] = []
        self.status, self.body = status, body
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                outer.requests.append(
                    {"path": self.path, "auth": self.headers.get("Authorization"), "body": json.loads(raw)}
                )
                self.send_response(outer.status)
                self.send_header("Content-Length", str(len(outer.body)))
                self.end_headers()
                self.wfile.write(outer.body)

            def log_message(self, *a):
                pass

        self._srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self._srv.server_address[1]}"
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    def close(self):
        self._srv.shutdown()


def _wake_daemon(tmp_path, gate_url, script=OK_RUNNER):
    d, broker = _daemon(tmp_path, script)
    d.wake_secret = "shh-wake"
    d.gate_url = gate_url
    return d, broker


def test_a_delivered_job_wakes_the_agent_with_authored_fields_only(tmp_path):
    gate = _FakeGate()
    d, broker = _wake_daemon(tmp_path, gate.url)
    _submit(d, broker, "01A")
    assert d.tick() == "delivered"
    d.tick()  # the wake dispatches on the next tick
    gate.close()
    assert len(gate.requests) == 1
    req = gate.requests[0]
    assert req["path"] == "/webhooks/handoff" and req["auth"] == "Bearer shh-wake"
    task = req["body"]["task"]
    assert req["body"]["handoff_id"] == "medchron-01A"
    assert "DELIVER" in task and "2026-PI-102" in task and "folder-9" in task
    assert "A.docx" not in task and "Alpha" not in task and "01/02/1980" not in task
    assert (d._daemon_state("01A").get("wake") or {}).get("pending") is False
    hb = json.loads((d.run_dir / "heartbeat.json").read_text())
    assert hb["wakes_pending"] == 0 and hb["wakes_failed"] == 0


def test_wake_failures_retry_then_write_the_loud_loss(tmp_path):
    gate = _FakeGate(status=404, body=b'{"error": "unknown route"}')
    d, broker = _wake_daemon(tmp_path, gate.url)
    _submit(d, broker, "01A")
    d.tick()
    for _ in range(6):
        d.tick()
    gate.close()
    wake = d._daemon_state("01A")["wake"]
    assert wake["pending"] is False and wake["outcome"].startswith("failed")
    assert d.wakes_failed == 1
    # The loss is an audit artifact: a same-state record carrying wake metadata.
    assert broker.records[-1][1] == "delivered" and broker.records[-1][2]["wake"]["wake_failed"] is True


def test_a_paused_seat_never_consumes_wake_attempts(tmp_path):
    gate = _FakeGate(status=503, body=b'{"error": "operator paused"}')
    d, broker = _wake_daemon(tmp_path, gate.url)
    _submit(d, broker, "01A")
    d.tick()
    for _ in range(8):
        d.tick()
    gate.close()
    wake = d._daemon_state("01A")["wake"]
    assert wake["pending"] is True and int(wake.get("attempts", 0)) == 0


# A decision hold's reason quotes the matter: the files it could not place.
HELD_RUNNER = """
import json, sys
print(json.dumps([{"unit": "alpha", "outcome": "held", "stage": "decide_orphans",
                   "reason": "1 pulled file(s) owned by no unit and matching no exclusion class: "
                             "['Alpha Example - Example Clinic records 2026.pdf']",
                   "dollars": 0.0, "pages": 5, "documents": 3}]))
"""


def test_a_held_job_wakes_with_the_stage_and_never_the_reason(tmp_path):
    d, broker = _wake_daemon(tmp_path, "http://127.0.0.1:1", script=HELD_RUNNER)  # nothing listens
    _submit(d, broker, "01B")
    assert d.tick() == "held"
    # The ledger keeps the full reason for a person reading their own tenant.
    assert broker.records[-1][1] == "held" and "Example Clinic records 2026.pdf" in broker.records[-1][2]["reason"]
    wake = d._daemon_state("01B")["wake"]
    task = wake["task"]
    # The wake names WHERE the run stopped in the DAG's own vocabulary and
    # nothing the tenant authored: no file name, no client name, no reason.
    assert wake["pending"] is True and "Held at: decide_orphans." in task and "rehearsal" in task
    assert "Example Clinic" not in task and "Alpha" not in task and ".pdf" not in task and "Reason:" not in task
    d.tick()
    assert int(d._daemon_state("01B")["wake"]["attempts"]) == 1


def test_a_runner_with_no_verdict_wakes_without_a_stage(tmp_path):
    d, broker = _wake_daemon(tmp_path, "http://127.0.0.1:1", script="import sys\nsys.exit(3)\n")
    _submit(d, broker, "01C")
    assert d.tick() == "failed"
    task = d._daemon_state("01C")["wake"]["task"]
    assert "Outcome: failed." in task and "Held at: runner (no verdict)." in task and "exited 3" not in task


# ---- the month's state, read fresh per run (2026-09-09) -----------------------
def test_every_run_reads_the_month_fresh_and_excludes_its_own_row(tmp_path):
    """The envelope's copy of the month was true at submission and is stale the
    moment any other job records cents, so the daemon re-reads before every run
    and every resume. Its own row is excluded, or a resume would be metered
    against the spend it already recorded."""
    d, broker = _daemon(tmp_path)
    _submit(d, broker, "01A")
    assert d.tick() == "delivered"
    first = yaml.safe_load((d.jobs / "01A" / "job.yaml").read_text())
    assert first["allowance_remaining_pages"] == 40 and first["month_cents_used"] == 0
    assert first["allowance_pages"] == 40 and first["month_pages_used"] == 0
    assert first["allowance_month"] == "2026-09"
    assert broker.allowance_calls == ["01A"]

    broker.month.update(remaining=5, pages_used=35, cents_used=1234, used=35)
    _submit(d, broker, "01B")
    assert d.tick() == "delivered"
    second = yaml.safe_load((d.jobs / "01B" / "job.yaml").read_text())
    assert second["allowance_remaining_pages"] == 5 and second["month_cents_used"] == 1234
    assert second["month_pages_used"] == 35
    assert broker.allowance_calls == ["01A", "01B"]


def test_a_job_defers_when_the_allowance_read_fails(tmp_path):
    """Deferred, not failed: the job stays claimed, nothing is recorded, and
    the next tick tries again."""
    d, broker = _daemon(tmp_path)
    _submit(d, broker, "01A")
    assert d.claim_next() == "01A"
    broker.down = True
    assert d.run_job("01A") == "deferred"
    assert broker.records == []
    broker.down = False
    assert d.tick() == "delivered"


def test_a_job_defers_while_the_firm_config_does_not_validate(tmp_path):
    """The rollout is safe in either order: customer.yaml and firm.yaml can
    land minutes apart, and a job queued in between waits rather than failing.
    Nothing is recorded, so the ledger carries no phantom transition."""
    d, broker = _daemon(tmp_path, firm_config=str(tmp_path / "absent-firm.yaml"))
    _submit(d, broker, "01A")
    assert d.tick() == "deferred"
    assert broker.records == [] and not (d.jobs / "01A" / "job.yaml").exists()
    # The firm config lands; the same claimed job runs on the next tick.
    d.firm_config = str(_firm_yaml(tmp_path))
    assert d.tick() == "delivered"
    assert [s for _, s, _ in broker.records] == ["running", "delivered"]


LIMIT_HELD_RUNNER = """
import json
print(json.dumps([{"unit": "alpha", "outcome": "held", "stage": "vision", "dollars": 0.0, "pages": 3312,
                   "documents": 0,
                   "reason": "chronology_package_page_allowance_per_month: the matter's file is 3,312 pages "
                             "and 500 pages remain in this cycle's allowance; the package was not started"}]))
"""


def test_a_limit_hold_is_recorded_as_a_hold_and_never_prefixed_refused(tmp_path):
    """A contract hold is the product working. `refused:` is the daemon's
    prefix for the runner's REFUSED vocabulary (an unexplained file, a broken
    artifact) and must not be stamped on a limit the firm itself authored."""
    d, broker = _daemon(tmp_path, LIMIT_HELD_RUNNER)
    _submit(d, broker, "01A")
    assert d.tick() == "held"
    job_id, state, fields = broker.records[-1]
    assert state == "held" and fields["pages"] == 3312
    assert fields["reason"].startswith("chronology_package_page_allowance_per_month: ")
    assert not fields["reason"].startswith("refused: ")
    assert "Held at: vision." in d._daemon_state("01A")["wake"]["task"]


def test_a_real_broker_envelope_round_trips_into_a_job_the_runner_parses(tmp_path):
    """The W1/W2 wire contract, both halves in one test: an envelope in the
    exact shape the broker's own submit() writes goes through the daemon and
    out the other side as a Job the runner parses, with the page fields
    intact. A rename on either side breaks this."""
    d, broker = _daemon(tmp_path)
    envelope = dict(BROKER_SUBMIT_ENVELOPE)
    broker.rows["01Q"] = {"state": "submitted"}
    (d.queue / "01Q.json").write_text(json.dumps(envelope))
    assert d.claim_next() == "01Q"
    jd = d._write_job_yaml("01Q")
    job = job_mod.load(jd)
    assert job.allowance_remaining_pages == 40 and job.allowance_remaining_documents == 40
    assert job.month_cents_used == 0 and job.allowance_month == "2026-09"
    assert job.matter_number == "2026-PI-102" and job.units[0].unit == "alpha"
