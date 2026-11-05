"""W7 of ADR 0104: an offer is never entered against the wrong plaintiff.

The answer key (``answer_keys/negotiation_plaintiff.json``) was written before
the code. Silence in a document is not "joint": it used to enter "Joint, all
plaintiffs." on the first plaintiff's tab, and the reader's null meant both. A
carrier repeating an offer is decided by the connector's ``match_existing``,
never by the reader; ``amount_confirmed`` is code's reading of the text, never
the reader's word.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobs.negotiation import extract as X, notice as N, rows as R

KEY = json.loads((Path(__file__).parent / "answer_keys" / "negotiation_plaintiff.json").read_text(encoding="utf-8"))
DOC = {"name": "Offer 10-07.pdf", "ext": ".pdf"}
MATTER = {"number": "200123", "title": "Doe v. Example"}


def _event(case: dict) -> dict:
    return {
        "kind": "offer",
        "by": "Example Mutual Insurance Company",
        "to": "Example client",
        "amount": 15000,
        "amount_confirmed": True,
        "date": "2026-10-07",
        "note": "",
        "author_side": "paying_side",
        "addressed_to": "our_firm",
        **case.get("event", {}),
        "plaintiff": case["plaintiff"],
    }


def _tabs(case: dict) -> list[dict]:
    got = case["tabs"]
    return KEY[got] if isinstance(got, str) else got


@pytest.mark.parametrize("case", KEY["cases"], ids=[c["name"] for c in KEY["cases"]])
def test_the_answer_key(case: dict) -> None:
    expect, tabs = case["expect"], _tabs(case)
    plan = R.plan_document([_event(case)], DOC, tabs, {"example"}, plaintiffs=case.get("plaintiffs"))
    entered = {pidx: tp.args for pidx, tp in plan["tabs"].items() if tp.args}
    if expect["tab"] is None:
        assert not entered
    else:
        assert list(entered) == [expect["tab"]]
        assert entered[expect["tab"]][0]["note"].startswith(expect["note_opens"])
    if expect["status"] is None:
        assert plan["offers"] == []
    else:
        rec = plan["offers"][0]
        # What the run's write does with a row that reads back: the notice's input.
        rec["status"] = "written" if rec["status"] == "to_write" else "not_entered"
        assert rec["status"] == expect["status"]
        text = N.compose(MATTER, DOC, rec, N.plaintiff_label(rec, tabs))
        assert text == expect["notice"]
    if "held" in expect:
        assert [h["reason"] for h in plan["held"]] == [expect["held"]]
    else:
        assert plan["held"] == []


def test_no_answer_key_case_ever_writes_joint_for_an_unstated_document() -> None:
    """Hard rule, stated apart from the table: "Joint, all plaintiffs." is only
    ever written when the reader said joint."""
    for case in KEY["cases"]:
        if case["plaintiff"]["kind"] == "joint":
            continue
        plan = R.plan_document([_event(case)], DOC, _tabs(case), {"example"}, plaintiffs=case.get("plaintiffs"))
        notes = [a.get("note", "") for tp in plan["tabs"].values() for a in tp.args]
        assert not any(n.startswith("Joint") for n in notes), case["name"]


# ---- what the reader is asked for -------------------------------------------------------------
def _answer(**event: object) -> str:
    return json.dumps(
        {
            "unsent_draft": False,
            "author_side": "paying_side",
            "addressed_to": "our_firm",
            "document_date": "2026-10-07",
            "events": [
                {
                    "kind": "offer",
                    "by": "Example Mutual",
                    "to": "client",
                    "amount": 15000,
                    "date": "2026-10-07",
                    "plaintiff": {"kind": "unstated", "index": None},
                    "note": "",
                    **event,
                }
            ],
        }
    )


def test_the_schema_asks_three_ways_about_the_plaintiff() -> None:
    props = X.ITEM["properties"]
    assert "plaintiff_index" not in props
    assert props["plaintiff"]["properties"]["kind"]["enum"] == ["joint", "index", "unstated"]
    assert set(props["plaintiff"]["required"]) == {"kind", "index"}


def test_the_prompt_no_longer_lets_the_reader_decide_what_code_decides() -> None:
    assert "oldest first" not in X.SYSTEM
    assert "repeating" not in X.SYSTEM and "reiterates" not in X.SYSTEM
    assert "amount_confirmed" not in X.SYSTEM
    assert "amount_confirmed" not in X.ITEM["properties"]
    assert "CURRENT NEGOTIATION DETAILS ROWS" not in X.render(DOC, "text", [])


def test_the_prompt_says_silence_is_not_joint() -> None:
    assert "unstated" in X.SYSTEM and "never because the document is silent" in X.SYSTEM


@pytest.mark.parametrize(
    "printed,confirmed",
    [
        ("We offer $15,000 to settle.", True),
        ("We offer 15,000.00 to settle.", True),
        ("We offer 15000 to settle.", True),
        ("We offer fifteen thousand dollars to settle.", False),
        ("Our limit is $150,000.", False),
    ],
)
def test_amount_confirmed_is_code_reading_the_document(printed: str, confirmed: bool) -> None:
    # The reader's own word, sent or not, never decides.
    assert X.parse(_answer(amount_confirmed=not confirmed), document=printed)[0]["amount_confirmed"] is confirmed


def test_read_document_confirms_against_the_text_it_read() -> None:
    class Doorway:
        def call(self, stage: str, **kw: object) -> SimpleNamespace:
            return SimpleNamespace(text=_answer(), stop_reason="end_turn")

    assert X.read_document(Doorway(), DOC, "An offer of $15,000.", [])[0]["amount_confirmed"] is True
    assert X.read_document(Doorway(), DOC, "An offer was made.", [])[0]["amount_confirmed"] is False


def test_the_plaintiffs_the_reader_is_given_include_one_with_no_tab() -> None:
    who = [{"plaintiff_index": 0, "name": "Ann Example"}, {"plaintiff_index": 1, "name": "Bo Example"}]
    prompt = X.render(DOC, "text", who)
    assert "- plaintiff_index 0: Ann Example" in prompt and "- plaintiff_index 1: Bo Example" in prompt
