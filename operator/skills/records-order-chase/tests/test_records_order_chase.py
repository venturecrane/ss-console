"""records-order-chase: the weekly list of records orders that have not come in (letter 416 #10).

What these pin, each with the harm it prevents:

* a location the vendor shows as completed is not listed, one it shows as
  anything else (including a status nobody has seen) is listed with the
  vendor's own word, so a request is never called received on a guess;
* a matter the vendor errors on, and an order whose matter is not known, are
  listed as "could not be checked", so a failed read never looks like a quiet
  week (the falsifier below fails if either is dropped);
* the email goes to the one authored recipient only, and every failure (no
  recipient, unreadable ledger, vendor not connected, read that did not run)
  sends nothing;
* no order placed through the Operator means no email at all;
* the body carries no em dash and stdout carries no matter number or name.

Run::

    cd operator/skills/records-order-chase && python3 -m pytest tests -q
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR))


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SKILL_DIR / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PRE_RUN = _load("records_order_chase_pre_run_under_test", "pre_run.py")
REPORT = PRE_RUN._REPORT

NOW = datetime(2026, 11, 3, 16, 9, tzinfo=timezone.utc)  # Tue 0809 Pacific
TODAY = date(2026, 11, 3)
M_PENDING = "11111111-1111-1111-1111-111111111111"
M_ERROR = "22222222-2222-2222-2222-222222222222"
RECIPIENT = "manager@firm.example"
CFG = {
    "business_hours": {"timezone": "America/Los_Angeles"},
    "personas": [
        {"slug": "operator", "skills": [{"name": "records-order-chase", "settings": {"recipient": RECIPIENT}}]}
    ],
}

ORDERS = [
    {"committed_at": "2026-10-06T18:00:00.000Z", "matter_id": M_PENDING, "location_count": 2},
    {"committed_at": "2026-10-20T18:00:00.000Z", "matter_id": M_ERROR, "location_count": 1},
    {"committed_at": "2026-10-21T18:00:00.000Z", "matter_id": None, "location_count": 1},
]
READS = {
    "vendorName": "Example Vendor",
    "matters": {
        M_PENDING: {
            "number": "203508",
            "client": "Jane Example",
            "locations": [
                {"id": "1", "request_id": "123456", "name": "Example Community Health Center", "status": "Completed"},
                {"id": "2", "request_id": "123457", "name": "Example Imaging", "status": "Not Started"},
            ],
        },
        M_ERROR: {"number": "201208", "client": "John Example", "vendorError": "http_500"},
    },
}


@pytest.fixture()
def seat(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_TIMEZONE", raising=False)
    rows: list[tuple[str, dict]] = []

    def fake_call(request):
        rows.append((request["action"], json.loads(request["row"]["metadata"])))
        return {"ok": True}

    monkeypatch.setattr(PRE_RUN, "_broker_call", fake_call)
    return {"home": tmp_path, "rows": rows}


def _envelope(home: Path) -> dict:
    return json.loads((home / ".smd" / "pre_run" / "records-order-chase.dispatch.json").read_text())


def _run(capsys, orders=ORDERS, reads=READS, cfg=CFG, **kw) -> dict:
    code = PRE_RUN.run(cfg, read_orders=lambda: orders, read_matters=lambda ids: reads, clock=lambda: NOW, **kw)
    assert code == 0
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_two_matters_one_complete_one_pending_one_erroring(seat, capsys):
    """FALSIFIER. Drop the erroring matter or the unknown-matter order from the
    report, list the completed location, or lose the pending one, and this fails."""
    verdict = _run(capsys)
    assert verdict["status"] == "chase_ready" and verdict["dispatch_expected"] is True
    assert (verdict["outstanding"], verdict["unchecked"], verdict["complete"]) == (1, 2, 1)
    dispatch = _envelope(seat["home"])["dispatches"][0]
    body = dispatch["full_body"]
    assert dispatch["recipients"] == [RECIPIENT] and dispatch["cc"] == []
    assert "Example Imaging" in body and "123457" in body and "Not Started" in body
    assert "Example Community Health Center" not in body and "123456" not in body
    assert "Matter 203508, Jane Example" in body
    assert "Ordered: 2026-10-06 (28 days ago)" in body
    assert "Matter 201208, John Example, order placed 2026-10-20: the vendor did not answer (HTTP 500)" in body
    assert "- An order placed 2026-10-21: the order's matter is not on record" in body
    assert dispatch["subject"] == "Records orders: 1 request not yet in, 2 could not be checked"
    assert "—" not in body and "—" not in dispatch["subject"]
    assert seat["rows"][-1][0] == "emitted_wake_append"


def test_the_follow_up_is_addressed_to_the_vendor_and_states_only_what_was_read(seat, capsys):
    _run(capsys)
    body = _envelope(seat["home"])["dispatches"][0]["full_body"]
    assert "Follow-up to send to Example Vendor:" in body
    assert (
        "Could you tell us where request 123457 stands? It is for records from Example Imaging for our client "
        "Jane Example (our matter 203508), ordered on 2026-10-06. Your system shows it as Not Started."
    ) in body
    assert "Nothing has been sent to Example Vendor or to any provider." in body


def test_an_unknown_status_is_shown_raw_and_never_read_as_received(seat, capsys):
    reads = json.loads(json.dumps(READS))
    reads["matters"][M_PENDING]["locations"][1]["status"] = "Records Delivered"
    reads["matters"][M_PENDING]["locations"][0]["status"] = ""
    verdict = _run(capsys, reads=reads)
    body = _envelope(seat["home"])["dispatches"][0]["full_body"]
    assert verdict["outstanding"] == 2
    assert "Status at Example Vendor: Records Delivered" in body
    assert "Status at Example Vendor: no status given" in body


def test_only_completed_reads_as_in():
    assert REPORT.is_complete("Completed") and REPORT.is_complete(" complete ")
    for status in ("Not Started", "In Progress", "Received", "Closed", "", None, "Completed partially"):
        assert not REPORT.is_complete(status)


def test_a_vendor_404_on_an_ordered_matter_is_listed_not_dropped(seat, capsys):
    reads = json.loads(json.dumps(READS))
    reads["matters"][M_PENDING] = {"number": "203508", "client": "Jane Example", "vendorError": "http_404"}
    verdict = _run(capsys, orders=ORDERS[:1], reads=reads)
    body = _envelope(seat["home"])["dispatches"][0]["full_body"]
    assert verdict["unchecked"] == 1
    assert "the vendor says it holds no order for this matter" in body


def test_every_order_in_sends_the_short_note(seat, capsys):
    reads = json.loads(json.dumps(READS))
    reads["matters"][M_PENDING]["locations"][1]["status"] = "Completed"
    verdict = _run(capsys, orders=ORDERS[:1], reads=reads)
    dispatch = _envelope(seat["home"])["dispatches"][0]
    assert verdict["outstanding"] == 0 and verdict["unchecked"] == 0
    assert dispatch["subject"] == "Records orders: nothing outstanding"
    assert "nothing to follow up this week" in dispatch["full_body"]


def test_no_orders_placed_sends_nothing(seat, capsys):
    verdict = _run(capsys, orders=[])
    assert verdict == {"wakeAgent": False, "status": "no_orders_placed", "dry_run": False, "orders": 0}
    assert not (seat["home"] / ".smd" / "pre_run" / "records-order-chase.dispatch.json").exists()
    assert seat["rows"][-1][0] == "suppressed_wake_append"


@pytest.mark.parametrize(
    ("kwargs", "status"),
    [
        ({"cfg": {"personas": []}}, "recipient_unauthored"),
        ({"reads": None}, "read_failed"),
        ({"reads": {"notConnected": True}}, "vendor_not_connected"),
    ],
)
def test_a_failed_run_sends_nothing(seat, capsys, kwargs, status):
    verdict = _run(capsys, **kwargs)
    assert verdict["wakeAgent"] is False and verdict["status"] == status
    assert not (seat["home"] / ".smd" / "pre_run" / "records-order-chase.dispatch.json").exists()


def test_an_unreadable_ledger_sends_nothing(seat, capsys):
    def broken():
        raise PRE_RUN.LedgerUnreadable("refused")

    code = PRE_RUN.run(CFG, read_orders=broken, read_matters=lambda ids: READS, clock=lambda: NOW)
    verdict = json.loads(capsys.readouterr().out.strip())
    assert code == 0 and verdict["status"] == "ledger_unreadable" and verdict["wakeAgent"] is False


def test_stdout_carries_no_matter_number_name_or_facility(seat, capsys):
    PRE_RUN.run(CFG, read_orders=lambda: ORDERS, read_matters=lambda ids: READS, clock=lambda: NOW)
    out = capsys.readouterr().out
    for value in ("203508", "201208", "Jane", "Example Imaging", "123457"):
        assert value not in out


def test_dry_run_writes_nothing(seat, capsys):
    verdict = _run(capsys, dry=True)
    assert verdict["status"] == "dry_run" and verdict["outstanding"] == 1
    assert not (seat["home"] / ".smd").exists() and seat["rows"] == []


def test_the_handoff_pairs_each_printed_matter_with_its_order_dates(seat, capsys):
    _run(capsys)
    handoff = json.loads((seat["home"] / ".smd" / "pre_run" / "records-order-chase.json").read_text())
    pairs = {r["matterNumber"]: r["dates"] for r in handoff["records"]}
    assert pairs == {"203508": ["2026-10-06"], "201208": ["2026-10-20"]}
    assert "2026-10-21" in handoff["dates"] and "2026-11-03" in handoff["dates"]


def test_the_envelope_hashes_match_the_bodies(seat, capsys):
    _run(capsys)
    env = _envelope(seat["home"])
    dispatch = env["dispatches"][0]
    assert dispatch["body_sha256_full"] == PRE_RUN._H.canonical_body_sha256(dispatch["full_body"])
    assert env["in_turn"][0]["template"] == dispatch["skeleton_body"] and env["in_turn_enforce"] is True


def test_the_matter_cap_counts_what_waits(seat, capsys, monkeypatch):
    monkeypatch.setattr(PRE_RUN, "MAX_MATTERS_PER_RUN", 1)
    seen: list[list[str]] = []

    def reads(ids):
        seen.append(list(ids))
        return READS

    PRE_RUN.run(CFG, read_orders=lambda: ORDERS, read_matters=reads, clock=lambda: NOW)
    verdict = json.loads(capsys.readouterr().out.strip())
    assert seen == [[M_PENDING]] and verdict["not_read"] == 1
    assert "1 more order was not checked this week" in _envelope(seat["home"])["dispatches"][0]["full_body"]
