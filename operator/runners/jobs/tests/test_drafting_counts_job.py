"""A propounded-set job end to end with the firm's counts (design B, B0 and
B1): the caption and the party plan run before the file is read, so a party
the matter does not have stops after one caption read; with
``discovery_counts`` authored the set's number is in its instruction, a short
set gets one "more requests" pass, the gate records its counts and the
attorney notes carry its "Set sizes" line. Every value here is invented.
"""

from __future__ import annotations

import json

import pytest
from test_drafting_parties import PartyClient, _seat_and_caption
from test_drafting_review import KINDS, Client, _run, one_set

SROG = "Special Interrogatories"
COUNTS = {
    "discovery_counts": {"special_interrogatories": {"min": 50, "max": 75}, "requests_for_admission": {"max": 50}}
}


@pytest.fixture(autouse=True)
def _checker(monkeypatch):
    from test_drafting_review import CHECKER

    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


@pytest.fixture
def pricing(tmp_path):
    from jobs_testkit import PRICING

    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    return p


def test_a_party_the_matter_does_not_have_stops_before_the_file_is_read(tmp_path, pricing):
    cap, seat = _seat_and_caption()
    client = PartyClient(caption=[cap], by_party={})
    r, v, seat = _run(tmp_path, pricing, client, request="Please draft RFAs set one to the defendant.", seat=seat)
    assert v.outcome == "failed" and v.reason.startswith("party_unknown: "), (v.stage, v.reason)
    state = json.loads((r.data / "state.json").read_text())
    assert state["caption"]["status"] == "done" and "transcribe" not in state
    assert client.count("DIGEST-PROMPT") == 0 and client.count("COMPOSE-PROMPT") == 0 and seat.created == []


def test_a_short_set_is_topped_up_once_counted_and_said_in_the_notes(tmp_path, pricing):
    client = Client(drafts={label: one_set(label) for label in KINDS})
    r, v, _seat = _run(tmp_path, pricing, client, inputs_over=COUNTS)
    assert v.outcome == "delivered", (v.stage, v.reason)
    composes = [json.dumps(c.get("messages")) for c in client.calls if "COMPOSE-PROMPT" in json.dumps(c.get("system"))]
    assert any("SET SIZE (the firm's range): write between 50 and 75 special interrogatories" in m for m in composes)
    assert sum("MORE REQUESTS. The set below has 3 special interrogatories" in m for m in composes) == 1
    units = json.loads((r.data / "state.json").read_text())["units"]
    srog = next(u for u in units if u["variant"] == "special_interrogatories")
    rec = json.loads((r.data / srog["key"] / "counts.json").read_text())
    assert rec["count"] == 6 and rec["target"] == [50, 75] and rec["topped_up"] == [3, 6]
    notes = (r.data / "notes-final.md").read_text()
    assert (
        "Set sizes: 6 special interrogatories; your range is 50 to 75; 3 more were drafted from the file; "
        "the file supported no more; no declaration (6 to this party, not over 35)"
    ) in notes
    assert "Set sizes: 3 requests for admission; your limit is 50; no declaration" in notes


def test_without_counts_authored_the_job_asks_and_records_nothing_new(tmp_path, pricing):
    client = Client(drafts={label: one_set(label) for label in KINDS})
    r, v, _seat = _run(tmp_path, pricing, client)
    assert v.outcome == "delivered", (v.stage, v.reason)
    composes = [json.dumps(c.get("messages")) for c in client.calls if "COMPOSE-PROMPT" in json.dumps(c.get("system"))]
    assert not any("SET SIZE" in m or "MORE REQUESTS" in m for m in composes)
    assert not list(r.data.rglob("counts.json")) and "Set sizes" not in (r.data / "notes-final.md").read_text()
