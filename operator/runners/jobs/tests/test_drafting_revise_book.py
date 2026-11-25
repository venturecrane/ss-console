"""A set revision with the firm-rules stage switched on (``phrase_book`` lists
``revise``; revise/book_run.py, A2): the stage's changes made paragraph by
paragraph in each filed file, candidate markers settled, the attorney's own
instructions and facts, the Index of Exhibits kept true, the notes written by
code, and every hold.

Driven through the real ``ReviseSetRun`` over the versioned seat of
``test_drafting_revise_set``; the firm-rules stage itself is another builder's
(``rulebook.stage``) and is stood in for by a function that applies a fixed
plan, so these tests pin what this package does with its answer. Every name,
number and sentence is invented.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import docx
import pytest
import yaml
from test_drafting_revise_set import NAMES, RULES, SetSeat, response_doc, text_of, world  # noqa: F401 - world is the pytest fixture these tests use

from jobs.drafting.revise import book_run
from jobs.drafting.rulebook import reading, stage as rule_stage
from jobs.drafting.rulebook.types import Applied, Clause, StageResult

PRIVACY = "Responding party objects to this request on the ground that it seeks private information."
BOOK = [
    {"key": "privacy", "kind": "objection", "text": PRIVACY.replace("request", "{item}"), "names": ["privacy"],
     "sets": [], "source": "invented"},
    {"key": "no_docs", "kind": "disposition", "text": "No responsive documents exist.", "names": ["no documents"],
     "sets": [], "source": "invented"},
]  # fmt: skip
NO_DOCS_RULE = {**RULES[2], "rule_id": "wr-plain", "wording": ["Say no documents plainly."], "at": "2026-11-20T10:00Z"}
PRIVACY_RULE = {**RULES[2], "rule_id": "wr-privacy", "wording": ["Object on privacy where it applies."]}


def _book_inputs(root: Path, stages: list[str]) -> Path:
    from drafting_testkit import make_inputs

    make_inputs(root)
    cfg = root / "drafting-firm.yaml"
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    (root / "phrase-book.json").write_text(json.dumps(BOOK), encoding="utf-8")
    data["inputs"]["phrase-book.json"] = hashlib.sha256((root / "phrase-book.json").read_bytes()).hexdigest()
    data["phrase_book"] = {"path": "phrase-book.json", "stages": stages}
    cfg.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return root


class Client:
    """The doorway's client: the email read answers ``items``; nothing else may call."""

    def __init__(self, items: list[dict] | Callable[[str], dict]) -> None:
        self.items, self.calls = items, []
        self.messages = SimpleNamespace(create=self._create, stream=None)

    def _create(self, **params: Any) -> Any:
        user = params["messages"][-1]["content"]
        user = user if isinstance(user, str) else user[0]["text"]
        assert "THE ATTORNEY'S EMAIL" in user, "only the instruction read calls the model here"
        self.calls.append(user)
        got = self.items(user) if callable(self.items) else {"items": self.items}
        usage = SimpleNamespace(input_tokens=500, output_tokens=100, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)  # fmt: skip
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(got))], stop_reason="end_turn",
                               usage=usage)  # fmt: skip


Plan = Callable[[Any], StageResult]


@pytest.fixture
def stage(monkeypatch):
    """The stand-in firm-rules stage: ``plans[doc_type](doc)`` or unchanged."""
    plans: dict[str, Plan] = {}
    seen: list[Any] = []
    #: rule id -> the clauses the stage's rule read would cache for it.
    clauses: dict[str, list[Clause]] = {}

    def run(job, doc):
        assert job.ask is not None and job.mode == "revise"
        seen.append(doc)
        cache = {
            reading.sha(r): {"rule": r["rule_id"], "clauses": [c.to_dict() for c in clauses.get(r["rule_id"], [])],
                             "entries": []}
            for r in rule_stage.frozen(job.data)
        }  # fmt: skip
        (Path(job.data) / reading.FILE).write_text(json.dumps(cache), encoding="utf-8")
        return plans[doc.doc_type](doc) if doc.doc_type in plans else StageResult(md=doc.md)

    monkeypatch.setattr(book_run.rule_stage, "run", run)
    return SimpleNamespace(plans=plans, seen=seen, clauses=clauses)


def _objection(rule: str, *, standing: bool = True, grounds: tuple[str, ...] = ("privacy",)) -> Clause:
    return Clause(rule, 1, "Object on privacy", standing, ("rfp_response",), "add_objection", (), grounds)


