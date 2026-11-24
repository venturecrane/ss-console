"""A propounded set's count is code's (design B, B1): the firm's target in the
unit's instruction, the request's own number when it states one, one "more
requests" pass below the minimum, renumbering at the gate, and one declaration
decision for sections 2030.050 and 2033.050 that the attach, the format check
and the review share. Every name, number and party here is invented.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from drafting_testkit import DECL, POS, firm_data

from jobs.drafting import counts, extend, format_check, render, set_items

FMT = firm_data()["format"]
COUNTS = {"special_interrogatories": {"min": 50, "max": 75}, "requests_for_admission": {"max": 50}}
DECL_2033 = """<!-- authoring comment that must never reach the document -->

# DECLARATION FOR ADDITIONAL DISCOVERY (CODE CIV. PROC. § 2033.050)

1. I am presently the attorney for `{{FILL: propounding party, as captioned | operative pleading}}`.

4. I have previously propounded a total of `{{FILL: number of requests for admission previously propounded to this party | prior sets}}` requests for admission to this party.

5. This set of requests for admission contains a total of `{{FILL: number of requests for admission in this set | the count}}` requests.
"""
HEAD = "| | |\n| --- | --- |\n| PROPOUNDING PARTY: | GAMMA EXAMPLE |\n| RESPONDING PARTY: | DELTA EXAMPLE |\n\n"


def _set(variant: str, numbers: list[int], tail: str = "Dated: x\n") -> str:
    label = extend.LABEL[variant]
    body = "\n\n".join(f"**{label} NO. {n}**\n\nState fact {n}." for n in numbers)
    return HEAD + '# DEFINITIONS\n\n"INCIDENT" means the fall.\n\n' + body + "\n\n" + tail


def _rfas(n: int) -> str:
    return _set("requests_for_admission", list(range(1, n + 1)))


class Firm:
    def __init__(self, forms: dict[str, str] | None = None, data: dict[str, Any] | None = None) -> None:
        self.forms = {"decl_2030_050": DECL, "pos": POS, **(forms or {})}
        self.data = data or {}

    def attachment(self, key: str) -> str:
        return self.forms[key]

    def model(self, stage: str) -> str:
        return "invented-model"


class Run:
    """What ``DraftingRun`` supplies to ``counts``' call sites."""

    def __init__(self, tmp: Path, request: str, data: dict[str, Any] | None = None, answer: str = "[]") -> None:
        self.cls, self.data = "discovery_set", tmp
        self.firm = Firm(data={"discovery_counts": COUNTS} if data is None else data)
        self.job = SimpleNamespace(request_text=request)
        self.calls: list[dict[str, Any]] = []
        self.state: dict[str, Any] = {}
        self.answer = answer
        self.doorway = SimpleNamespace(call=self._call)
        self.log = lambda _m: None

    def _call(self, stage: str, **kw: Any) -> Any:
        self.calls.append({"stage": stage, **kw})
        return SimpleNamespace(text=self.answer)

    def _is_done(self, stage: str) -> bool:
        return self.state.get(stage, {}).get("status") == "done"

    def _done(self, stage: str, **info: Any) -> None:
        self.state[stage] = {"status": "done", **info}

    def _state(self) -> dict[str, Any]:
        return self.state


def _unit(tmp: Path, variant: str, key: str = "set-1") -> Any:
    return SimpleNamespace(key=key, variant=variant, data=tmp, stage=lambda s: f"{key}:{s}" if key else s)


# ---- the one declaration decision -----------------------------------------------------------


@pytest.mark.parametrize(
    "variant, count, prior, want",
    [
        ("special_interrogatories", 36, 0, "decl_2030_050"),
        ("special_interrogatories", 35, 0, None),
        ("special_interrogatories", 20, 16, "decl_2030_050"),
        ("requests_for_admission", 36, 0, "decl_2033_050"),
        ("requests_for_admission", 30, 6, "decl_2033_050"),
        ("requests_for_admission", 35, 0, None),
        ("requests_for_admission", 0, 40, None),
        ("requests_for_production", 90, 0, None),
    ],
)
def test_one_decision_for_both_declarations(variant, count, prior, want):
    assert counts.declaration(variant, count, prior) == want


