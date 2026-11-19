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
VALUE = {"class": "value", "exhibit": 1, "page": 2, "quote": "pulse was 72", "action": "replace",
         "replacement": "pulse was 78", "problem": "the page records 78"}  # fmt: skip
DATE = {"class": "date", "exhibit": 1, "page": 2, "quote": "01/05/2025", "action": "none", "replacement": ""}
STATED = {"answer": "STATED", "exhibit": 1, "page": 2, "value": "78"}


def _reply(*items: dict[str, Any]) -> str:
    return json.dumps({"findings": list(items), "complete": True})


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

    def __init__(self, read: Callable[[str], str] = _read_reply, confirm: dict | None = None) -> None:
        self.read, self.confirm = read, confirm or STATED
        self.calls: list[str] = []
        self.messages = self

    def stream(self, **kw: Any) -> _Stream:
        text = "\n".join(str(b.get("text", "")) for b in kw["messages"][0]["content"])
        kind = "read" if "ENTRY " in text or "PRIOR HISTORY BLOCK" in text else "confirm"
        self.calls.append(kind)
        return _Stream(self.read(text) if kind == "read" else json.dumps(self.confirm))


@pytest.fixture
def scene(job_dir: Path, firm_config_path: Path, data_root: Path):
    def make(client: Client | None = None, doc: str = DOC):
        log: list[str] = []
        sr = _sr(job_dir, firm_config_path, data_root, client, log)
        _exhibit_set(sr, PAGES)
        rd = sr.slug_dir / "runs" / "alpha"
        rd.mkdir(parents=True, exist_ok=True)
        (rd / "final-chronology.md").write_text(doc, encoding="utf-8")
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
    assert set(full.exit_map) == {EA.EXIT_HELD, EA.EXIT_INFRA, EA.EXIT_PENDING}
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