def _swap(old: str, new: str, number: str, source: str = "rule:wr-plain") -> Plan:
    return lambda doc: StageResult(md=doc.md.replace(old, new, 1), applied=(Applied(source, number),))


def _run(world: Path, seat: SetSeat, items: Any = (), request: str = "Please apply all new rules to the responses.",
         stages: tuple[str, ...] = ("revise",), rules: list[dict] | None = None, notes: dict | None = None):  # fmt: skip
    from drafting_testkit import envelope
    from jobs_testkit import PRICING
    from test_drafting_revise_set import _units
    from test_firm_wording import _store

    from jobs.drafting.revise.set_run import ReviseSetRun

    _store(world / "spec", rules if rules is not None else [NO_DOCS_RULE])
    jd = world / "jobs" / "01JBBBBBBBBBBBBBBBBBBBBBBB"
    jd.mkdir(parents=True, exist_ok=True)
    units = [u for u in _units(seat) if u["file_id"] != (notes or {}).get("file_id")]  # notes are no draft
    block = {"prior_job_id": units[0]["prior_job_id"], "folder_id": "folder-0001", "units": units}
    if notes is not None:
        block["notes"] = notes
    env = {**envelope(job_id=jd.name, cls="discovery_response"), "request_text": request, "slug": "example",
           "month_cents_used": 0, "allowance_remaining": 0, "revise": block}  # fmt: skip
    (jd / "job.json").write_text(json.dumps(env))
    (world / "pricing.json").write_text(json.dumps(PRICING))
    client = Client(list(items) if not callable(items) else items)
    run = ReviseSetRun(jd, inputs_dir=str(_book_inputs(world / "inputs", list(stages))),
                       pricing=str(world / "pricing.json"), seat_factory=lambda: seat, client=client,
                       log=lambda m: None)  # fmt: skip
    return run.run(), client, jd


def _two() -> SetSeat:
    return SetSeat({f"file-{k}": (NAMES[k], response_doc(k)) for k in ("special", "rfp")})


def _notes(jd: Path) -> str:
    return (jd / "data" / "revise-set-notes.md").read_text()


# ---- the switch -------------------------------------------------------------------------------
def test_switched_on_a_rule_with_no_quoted_words_is_applied_not_held(world, stage) -> None:
    """FALSIFIER: keep today's hold and a firm whose rules quote no words gets nothing."""
    stage.plans["requests_for_production"] = _swap("answers item 2. No documents exist.",
                                                   "answers item 2. No responsive documents exist.", "2")  # fmt: skip
    v, client, jd = _run(world, _two())
    assert v.outcome == "delivered", (v.stage, v.reason)
    assert client.calls == []  # the email names no response: no instruction read
    rfp = text_of(_sent(world, "file-rfp"))
    assert "Responding party answers item 2. No responsive documents exist." in rfp
    assert "Responding party answers item 1. No documents exist." in rfp  # untouched
    notes = _notes(jd)
    assert "Response 2: changed, per the firm's rule wr-plain of 2026-11-20." in notes


_SEATS: dict[str, SetSeat] = {}


def _sent(world: Path, fid: str) -> bytes:
    return _SEATS["seat"].files[fid][-1]


@pytest.fixture(autouse=True)
def _track(monkeypatch):
    real = SetSeat.__init__

    def init(self, files):
        real(self, files)
        _SEATS["seat"] = self

    monkeypatch.setattr(SetSeat, "__init__", init)


def test_switched_off_the_revision_is_todays(world, stage) -> None:
    """``first_draft`` only: the stage is never called and today's hold stands."""
    v, _client, _jd = _run(world, _two(), stages=("first_draft",))
    assert v.outcome == "held" and v.reason.startswith("revise_unchanged:") and stage.seen == []


def test_a_response_the_stage_changed_with_nothing_applied_files_nothing(world, stage) -> None:
    stage.plans["requests_for_production"] = lambda doc: StageResult(md=doc.md.replace("item 1.", "item one."))
    v, _c, _jd = _run(world, _two())
    assert v.outcome == "failed" and v.reason.startswith("revise_layout_lost:"), v.reason
    assert _SEATS["seat"].sent == []