# ---- reading and numbering the set ----------------------------------------------------------


def test_requests_are_read_as_their_labels_and_a_sentence_naming_one_is_not_a_label():
    doc = _set("special_interrogatories", [1, 2]) + "Special Interrogatory No. 41 asked the date.\n"
    assert set_items.count(doc, "special_interrogatories") == 2
    inline = "REQUEST FOR ADMISSION NO. 1: Admit A.\n\nREQUEST FOR ADMISSION NO. 2: Admit B.\n"
    items = set_items.read(inline, "requests_for_admission")
    assert [i.text(inline) for i in items] == [
        "REQUEST FOR ADMISSION NO. 1: Admit A.",
        "REQUEST FOR ADMISSION NO. 2: Admit B.",
    ]


def test_the_time_and_place_paragraph_after_the_last_request_is_the_sets_own():
    doc = _set("requests_for_production", [1, 2], tail="Produce them at the office on the date set.\n\nDated: x\n")
    last = set_items.read(doc, "requests_for_production")[-1]
    assert last.text(doc).endswith("State fact 2.")
    out, added = set_items.append(doc, "requests_for_production", "REQUEST FOR PRODUCTION NO. 3\n\nAll photos.")
    assert added == 1
    assert out.index("All photos.") < out.index("Produce them at the office")


def test_renumbering_puts_labels_in_order_and_follows_the_sets_own_references():
    doc = _set("requests_for_admission", [1, 2, 4, 5]) + "Request for Admission No. 4 is the key one.\n"
    r = set_items.renumber(doc, "requests_for_admission")
    assert r.after == [1, 2, 3, 4] and r.remapped == {4: 3}
    assert "Request for Admission No. 3 is the key one." in r.doc
    assert set_items.renumber(r.doc, "requests_for_admission").changed is False
    assert set_items.numbers_note(r, "requests_for_admission")[0].startswith(
        "Numbering: the requests for admission were numbered again in order (former No. 4 is now No. 3"
    )


def test_a_reference_to_a_shared_number_is_named_never_guessed():
    doc = _set("special_interrogatories", [1, 2, 2]) + "See Special Interrogatory No. 2.\n"
    r = set_items.renumber(doc, "special_interrogatories")
    assert r.after == [1, 2, 3] and r.unresolved == [2]
    assert "See Special Interrogatory No. 2." in r.doc
    assert "left as written for your review" in set_items.numbers_note(r, "special_interrogatories")[1]


def test_a_set_continuing_from_an_earlier_set_keeps_its_first_number():
    r = set_items.renumber(_set("special_interrogatories", [36, 38]), "special_interrogatories")
    assert r.after == [36, 37]


# ---- the request's own number -----------------------------------------------------------------


def test_the_request_number_is_kept_only_from_the_emails_own_words():
    email = "Please draft RFAs, about 40 RFAs, and SROGs to the defense on matter 200999."
    answer = json.dumps(
        [
            {"set": "requests_for_admission", "number": 40, "quote": "about 40 RFAs"},
            {"set": "special_interrogatories", "number": 60, "quote": "about 60 SROGs"},  # not in the email
            {"set": "requests_for_production", "number": 99, "quote": "about 40 RFAs"},  # digits not in the quote
            {"set": "form_interrogatories", "number": 5, "quote": "5"},
        ]
    )
    got = counts.keep(answer, email)
    assert got == {"requests_for_admission": counts.Asked(40, "about 40 RFAs")}
    assert counts.keep("not json", email) == {}


def test_the_ask_is_spent_once_and_only_when_a_number_sits_near_a_set(tmp_path):
    run = Run(
        tmp_path,
        "Draft about 40 RFAs.",
        answer='[{"set": "requests_for_admission", "number": 40, "quote": "about 40 RFAs"}]',
    )
    assert counts.asked(run)["requests_for_admission"].number == 40
    assert counts.asked(run)["requests_for_admission"].number == 40
    assert len(run.calls) == 1 and run.calls[0]["stage"] == counts.ASK_STAGE
    quiet = Run(tmp_path / "q", "Draft RFAs for the 11/23 hearing on matter 200999.")
    (tmp_path / "q").mkdir()
    assert counts.asked(quiet) == {} and quiet.calls == []
    dark = Run(tmp_path / "d", "Draft about 40 RFAs.", data={})
    (tmp_path / "d").mkdir()
    assert counts.asked(dark) == {} and dark.calls == []


