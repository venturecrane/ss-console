"""The full chronology's `audit` stage (``stages/entry_audit.py``): each entry
read line by line against its cited pages, each proposed correction verified
by a second reader on the page pictures, applied once as a whole-line swap
or a new paragraph; nothing removed; an entry not read holds; a resume and a
replay pay nothing for what is answered. Through the real StageRun and
Doorway, with a scripted SDK client. Every name, date and value is invented."""

from __future__ import annotations

import json
import re
from dataclasses import replace as dc_replace
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any, Callable

import pytest

from jobs.medchron import core_counts, dag, dag_update
from jobs.medchron.audit import claims as CL
from jobs.medchron.gates import claim_audit
from jobs.medchron.update import entry_confirm as EC, entry_read as ER
from jobs.stages import entry_audit as EA, entry_audit_apply as AP, entry_audit_io as IO
from test_audit import Usage, _exhibit_set, _sr

PAGE = (
    "Follow up visit for neck pain after the collision. Range of motion is reduced and home exercises "
    "were reviewed with the patient in detail today. The plan is to continue therapy and reassess. "
)
PAGES = [PAGE, PAGE + "Pulse 78 at triage.", PAGE + "Therapy twice weekly.", PAGE, PAGE + "Gait steady.", PAGE]
E1 = (
    "01/05/2025\nExample Clinic | Medical Diagnoses\n\nMedical Diagnoses\n"
    "The pulse was 72 at triage during the visit. (Exhibit 1 - p. 2)\n\n"
    "All Other Information\nTherapy twice weekly was planned at the visit. (Exhibit 1 - p. 3)"
)
FILLER = " ".join(f"Home exercise number {w} was reviewed with the patient." for w in "abcdefghijklmn")
E2 = f"02/10/2025\nSample Therapy | All Other Information\n\nAll Other Information\n{FILLER} (Exhibit 1 - p. 5)"
DOC = (
    "Alpha Example - Medical Chronology\n\n## Treatment Timeline\n\n## Diagnostic Highlights\n\n"
    f"## Medical Chronology\n\n{E1}\n\n{E2}\n\n## Exhibit List\n"
)
VALUE = {"line": "S1", "class": "value", "exhibit": 1, "page": 2, "quote": "pulse was 72", "action": "replace",
         "replacement": "pulse was 78", "problem": "the page records 78"}  # fmt: skip
DATE = {"line": "D", "class": "date", "exhibit": 1, "page": 2, "quote": "01/05/2025", "action": "redate"}
ADD = "Gait was steady during the follow up visit. (Exhibit 1 - p. 3)"
OMITTED = {"line": "S2", "class": "omitted", "exhibit": 1, "page": 3, "quote": "Therapy twice weekly",
           "action": "add", "replacement": ADD}  # fmt: skip
YES = {"original_wrong": True, "corrected_supported": True, "page": {"exhibit": 1, "page": 2}, "reason": "78"}
ALL_OK = {"D": "ok", "P": "ok", **{f"S{i}": "ok" for i in range(1, 60)}}


def _reply(*items: dict[str, Any]) -> str:
    return json.dumps({"lines": ALL_OK, "findings": list(items), "complete": True})


def _read_reply(text: str) -> str:
    """N1 finds a value and a date; any other unit finds nothing."""
    return _reply(VALUE, DATE) if "ENTRY N1" in text else _reply()


class _Stream:
    def __init__(self, text: str) -> None:
        self.m = NS(content=[NS(type="text", text=text)], stop_reason="end_turn", usage=Usage())

    def __enter__(self) -> _Stream:
        return self

    def __exit__(self, *a: object) -> bool:
        return False

    def get_final_message(self) -> Any:
        return self.m


class Client:
    """The SDK client, scripted: a read answers `read(text)`, a verify `verify(text)`."""

    def __init__(
        self,
        read: Callable[[str], str] = _read_reply,
        verify: dict | Callable[[str], dict] | None = None,
        fail: Callable[[str, str], Exception | None] = lambda _k, _t: None,
    ) -> None:
        self.read, self.fail = read, fail
        self.verify = verify if callable(verify) else (lambda _t, v=verify or YES: v)
        self.calls: list[str] = []
        self.texts: list[str] = []
        self.messages = self

    def stream(self, **kw: Any) -> _Stream:
        text = "\n".join(str(b.get("text", "")) for b in kw["messages"][0]["content"])
        kind = "read" if "PAGES SHOWN:" in text else "verify"
        self.calls.append(kind)
        self.texts.append(text)
        exc = self.fail(kind, text)
        if exc is not None:
            raise exc
        return _Stream(self.read(text) if kind == "read" else json.dumps(self.verify(text)))


