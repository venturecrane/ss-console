"""A stored prior value the gates would refuse is read again or checked against
its fetched text, never handed to the gates as-is (``seedcheck``), and a proof
run starts from a copy of the real state (``proof_run.seed_state``).

WHY (2026-10-13, A&P job 01M4VKNWGS33KFTWVNQZMMZ4V2). The live Irma list held
at the gates on the hand-run seed (save dates, dates without a source, case
statuses without a source) while the proof run of the same scope, started
from an EMPTY state, read everything fresh and passed."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from litigation_testkit import M1, LitSeat, ScriptedLit, make_inputs, make_job, matter_docs
from medchron import proof_run
from medchron.litigation import manifest, seedcheck, update, vocab
from medchron.litigation.firm import load
from medchron.litigation.run import LitigationRun

TODAY = dt.date(2026, 10, 7)


@pytest.fixture(autouse=True)
def _pricing(tmp_path: Path, pricing_path: Path) -> None:
    (tmp_path / "pricing.json").write_text(pricing_path.read_text())


def _run(tmp_path: Path, client: ScriptedLit, seat: LitSeat, job: str, job_id: str) -> LitigationRun:
    return LitigationRun(
        make_job(tmp_path / job, job_id=job_id),
        inputs_dir=str(make_inputs(tmp_path / "inputs")),
        pricing=str(tmp_path / "pricing.json"),
        state_dir=tmp_path / "state",
        seat_factory=lambda: seat,
        client=client,
        log=lambda m: None,
        today=lambda: TODAY,
        readback_pause=0.0,
    )


def _seed(tmp_path: Path, seat: LitSeat, answered: dict) -> None:
    """A delivered list on the volume whose answer date is then replaced the
    way the hand-run seed carries it: the date its source file was SAVED."""
    first = _run(tmp_path, ScriptedLit(), seat, "first", "01KTJ0BX0000000000000000AA")
    assert first.run().verdict == "delivered"
    prior = manifest.load_prior(tmp_path / "state", M1)
    prior["defendants"][0]["answered"] = {**prior["defendants"][0]["answered"], **answered}
    (tmp_path / "state" / "matters" / f"{M1}.json").write_text(json.dumps(prior))


# ---- (a) a seed value with no text this job holds is read again --------------------------
def test_a_seed_save_date_with_no_text_is_read_again_and_the_list_passes_the_gates(tmp_path):
    seat = LitSeat({M1: matter_docs()})
    # "Answer Delta.pdf" was SAVED 2026-05-01; its text says April 30. The seed
    # carries the save date. On the old code the matter is "unchanged", nothing
    # is fetched, and the gates refuse the stored value: the live hold.
    _seed(tmp_path, seat, {"date": "2026-05-01"})
    client = ScriptedLit()
    r = _run(tmp_path, client, seat, "second", "01KTJ0BX0000000000000000BB")
    v = r.run()
    assert v.verdict == "delivered", v.reason
    plan = json.loads((r.data / "plan.json").read_text())[M1]
    assert plan["seed_unresolved"] == [vocab.GROUP_DEFENDANTS]
    assert vocab.GROUP_DEFENDANTS in plan["read_groups"] and client.calls  # read again, paid
    final = json.loads((r.data / "final.json").read_text())[0]
    assert final["defendants"][0]["answered"]["date"] == "2026-04-30"  # the document's date, not the save date
    assert json.loads((r.data / "gates.json").read_text())["passed"]


def test_a_seed_save_date_its_fetched_text_carries_passes_with_no_read(tmp_path):
    docs = [d if d[0] != "f-a1" else (d[0], d[1], d[2], "2026-04-30T10:00:00") for d in matter_docs()]
    seat = LitSeat({M1: docs})
    _seed(tmp_path, seat, {"date": "2026-04-30"})  # equals the save date AND the text says April 30
    client = ScriptedLit()
    r = _run(tmp_path, client, seat, "second", "01KTJ0BX0000000000000000BB")
    v = r.run()
    assert v.verdict == "delivered", v.reason
    plan = json.loads((r.data / "plan.json").read_text())[M1]
    assert plan["seed_files"] == ["f-a1"] and plan["seed_unresolved"] == []
    assert plan["read_groups"] == [] and client.calls == []  # fetched, checked by code, $0
    assert v.matters_reread == 0


def test_a_seed_date_without_a_source_and_an_unsourced_status_are_hard_gaps():
    files = [{"id": "c", "name": "Complaint.pdf", "created": "2026-03-01", "modified": "2026-03-01"}]
    prior = {
        "fields_read": list(vocab.GROUPS),
        "case_status": {"value": "open-ish", "source": None},
        "complaint_filed": {"date": "2026-02-27", "source": {"file_id": "c"}},
        "defendants": [
            {"name": "Delta", "status": vocab.SERVED_NO_ANSWER, "served": {"date": "2026-04-01", "source": None}}
        ],
        "discovery_propounded": [{"set": "Set One", "date": "2026-05-01", "source": {"file_id": "gone"}}],
    }
    g = seedcheck.gaps(prior, files, TODAY)
    assert g["hard"] == list(vocab.GROUPS) and g["soft"] == {}
    p = manifest._plan(files, prior, {"c": ""}, ["c"], TODAY)
    assert p["read_groups"] == list(vocab.GROUPS) and p["reason"] == "seed_gaps" and p["audit"] == "changed"


def test_a_past_court_date_on_an_active_seed_is_read_again():
    files = [{"id": "c", "name": "Complaint.pdf", "created": "2026-03-01", "modified": "2026-03-01"}]
    prior = {
        "fields_read": list(vocab.GROUPS),
        "case_status": {"value": vocab.ACTIVE, "source": {"file_id": "c"}},
        "complaint_filed": {"date": "2026-02-27", "source": {"file_id": "c"}},
        "next_court_date": {"date": "2026-09-01", "source": {"file_id": "c"}},
        "defendants": [{"name": "Delta", "status": vocab.NOT_SERVED}],
    }
    assert seedcheck.gaps(prior, files, TODAY)["hard"] == [vocab.GROUP_CASE]


# ---- (b) a changed matter with missing groups reads both --------------------------------
def test_a_changed_matter_with_a_missing_group_reads_its_update_and_the_missing_group(tmp_path):
    firm = load(make_inputs(tmp_path / "in"))
    base = [{"id": "c", "name": "Complaint", "ext": ".pdf", "modified": "2026-03-01", "created": "2026-03-01"}]
    prior = {
        "fields_read": [vocab.GROUP_CASE, vocab.GROUP_DEFENDANTS],  # the seed predates discovery
        "case_status": {"value": vocab.NOT_FILED, "source": {"file_id": "c"}},
    }
    pos = base + [{"id": "p", "name": "POS Delta", "ext": ".pdf", "modified": "2026-09-01", "created": "2026-09-01"}]
    p = manifest.plan_matter(pos, prior, manifest.current_manifest(base), firm, TODAY)
    # Old code returned the update plan alone: case + defendants, discovery never read.
    assert p["reason"] == "changed" and p["trigger_files"] == ["p"]
    assert p["read_groups"] == list(vocab.GROUPS) and p["seed_fresh"] == [vocab.GROUP_DISCOVERY]
    s = seedcheck.settle(update.screen_plan(p, {}, firm), lambda fid: None)
    assert s["read_groups"] == list(vocab.GROUPS) and "mode" not in s  # one full read, not an update
    assert "p" in s["candidates"] and "c" in s["candidates"]


def test_a_changed_matter_with_no_gap_stays_an_update(tmp_path):
    firm = load(make_inputs(tmp_path / "in"))
    base = [{"id": "c", "name": "Complaint", "ext": ".pdf", "modified": "2026-03-01", "created": "2026-03-01"}]
    prior = {"fields_read": list(vocab.GROUPS), "case_status": {"value": vocab.NOT_FILED, "source": {"file_id": "c"}}}
    pos = base + [{"id": "p", "name": "POS Delta", "ext": ".pdf", "modified": "2026-09-01", "created": "2026-09-01"}]
    p = manifest.plan_matter(pos, prior, manifest.current_manifest(base), firm, TODAY)
    s = seedcheck.settle(update.screen_plan(p, {}, firm), lambda fid: None)
    assert s["mode"] == update.MODE and s["read_groups"] == [vocab.GROUP_CASE, vocab.GROUP_DEFENDANTS]


# ---- (c) a proof run starts from a copy of the real state --------------------------------
class _Lane:
    def __init__(self, state_dir: str | None) -> None:
        self.state_dir = state_dir
        self.chowned: list[Path] = []

    def _chown_child(self, p: Path) -> None:
        self.chowned.append(p)


def test_a_proof_state_is_a_copy_of_the_real_state_and_writes_nothing_back(tmp_path):
    real = tmp_path / "real-state"
    (real / "matters").mkdir(parents=True)
    (real / "matters" / "m.json").write_text('{"case_status": {"value": "Active"}}')
    (real / "baseline.json").write_text("{}")
    d = _Lane(str(real))
    dest = proof_run.seed_state(d, tmp_path / "attempt")
    assert dest == tmp_path / "attempt" / "state" and d.chowned == [dest]
    assert (dest / "matters" / "m.json").read_text() == (real / "matters" / "m.json").read_text()
    (dest / "matters" / "m.json").write_text("{}")  # the twin writes its copy
    (dest / "matters" / "new.json").write_text("{}")
    assert json.loads((real / "matters" / "m.json").read_text())["case_status"]["value"] == "Active"
    assert sorted(p.name for p in (real / "matters").iterdir()) == ["m.json"]
    # A resumed proof keeps its own progress: the copy is made once per attempt.
    assert proof_run.seed_state(d, tmp_path / "attempt") == dest and (dest / "matters" / "new.json").is_file()


def test_a_proof_state_is_empty_when_the_lane_has_none(tmp_path):
    for lane in (_Lane(str(tmp_path / "absent")), _Lane(None)):
        dest = proof_run.seed_state(lane, tmp_path / f"a{id(lane)}")
        assert not dest.exists() or not any(dest.iterdir())
        assert lane.chowned == []


def test_the_litigation_twin_reads_the_copied_state(tmp_path):
    from test_litigation_lane import _lane

    lane, _client = _lane(tmp_path)
    (Path(lane.state_dir) / "matters").mkdir(parents=True)
    (Path(lane.state_dir) / "matters" / "m.json").write_text("{}")
    t = proof_run.twin(lane, tmp_path / "run" / "x")
    assert t.state_dir == str(tmp_path / "run" / "x" / "state") and t.state_dir != lane.state_dir
    assert (Path(t.state_dir) / "matters" / "m.json").is_file()


def test_the_negotiation_proof_cursor_starts_as_a_copy_of_the_lanes(tmp_path, monkeypatch):
    import argparse

    from medchron import __main__ as cli, proof_mode
    from medchron.negotiation import dryrun, run as neg_run

    lane_state = tmp_path / "twin-state"  # what the twin hands its child (a copy of the real cursor)
    lane_state.mkdir()
    (lane_state / "cursor.json").write_text('{"m": "2026-10-01"}')
    seen = {}

    class FakeDry:
        def __init__(self, job_dir, *, state_dir, **kw):
            seen["copied"] = (state_dir / "cursor.json").read_text()
            (state_dir / "cursor.json").write_text("{}")  # the dry run moves its own copy

        def run(self):
            return {"verdict": "delivered", "cents": 0}

    monkeypatch.setattr(dryrun, "NegotiationDryRun", FakeDry)
    monkeypatch.setenv(neg_run.STATE_ENV, str(lane_state))
    monkeypatch.setenv(proof_mode.PROOF_ENV, "1")
    (tmp_path / "job").mkdir()
    cli._cmd_negotiate(argparse.Namespace(job_dir=str(tmp_path / "job"), state_dir=None, pricing=None))
    assert seen["copied"] == '{"m": "2026-10-01"}'
    assert (lane_state / "cursor.json").read_text() == '{"m": "2026-10-01"}'  # nothing written back