# ---- the instruction ------------------------------------------------------------------------


def test_the_firms_number_is_written_into_the_units_instruction(tmp_path):
    run = Run(tmp_path, "Draft SROGs and RFAs.")
    line = counts.instruction(run, "special_interrogatories", "THIS DOCUMENT: draft ONLY the set.")
    assert line.startswith("THIS DOCUMENT: draft ONLY the set. SET SIZE (the firm's range): write between 50 and 75")
    assert "no more than 50 requests for admission" in counts.instruction(run, "requests_for_admission", "x")
    assert counts.instruction(run, "requests_for_production", "x") == "x"
    assert counts.instruction(run, "special_interrogatories", None).startswith(
        "THIS DOCUMENT (set by the job): SET SIZE"
    )


def test_the_requests_own_number_wins(tmp_path):
    run = Run(
        tmp_path,
        "Draft about 40 RFAs.",
        answer='[{"set": "requests_for_admission", "number": 40, "quote": "about 40 RFAs"}]',
    )
    line = counts.instruction(run, "requests_for_admission", "x")
    assert 'write 40 requests for admission, the number the request states ("about 40 RFAs")' in line


def test_nothing_changes_without_targets_or_on_a_response(tmp_path):
    dark = Run(tmp_path, "Draft SROGs.", data={})
    assert counts.instruction(dark, "special_interrogatories", "x") == "x"
    assert counts.instruction(dark, "special_interrogatories", None) is None
    resp = Run(tmp_path, "Draft responses.")
    resp.cls = "discovery_response"
    assert counts.instruction(resp, "special_interrogatories", "x") == "x"
    doc = _set("special_interrogatories", [1, 3])
    assert counts.at_gate(dark, _unit(tmp_path, "special_interrogatories"), doc) == doc
    assert not (tmp_path / counts.FILE).exists()


# ---- the top-up pass and the gate --------------------------------------------------------------


class Drafter:
    context = "THE ATTORNEY'S REQUEST: draft SROGs."

    def __init__(self, more: str) -> None:
        self.more, self.users = more, []

    def compose_system(self) -> str:
        return "SYSTEM"

    def _call(self, stage: str, system: str, user: str, cid: str) -> Any:
        self.users.append(user)
        return SimpleNamespace(text=self.more)


def test_a_short_set_gets_one_more_requests_pass_before_the_audit(tmp_path):
    run = Run(tmp_path, "Draft SROGs.")
    u = _unit(tmp_path, "special_interrogatories")
    (tmp_path / "draft-v1.md").write_text(
        _set("special_interrogatories", list(range(1, 41))) + "\n=== ATTORNEY NOTES ===\n\nnotes\n", encoding="utf-8"
    )
    d = Drafter("SPECIAL INTERROGATORY NO. 41\n\nState fact x.\n\nSPECIAL INTERROGATORY NO. 7\n\nState fact y.")
    counts.after_compose(run, u, d, "DIGEST")
    counts.after_compose(run, u, d, "DIGEST")  # once per unit
    assert len(d.users) == 1
    assert "has 40 special interrogatories; the firm's number for this set is 50 to 75" in d.users[0]
    assert "between 10 and 35 further" in d.users[0] and "DIGEST" in d.users[0]
    text = (tmp_path / "draft-v1.md").read_text(encoding="utf-8")
    assert set_items.count(text, "special_interrogatories") == 42 and "=== ATTORNEY NOTES ===\n\nnotes" in text
    assert [i.number for i in set_items.read(text, "special_interrogatories")][-2:] == [41, 42]
    assert run.state["set-1:topup"] == {"status": "done", "before": 40, "after": 42}


