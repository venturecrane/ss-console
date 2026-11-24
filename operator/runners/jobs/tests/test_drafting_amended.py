"""Amended responses after the other side's meet-and-confer letter (``challenge.py``,
``amended.py``, ``meetconfer/memo.py``).

Every name, number and date is invented. The letter shapes follow the
2026-11-23 count of the three letters on one real matter: text PDFs that name numbers as
lists and ranges ("Nos. 1-9", "1, 2, 4-8, 10"), dotted form numbers, never
"all". The reads are injected: no test calls a model.
"""

from __future__ import annotations

import json

import pytest
from test_drafting_served_find import CAPTION, DEF, Fake, matter, response, srog, values

from jobs.drafting import amended, challenge, review, served, served_find
from jobs.drafting.meetconfer import memo
from jobs.drafting.served_docs import Doc

SROG = "special_interrogatories"
RFP = "requests_for_production"


def letter(pages: list[str], name: str = "DEF-SROGS1-M&C.pdf", listed: str = "2026-11-05") -> Doc:
    return Doc("L1", name, "/Discovery/DEF", {i: t for i, t in enumerate(pages, 1)}, listed, "shaL1")


def target(kind: str = SROG, n: int = 22) -> challenge.Target:
    return challenge.Target(kind, 1, tuple(str(i) for i in range(1, n + 1)))


def item(quote: str, numbers: list[str], page: int = 1, kind: str = SROG, set_number: int | None = 1) -> dict:
    return {"set_type": kind, "set_number": set_number, "numbers": numbers, "quote": quote, "page": page}


class Asks:
    """The injected read: one scripted answer per call, in order."""

    def __init__(self, *answers: list[dict]) -> None:
        self.answers, self.calls = list(answers), []

    def __call__(self, system: str, user: str, max_tokens: int) -> str:
        self.calls.append(user)
        return json.dumps({"items": self.answers[min(len(self.calls), len(self.answers)) - 1]})


# ---- numbers in prose ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, want",
    [
        ("Your responses to Special Interrogatory Nos. 1-9 are evasive.", [str(i) for i in range(1, 10)]),
        ("Request Nos. 1, 2, 4-8, 10 are deficient.", ["1", "2", "4", "5", "6", "7", "8", "10"]),
        ("Interrogatories 12 through 15 remain unanswered.", ["12", "13", "14", "15"]),
        ("Form Interrogatories 12.1, 12.3 and 14.1 and No. 14.2.", ["12.1", "12.3", "14.1", "14.2"]),
        ("No. 8 and No. 6 still have no production.", ["8", "6"]),
        ("under Code of Civil Procedure section 2030.300 by March 3, 2026", []),
    ],
)
def test_code_reads_every_number_a_letter_names_ranges_written_out(text, want):
    assert challenge.expand(text) == want


def test_a_dotted_range_is_the_served_numbers_between_its_ends():
    assert challenge.expand("Form Interrogatories 12.1-12.3", ("12.1", "12.3", "14.1")) == ["12.1", "12.3"]


# ---- one read per letter, checked by code ----------------------------------------------------

BODY = (
    "MEET AND CONFER re Plaintiff's responses to Special Interrogatories, Set One. "
    "Your responses to Special Interrogatory Nos. 1-9 are evasive and incomplete. "
    "In particular, Nos. 1, 4 and 7 do not identify any witness."
)


def test_a_letter_read_keeps_only_items_whose_quote_and_numbers_check():
    d = letter([BODY])
    ask = Asks([
        item("Your responses to Special Interrogatory Nos. 1-9 are evasive and incomplete.", [str(i) for i in range(1, 10)]),
        item("In particular, Nos. 1, 4 and 7 do not identify any witness.", ["1", "4", "7"]),
    ])  # fmt: skip
    kept, refused = challenge.read_letter(d, ask, [target()])
    assert refused == [] and len(ask.calls) == 1
    assert {n for i in kept for n in i.numbers} == {str(i) for i in range(1, 10)}


def test_a_range_read_short_is_refused_retried_then_held_challenge_unread():
    """Design C example 7: "Nos. 12 through 15" read as 12 and 15 stops before the amended sets are paid for."""
    d = letter(["MEET AND CONFER. Special Interrogatory Nos. 12 through 15 are deficient; further responses due."])
    short = [item("Special Interrogatory Nos. 12 through 15 are deficient", ["12", "15"])]
    ask = Asks(short, short)
    with pytest.raises(challenge.Unread) as exc:
        challenge.read_letter(d, ask, [target()])
    assert str(exc.value).startswith("challenge_unread: ") and len(ask.calls) == 2
    assert "YOUR LAST READ MISSED" in ask.calls[1]


def test_a_number_the_read_leaves_out_is_found_by_code_and_holds():
    d = letter([BODY])
    ask = Asks([item("Your responses to Special Interrogatory Nos. 1-9 are evasive and incomplete.",
                     [str(i) for i in range(1, 10)])])  # fmt: skip
    with pytest.raises(challenge.Unread, match="nos. 1, 4 and 7"):
        challenge.read_letter(d, ask, [target()])