@pytest.fixture
def scene(job_dir: Path, firm_config_path: Path, data_root: Path):
    def make(client: Client | None = None, doc: str = DOC, fresh: bool = True):
        """A StageRun on the scene; `fresh` False keeps what an earlier run left."""
        log: list[str] = []
        sr = _sr(job_dir, firm_config_path, data_root, client, log)
        rd = sr.slug_dir / "runs" / "alpha"
        if fresh:
            _exhibit_set(sr, PAGES)
            rd.mkdir(parents=True, exist_ok=True)
            (rd / "final-chronology.md").write_text(doc, encoding="utf-8")
        sr.doorway.sleep = lambda _s: None  # a retried call never waits in a test
        return sr, rd / "final-chronology.md", log

    return make


def _record(sr: Any) -> dict[str, Any]:
    return json.loads((sr.slug_dir / "runs" / "alpha" / IO.RECORD).read_text(encoding="utf-8"))


def _sentences(md: str) -> int:
    return ER.count_sentences(CL.body_of(md))


# ---- the thin path ------------------------------------------------------------------
def test_a_wrong_value_is_corrected_on_a_yes_yes_verify_with_one_write(scene, monkeypatch) -> None:
    client = Client()
    sr, doc, log = scene(client)
    writes: list[str] = []
    real = Path.write_text

    def counted(self: Path, data: str, *a: Any, **kw: Any) -> int:
        if self == doc:
            writes.append(data)
        return real(self, data, *a, **kw)

    monkeypatch.setattr(Path, "write_text", counted)
    assert EA.run(sr) == 0, log
    out = doc.read_text(encoding="utf-8")
    assert "The pulse was 78 at triage during the visit. (Exhibit 1 - p. 2)" in out and "pulse was 72" not in out
    assert "01/05/2025" in out and _sentences(out) == _sentences(DOC)
    assert len(writes) == 1  # one write, after every verify returned
    assert client.calls.count("read") == 2 and client.calls.count("verify") == 1  # a date finding is never verified
    rec = _record(sr)
    assert rec["complete"] is True and rec["applied"] == 1 and rec["recorded"] == 1
    assert rec["recorded_by_reason"] == {EC.RECORD_ONLY_CLASS: 1}
    assert "pulse" not in json.dumps(rec).lower()  # no document text in the record
    assert claim_audit.check(sr.slug_dir, "alpha")[0]
    assert core_counts.derived(sr.slug_dir.parents[1]) == {"entry_read_applied": 1, "entry_read_recorded": 1}


@pytest.mark.parametrize(
    ("verify", "reason"),
    [
        ({**YES, "original_wrong": False}, EC.NOT_WRONG),
        ({**YES, "corrected_supported": False}, EC.NOT_SUPPORTED),
        ({**YES, "page": {"exhibit": 1, "page": 1}}, EC.PAGE_NOT_CITED),  # a neighbour page the line does not cite
        ({"verdict": "yes"}, EC.VERIFY_UNPARSED),
    ],
)
def test_a_verify_that_does_not_say_yes_twice_leaves_the_line_untouched(scene, verify, reason) -> None:
    client = Client(verify=verify)
    sr, doc, log = scene(client)
    assert EA.run(sr) == 0, log
    assert doc.read_text(encoding="utf-8") == DOC
    assert _record(sr)["recorded_by_reason"].get(reason) == 1


def test_an_add_lands_as_its_own_paragraph_and_every_other_paragraph_is_byte_identical(scene) -> None:
    client = Client(
        lambda t: _reply(OMITTED) if "ENTRY N1" in t else _reply(), {**YES, "page": {"exhibit": 1, "page": 3}}
    )
    sr, doc, log = scene(client)
    entries = sr.slug_dir / "runs" / "alpha" / "entries_scoped_final.md"
    entries.write_text(f"{E1}\n\n{E2}\n", encoding="utf-8")
    assert EA.run(sr) == 0, log
    out = doc.read_text(encoding="utf-8")
    new_e1 = ER.B.entries_of(out)[1][0]
    assert new_e1.split("\n\n") == E1.split("\n\n") + [ADD]  # its own paragraph, after its line's paragraph
    assert ER.B.entries_of(out)[1][1] == E2 and _sentences(out) == _sentences(DOC) + 1
    assert new_e1 in entries.read_text(encoding="utf-8")