def test_an_objection_paragraph_is_added_in_the_filed_file(world, stage) -> None:
    """Block level. FALSIFIER: the phrase-in-a-sentence edit cannot add a paragraph."""

    def add(doc):
        md = doc.md.replace("Responding party answers item 3.", f"{PRIVACY}\n\nResponding party answers item 3.", 1)
        return StageResult(md=md, applied=(Applied("rule:wr-privacy", "3"),))

    stage.plans["requests_for_production"] = add
    v, _c, _jd = _run(world, _two(), rules=[NO_DOCS_RULE, PRIVACY_RULE])
    assert v.outcome == "delivered", v.reason
    rfp = text_of(_sent(world, "file-rfp"))
    i = rfp.index(PRIVACY)
    assert rfp[i - 1] == "RESPONSE TO REQUEST FOR PRODUCTION NO. 3:" and rfp[i + 1].startswith(
        "Responding party answers item 3"
    )


# ---- markers ----------------------------------------------------------------------------------
def test_candidate_markers_named_by_a_rule_are_taken_and_others_left(world, stage) -> None:
    marked = "{{ATTORNEY: CANDIDATE OBJECTION: privacy | it asks for tax returns}} Responding party answers item 1."
    seat = SetSeat({"file-rfp": (NAMES["rfp"], response_doc("rfp", {
        "1": marked, "2": "{{ATTORNEY: CANDIDATE OBJECTION: compound | two asks}} Answer two.",
        "3": "{{ATTORNEY: confirm the visit date}} Answer three."}))})  # fmt: skip
    stage.clauses["wr-privacy"] = [_objection("wr-privacy")]
    v, _c, jd = _run(world, seat, rules=[NO_DOCS_RULE, PRIVACY_RULE])
    assert v.outcome == "delivered", v.reason
    rfp = text_of(_sent(world, "file-rfp"))
    assert f"{PRIVACY} Responding party answers item 1." in rfp
    assert any(p.startswith("{{ATTORNEY: CANDIDATE OBJECTION: compound") for p in rfp)  # no book entry: left
    assert "{{ATTORNEY: confirm the visit date}} Answer three." in rfp  # never touched
    notes = _notes(jd)
    assert "Response 1: changed, per the objection proposed in the draft" in notes
    assert "Response 2: left as drafted and marked for you" in notes


def test_a_marker_no_rule_names_is_left(world, stage) -> None:
    marked = "{{ATTORNEY: CANDIDATE OBJECTION: privacy | why}} Responding party answers item 1."
    seat = SetSeat({"file-rfp": (NAMES["rfp"], response_doc("rfp", {"1": marked}))})
    # The privacy rule speaks of one other matter's response only: never a standing ground here.
    stage.clauses["wr-privacy"] = [_objection("wr-privacy", standing=False)]
    v, _c, jd = _run(world, seat, rules=[NO_DOCS_RULE, PRIVACY_RULE])
    assert v.outcome == "held" and v.reason.startswith("revise_unchanged:"), v.reason
    assert "nothing in your request asks for it" in _notes(jd)


# ---- the attorney's instructions --------------------------------------------------------------
def test_an_email_instruction_reaches_only_its_own_document(world, stage) -> None:
    email = "Please apply the rules. For RFP No. 3 add a privacy objection."
    item = {"quote": "For RFP No. 3 add a privacy objection", "numbers": ["3"], "set_type": "",
            "action": "add_objection", "keys": ["privacy"]}  # fmt: skip
    stage.plans["requests_for_production"] = lambda doc: StageResult(
        md=doc.md.replace("Responding party answers item 3.", f"{PRIVACY} Responding party answers item 3."),
        applied=(Applied("rule:email", "3"),),
    )
    v, client, jd = _run(world, _two(), [item], request=email)
    assert v.outcome == "delivered", v.reason
    assert len(client.calls) == 1
    (doc,) = stage.seen  # the email names the production set: the special responses are not in scope
    assert doc.doc_type == "requests_for_production" and doc.instructions[0].numbers == ("3",)
    assert "Response 3: changed, per your request." in _notes(jd)


def test_an_instruction_code_cannot_verify_files_nothing(world, stage) -> None:
    """Refused twice (the quote is not his words): nothing filed, the stage never runs."""
    email = "For RFP No. 3 add a privacy objection."
    item = {"quote": "For RFP No. 3 add a relevance objection", "numbers": ["3"], "action": "add_objection",
            "keys": ["privacy"]}  # fmt: skip
    v, client, _jd = _run(world, _two(), [item], request=email)
    assert v.outcome == "failed" and v.reason.startswith("revise_unchecked:"), v.reason
    assert len(client.calls) == 2 and stage.seen == [] and _SEATS["seat"].sent == []


