"""Proven delivery for the negotiation watch (2026-10-13): every row about to be
written is checked against the document (code, then an independent model
read) and HELD on a miss; the delivered verdict carries the proof block.

The 2026-10-10 .. 10-13 failures each have a falsifier here: an amount the
document never prints, a date it never prints, the firm's own letter to a
lienholder announced as a new offer (ss#3139), and a 998 offer recorded as a
demand. Every name, number and date is invented."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from medchron import arrivals
from medchron.negotiation import dryrun, offer_check, run as run_mod
from medchron.proof import gate
from test_negotiation import FILES, JOB, M1, NEW, PRICING, Layout, Seat, _offer

LETTER = "Example Mutual offers $15,000 to settle all claims. Dated October 7, 2026."
CLEAN = "| GRADE | check | finding | cite |\n|---|---|---|---|\n\nVERDICT: 0 MUST-FIX"


def _table(*rows: str) -> str:
    return (
        "| GRADE | check | finding | cite |\n|---|---|---|---|\n"
        + "\n".join(rows)
        + (f"\n\nVERDICT: {sum('MUST-FIX' in r for r in rows)} MUST-FIX")
    )


class ReviewClient:
    """The model behind the doorway: answers every review call with ``answer``
    (the extraction itself is stubbed at ``read_document``)."""

    def __init__(self, answer: str = CLEAN) -> None:
        self.answer = answer
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(stream=self._stream, create=self._msg)

    def _msg(self, **params):
        self.calls.append(params)
        usage = SimpleNamespace(
            input_tokens=2000, output_tokens=300, cache_read_input_tokens=0, cache_creation_input_tokens=0
        )
        return SimpleNamespace(content=[{"type": "text", "text": self.answer}], stop_reason="end_turn", usage=usage)

    def _stream(self, **params):
        from demand_testkit import _Stream

        return _Stream(self._msg(**params))


def _job(tmp_path):
    jd = tmp_path / "job"
    (jd / "data").mkdir(parents=True)
    (jd / "job.json").write_text(
        json.dumps(
            {
                "kind": "negotiation",
                "job_id": JOB,
                "slug": "example",
                "matter_statuses": ["Open"],
                "firm_words": ["ashton"],
                "per_job_cap_usd": 5.0,
                "monthly_budget_usd": 25.0,
                "month_cents_used": 0,
            }
        )
    )
    pricing = tmp_path / "pricing.json"
    pricing.write_text(json.dumps(PRICING))
    return jd, pricing


def _run(tmp_path, monkeypatch, events, *, text=LETTER, answer=CLEAN, layout=None, files=NEW):
    arrivals.commit("negotiation", M1, FILES, data=tmp_path / "state")
    jd, pricing = _job(tmp_path)
    monkeypatch.setattr(run_mod.read_mod, "read_document", lambda *a, **k: list(events))
    client = ReviewClient(answer)
    layout = layout or Layout()
    r = run_mod.NegotiationRun(
        jd,
        state_dir=tmp_path / "state",
        pricing=str(pricing),
        seat_factory=lambda: Seat(files),
        layout=layout,
        client=client,
        log=lambda m: None,
    )
    monkeypatch.setattr(r, "_text", lambda mid, f: text)
    return r, layout, client


# ---- the code check -----------------------------------------------------------------------
def test_a_clean_offer_is_written_reviewed_and_proven(tmp_path, monkeypatch):
    r, layout, client = _run(tmp_path, monkeypatch, [_offer()])
    v = r.run()
    assert len(layout.calls) == 1 and len(v["notices"]) == 1 and v["held"] == []
    assert len(client.calls) == 1 and "row 1: kind offer" in json.dumps(client.calls[0]["messages"])
    p = v["proof"]
    assert p["readback"] == "confirmed" and p["rows"] == 1 and p["review"] == "passed"
    assert p["review_model"] == offer_check.REVIEW_MODEL and p["review_cents"] > 0 and p["alerts"] == []
    assert "nothing_to_file" not in p and gate.problem({"outcome": "delivered", "proof": p}) is None


@pytest.mark.parametrize("spelling", ["$15,000", "$15,000.00", "15000", "15,000"])
def test_the_amount_may_be_printed_in_any_spelling(tmp_path, monkeypatch, spelling):
    text = f"Example Mutual offers {spelling} to settle. Dated 10/7/2026."
    r, layout, _ = _run(tmp_path, monkeypatch, [_offer()], text=text)
    assert r.run()["held"] == [] and len(layout.calls) == 1


def test_an_amount_not_in_the_document_is_held_not_written(tmp_path, monkeypatch):
    """FALSIFIER: trust amount_confirmed and $25,000 goes into the firm's record
    from a letter that offers $15,000."""
    r, layout, client = _run(tmp_path, monkeypatch, [_offer(amount=25000, amount_confirmed=True)])
    v = r.run()
    assert layout.calls == [] and v["notices"] == [] and client.calls == []
    assert v["offers_held_review"] == 1 and v["held"][0]["held_by"] == "code"
    assert "$25,000.00 is not printed" in v["held"][0]["reason"]
    # A first hold retries next run; it pages only when final (the three-attempt test).
    assert v["proof"]["alerts"] == [] and v["proof"]["nothing_to_file"] is True


def test_a_date_not_in_the_document_nor_its_header_is_held(tmp_path, monkeypatch):
    r, layout, _ = _run(tmp_path, monkeypatch, [_offer(date="2026-09-15")])
    v = r.run()
    assert layout.calls == [] and v["notices"] == []
    assert "9/15/26 is not printed" in v["held"][0]["reason"]


def test_an_emails_date_header_dates_the_offer(tmp_path, monkeypatch):
    text = "From: a@example.com\nDate: Wed, 07 Oct 2026 09:12:00 -0700\nSubject: offer\n\nWe offer $15,000."
    r, layout, _ = _run(
        tmp_path,
        monkeypatch,
        [_offer()],
        text=text,
        files=FILES + [{"id": "f2", "name": "RE offer", "ext": ".msg", "modified": "1"}],
    )
    assert r.run()["held"] == [] and len(layout.calls) == 1


# ---- the model review -----------------------------------------------------------------------
def test_the_firms_own_letter_to_a_lienholder_is_held_by_the_review(tmp_path, monkeypatch):
    """FALSIFIER (ss#3139): the reader stamped the firm's letter to a lienholder
    as the carrier writing to us; the review's check 1 catches it."""
    answer = _table("| MUST-FIX | 1 | the firm wrote this letter to a lienholder | row 1: 'Dear Lien Department' |")
    r, layout, _ = _run(tmp_path, monkeypatch, [_offer()], answer=answer)
    v = r.run()
    assert layout.calls == [] and v["notices"] == []
    assert v["held"][0]["held_by"] == "review" and "check 1" in v["held"][0]["reason"]
    assert v["proof"]["alerts"] == [] and v["proof"]["review"] == "passed"


def test_a_998_offer_labeled_demand_is_held(tmp_path, monkeypatch):
    demand = _offer(kind="demand", by="Ashton for the client", author_side="our_firm", addressed_to="paying_side")
    answer = _table("| MUST-FIX | 4 | this is a CCP 998 offer, not a demand | row 1: 'pursuant to CCP 998' |")
    r, layout, _ = _run(tmp_path, monkeypatch, [demand], answer=answer)
    v = r.run()
    assert layout.calls == [] and v["held"][0]["kind"] == "demand" and "check 4" in v["held"][0]["reason"]


def test_a_must_fix_without_a_cite_does_not_hold(tmp_path, monkeypatch):
    """The closed-list rule: an uncited MUST-FIX is downgraded, not a hold."""
    answer = _table("| MUST-FIX | 2 | the amount looks low |  |")
    r, layout, _ = _run(tmp_path, monkeypatch, [_offer()], answer=answer)
    assert r.run()["held"] == [] and len(layout.calls) == 1


def test_a_review_hold_on_one_row_leaves_the_other_entered(tmp_path, monkeypatch):
    text = LETTER + " Our demand of $90,000 was made October 1, 2026."
    demand = _offer(kind="demand", by="Ashton", amount=90000, date="2026-10-01", author_side="paying_side")
    answer = _table("| MUST-FIX | 4 | that figure was a 998 offer | row 1: 'CCP 998' |")
    r, layout, _ = _run(tmp_path, monkeypatch, [demand, _offer()], text=text, answer=answer)
    v = r.run()
    assert len(v["held"]) == 1 and v["held"][0]["amount"] == 90000
    assert len(layout.calls) == 1 and layout.calls[0][1][0]["offer_amount"] == "15000"


def test_a_review_that_never_finishes_leaves_the_document_new(tmp_path, monkeypatch):
    r, layout, _ = _run(tmp_path, monkeypatch, [_offer()], answer="| MUST-FIX | 1 | cut off")
    v = r.run()
    assert layout.calls == [] and v["docs_failed"] == 1
    assert arrivals.cursor("negotiation", M1, data=tmp_path / "state") == {"f1": "1"}


# ---- a held document is never lost -----------------------------------------------------------
def test_a_held_offers_document_stays_new_then_is_kept_for_smd_after_three(tmp_path, monkeypatch):
    for attempt in range(1, 4):
        run_dir = tmp_path / f"t{attempt}"
        run_dir.mkdir()
        r, _, _ = _run(run_dir, monkeypatch, [_offer(amount=25000)])
        r.state = tmp_path / "state"
        if attempt == 1:
            arrivals.commit("negotiation", M1, FILES, data=r.state)
        v = r.run()
        cur = arrivals.cursor("negotiation", M1, data=tmp_path / "state")
        assert v["held"][0]["retry"] is (attempt < 3)
        # FALSIFIER (2026-10-15): paging every attempt paged one document three times.
        assert v["proof"]["alerts"] == ([] if attempt < 3 else ["review_held"])
        if attempt < 3:
            assert cur == {"f1": "1"} and not (tmp_path / "state" / offer_check.HELD_FILE).exists()
        else:
            assert cur == {"f1": "1", "f2": "1"}
            kept = json.loads((tmp_path / "state" / offer_check.HELD_FILE).read_text())
            assert kept[0]["file_id"] == "f2" and kept[0]["amount"] == 25000


def test_a_final_hold_of_the_firms_own_demand_does_not_page(tmp_path, monkeypatch):
    """FALSIFIER (2026-10-15): every page that week was the firm's own demand
    held correctly. It is kept for SMD in held.json and never pages."""
    demand = _offer(kind="demand", by="Ashton for the client", amount=90000, author_side="our_firm")
    for attempt in range(1, 4):
        run_dir = tmp_path / f"d{attempt}"
        run_dir.mkdir()
        r, _, _ = _run(run_dir, monkeypatch, [demand])
        r.state = tmp_path / "state"
        if attempt == 1:
            arrivals.commit("negotiation", M1, FILES, data=r.state)
        v = r.run()
        assert v["held"][0]["kind"] == "demand" and v["held"][0]["retry"] is (attempt < 3)
        assert v["proof"]["alerts"] == []
    kept = json.loads((tmp_path / "state" / offer_check.HELD_FILE).read_text())
    assert kept[0]["kind"] == "demand" and kept[0]["amount"] == 90000


@pytest.mark.parametrize("kind", sorted(offer_check.rows_mod.NOTICE_KINDS))
def test_every_offer_kind_pages_when_final(kind):
    assert offer_check.pages_smd({"kind": kind}) is True


@pytest.mark.parametrize("kind", ["demand", "998_offer_by_us", "acceptance", "other", ""])
def test_the_firms_own_and_non_offer_kinds_do_not_page(kind):
    assert offer_check.pages_smd({"kind": kind}) is False


# ---- the read-back and the proof block ----------------------------------------------------------
def test_a_readback_mismatch_rides_as_an_alert(tmp_path, monkeypatch):
    r, _, _ = _run(tmp_path, monkeypatch, [_offer()], layout=Layout(status="readback_mismatch"))
    v = r.run()
    assert v["rows_readback_mismatch"] == 1 and v["notices"][0]["status"] == "not_entered"
    assert v["proof"]["alerts"] == ["readback_mismatch"] and v["proof"]["rows"] == 0


def test_a_run_with_nothing_new_delivers_nothing_to_file(tmp_path, monkeypatch):
    r, layout, client = _run(tmp_path, monkeypatch, [], files=FILES)
    v = r.run()
    p = v["proof"]
    assert p["nothing_to_file"] is True and p["rows"] == 0 and p["review"] == "code_only"
    assert p["review_cents"] == 0 and client.calls == [] and layout.calls == []
    assert gate.problem({"outcome": "delivered", "proof": p}) is None


def test_a_failed_verdict_carries_no_proof(tmp_path, monkeypatch):
    r, _, _ = _run(tmp_path, monkeypatch, [])
    r._seat = SimpleNamespace(client=SimpleNamespace(get=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))))
    v = r.run()
    assert v["verdict"] == "failed" and "proof" not in v