def test_two_findings_on_one_line_verify_and_apply_only_the_first(scene) -> None:
    second = {**VALUE, "quote": "at triage", "replacement": "at intake"}
    client = Client(lambda t: _reply(VALUE, second) if "ENTRY N1" in t else _reply())
    sr, doc, log = scene(client)
    assert EA.run(sr) == 0, log
    assert client.calls.count("verify") == 1 and _record(sr)["recorded_by_reason"] == {EC.SAME_LINE: 1}
    assert "pulse was 78 at triage" in doc.read_text(encoding="utf-8")


def test_the_same_added_sentence_twice_is_added_once() -> None:
    from types import SimpleNamespace

    units = ER.plan(DOC, {1}, {"Medical Diagnoses", "All Other Information"}, lambda ex: 6)
    n1 = next(u for u in units if u.anchor == "N1")
    f = ER.parse(_reply(OMITTED), n1).findings[0]
    g = dc_replace(f, line="S1", quote="pulse was 72", replacement=ADD.replace(" during the follow up visit", ""))
    sr = SimpleNamespace(
        unit=SimpleNamespace(unit="alpha"), job=SimpleNamespace(joint=False), slug_dir=Path("/nowhere")
    )
    got = AP.propose(sr, [("a", n1, f), ("b", n1, g)])  # type: ignore[arg-type]
    assert isinstance(got[0], EC.Proposal) and got[1] == EC.DUPLICATE_ADD


def test_the_tripwire_holds_and_leaves_the_document_byte_identical(scene, monkeypatch) -> None:
    monkeypatch.setattr(ER, "EDIT_SHARE", 0.0)
    sr, doc, log = scene(Client())
    assert EA.run(sr) == EA.EXIT_HELD
    assert doc.read_bytes() == DOC.encode()
    assert any("tripwire" in ln for ln in log) and _record(sr)["held"].startswith("tripwire")
    assert not claim_audit.check(sr.slug_dir, "alpha")[0]


def test_a_released_tripwire_applies_only_for_the_document_it_names(scene, monkeypatch) -> None:
    monkeypatch.setattr(ER, "EDIT_SHARE", 0.0)
    sr, doc, log = scene(Client())
    assert EA.run(sr) == EA.EXIT_HELD and doc.read_text(encoding="utf-8") == DOC
    sha = re.search(r"doc_sha ([0-9a-f]{64})", " ".join(log)).group(1)  # the hold line names it for SMD
    rel = sr.slug_dir / "runs" / "alpha" / IO.DIR / IO.RELEASE
    why = "read every edit against its page"
    for bad in ({"by": "smd-operator", "why": ""}, {"by": "smd-operator", "why": why},
                {"by": "smd-operator", "why": why, "doc_sha": "0" * 64}):  # fmt: skip
        rel.write_text(json.dumps(bad), encoding="utf-8")
        sr2, _d, log2 = scene(Client(), fresh=False)
        assert EA.run(sr2) == EA.EXIT_HELD and any("release is ignored" in ln for ln in log2)
    rel.write_text(json.dumps({"by": "smd-operator", "why": why, "doc_sha": sha}), encoding="utf-8")
    sr3, _d, log3 = scene(Client(), fresh=False)
    assert EA.run(sr3) == 0, log3
    assert "pulse was 78" in doc.read_text(encoding="utf-8")
    assert _record(sr)["tripwire_release"] == {"by": "smd-operator", "why": why, "doc_sha": sha}


# ---- reading --------------------------------------------------------------------------
def _whole_n1(text: str) -> bool:
    return "ENTRY N1" in text and "pp. 1, 2, 3, 4" in text


def test_an_unparsed_entry_is_asked_again_then_read_page_by_page_each_told_so(scene) -> None:
    client = Client(lambda t: "I could not finish." if _whole_n1(t) else _read_reply(t))
    sr, doc, log = scene(client)
    assert EA.run(sr) == 0, log
    assert client.calls.count("read") == 2 + 2 + 1  # N1 twice, its two cited pages alone, N2 once
    subs = [t for t in client.texts if "ENTRY N1 (only the pages below are shown)" in t]
    assert len(subs) == 2 and (sr.slug_dir / "runs" / "alpha" / IO.DIR / "N1~s1.read1.json").is_file()
    assert _record(sr)["complete"] is True


