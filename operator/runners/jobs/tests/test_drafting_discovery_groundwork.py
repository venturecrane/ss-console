"""The discovery groundwork is dark: the new optional sections of
``drafting-firm.yaml`` (``phrase_book``, ``discovery_counts``, the
``decl_2033_050`` attachment) are validated in the closed schema and change
nothing when absent, and the rules-stage hooks return what they were given.
Every value here is invented.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from drafting_testkit import make_inputs

from jobs.drafting import counts, firm as firm_mod, meetconfer
from jobs.drafting.rulebook import book, hook, parts, stage
from jobs.drafting.rulebook.types import StageDoc, StageJob

BOOK = [
    {
        "key": "privacy",
        "kind": "objection",
        "text": "Responding party objects to Request No. {item} as it seeks private information.",
        "names": ["privacy"],
        "sets": ["requests_for_production"],
        "source": "invented",
    }
]


def _inputs(tmp_path: Path, files: dict[str, str] | None = None, **sections) -> Path:
    root = make_inputs(tmp_path / "inputs")
    cfg = root / "drafting-firm.yaml"
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    for rel, text in (files or {}).items():
        (root / rel).write_text(text, encoding="utf-8")
        data["inputs"][rel] = hashlib.sha256((root / rel).read_bytes()).hexdigest()
    for k, v in sections.items():
        if k == "attachments":
            data["attachments"].update(v)
        else:
            data[k] = v
    cfg.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return root


def _errors(root: Path) -> list[str]:
    data = yaml.safe_load((root / "drafting-firm.yaml").read_text(encoding="utf-8"))
    return firm_mod.validate(data)


def test_a_firm_without_the_new_sections_validates_and_reads_no_book(tmp_path):
    f = firm_mod.load(str(_inputs(tmp_path)))
    assert book.stages(f.data) == () and book.load(f) is None and counts.targets(f.data) == {}


def test_an_authored_phrase_book_is_validated_pinned_and_loaded(tmp_path):
    section = {"path": "phrase-book.json", "stages": ["revise"]}
    root = _inputs(tmp_path, {"phrase-book.json": json.dumps(BOOK)}, phrase_book=section)
    f = firm_mod.load(str(root))
    assert book.stages(f.data) == ("revise",)
    b = book.load(f)
    assert b is not None and b.text("privacy", item="4").startswith("Responding party objects to Request No. 4")
    assert b.find("responding party objects to request no. {item} as it  seeks private information.") == "privacy"


@pytest.mark.parametrize(
    ("section", "fragment"),
    [
        ({"path": "phrase-book.json"}, "expected exactly"),
        ({"path": "phrase-book.json", "stages": ["review"]}, "is not one of"),
        ({"path": "phrase-book.json", "stages": ["revise", "revise"]}, "named twice"),
        ({"path": "phrase-book.json", "stages": []}, "one or more"),
        ({"path": "phrase-book.json", "stages": ["revise"], "extra": 1}, "expected exactly"),
    ],
)
def test_a_malformed_phrase_book_section_is_refused(tmp_path, section, fragment):
    root = _inputs(tmp_path, {"phrase-book.json": "[]"}, phrase_book=section)
    assert any(fragment in e for e in _errors(root)), _errors(root)


def test_a_phrase_book_file_not_pinned_is_refused(tmp_path):
    root = _inputs(tmp_path, phrase_book={"path": "unpinned.json", "stages": ["first_draft"]})
    assert "phrase_book.path: unpinned.json is not pinned in inputs" in _errors(root)


def test_a_malformed_book_file_does_not_parse():
    with pytest.raises(book.BookError):
        book.parse(json.dumps([{**BOOK[0], "kind": "flourish"}]))
    with pytest.raises(book.BookError):
        book.parse(json.dumps(BOOK + BOOK))


def test_discovery_counts_is_a_closed_shape(tmp_path):
    good = {"special_interrogatories": {"min": 50, "max": 75}, "requests_for_admission": {"max": 50}}
    f = firm_mod.load(str(_inputs(tmp_path, discovery_counts=good)))
    assert counts.targets(f.data) == {"special_interrogatories": (50, 75), "requests_for_admission": (None, 50)}
    assert counts.validate({"special_interrogatories": {"min": 80, "max": 75}}) == [
        "discovery_counts.special_interrogatories: min is above max"
    ]
    assert counts.validate({"depositions": {"max": 5}})[0].startswith("discovery_counts.depositions: unknown set")
    assert "expected {min?, max?}" in counts.validate({"requests_for_admission": {"most": 5}})[0]
    assert "whole number" in counts.validate({"requests_for_admission": {"max": 0}})[0]
    assert counts.validate({}) and counts.validate([1])


def test_the_2033_declaration_attachment_is_accepted_when_pinned_and_refused_when_not(tmp_path):
    ok = _inputs(
        tmp_path / "a",
        {"decl-2033-050.md": "DECLARATION (invented)"},
        attachments={"decl_2033_050": "decl-2033-050.md"},
    )
    assert _errors(ok) == []
    bad = _inputs(tmp_path / "b", attachments={"decl_2033_050": "decl-2033-050.md"})
    assert "attachments.decl_2033_050: decl-2033-050.md is not pinned in inputs" in _errors(bad)


def test_the_stage_returns_the_document_unchanged_and_sends_nothing(tmp_path):
    job = StageJob(data=tmp_path, firm=None, ask=None, log=print, request_text="invented", mode="revise")
    doc = StageDoc(key="", md="RESPONSE TO REQUEST NO. 1:\n\nInvented.", doc_type="rfp_response")
    res = stage.run(job, doc)
    assert res.md == doc.md and not (res.applied or res.left or res.findings or res.notes)
    assert stage.plan_chars(job, [doc]) == 0 and not stage.fresh(tmp_path, "discovery_response", {}).moved
    assert parts.read(doc.md, "rfp_response").items == {}


def _run(firm_data: dict) -> SimpleNamespace:
    return SimpleNamespace(
        firm=SimpleNamespace(data=firm_data),
        log=print,
        cls="discovery_response",
        data=Path("/nonexistent"),
        job=SimpleNamespace(request_text="invented"),
    )


@pytest.mark.parametrize("stages", [None, ["revise"], ["first_draft"], ["first_draft", "revise"]])
def test_the_hooks_return_what_they_were_given_on_or_off(stages):
    data = {} if stages is None else {"phrase_book": {"path": "p.json", "stages": stages}}
    unit = SimpleNamespace(key="set-1", variant="requests_for_production", data=Path("/nonexistent"))
    md = "REQUEST FOR PRODUCTION NO. 1:\n\nInvented."
    assert hook.at_gate(_run(data), unit, md) == md
    assert hook.at_revise(_run(data), 0, md, "requests_for_production") == md
    assert hook.enabled(_run(data).firm, "revise") is bool(stages and "revise" in stages)


def test_the_counts_interfaces_answer_with_nothing_recorded():
    assert counts.facts(object()) is None
    assert counts.declaration("requests_for_admission", 40) == "decl_2033_050"


def test_a_challenge_carries_the_letters_own_words():
    c = meetconfer.Challenge("special_interrogatories", (4, 7), "Nos. 4 and 7 are evasive", "letter.pdf", 2)
    assert c.numbers == (4, 7) and not c.general
