"""The full chronology's `audit` stage (``stages/entry_audit.py``): each entry
read against its cited pages, each finding confirmed blind, decided in code,
applied once; nothing removed; an entry not read holds; a resume and a replay
pay nothing for what is answered. Through the real StageRun and Doorway, with
a scripted SDK client. Every name, date and value is invented."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any, Callable

import pytest

from jobs.medchron import core_counts, dag, dag_update
from jobs.medchron.audit import claims as CL
from jobs.medchron.gates import claim_audit
from jobs.medchron.update import entry_read as ER
from jobs.stages import audit_loop, entry_audit as EA, entry_audit_io as IO
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
DATE = {
    "line": "D",
    "class": "date",
    "exhibit": 1,
    "page": 2,
    "quote": "01/05/2025",
    "action": "none",
    "replacement": "",
}
STATED = {"answer": "STATED", "exhibit": 1, "page": 2, "value": "78"}


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
    """The SDK client, scripted: a read answers `read(text)`, a confirm `confirm`."""

    def __init__(
        self,
        read: Callable[[str], str] = _read_reply,
        confirm: dict | None = None,
        fail: Callable[[str, str], Exception | None] = lambda _k, _t: None,
    ) -> None:
        self.read, self.confirm, self.fail = read, confirm or STATED, fail
        self.calls: list[str] = []
        self.messages = self

    def stream(self, **kw: Any) -> _Stream:
        text = "\n".join(str(b.get("text", "")) for b in kw["messages"][0]["content"])
        kind = "read" if "ENTRY " in text or "PRIOR HISTORY BLOCK" in text else "confirm"
        self.calls.append(kind)
        exc = self.fail(kind, text)
        if exc is not None:
            raise exc
        return _Stream(self.read(text) if kind == "read" else json.dumps(self.confirm))


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
def test_a_value_is_corrected_from_the_page_a_date_is_recorded_and_nothing_is_removed(scene, monkeypatch) -> None:
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
    assert "01/05/2025" in out and _sentences(out) >= _sentences(DOC)
    assert len(writes) == 1  # one write, after every confirm returned
    assert client.calls.count("read") == 2 and client.calls.count("confirm") == 1  # a date finding is not confirmed
    rec = _record(sr)
    assert rec["complete"] is True and rec["applied"] == 1 and rec["recorded"] == 1
    n1 = next(r for r in rec["rows"] if r["key"] == "N1")
    assert n1["status"] == "read" and n1["recorded"][0]["class"] == "date"
    assert "pulse" not in json.dumps(rec).lower()  # no document text in the record
    assert claim_audit.check(sr.slug_dir, "alpha")[0]
    assert core_counts.derived(sr.slug_dir.parents[1]) == {"entry_read_applied": 1, "entry_read_recorded": 1}


def test_a_blind_read_that_disagrees_is_recorded_and_the_document_is_untouched(scene) -> None:
    sr, doc, log = scene(Client(confirm={"answer": "STATED", "exhibit": 1, "page": 2, "value": "76"}))
    assert EA.run(sr) == 0, log
    assert doc.read_text(encoding="utf-8") == DOC
    assert _record(sr)["applied"] == 0 and _record(sr)["recorded"] == 2


def test_the_tripwire_holds_and_leaves_the_document_byte_identical(scene, monkeypatch) -> None:
    monkeypatch.setattr(ER, "EDIT_SHARE", 0.0)
    sr, doc, log = scene(Client())
    assert EA.run(sr) == EA.EXIT_HELD
    assert doc.read_bytes() == DOC.encode()
    assert any("tripwire" in ln for ln in log) and _record(sr)["held"].startswith("tripwire")
    assert not claim_audit.check(sr.slug_dir, "alpha")[0]


def test_an_unparsed_entry_is_asked_again_then_read_page_by_page(scene) -> None:
    def read(text: str) -> str:
        whole = "ENTRY N1" in text and "pp. 1, 2, 3, 4" in text
        return "I could not finish." if whole else _read_reply(text)

    client = Client(read)
    sr, doc, log = scene(client)
    assert EA.run(sr) == 0, log
    assert client.calls.count("read") == 2 + 2 + 1  # N1 twice, its two cited pages alone, N2 once
    assert (sr.slug_dir / "runs" / "alpha" / IO.DIR / "N1~s1.read1.json").is_file()
    assert _record(sr)["complete"] is True


def test_an_entry_that_never_parses_holds_and_fails_the_gate(scene) -> None:
    client = Client(lambda t: "no answer" if "ENTRY N1" in t else _reply())
    sr, doc, log = scene(client)
    assert EA.run(sr) == EA.EXIT_HELD
    assert client.calls.count("read") == 2 + 2 * 2 + 1 and "confirm" not in client.calls
    assert doc.read_text(encoding="utf-8") == DOC
    ok, summary = claim_audit.check(sr.slug_dir, "alpha")
    assert not ok and summary["unread"] == ["N1"]


def test_a_resume_pays_nothing_for_what_was_answered(scene, monkeypatch) -> None:
    client = Client()
    sr, doc, log = scene(client)
    monkeypatch.setattr(ER, "EDIT_SHARE", 0.0)
    assert EA.run(sr) == EA.EXIT_HELD  # every answer saved, the document untouched
    paid = len(client.calls)
    monkeypatch.setattr(ER, "EDIT_SHARE", 0.08)
    assert EA.run(sr) == 0, log
    assert len(client.calls) == paid and "pulse was 78" in doc.read_text(encoding="utf-8")


def test_a_replay_writes_requests_and_settles_from_answer_files(scene, monkeypatch) -> None:
    monkeypatch.setenv(IO.REPLAY_ENV, "1")

    class Never(Client):
        def stream(self, **kw: Any) -> _Stream:
            raise AssertionError("a replay never calls the model")

    sr, doc, log = scene(Never())
    d = sr.slug_dir / "runs" / "alpha" / IO.DIR

    def answer(kind: str, reply: Callable[[dict], str]) -> int:
        reqs = sorted(d.glob(f"*.{kind}.request.json"))
        for p in reqs:
            req = json.loads(p.read_text(encoding="utf-8"))
            text = "\n".join(b.get("text", "") for b in req["content"])
            out = {"hash": req["hash"], "text": reply({"text": text}), "stop_reason": "end_turn"}
            (d / f"{req['name']}.{req['kind']}.json").write_text(json.dumps(out), encoding="utf-8")
        return len(reqs)

    assert EA.run(sr) == EA.EXIT_PENDING and doc.read_text(encoding="utf-8") == DOC
    assert answer("read1", lambda r: _read_reply(r["text"])) == 2
    assert EA.run(sr) == EA.EXIT_PENDING
    assert answer("confirm1", lambda _r: json.dumps(STATED)) == 1
    assert EA.run(sr) == 0, log
    assert "pulse was 78" in doc.read_text(encoding="utf-8") and _record(sr)["calls"] == 0


# ---- the gate, the rows, the projection --------------------------------------------
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


def test_the_full_lane_reads_entries_and_the_update_lane_keeps_the_claim_loop() -> None:
    full = dag.by_name()["audit"]
    assert full.runner is EA.run and full.rehearse is EA.rehearse and full.paid
    assert set(full.exit_map) == {EA.EXIT_HELD, EA.EXIT_DEFECT, EA.EXIT_INFRA, EA.EXIT_PENDING}
    assert full.exit_map[EA.EXIT_DEFECT] == (dag.FAILED, "audit: the stage crashed (a defect)")
    assert full.exit_map[EA.EXIT_HELD][0] == dag.HELD and full.exit_map[EA.EXIT_INFRA][0] == dag.FAILED
    # the broker resumes an audit hold by this prefix (workspace_broker AUDIT_GATE_HOLD_PREFIX)
    assert EA.EXIT_HELD == 1 and full.exit_map[1][1].startswith("audit coverage:")
    upd = next(s for s in dag_update.UPDATE_STAGES if s.name == "audit")
    assert upd.runner is audit_loop.run and upd.rehearse is audit_loop.rehearse
    assert set(upd.exit_map) == {1, 2, 3} and upd.exit_map[1][1].startswith("audit coverage")


def test_rehearse_projects_the_reads_and_confirms_from_the_built_document(scene) -> None:
    sr, _doc, _log = scene(None)
    lines = EA.rehearse(sr)
    assert len(lines) == 1 and lines[0].startswith("2 entr(ies) to read from the built chronology, 0 already read; ")
    assert lines[0].endswith(" USD")
    assert IO.tokens(sr.slug_dir, "alpha", sr.cfg)[2] > 0


# ---- the fix round (2026-11-18) -------------------------------------------------------
def test_a_bad_saved_reply_is_asked_again_and_only_the_unread_entry_is_paid_for(scene) -> None:
    sr, doc, log = scene(Client(lambda t: "no answer" if "ENTRY N1" in t else _reply()))
    assert EA.run(sr) == EA.EXIT_HELD
    again = Client()
    sr2, _doc, log2 = scene(again, fresh=False)
    assert EA.run(sr2) == 0, log2
    assert again.calls.count("read") == 1  # N1 asked afresh; N2's good answer reused
    assert "pulse was 78" in doc.read_text(encoding="utf-8")
    saved = sr.slug_dir / "runs" / "alpha" / IO.DIR / "N1.read1.json"
    row = json.loads(saved.read_text(encoding="utf-8"))
    saved.write_text(json.dumps({**row, "stop_reason": "max_tokens"}), encoding="utf-8")
    doc.write_text(DOC, encoding="utf-8")
    third = Client()
    assert EA.run(scene(third, fresh=False)[0]) == 0
    assert third.calls.count("read") == 1  # a reply that ran out is never reused


def test_a_released_tripwire_applies_only_for_the_document_it_names(scene, monkeypatch) -> None:
    import re

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


def test_a_network_failure_in_a_confirm_exits_for_a_retry(scene) -> None:
    from jobs.deadline import CallDeadlineError

    client = Client(fail=lambda k, _t: CallDeadlineError("no reply") if k == "confirm" else None)
    sr, doc, log = scene(client)
    assert EA.run(sr) == EA.EXIT_INFRA, log
    assert doc.read_text(encoding="utf-8") == DOC


def test_a_confirm_that_errors_otherwise_is_recorded_confirm_failed(scene) -> None:
    client = Client(fail=lambda k, _t: ValueError("a malformed reply object") if k == "confirm" else None)
    sr, doc, log = scene(client)
    assert EA.run(sr) == 0, log
    assert _record(sr)["recorded_by_reason"].get("confirm_failed") == 1 and doc.read_text(encoding="utf-8") == DOC


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
        if kind == "read" and "ENTRY N1" in text and "pp. 1, 2, 3, 4" in text:
            resp = httpx.Response(413, request=httpx.Request("POST", "https://api.example.invalid/v1/messages"))
            return anthropic.APIStatusError("request too large", response=resp, body=None)
        return None

    client = Client(fail=big)
    sr, doc, log = scene(client)
    assert EA.run(sr) == 0, log
    assert (sr.slug_dir / "runs" / "alpha" / IO.DIR / "N1~s2.read1.json").is_file()
    assert _record(sr)["complete"] is True


def test_the_confirm_phase_past_the_cap_is_the_firms_ordinary_cap_hold(scene, monkeypatch, pricing_path) -> None:
    from dataclasses import replace as dc_replace

    from jobs.limits import CAP_SETTING, LimitHold

    monkeypatch.setenv("JOBS_PRICING_JSON", str(pricing_path))
    client = Client()
    sr, doc, log = scene(client)
    sr.job = dc_replace(sr.job, cap_usd=0.000001)
    with pytest.raises(LimitHold) as hold:
        EA.run(sr)
    assert "confirm" not in client.calls and doc.read_text(encoding="utf-8") == DOC
    # word for word what the driver tells the firm before any paid stage (limits.check_before_paid)
    assert hold.value.reason == (
        f"{CAP_SETTING}: this run's projected cost would exceed the job's cost cap before audit; "
        "the package was not started"
    )


def test_an_added_sentence_reaches_the_entries_copy(scene) -> None:
    add = "Gait was steady during the follow up visit. (Exhibit 1 - p. 3)"
    item = {"line": "S2", "class": "omitted", "exhibit": 1, "page": 3, "quote": "Therapy twice weekly", "action": "add",
            "replacement": add}  # fmt: skip
    client = Client(lambda t: _reply(item) if "ENTRY N1" in t else _reply(), {"answer": "YES", "exhibit": 1, "page": 3})
    sr, doc, log = scene(client)
    entries = sr.slug_dir / "runs" / "alpha" / "entries_scoped_final.md"
    entries.write_text(f"{E1}\n\n{E2}\n", encoding="utf-8")
    assert EA.run(sr) == 0, log
    assert add in doc.read_text(encoding="utf-8") and add in entries.read_text(encoding="utf-8")


def test_a_crash_is_a_defect_never_a_hold(scene, monkeypatch) -> None:
    from jobs.stages import entry_audit_apply as AP

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("an invented defect")

    monkeypatch.setattr(AP, "decide", boom)
    sr, doc, log = scene(Client())
    assert EA.run(sr) == EA.EXIT_DEFECT and any("CRASHED (RuntimeError" in ln for ln in log)


def test_a_redone_audit_reopens_render_manifest_and_review(tmp_path: Path) -> None:
    from jobs.medchron.state import RunState

    row = dag.by_name()["audit"]
    assert row.invalidates == ("render", "manifest", "review")
    st = RunState.load_or_new(tmp_path / "state.json", slug="example-matter", unit="alpha")
    for name in ("render", "manifest", "review"):
        st.finish(name, status="done", exit_code=0, dollars=0.0, pages=0)
    st.invalidate(list(row.invalidates))
    assert {n: st.stage(n).status for n in ("render", "manifest", "review")} == dict.fromkeys(
        ("render", "manifest", "review"), "pending"
    )
    assert next(s for s in dag_update.UPDATE_STAGES if s.name == "audit").invalidates == ("update_review",)


# ---- round 3 (2026-11-18) -----------------------------------------------------------
MAP = (
    "## ENTRIES\n\n" + E1 + "\n\n## INDEX\n"
    "2025-01-05 | Example Clinic | M54.5 | clinic note\n"
    "2025-03-09 | Nowhere Clinic | S13.4XXA | other note\n"
)


def test_an_index_gap_is_counted_and_never_shown_to_the_read(scene) -> None:
    seen: list[str] = []
    client = Client(lambda t: seen.append(t) or _read_reply(t))
    sr, doc, log = scene(client)
    (sr.slug_dir / "runs" / "alpha" / "map-01.md").write_text(MAP, encoding="utf-8")
    assert EA.run(sr) == 0, log
    assert seen and not any("M54.5" in t or "INDEX" in t for t in seen)
    rec = _record(sr)
    assert rec["gaps_codes"] == 1 and rec["gaps_no_entry"] == 1


def test_the_same_added_sentence_from_two_parts_is_added_once() -> None:
    from jobs.medchron.update import review_repair as RR
    from jobs.stages import entry_audit_apply as AP

    def add(text: str, anchor: str = "N1") -> Any:
        return ("apply", RR.Finding(RR.MUST, "omitted", anchor, 1, 2, "", "add", text, ""))

    got = AP.dedupe_adds([
        add("Gait was steady. (Exhibit 1 - p. 2)"),
        add("Gait was steady at the visit. (Exhibit 1 - p. 3)"),  # contains the first: a duplicate
        add("Gait was steady. (Exhibit 1 - p. 2)", anchor="N2"),  # another entry: kept
    ])  # fmt: skip
    assert [d[0] for d in got] == ["apply", "record", "apply"] and got[1][1] == AP.DUPLICATE_ADD


def test_an_api_refusal_of_one_read_holds_the_unit_unread_and_never_fails_the_job(scene) -> None:
    client = Client(fail=lambda k, t: ValueError("refused") if k == "read" and "ENTRY N1" in t else None)
    sr, doc, log = scene(client)
    assert EA.run(sr) == EA.EXIT_HELD, log
    n1 = next(r for r in _record(sr)["rows"] if r["key"] == "N1")
    assert n1["status"] == "unread" and n1["why"] == "the API refused the read"
    assert doc.read_text(encoding="utf-8") == DOC


def test_a_resume_uses_saved_page_by_page_answers_and_never_asks_the_whole_entry_again(scene) -> None:
    def read(text: str) -> str:
        whole = "ENTRY N1" in text and "pp. 1, 2, 3, 4" in text
        return "I could not finish." if whole else _read_reply(text)

    sr, doc, log = scene(Client(read))
    assert EA.run(sr) == 0, log
    doc.write_text(DOC, encoding="utf-8")
    again = Client()
    assert EA.run(scene(again, fresh=False)[0]) == 0
    assert again.calls.count("read") == 0


def test_an_added_sentence_goes_right_after_its_line_and_line_verdicts_are_recorded(scene, monkeypatch) -> None:
    monkeypatch.setattr(ER, "EDIT_SHARE", 0.5)  # two edits on a 16-sentence scene
    add = "Gait was steady during the follow up visit. (Exhibit 1 - p. 3)"
    om = {"line": "S1", "class": "omitted", "exhibit": 1, "page": 3, "quote": "pulse was 72", "action": "add",
          "replacement": add}  # fmt: skip
    lines = {**ALL_OK, "S1": "problem"}

    def read(t: str) -> str:
        if "ENTRY N1" not in t:
            return _reply()
        return json.dumps({"lines": lines, "findings": [VALUE, om], "complete": True})

    client = Client(read, {"answer": "YES", "exhibit": 1, "page": 3, "value": "78"})
    client.confirm = None  # answered per question below

    def stream(**kw: Any) -> _Stream:
        text = "\n".join(str(b.get("text", "")) for b in kw["messages"][0]["content"])
        if "ENTRY " in text:
            client.calls.append("read")
            return _Stream(read(text))
        client.calls.append("confirm")
        yes = "Is every statement" in text
        return _Stream(json.dumps({"answer": "YES", "exhibit": 1, "page": 3} if yes else STATED))

    client.stream = stream  # type: ignore[method-assign]
    sr, doc, log = scene(client)
    assert EA.run(sr) == 0, log
    out = doc.read_text(encoding="utf-8")
    assert f"The pulse was 78 at triage during the visit. (Exhibit 1 - p. 2)\n{add}\n" in out
    rec = _record(sr)
    n1 = next(r for r in rec["rows"] if r["key"] == "N1")
    assert (n1["lines_ok"], n1["lines_problem"], n1["lines_unreadable"]) == (3, 1, 0)
    assert rec["lines_problem"] == 1 and rec["lines_ok"] == 3 + 16 and rec["applied"] == 2  # N2: D, P, 14 sentences


def test_the_projection_prices_output_at_the_expected_figures(scene) -> None:
    sr, _doc, _log = scene(None)
    _n, _done, _tin, tout = IO.tokens(sr.slug_dir, "alpha", sr.cfg)
    assert tout == 2 * (IO.READ_OUT_EXPECTED + IO.CONFIRMS_PER_UNIT * IO.CONFIRM_OUT_EXPECTED) == 2 * 4_300