def test_a_one_page_read_of_a_line_citing_more_pages_is_recorded_partial_view(scene) -> None:
    both = DOC.replace("during the visit. (Exhibit 1 - p. 2)", "during the visit. (Exhibit 1 - p. 2-3)")
    client = Client(lambda t: "I could not finish." if _whole_n1(t) else _read_reply(t))
    sr, doc, log = scene(client, both)
    assert EA.run(sr) == 0, log
    assert "verify" not in client.calls and _record(sr)["recorded_by_reason"].get(EC.PARTIAL_VIEW, 0) >= 1
    assert doc.read_text(encoding="utf-8") == both


def test_an_entry_that_never_parses_holds_naming_the_unit_and_why(scene) -> None:
    client = Client(lambda t: "no answer" if "ENTRY N1" in t else _reply())
    sr, doc, log = scene(client)
    assert EA.run(sr) == EA.EXIT_HELD
    assert client.calls.count("read") == 2 + 2 * 2 + 1 and "verify" not in client.calls
    assert doc.read_text(encoding="utf-8") == DOC
    assert any("HELD" in ln and "N1 (a page read alone did not parse)" in ln for ln in log)
    ok, summary = claim_audit.check(sr.slug_dir, "alpha")
    assert not ok and summary["unread"] == ["N1"]


def test_a_bad_saved_reply_is_asked_again_and_only_the_unread_entry_is_paid_for(scene) -> None:
    sr, doc, log = scene(Client(lambda t: "no answer" if "ENTRY N1" in t else _reply()))
    assert EA.run(sr) == EA.EXIT_HELD
    again = Client()
    sr2, _doc, log2 = scene(again, fresh=False)
    assert EA.run(sr2) == 0, log2
    assert again.calls.count("read") == 1  # N1 asked afresh; N2's good answer reused
    saved = sr.slug_dir / "runs" / "alpha" / IO.DIR / "N1.read1.json"
    row = json.loads(saved.read_text(encoding="utf-8"))
    saved.write_text(json.dumps({**row, "stop_reason": "max_tokens"}), encoding="utf-8")
    doc.write_text(DOC, encoding="utf-8")
    third = Client()
    assert EA.run(scene(third, fresh=False)[0]) == 0
    assert third.calls.count("read") == 1  # a reply that ran out is never reused


def test_a_resume_uses_saved_page_by_page_answers_and_never_asks_the_whole_entry_again(scene) -> None:
    sr, doc, log = scene(Client(lambda t: "I could not finish." if _whole_n1(t) else _read_reply(t)))
    assert EA.run(sr) == 0, log
    doc.write_text(DOC, encoding="utf-8")
    again = Client()
    assert EA.run(scene(again, fresh=False)[0]) == 0
    assert again.calls.count("read") == 0


def test_a_resume_pays_nothing_for_what_was_answered(scene, monkeypatch) -> None:
    client = Client()
    sr, doc, log = scene(client)
    monkeypatch.setattr(ER, "EDIT_SHARE", 0.0)
    assert EA.run(sr) == EA.EXIT_HELD  # every answer saved, the document untouched
    paid = len(client.calls)
    monkeypatch.setattr(ER, "EDIT_SHARE", 0.08)
    assert EA.run(sr) == 0, log
    assert len(client.calls) == paid and "pulse was 78" in doc.read_text(encoding="utf-8")


def test_an_entry_whose_cited_page_is_past_its_exhibit_is_unread_and_never_sent(scene) -> None:
    client = Client()
    sr, doc, log = scene(client, DOC.replace("(Exhibit 1 - p. 5)", "(Exhibit 1 - p. 9)"))
    assert EA.run(sr) == EA.EXIT_HELD
    assert client.calls.count("read") == 1  # N1 only
    n2 = next(r for r in _record(sr)["rows"] if r["key"] == "N2")
    assert n2["status"] == "unread" and n2["why"] == EA.PAGE_MISSING