def test_a_set_at_its_minimum_spends_nothing(tmp_path):
    run = Run(tmp_path, "Draft SROGs.")
    (tmp_path / "draft-v1.md").write_text(_set("special_interrogatories", list(range(1, 51))), encoding="utf-8")
    d = Drafter("")
    counts.after_compose(run, _unit(tmp_path, "special_interrogatories"), d, "DIGEST")
    rfa = Run(tmp_path / "r", "Draft RFAs.")
    (tmp_path / "r").mkdir()
    (tmp_path / "r" / "draft-v1.md").write_text(_rfas(3), encoding="utf-8")
    counts.after_compose(rfa, _unit(tmp_path / "r", "requests_for_admission"), d, "DIGEST")  # no minimum
    assert d.users == []


def test_the_gate_numbers_the_set_and_records_its_counts(tmp_path):
    run = Run(tmp_path, "Draft SROGs.")
    run.state["set-1:topup"] = {"status": "done", "before": 40, "after": 42}
    u = _unit(tmp_path, "special_interrogatories")
    out = counts.at_gate(run, u, _set("special_interrogatories", [*range(1, 41), 43, 44]))
    assert [i.number for i in set_items.read(out, "special_interrogatories")][-2:] == [41, 42]
    f = counts.facts(u)
    assert f is not None and f.count == 42 and f.target == (50, 75) and f.topped_up == (40, 42)
    assert f.numbering and "former No. 43 is now No. 41" in f.numbering[0]
    counts.at_gate(run, u, out)  # gated again after a format repair: what changed is kept
    assert counts.facts(u).numbering == f.numbering


# ---- the declaration attached, checked and reviewed ----------------------------------------------


def _facts(variant: str, n: int, **kw: Any) -> counts.Facts:
    return counts.Facts(
        variant=variant,
        count=n,
        target=tuple(counts.targets({"discovery_counts": COUNTS}).get(variant, (None, None))),
        **kw,
    )


def test_an_admission_set_over_35_carries_the_2033_declaration_when_the_firm_authored_it():
    doc, notes = render.attach(_rfas(40), "discovery_set", Firm({"decl_2033_050": DECL_2033}), "")
    assert (
        "§ 2033.050" in doc
        and "a total of 0 requests for admission" in doc
        and "contains a total of 40 requests" in doc
    )
    assert doc.index("2033.050") < doc.index("PROOF OF SERVICE") and "GAMMA EXAMPLE" in doc.split("2033.050")[1]
    assert any(n.startswith("section 2033.050 declaration attached: 40 requests for admission") for n in notes)


def test_prior_admission_sets_count_toward_the_2033_declaration():
    digest = (
        "## DISCOVERY-SETS\nRequests for Admission, Set One | One | Gamma Example -> Delta Example | "
        "requests for admission | 10 | 03/01/2026 | FILE: RFA one.pdf\n"
    )
    corpus = {
        "RFA one.pdf": "REQUESTS FOR ADMISSION, SET ONE\nPROPOUNDING PARTY: GAMMA EXAMPLE\nRESPONDING PARTY: DELTA EXAMPLE\nPROOF OF SERVICE"
    }
    doc, _ = render.attach(_rfas(30), "discovery_set", Firm({"decl_2033_050": DECL_2033}), digest, corpus)
    assert "a total of 10 requests for admission" in doc and "contains a total of 30 requests" in doc
    plain, _ = render.attach(_rfas(30), "discovery_set", Firm({"decl_2033_050": DECL_2033}), digest, {})
    assert "2033.050" not in plain  # a prior set the file does not bear out is never counted


def test_with_no_2033_form_and_no_counts_an_admission_set_is_as_before():
    doc, notes = render.attach(_rfas(40), "discovery_set", Firm(), "")
    assert "2033" not in doc and not any("2033" in n for n in notes)
    on, notes = render.attach(_rfas(40), "discovery_set", Firm(data={"discovery_counts": COUNTS}), "")
    assert "2033" not in on
    assert any("no section 2033.050 declaration form is on file" in n for n in notes)