def test_a_number_his_email_names_that_no_instruction_covers_files_nothing(world, stage) -> None:
    v, client, _jd = _run(world, _two(), [], request="Fix RFP No. 2 please.")
    assert v.outcome == "failed" and "no instruction kept" in v.reason and len(client.calls) == 2


def test_a_fact_he_states_is_written_per_attorney(world, stage) -> None:
    email = "On special rog No. 2, the client had no UM coverage."
    item = {"quote": "On special rog No. 2, the client had no UM coverage", "numbers": ["2"], "action": "add_fact",
            "keys": [], "text": "Responding party had no UM coverage."}  # fmt: skip
    v, _c, jd = _run(world, _two(), [item], request=email)
    assert v.outcome == "delivered", v.reason
    special = text_of(_sent(world, "file-special"))
    assert "Responding party answers item 2. No documents exist. Responding party had no UM coverage." in special
    assert "(per attorney, from your request)" in _notes(jd)
    assert _SEATS["seat"].sent == ["file-special"]


# ---- the index --------------------------------------------------------------------------------
def _prod(answers: dict[str, str], index: list[str] | None) -> bytes:
    d = docx.Document()
    d.add_paragraph("PLAINTIFF'S RESPONSES TO REQUESTS FOR PRODUCTION")
    for n in ("1", "2", "3"):
        d.add_paragraph(f"REQUEST FOR PRODUCTION NO. {n}:")
        d.add_paragraph(f"Produce item {n}.")
        d.add_paragraph(f"RESPONSE TO REQUEST FOR PRODUCTION NO. {n}:")
        d.add_paragraph(answers.get(n, f"Responding party answers item {n}."))
    for line in index or []:
        d.add_paragraph(line)
    d.add_paragraph("DATED: December 1, 2026")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


INDEX = ["INDEX OF EXHIBITS", "EXHIBIT A: SAMPLE RECORDS", "1. Records of Example Clinic (Request Nos. 1, 2)",
         "EXHIBIT B: SAMPLE BILLS", "1. Bills of Example Clinic (Request No. 2)"]  # fmt: skip
ANS = {"1": "Documents can be found at Exhibit A.", "2": "Documents can be found at Exhibits A and B."}


def test_the_index_in_the_document_follows_the_revision(world, stage) -> None:
    seat = SetSeat({"file-rfp": (NAMES["rfp"], _prod(ANS, INDEX))})
    stage.plans["requests_for_production"] = _swap("found at Exhibit A.", "found at Exhibit B.", "1")
    v, _c, jd = _run(world, seat)
    assert v.outcome == "delivered", v.reason
    rfp = text_of(_sent(world, "file-rfp"))
    assert (
        "1. Records of Example Clinic (Request No. 2)" in rfp
        and "1. Bills of Example Clinic (Request Nos. 1, 2)" in rfp
    )
    assert "Index of Exhibits: Exhibit B: Request No(s). 1, 2." in _notes(jd)


def _index_file() -> bytes:
    d = docx.Document()
    d.add_paragraph("PLAINTIFF'S INDEX OF EXHIBITS")
    for line in INDEX[1:]:
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_an_index_file_is_revised_with_its_production_document(world, stage) -> None:
    seat = SetSeat({"file-rfp": (NAMES["rfp"], _prod(ANS, None)),
                    "file-idx": ("Index of Exhibits, Set One.docx", _index_file())})  # fmt: skip
    stage.plans["requests_for_production"] = _swap("found at Exhibit A.", "found at Exhibit B.", "1")
    v, _c, jd = _run(world, seat)
    assert v.outcome == "delivered", v.reason
    assert _SEATS["seat"].sent == ["file-rfp", "file-idx"]
    idx = text_of(_sent(world, "file-idx"))
    assert (
        "1. Records of Example Clinic (Request No. 2)" in idx
        and "1. Bills of Example Clinic (Request Nos. 1, 2)" in idx
    )