def test_a_quote_not_on_its_page_or_a_number_outside_the_served_set_is_refused():
    d = letter([BODY])
    kept, why = challenge.verify(
        [item("Nos. 1-9 are wholly evasive.", [str(i) for i in range(1, 10)]),
         item("Special Interrogatory Nos. 1-9", [str(i) for i in range(1, 10)])],
        d, [target(n=5)],
    )  # fmt: skip
    assert kept == []
    assert "not on that page" in why[0] and "not in the served set" in why[1]


def test_a_letter_covering_two_sets_keeps_each_against_its_own_set():
    text = (
        "MEET AND CONFER. Special Interrogatory Nos. 2 and 3 are evasive. "
        "Request for Production Nos. 4-6 show no documents; further responses are required."
    )
    d = letter([text])
    ask = Asks([
        item("Special Interrogatory Nos. 2 and 3 are evasive.", ["2", "3"]),
        item("Request for Production Nos. 4-6 show no documents", ["4", "5", "6"], kind=RFP, set_number=None),
    ])  # fmt: skip
    kept, _ = challenge.read_letter(d, ask, [target(), target(RFP, 10)])
    by = {(i.set_type, i.numbers) for i in kept}
    assert by == {(SROG, ("2", "3")), (RFP, ("4", "5", "6"))}


def test_all_responses_is_kept_only_when_the_quote_says_all():
    d = letter(["MEET AND CONFER. All of your responses to the requests for production are deficient."])
    kept, _ = challenge.verify(
        [item("All of your responses to the requests for production are deficient.", ["all"], kind=RFP)],
        d, [target(RFP, 3)],
    )  # fmt: skip
    assert kept[0].numbers == ("1", "2", "3")


# ---- the trigger -----------------------------------------------------------------------------


def _pair():
    s = served.ServedSet(SROG, 1, "Defendant X", "Plaintiff Y", 3, "", "operator")
    return [(s, letter(["x"], name="Resp.pdf"))]


@pytest.mark.parametrize(
    "request_text, owed, letters, is_proven, want",
    [
        ("Draft responses to the discovery.", True, True, True, amended.NOT),
        ("Draft responses to the discovery.", False, True, True, amended.AMENDED),
        ("Draft responses to the discovery.", False, False, True, amended.NOT),
        ("Please do further responses to the rogs.", False, False, True, "no letter"),
        ("Please do further responses to the rogs.", False, True, False, "not proven"),
    ],
)
def test_amended_runs_on_the_wording_or_the_file_and_only_where_proven(request_text, owed, letters, is_proven, want):
    got = amended.decide(request_text, owed, _pair(), [letter([BODY])] if letters else [], lambda: is_proven)
    assert got == want if want in (amended.NOT, amended.AMENDED) else want in got


def test_the_file_state_finds_the_answered_set_and_the_later_letter(tmp_path, monkeypatch):
    """Our served response answers the only set and the other side's letter came after: amended, at $0."""
    monkeypatch.setattr(amended, "proven", lambda *a: True)
    body = "MEET AND CONFER. Your response to Special Interrogatory No. 1 is evasive; further responses are due."
    docs = [
        {"id": "s1", "name": "Def SROGs Set One.pdf", "pages": srog()},
        {"id": "r1", "name": "Resp SROGs.pdf", "pages": response("SPECIAL INTERROGATORIES"),
         "created": "2026-10-30T09:00:00"},
        {"id": "L1", "name": "DEF-SROGS1-M&C.pdf", "folder": "/Discovery/DEF", "pages": [body],
         "created": "2026-11-05T09:00:00"},
    ]  # fmt: skip
    data, jd = matter(tmp_path, docs)
    fake = Fake({"SPECIAL INTERROGATORIES, SET": values(SROG, 22)})
    served_find.find(data, jd, "They sent a meet and confer on our rogs, see attached.", fake.reader(),
                     clients=["Gamma Example"])  # fmt: skip
    rec = amended.load(data)
    assert rec["letters"] == ["L1"] and rec["sets"][0]["responses"] == {"1": "Gamma Example."}
    ask = Asks([item("Your response to Special Interrogatory No. 1 is evasive", ["1"])])
    ch = amended.challenge_stage(data, jd, ask)
    units = amended.plan_units(served.read(jd), ch)
    assert units == [{"key": "set-1", "variant": SROG, "label": "Amended Responses to Special Interrogatories, Set One",
                      "served": 0, "numbers": ["1"]}]  # fmt: skip
    text = amended.instruction(data, served.read(jd)[0], ["1"])
    assert "SPECIAL INTERROGATORY NO. 1: State example fact 1." in text and "NO. 2:" not in text
    assert "OUR SERVED RESPONSE: Gamma Example." in text and "is evasive" in text


def test_the_runner_marks_the_envelope_and_the_lane_key_reads_amended(tmp_path):
    from jobs import lane_proof

    env = {"kind": "drafting", "document_class": "discovery_response"}
    (tmp_path / "envelope.json").write_text(json.dumps(env), encoding="utf-8")
    amended.mark_envelope(tmp_path)
    marked = json.loads((tmp_path / "envelope.json").read_text(encoding="utf-8"))
    assert lane_proof.lane_key("drafting", marked) == amended.KEY
    assert lane_proof.lane_key("drafting", env) == "document-drafter.discovery_response"