def test_a_request_refused_as_too_large_is_read_page_by_page(scene) -> None:
    import anthropic
    import httpx

    def big(kind: str, text: str) -> Exception | None:
        if kind == "read" and _whole_n1(text):
            resp = httpx.Response(413, request=httpx.Request("POST", "https://api.example.invalid/v1/messages"))
            return anthropic.APIStatusError("request too large", response=resp, body=None)
        return None

    sr, doc, log = scene(Client(fail=big))
    assert EA.run(sr) == 0, log
    assert (sr.slug_dir / "runs" / "alpha" / IO.DIR / "N1~s2.read1.json").is_file()
    assert _record(sr)["complete"] is True


def test_an_api_refusal_of_one_read_holds_the_unit_unread_and_never_fails_the_job(scene) -> None:
    client = Client(fail=lambda k, t: ValueError("refused") if k == "read" and "ENTRY N1" in t else None)
    sr, doc, log = scene(client)
    assert EA.run(sr) == EA.EXIT_HELD, log
    n1 = next(r for r in _record(sr)["rows"] if r["key"] == "N1")
    assert n1["status"] == "unread" and n1["why"] == "the API refused the read"


# ---- verify failures, cost, replay ----------------------------------------------------
def test_a_network_failure_in_a_verify_exits_for_a_retry(scene) -> None:
    from jobs.deadline import CallDeadlineError

    client = Client(fail=lambda k, _t: CallDeadlineError("no reply") if k == "verify" else None)
    sr, doc, log = scene(client)
    assert EA.run(sr) == EA.EXIT_INFRA, log
    assert doc.read_text(encoding="utf-8") == DOC


def test_a_verify_the_api_refuses_twice_is_recorded_verify_failed(scene) -> None:
    client = Client(fail=lambda k, _t: ValueError("refused") if k == "verify" else None)
    sr, doc, log = scene(client)
    assert EA.run(sr) == 0, log
    assert _record(sr)["recorded_by_reason"].get(EC.VERIFY_FAILED) == 1 and doc.read_text(encoding="utf-8") == DOC


def test_the_verify_phase_past_the_cap_is_the_firms_ordinary_cap_hold(scene, monkeypatch, pricing_path) -> None:
    from jobs.limits import CAP_SETTING, LimitHold

    monkeypatch.setenv("JOBS_PRICING_JSON", str(pricing_path))
    client = Client()
    sr, doc, log = scene(client)
    sr.job = dc_replace(sr.job, cap_usd=0.000001)
    with pytest.raises(LimitHold) as hold:
        EA.run(sr)
    assert "verify" not in client.calls and doc.read_text(encoding="utf-8") == DOC
    assert hold.value.reason == (
        f"{CAP_SETTING}: this run's projected cost would exceed the job's cost cap before audit; "
        "the package was not started"
    )


def test_a_replay_writes_requests_and_settles_from_answer_files(scene, monkeypatch) -> None:
    monkeypatch.setenv(IO.REPLAY_ENV, "1")

    class Never(Client):
        def stream(self, **kw: Any) -> _Stream:
            raise AssertionError("a replay never calls the model")

    sr, doc, log = scene(Never())
    d = sr.slug_dir / "runs" / "alpha" / IO.DIR

    def answer(kind: str, reply: Callable[[str], str]) -> int:
        reqs = sorted(d.glob(f"*.{kind}.request.json"))
        for p in reqs:
            req = json.loads(p.read_text(encoding="utf-8"))
            text = "\n".join(b.get("text", "") for b in req["content"])
            out = {"hash": req["hash"], "text": reply(text), "stop_reason": "end_turn"}
            (d / f"{req['name']}.{req['kind']}.json").write_text(json.dumps(out), encoding="utf-8")
        return len(reqs)

    assert EA.run(sr) == EA.EXIT_PENDING and doc.read_text(encoding="utf-8") == DOC
    assert answer("read1", _read_reply) == 2
    assert EA.run(sr) == EA.EXIT_PENDING
    req = json.loads((d / "N1.f0.verify1.request.json").read_text(encoding="utf-8"))
    assert {b["type"] for b in req["content"]} == {"text", "image"}  # pictures named by page, never embedded
    assert answer("verify1", lambda _t: json.dumps(YES)) == 1
    assert EA.run(sr) == 0, log
    assert "pulse was 78" in doc.read_text(encoding="utf-8") and _record(sr)["calls"] == 0


