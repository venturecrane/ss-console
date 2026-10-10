"""The negotiation dry run: the whole pipeline on real-shaped data, stopping
before any write or email. Each test fails if the guard it names is removed."""

from __future__ import annotations

import json

import pytest
from test_negotiation import M1, PRICING, Layout, Seat, _offer

from medchron import arrivals
from medchron.negotiation import dryrun, run as run_mod

FILES = [
    {"id": "f1", "name": "Offer letter 9-30", "ext": ".pdf", "modified": "1", "created": "2026-10-08T22:10:00Z"},
    {"id": "f2", "name": "Offer letter 10-09", "ext": ".pdf", "modified": "1", "created": "2026-10-09T15:30:00Z"},
]


def _dry(tmp_path, monkeypatch, layout, events=None, **kw):
    wd = dryrun.write_job(
        tmp_path / "dry",
        slug="example",
        seed_saved_before="2026-10-09T14:00:00Z",
        design="",
        firm_words=["ashton"],
        statuses=["Open"],
        cap_usd=5.0,
        job_id="01DRYRVN000000000000000000",
    )
    pricing = tmp_path / "pricing.json"
    pricing.write_text(json.dumps(PRICING))
    monkeypatch.setattr(run_mod.read_mod, "read_document", lambda *a, **k: events or [_offer()])
    r = dryrun.NegotiationDryRun(
        wd, pricing=str(pricing), seat_factory=lambda: Seat(FILES), layout=layout, log=lambda m: None, **kw
    )
    monkeypatch.setattr(r, "_text", lambda mid, f: "letter text")
    return r, wd


def test_the_dry_run_plans_and_composes_but_never_writes(tmp_path, monkeypatch):
    """FALSIFIER: let the dry run call the real _write and the layout's
    add_negotiation_rows is reached (the read-only handle raises)."""
    layout = Layout()
    r, wd = _dry(tmp_path, monkeypatch, layout)
    summary = r.run()
    assert layout.calls == [], "add_negotiation_rows was called on the dry-run path"
    assert summary["dry_run"] is True and summary["candidates"] == 1 and summary["rows_planned"] == 1
    assert summary["emails"] == 1
    report = json.loads((wd / dryrun.REPORT).read_text())
    e = report["entries"][0]
    assert e["file"] == "Offer letter 10-09.pdf" and e["date_created"] == "2026-10-09T15:30:00Z"
    assert e["rows_planned"][0]["offer_amount"] == "15000" and e["offers"][0]["status"] == "written"
    assert e["emails"][0].startswith("New offer on matter 200123, Doe v. Example.")
    assert "Entered in Negotiation Details, row 1." in e["emails"][0]
    assert "would write" in (wd / dryrun.REPORT_TEXT).read_text()


def test_already_present_is_reported_with_no_email(tmp_path, monkeypatch):
    layout = Layout(rows=[{"row": 0, "offer_amount": 15000, "offer_date": "2026-10-07"}], details="Entered 10/9/26.")
    r, wd = _dry(tmp_path, monkeypatch, layout)
    summary = r.run()
    assert summary["emails"] == 0 and summary["rows_planned"] == 0
    assert json.loads((wd / dryrun.REPORT).read_text())["entries"][0]["offers"] == []


def test_the_read_only_layout_refuses_the_write():
    handle = dryrun._ReadOnlyLayout(Layout())
    with pytest.raises(dryrun.DryRunWriteRefused):
        handle.add_negotiation_rows("m", [{"offer_date": "2026-10-07"}])


def test_the_dry_run_refuses_the_lanes_real_cursor(tmp_path, monkeypatch):
    """FALSIFIER: drop the guard and a dry run seeds the lane's real cursor, so
    the first live run would skip every document the dry run saw."""
    real = tmp_path / "real-state"
    monkeypatch.setenv(run_mod.STATE_ENV, str(real))
    with pytest.raises(dryrun.DryRunWriteRefused):
        _dry(tmp_path, monkeypatch, Layout(), state_dir=real / "x")
    r, wd = _dry(tmp_path, monkeypatch, Layout())
    r.run()
    assert arrivals.cursor("negotiation", M1, data=wd / "state") is not None
    assert not real.exists()


def test_attempts_are_never_recorded(tmp_path, monkeypatch):
    r, wd = _dry(tmp_path, monkeypatch, Layout())
    monkeypatch.setattr(r, "_text", lambda mid, f: (_ for _ in ()).throw(RuntimeError("model down")))
    summary = r.run()
    assert summary["read_errors"] == 1
    assert not (wd / "state" / "attempts.json").exists()


def test_only_matters_limits_the_run(tmp_path, monkeypatch):
    r, _wd = _dry(tmp_path, monkeypatch, Layout(), only_matters=["999999"])
    assert r.run()["candidates"] == 0


def test_a_second_copy_of_the_same_letter_plans_as_a_live_run_would(tmp_path, monkeypatch):
    """The firm's data held four saved copies of one email thread. A live run
    writes the first and finds the rest already present; the dry run must
    report the same (one row, one email), not one row per copy."""
    copies = [
        {**FILES[1], "id": f"c{i}", "name": f"RE: SETTLEMENT copy {i}", "created": f"2026-10-09T18:5{i}:00Z"}
        for i in range(3)
    ]
    layout = Layout()
    wd = dryrun.write_job(
        tmp_path / "dry2",
        slug="example",
        seed_saved_before="2026-10-09T14:00:00Z",
        design="",
        firm_words=["ashton"],
        statuses=["Open"],
        cap_usd=5.0,
        job_id="01DRYRVN000000000000000000",
    )
    pricing = tmp_path / "pricing.json"
    pricing.write_text(json.dumps(PRICING))
    monkeypatch.setattr(run_mod.read_mod, "read_document", lambda *a, **k: [_offer()])
    r = dryrun.NegotiationDryRun(
        wd, pricing=str(pricing), seat_factory=lambda: Seat([FILES[0], *copies]), layout=layout, log=lambda m: None
    )
    monkeypatch.setattr(r, "_text", lambda mid, f: "letter text")
    summary = r.run()
    assert summary["candidates"] == 3 and summary["rows_planned"] == 1 and summary["emails"] == 1
    assert layout.calls == []