def test_our_served_paper_reads_by_number_with_pleading_line_numbers_dropped():
    text = "1\nRESPONSE TO SPECIAL INTERROGATORY NO. 1:\n2\nGamma Example.\nRESPONSE TO SPECIAL INTERROGATORY NO. 2:\nNone.\n"
    assert amended.our_responses(text, SROG) == {"1": "Gamma Example.", "2": "None."}


def test_the_title_reads_amended_by_code_and_item_headings_are_untouched():
    doc = f"{CAPTION}PLAINTIFF'S RESPONSES TO SPECIAL INTERROGATORIES, SET ONE\nRESPONSE TO SPECIAL INTERROGATORY NO. 4:\nX."
    got, said = amended.retitle(doc)
    assert "PLAINTIFF'S AMENDED RESPONSES TO SPECIAL INTERROGATORIES" in got and said
    assert "\nRESPONSE TO SPECIAL INTERROGATORY NO. 4:" in got
    assert amended.retitle(got) == (got, [])


# ---- the review counts the subset -----------------------------------------------------------


def _paper(numbers: list[int]) -> review.Paper:
    body = "\n".join(f"SPECIAL INTERROGATORY NO. {n}:\nState example fact {n}.\nRESPONSE TO SPECIAL INTERROGATORY "
                     f"NO. {n}:\nAnswer {n}." for n in numbers)  # fmt: skip
    return review.Paper("set-1", "Amended Responses", body, "plain")


def test_the_review_accepts_exactly_the_challenged_subset():
    s = served.ServedSet(SROG, 1, DEF, "Gamma Example", 9, "\n".join(srog(9)[1:2]), "operator")
    plan = [{"key": "set-1", "variant": SROG, "label": "Amended", "served": 0, "numbers": ["4", "7", "8", "9"]}]
    m = served.review_map([s], plan)
    assert review.response_findings([_paper([4, 7, 8, 9])], m) == []
    found = review.response_findings([_paper([4, 7, 8])], m)
    assert found and "no response to number 9" in found[0].finding
    assert review.response_findings([_paper([4, 5, 7, 8, 9])], m)


# ---- the memo ---------------------------------------------------------------------------------


def test_the_memo_assembles_each_challenged_request_with_position_by_code(tmp_path):
    s = served.ServedSet(SROG, 1, DEF, "Gamma Example", 3, "SPECIAL INTERROGATORY NO. 1:\nName witnesses.\n"
                         "SPECIAL INTERROGATORY NO. 2:\nState damages.\nSPECIAL INTERROGATORY NO. 3:\nState income.\n",
                         "operator")  # fmt: skip
    ch = challenge.Challenges(
        [{"id": "L1", "name": "M&C.pdf", "listed": "2026-11-05"}],
        [challenge.Item(SROG, 1, ("1", "2"), "Nos. 1 and 2 are evasive.", "M&C.pdf", 2)],
    )
    rec = {"sets": [{"set_type": SROG, "set_number": 1, "response_listed": "2026-10-30",
                     "responses": {"1": "None known.", "2": "Unknown at this time."}}]}  # fmt: skip
    final = ("SPECIAL INTERROGATORY NO. 1:\nName witnesses.\n\nRESPONSE TO SPECIAL INTERROGATORY NO. 1:\n\n"
             "Alpha Witness and Beta Witness.\n\nSPECIAL INTERROGATORY NO. 2:\nState damages.\n\n"
             "RESPONSE TO SPECIAL INTERROGATORY NO. 2:\n\nUnknown at this time.\n")  # fmt: skip
    (tmp_path / "rulebook").mkdir()
    (tmp_path / "rulebook" / "set-1.json").write_text(
        json.dumps({"applied": [{"source": "rule:r9", "item": "2", "key": "k"}]})
    )
    rules = [{"rule_id": "r9", "text": "Say unknown when the client does not know.", "at": "2026-11-02T10:00:00Z"}]
    es = memo.entries([s], ch, rec, {(SROG, 1): ("set-1", tmp_path, final)}, rules)
    assert [(e.number, e.position) for e in es] == [("1", memo.CHANGED), ("2", memo.UNCHANGED)]
    assert es[1].rules == ['"Say unknown when the client does not know." (stated 2026-11-02)'] and es[0].rules == []

    def ask(system: str, user: str, max_tokens: int) -> str:
        return json.dumps({es[0].id: "The challenge has merit; the file names two witnesses.",
                           es[1].id: "Weak: 2,000 dollars is claimed."})  # fmt: skip

    said = memo.assess(es, ask)
    assert es[0].id in said and es[1].id not in said  # a number not in the item's inputs is refused
    md = memo.render_md(es, said, ch, rec, "Matter 100001")
    assert md.count("**Draft assessment.**") == 2 and memo.NO_ASSESSMENT in md and memo.DEADLINE in md
    assert '"Nos. 1 and 2 are evasive." (M&C.pdf, page 2)' in md and "Alpha Witness and Beta Witness." in md
