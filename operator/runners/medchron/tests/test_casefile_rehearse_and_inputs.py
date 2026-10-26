"""The $0 rehearsal (packs measured, no model call, nothing filed, no state
written), the closed-schema firm inputs, and the ranking decided in code."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
import yaml

import casefile_testkit as kit
from medchron.casefile import firm as firm_mod, rank as rank_mod, state as state_mod
from medchron.casefile.rehearse import RehearsalRun


def test_a_rehearsal_measures_every_pack_and_pays_and_files_nothing(tmp_path: Path, monkeypatch, pricing_path):
    for k, v in kit.environ(tmp_path).items():
        monkeypatch.setenv(k, v)
    seat = kit.CfSeat()
    report = tmp_path / "report.json"
    r = RehearsalRun(
        kit.make_job(tmp_path / "job"), report_path=report, inputs_dir=str(kit.make_inputs(tmp_path / "inputs")),
        pricing=str(pricing_path), state_dir=tmp_path / "state", seat_factory=lambda: seat, log=lambda m: None,
        today=lambda: dt.date(2026, 10, 26),
    )  # fmt: skip
    v = json.loads(r.run().to_json())
    assert v["verdict"] == "held" and v["reason"].startswith("rehearsal: 2 matters measured")
    got = json.loads(report.read_text())["rehearsal"]
    assert set(got["matters"]) == {"100001", "100002"}
    assert all(m["pack_chars"] > 0 and m["projected_input_tokens"] > 0 for m in got["matters"].values())
    assert v["cents"] == 0 and seat.uploads == 0
    assert state_mod.load_prior(tmp_path / "state", kit.M1) is None


def test_the_firm_inputs_are_a_closed_schema_with_their_prompts(tmp_path: Path):
    root = kit.make_inputs(tmp_path / "inputs")
    assert firm_mod.main([str(root)]) == 0
    doc = yaml.safe_load((root / "casefile-firm.yaml").read_text())
    (root / "casefile-firm.yaml").write_text(yaml.safe_dump({**doc, "read_wokers": 2}))
    with pytest.raises(firm_mod.CasefileConfigError, match="unknown key"):
        firm_mod.load(root)
    (root / "casefile-firm.yaml").write_text(yaml.safe_dump(doc))
    (root / "prompts" / "auditor.md").write_text("  ")
    with pytest.raises(firm_mod.CasefileConfigError, match="empty"):
        firm_mod.load(root)


def test_every_model_role_defaults_to_the_read_model_and_tiers_one_at_a_time(tmp_path: Path):
    f = firm_mod.load(kit.make_inputs(tmp_path / "a", models={"read": "m-read", "audit": "m-audit"}))
    assert (f.model("read"), f.model("audit"), f.model("screen")) == ("m-read", "m-audit", "m-read")


def _firm(tmp_path: Path) -> firm_mod.CasefileFirm:
    return firm_mod.load(kit.make_inputs(tmp_path / "inputs"))


def test_rank_is_code_a_near_statute_pulls_today_and_a_pending_file_is_never_on_track(tmp_path: Path):
    f, today = _firm(tmp_path), dt.date(2026, 10, 26)
    inv = {"matter": {"status": "Open"}, "files": [], "tasks_open": [], "tasks_done": [], "events": [], "memos": []}
    near = rank_mod.rank({"statute_date": "2026-11-20", "posture": "pre_demand"}, inv, f, today, set())
    assert near["priority"] == 1 and "11/20/26" in near["reason"]
    filed = rank_mod.rank({"statute_date": "2026-11-20", "posture": "litigation"}, inv, f, today, set())
    assert filed["priority"] == 4
    pending = rank_mod.rank({"posture": "unknown"}, {**inv, "matter": {"status": "Pending"}}, f, today, set())
    assert pending["priority"] == 2


def test_rank_reads_court_events_and_overdue_money_tasks_by_the_firms_words(tmp_path: Path):
    f, today = _firm(tmp_path), dt.date(2026, 10, 26)
    inv = {
        "matter": {"status": "Open"},
        "files": [],
        "tasks_done": [],
        "memos": [],
        "events": [{"startTime": "2026-11-02T09:00:00", "subject": "Hearing on motion"}],
        "tasks_open": [{"subject": "Confirm lien amount", "dueDate": "2026-10-01"}],
    }
    got = rank_mod.rank({"posture": "pre_demand"}, inv, f, today, set())
    assert got["priority"] == 1 and "Hearing" in got["reason"]
    assert [k["date"] for k in got["key_dates"]] == ["2026-10-01", "2026-11-02"]