def test_the_projection_prices_reads_and_three_picture_verifies_at_expected_output(scene) -> None:
    sr, _doc, _log = scene(None)
    lines = EA.rehearse(sr)
    assert lines[0].startswith("2 entr(ies) to read from the built chronology, 0 already read; ")
    _n, _done, _tin, tout = IO.tokens(sr.slug_dir, "alpha", sr.cfg)
    assert tout == 2 * (IO.READ_OUT_EXPECTED + IO.VERIFIES_PER_UNIT * IO.VERIFY_OUT_EXPECTED) == 2 * 3_400


# ---- the stage around it ---------------------------------------------------------------
MAP = (
    "## ENTRIES\n\n" + E1 + "\n\n## INDEX\n"
    "2025-01-05 | Example Clinic | M54.5 | clinic note\n"
    "2025-03-09 | Nowhere Clinic | S13.4XXA | other note\n"
)


def test_an_index_gap_is_counted_and_never_shown_to_the_read(scene) -> None:
    client = Client()
    sr, doc, log = scene(client)
    (sr.slug_dir / "runs" / "alpha" / "map-01.md").write_text(MAP, encoding="utf-8")
    assert EA.run(sr) == 0, log
    assert client.texts and not any("M54.5" in t or "INDEX" in t for t in client.texts)
    rec = _record(sr)
    assert rec["gaps_codes"] == 1 and rec["gaps_no_entry"] == 1


def test_line_verdicts_are_recorded_per_unit_and_in_total(scene) -> None:
    lines = {**ALL_OK, "S1": "problem"}
    client = Client(lambda t: json.dumps({"lines": lines, "findings": [VALUE], "complete": True}) if "ENTRY N1" in t
                    else _reply())  # fmt: skip
    sr, doc, log = scene(client)
    assert EA.run(sr) == 0, log
    rec = _record(sr)
    n1 = next(r for r in rec["rows"] if r["key"] == "N1")
    assert (n1["lines_ok"], n1["lines_problem"], n1["lines_unreadable"]) == (3, 1, 0)
    assert rec["lines_problem"] == 1 and rec["lines_ok"] == 3 + 16  # N2: D, P and 14 sentences


def test_a_crash_is_a_defect_never_a_hold(scene, monkeypatch) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("an invented defect")

    monkeypatch.setattr(AP, "decide", boom)
    sr, doc, log = scene(Client())
    assert EA.run(sr) == EA.EXIT_DEFECT and any("CRASHED (RuntimeError" in ln for ln in log)


def test_the_gate_reads_the_record_and_falls_back_to_the_claim_rule(tmp_path: Path, monkeypatch) -> None:
    rd = tmp_path / "runs" / "alpha"
    rd.mkdir(parents=True)
    monkeypatch.setattr(claim_audit.coverage, "check", lambda _p, _log: (False, {"old": True}))
    assert claim_audit.check(tmp_path, "alpha") == (False, {"old": True})  # no record: the claim rule
    rows = [{"key": "N1", "status": "read"}, {"key": "N2", "status": "read"}]
    (rd / IO.RECORD).write_text(json.dumps({"complete": True, "held": "", "rows": rows}))
    assert claim_audit.check(tmp_path, "alpha")[0]
    rows[1]["status"] = "unread"
    (rd / IO.RECORD).write_text(json.dumps({"complete": False, "held": "", "rows": rows}))
    assert claim_audit.check(tmp_path, "alpha") == (False, {"entry_read": True, "units": 2, "unread": ["N2"],
                                                            "held": ""})  # fmt: skip


def test_both_lanes_read_entries_and_the_update_reopens_what_reads_its_document() -> None:
    full = dag.by_name()["audit"]
    assert full.runner is EA.run and full.rehearse is EA.rehearse and full.paid
    assert set(full.exit_map) == {EA.EXIT_HELD, EA.EXIT_DEFECT, EA.EXIT_INFRA, EA.EXIT_PENDING}
    assert full.exit_map[EA.EXIT_DEFECT] == (dag.FAILED, "audit: the stage crashed (a defect)")
    assert EA.EXIT_HELD == 1 and full.exit_map[1][1].startswith("audit coverage:")  # the broker resumes by it
    assert full.invalidates == ("render", "manifest", "review")
    upd = next(s for s in dag_update.UPDATE_STAGES if s.name == "audit")
    assert upd.runner is EA.run and upd.rehearse is EA.rehearse and upd.paid
    assert set(upd.exit_map) == set(full.exit_map) and upd.exit_map[1][1].startswith("audit coverage:")
    assert upd.invalidates == (
        "update_final_set",
        "update_assemble",
        "update_render",
        "update_proofs",
        "update_manifest",
    )
    assert "update_review" not in [s.name for s in dag_update.UPDATE_STAGES]