def test_the_set_sizes_lines_say_the_count_the_range_and_the_declaration():
    _doc, notes = render.attach(
        _set("special_interrogatories", list(range(1, 42))),
        "discovery_set",
        Firm(),
        "",
        facts=_facts("special_interrogatories", 41),
    )
    assert (
        "Set sizes: 41 special interrogatories; your range is 50 to 75; the file supported no more; "
        "declaration attached (41 to this party, over 35)"
    ) in notes
    _doc, notes = render.attach(_rfas(60), "discovery_set", Firm(), "", facts=_facts("requests_for_admission", 60))
    assert any(
        n.startswith(
            "Set sizes: 60 requests for admission; your limit is 50; above that number, kept as drafted with none removed"
        )
        for n in notes
    )
    asked = _facts("requests_for_admission", 12, asked=counts.Asked(12, "a dozen RFAs, 12 max"))
    _doc, notes = render.attach(_rfas(12), "discovery_set", Firm(), "", facts=asked)
    assert (
        'Set sizes: 12 requests for admission; your request asked for 12 ("a dozen RFAs, 12 max"); no declaration (12 to this party, not over 35)'
        in notes
    )


def _check(tmp_path: Path, md: str, attachments: Any = None, digest: str = "") -> format_check.Result:
    path, _ = render.render(md, "discovery_set", tmp_path / "set.docx", FMT)
    return format_check.check(path, "discovery_set", FMT, digest, None, attachments=attachments)


def test_the_format_check_allows_and_requires_the_2033_declaration_by_the_same_rule(tmp_path):
    firm = Firm({"decl_2033_050": DECL_2033})
    attached, _ = render.attach(_rfas(40), "discovery_set", firm, "")
    assert not [f for f in _check(tmp_path, attached).fails if "declaration" in f]
    bare, _ = render.attach(_rfas(40), "discovery_set", Firm(), "")
    assert not [f for f in _check(tmp_path, bare).fails if "declaration" in f]  # the firm's form is not known: dark
    req = _check(tmp_path, bare, attachments=["decl_2030_050", "pos", "decl_2033_050"]).fails
    assert any("section 2033.050 declaration required (40 requests for admission" in f for f in req)
    small = _rfas(20).replace("Dated: x\n", "Dated: x\n\n" + DECL_2033.split("-->")[1] + "\n" + POS.split("-->")[1])
    assert any("section 2033.050 declaration not allowed" in f for f in _check(tmp_path, small).fails)
    wrong = _rfas(40).replace("Dated: x\n", "Dated: x\n\n" + DECL.split("-->")[1] + "\n" + POS.split("-->")[1])
    assert any("section 2030.050 declaration not allowed" in f for f in _check(tmp_path, wrong).fails)


def test_a_model_written_2033_declaration_is_caught_and_stripped():
    title = "DECLARATION PURSUANT TO CODE OF CIVIL PROCEDURE SECTION 2033.050"
    small = _rfas(20) + f"\n**{title}**\n\nI declare.\n"
    found = render.decl_problems("Document 1", small, "", {})
    assert found and found[0][0] == "1 section 2033.050 declaration"
    doc, _ = render.attach(small, "discovery_set", Firm(), "")
    assert "I declare." not in doc


def test_the_review_reads_the_declaration_by_the_same_rule():
    firm = Firm({"decl_2033_050": DECL_2033})
    attached, _ = render.attach(_rfas(40), "discovery_set", firm, "")
    assert render.decl_problems("Document 1", attached, "", {}) == []
    off = attached.replace("contains a total of 40 requests", "contains a total of 41 requests")
    assert render.decl_problems("Document 1", off, "", {}) == [
        ("1 section 2033.050 declaration", "Document 1: the declaration counts 41; the set has 40")
    ]
    small = _rfas(20) + "\n" + DECL_2033.split("-->")[1]
    assert render.decl_problems("Document 1", small, "", {})[0][1].startswith(
        "Document 1 carries a section 2033.050 declaration, but the proven cumulative count is 20"
    )
    srogs = _set("special_interrogatories", list(range(1, 41))) + "\n" + DECL_2033.split("-->")[1]
    assert "on a set of special interrogatories" in render.decl_problems("Document 1", srogs, "", {})[0][1]