def test_exhibit_letters_resolve_from_the_paired_index_file(world, stage) -> None:
    """The production document carries no index of its own. FALSIFIER: hand the
    stage the document alone and every production phrase is exhibit_unresolved."""
    from jobs.drafting.revise import responses as rs
    from jobs.drafting.rulebook import makers

    seat = SetSeat({"file-rfp": (NAMES["rfp"], _prod({"3": "Documents will be produced."}, None)),
                    "file-idx": ("Index of Exhibits, Set One.docx", _index_file())})  # fmt: skip

    def produce(doc):
        sentence, why = makers.words(book_run.book_mod.parse(json.dumps(BOOK_X)), "found", "Request",
                                     rs.read(doc.md, doc.hint), ("SAMPLE BILLS",))  # fmt: skip
        assert why == "", why
        md = doc.md.replace("Documents will be produced.", sentence, 1)
        return StageResult(md=md, applied=(Applied("rule:wr-plain", "3", "found"),))

    stage.plans["requests_for_production"] = produce
    v, _c, _jd = _run(world, seat)
    assert v.outcome == "delivered", v.reason
    rfp = text_of(_sent(world, "file-rfp"))
    assert "Responsive documents can be found at Exhibit B." in rfp and "INDEX OF EXHIBITS" not in rfp
    assert "1. Bills of Example Clinic (Request Nos. 2, 3)" in text_of(_sent(world, "file-idx"))


BOOK_X = [{"key": "found", "kind": "disposition", "text": "Responsive documents can be found at Exhibit {exhibit}.",
           "names": ["found"], "sets": [], "source": "invented"}]  # fmt: skip


@pytest.mark.parametrize("prods", [0, 2])
def test_an_index_file_without_one_production_document_files_nothing(world, stage, prods) -> None:
    files = {"file-idx": ("Index of Exhibits, Set One.docx", _index_file()),
             "file-special": (NAMES["special"], response_doc("special"))}  # fmt: skip
    for i in range(prods):
        files[f"file-rfp{i}"] = (f"Responses to Requests for Production, Set {i + 1}.docx", _prod(ANS, None))
    v, _c, _jd = _run(world, SetSeat(files))
    assert v.outcome == "failed" and v.reason.startswith("index_unpaired:"), v.reason
    assert stage.seen == []


# ---- staff saves, notes filed -----------------------------------------------------------------
def test_a_staff_save_during_the_revision_holds_never_replays_the_plan(world, stage, monkeypatch) -> None:
    from jobs.drafting.revise import set_run

    stage.plans["requests_for_production"] = _swap("answers item 2. No documents exist.", "answers item 2. None.", "2")
    real = set_run.ReviseSetRun._manifest_set

    def manifest_then_save(self):
        real(self)
        _SEATS["seat"].staff_save("file-rfp", response_doc("rfp", {"1": "Staff wrote this."}))

    monkeypatch.setattr(set_run.ReviseSetRun, "_manifest_set", manifest_then_save)
    v, _c, _jd = _run(world, _two())
    assert v.outcome == "held" and v.reason.startswith("filing_refused:"), v.reason
    assert _SEATS["seat"].sent == []


def _with_notes(stage) -> tuple[SetSeat, dict]:
    d = docx.Document()
    d.add_paragraph("Notes on the responses as first drafted.")
    buf = io.BytesIO()
    d.save(buf)
    seat = _two()
    seat.names["file-notes"] = "Attorney Notes.docx"
    seat.files["file-notes"] = [buf.getvalue()]
    stage.plans["requests_for_production"] = _swap("answers item 2. No documents exist.", "answers item 2. None.", "2")
    notes = {"file_id": "file-notes", "name": "Attorney Notes.docx", "size": len(buf.getvalue()),
             "sha256": hashlib.sha256(buf.getvalue()).hexdigest(), "folder_id": "folder-0001"}  # fmt: skip
    return seat, notes


def test_notes_saved_by_staff_during_the_revision_hold_before_any_upload(world, stage, monkeypatch) -> None:
    """FALSIFIER: check the notes only when they are sent and the drafts are
    already filed when the notes are refused: half a filing."""
    from jobs.drafting.revise import set_run

    seat, notes = _with_notes(stage)
    real = set_run.ReviseSetRun._manifest_set

    def manifest_then_save(self):
        real(self)
        seat.staff_save("file-notes", seat.files["file-notes"][0] + b"")

    monkeypatch.setattr(set_run.ReviseSetRun, "_manifest_set", manifest_then_save)
    v, _c, _jd = _run(world, seat, notes=notes)
    assert v.outcome == "held" and v.reason.startswith("filing_refused:"), v.reason
    assert seat.sent == []


def test_the_attorney_notes_are_filed_as_a_new_version_after_the_documents(world, stage) -> None:
    seat, notes = _with_notes(stage)
    v, _c, _jd = _run(world, seat, notes=notes)
    assert v.outcome == "delivered", v.reason
    assert seat.sent == ["file-rfp", "file-notes"]
    filed = " ".join(text_of(seat.files["file-notes"][-1]))
    assert "Response 2: changed" in filed and "Notes on the responses as first drafted." in filed