def test_a_redone_audit_reopens_render_manifest_and_review(tmp_path: Path) -> None:
    from jobs.medchron.state import RunState

    row = dag.by_name()["audit"]
    st = RunState.load_or_new(tmp_path / "state.json", slug="example-matter", unit="alpha")
    for name in ("render", "manifest", "review"):
        st.finish(name, status="done", exit_code=0, dollars=0.0, pages=0)
    st.invalidate(list(row.invalidates))
    assert {st.stage(n).status for n in ("render", "manifest", "review")} == {"pending"}


# ---- round 5 (2026-11-18) -------------------------------------------------------------
#: The read and verify requests of the scene, hashed before round 5: a proof
#: run's saved replies are keyed by these, so round 5 must not move them.
PINNED = {
    "N1": "dbc7672478f25ace447433ed8fb723c0d1fc5670092575cd4a669f41b2dca584",
    "N2": "807304ee0d81545a497cc9a6d0a17bf190293cc41a6719340debe90ac7b4fec2",
    "verify-value": "a1d9cf58c0ca8304b36bc1c43c0dcc6eed981b784872538ec555cd927fcf7345",
    "verify-omitted": "bfa77562dfd6bbc6de9290ff958f9cccdda03862140d64b1541096931fe8b132",
}


def test_read_and_verify_requests_are_unchanged_by_round_5(scene) -> None:
    from jobs.medchron.page_mode import Modes
    from jobs.stages.read_inputs import client_line, image_source

    sr, _d, _l = scene(None)
    pl = IO.plan(sr.slug_dir, "alpha", sr.cfg, DOC)
    modes = Modes.open(sr.slug_dir, "alpha")
    got = {}
    for u in pl.units:
        s, c = ER.request(u, sr.cfg, client_line(sr), image_source(sr), modes)
        got[u.key] = IO.request_hash(IO.model(sr.cfg), s, c)
    modes.close()
    n1 = next(u for u in pl.units if u.anchor == "N1")
    for f in ER.parse(_reply(VALUE, OMITTED), n1).findings:
        s, c = EC.verify_request(EC.propose(f, n1), sr.cfg, client_line(sr), image_source(sr))
        got[f"verify-{f.klass}"] = IO.request_hash(IO.model(sr.cfg), s, c)
    assert got == PINNED


def _page_of_proposal(text: str) -> dict[str, Any]:
    """A verify that says yes on the page the proposed sentence (or the line) cites."""
    tail = text.split("PROPOSED", 1)[-1]
    m = re.findall(r"\(Exhibit (\d+) - p\. (\d+)", tail) or re.findall(r"\(Exhibit (\d+) - p\. (\d+)", text)
    return {**YES, "page": {"exhibit": int(m[-1][0]), "page": int(m[-1][1])}}


def test_a_pointer_is_recorded_and_never_verified(scene) -> None:
    ptr = {**VALUE, "class": "pointer", "page": 3, "replacement": "(Exhibit 1 - p. 3)"}
    client = Client(lambda t: _reply(ptr) if "ENTRY N1" in t else _reply())
    sr, doc, log = scene(client)
    assert EA.run(sr) == 0, log
    assert "verify" not in client.calls and doc.read_text(encoding="utf-8") == DOC
    assert _record(sr)["recorded_by_reason"] == {EC.POINTER_RECORDED: 1}


def test_a_date_line_swap_is_recorded_and_the_entries_stay(scene) -> None:
    redate = {**VALUE, "line": "D", "quote": "01/05/2025", "replacement": "1/5/2025"}
    client = Client(lambda t: _reply(redate) if "ENTRY N1" in t else _reply())
    sr, doc, log = scene(client)
    assert EA.run(sr) == 0, log
    out = doc.read_text(encoding="utf-8")
    assert out == DOC and len(ER.B.entries_of(out)[1]) == 2
    assert _record(sr)["recorded_by_reason"] == {EC.HEADER_LINE: 1}


def test_a_swap_that_drops_the_period_is_applied_with_the_period(scene) -> None:
    drop = {**VALUE, "quote": "pulse was 72 at triage during the visit.", "replacement": "pulse was 78 at triage"}
    sr, doc, log = scene(Client(lambda t: _reply(drop) if "ENTRY N1" in t else _reply()))
    assert EA.run(sr) == 0, log
    out = doc.read_text(encoding="utf-8")
    assert "The pulse was 78 at triage. (Exhibit 1 - p. 2)" in out and _sentences(out) == _sentences(DOC)