# ---- the dry run reports what would be held, writing nothing ---------------------------------------
def test_the_dry_run_reports_the_holds_and_writes_nothing(tmp_path, monkeypatch):
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
    monkeypatch.setattr(run_mod.read_mod, "read_document", lambda *a, **k: [_offer(amount=25000), _offer()])
    files = [
        {"id": "f1", "name": "Intake", "ext": ".pdf", "modified": "1", "created": "2026-10-08T22:10:00Z"},
        {"id": "f2", "name": "Offer letter", "ext": ".pdf", "modified": "1", "created": "2026-10-09T15:30:00Z"},
    ]
    layout = Layout()
    r = dryrun.NegotiationDryRun(
        wd,
        pricing=str(pricing),
        seat_factory=lambda: Seat(files),
        layout=layout,
        client=ReviewClient(),
        log=lambda m: None,
    )
    monkeypatch.setattr(r, "_text", lambda mid, f: LETTER)
    summary = r.run()
    assert layout.calls == [] and summary["held"] == 1 and summary["rows_planned"] == 1
    report = json.loads((wd / dryrun.REPORT).read_text())
    held = report["entries"][0]["held"]
    assert held[0]["amount"] == 25000 and "is not printed" in held[0]["reason"]
    assert "would hold" in (wd / dryrun.REPORT_TEXT).read_text()