def test_one_bad_edit_among_three_is_reverted_and_the_other_two_apply(scene, monkeypatch) -> None:
    monkeypatch.setattr(ER, "EDIT_SHARE", 0.5)
    bad = {"line": "S3", "class": "omitted", "exhibit": 1, "page": 5, "quote": "number c was",
           "action": "add", "replacement": "6/10 (morning) gait was steady and even. (Exhibit 1 - p. 5)"}  # fmt: skip

    def read(t: str) -> str:
        return _reply(VALUE, OMITTED) if "ENTRY N1" in t else _reply(bad)

    sr, doc, log = scene(Client(read, _page_of_proposal))
    assert EA.run(sr) == 0, log
    out = doc.read_text(encoding="utf-8")
    assert "pulse was 78" in out and f"\n\n{ADD}" in out and "6/10 (morning)" not in out
    rec = _record(sr)
    assert rec["applied"] == 2 and rec["recorded_by_reason"] == {AP.BREAKS_STRUCTURE: 1}
    assert rec["breaks_structure_by_invariant"] in ({"a": 1}, {"b": 1})
    assert len(ER.B.entries_of(out)[1]) == 2 and ER.B.entries_of(out)[1][1] == E2


def test_each_invariant_can_fail() -> None:
    e = EC.Edit("swap", "N1", "x", "Gait was steady. (Exhibit 1 - p. 2)")
    swapped = DOC.replace("The pulse was 72 at triage during the visit. (Exhibit 1 - p. 2)", e.new)
    assert AP.broken(DOC, swapped, e) == ""
    assert AP.broken(DOC, DOC.replace("01/05/2025\n", "01/06/2025\n"), e) == "a"
    two = EC.Edit("swap", "N1", "x", "One. Two. (Exhibit 1 - p. 2)")
    assert AP.broken(DOC, DOC.replace("The pulse was 72 at triage during the visit. ", ""), two) == "b"
    both = DOC.replace("Therapy twice", "Therapy thrice").replace("Home exercise number a", "Home exercise number z")
    assert AP.broken(DOC, both, e) == "c"
    moved = DOC.replace("during the visit. (Exhibit 1 - p. 2)", "during the visit. (Exhibit 1 - p. 3)")
    assert AP.broken(DOC, moved, e) == "d"
    prov = EC.Edit("swap", "N1", "x", "Seen at | Other Clinic now. (Exhibit 1 - p. 2)")
    assert (
        AP.broken(DOC, DOC.replace("The pulse was 72 at triage during the visit.", "Seen at | Other Clinic now."), prov)
        == "e"
    )


def test_two_adds_after_one_paragraph_keep_document_order() -> None:
    line = "The pulse was 72 at triage during the visit. (Exhibit 1 - p. 2)"
    first = EC.Edit("add", "N1", line, "First added sentence about the visit. (Exhibit 1 - p. 2)")
    second = EC.Edit("add", "N1", line, "Second added sentence about the visit. (Exhibit 1 - p. 2)")
    out, broke = AP.edit_document(DOC, [first, second])
    assert (
        not broke and out.index(first.new) < out.index(second.new) and f"{line}\n\n{first.new}\n\n{second.new}" in out
    )


def test_a_verify_with_no_picture_is_never_sent(scene, monkeypatch) -> None:
    monkeypatch.setattr(EA, "image_source", lambda _sr: lambda _x, _p: None)
    client = Client()
    sr, doc, log = scene(client)
    assert EA.run(sr) == 0, log
    assert "verify" not in client.calls and _record(sr)["recorded_by_reason"].get(EC.NO_PICTURE) == 1


def test_a_unit_whose_every_sentence_is_unreadable_is_counted(scene) -> None:
    dark = {"D": "ok", "P": "ok", **{f"S{i}": "unreadable" for i in range(1, 15)}}
    client = Client(lambda t: json.dumps({"lines": dark, "findings": [], "complete": True}) if "ENTRY N2" in t
                    else _reply())  # fmt: skip
    sr, doc, log = scene(client)
    assert EA.run(sr) == 0, log
    assert _record(sr)["units_all_unreadable"] == 1
