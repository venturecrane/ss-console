"""A chronology runs from one start to delivery without an SMD-only hold, end to
end through the real driver, a fake seat and a scripted model.

WHY (2026-10-23). The Curtis chronology (A&P matter 200530) stopped eight
times in 22 hours; seven of the stops were holds only SMD could clear, each
cleared by a merge in the middle of the job: a residue file at the orphans
decision, a file the composer read and drew nothing from at the coverage gate,
a fold-time identity hold that read as ours, so the firm was never asked. The
rule since: a job stops for a record naming another person (asked of the
firm), the authored cost limits, or an integrity falsifier. Everything else is
decided by rule and named to the firm in Records Reviewed and Limitations, and
no record the map drew entries from is ever disclosed as "not cited".

Each test runs the whole DAG and names the stop it stands against.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from medchron import coverage_decide, dag, decisions, redo_doc
from medchron.proof import holds
from medchron.state import RunState, state_path
from medchron_testkit import FakeSeat, doc_row, make_pdf
from test_decisions_driver import PROSE, _driver, _NoNetwork, _text_msg

INTAKE = "intake scan.pdf"
TRANSCRIBED = (
    "Example Clinic new patient intake questionnaire. Name Alpha Example. Preferred pharmacy on file. "
    "Emergency contact recorded. Insurance card copied at the front desk. Signature on file."
)


def _map(*seen: str) -> str:
    return (
        "## ENTRIES\n01/20/2026\nExample Clinic | Patient Complaints & Limitations\n\n"
        "The patient reports neck pain after the collision rated 6 of 10. (FILE: f1.pdf, p. 2)\n\n"
        "Medical Diagnoses\n\nCervical strain. (FILE: f1.pdf, p. 3)\n\n"
        "## INDEX\n2026-01-20 | Example Clinic | S13.4XXA | f1.pdf\n\n## BILLING-DATES\nnone in this chunk\n\n"
        "## CONFLICTS / REFERENCED-BUT-ABSENT\nnone observed\n\n## FILES-SEEN\n"
        "=== FILE: f1.pdf (fileId f1) === entries: 1\n" + "".join(f"{s}\n" for s in seen)
    )


class _Model(_NoNetwork):
    """The driver suite's scripted model, plus the vision transcription of
    the scanned intake form and a map whose FILES-SEEN block is the test's."""

    def __init__(self, map_text: str) -> None:
        super().__init__()
        self.map_text = map_text

    def create(self, **kw):
        if "transcribe one scanned page" in self._system(kw):
            return _text_msg(TRANSCRIBED)
        return super().create(**kw)

    def stream(self, **kw):
        if "transcribe one scanned page" in self._system(kw):
            self.calls.append(kw)
            from test_decisions_driver import _Stream

            return _Stream(_text_msg(TRANSCRIBED))
        if "INDEPENDENT PRE-DELIVERY REVIEW" in self._system(kw):
            return super().stream(**kw)
        self.calls.append(kw)
        from test_decisions_driver import _Stream

        return _Stream(_text_msg(self.map_text))


def _seat(extra: list[tuple[str, str, bytes]] = ()) -> FakeSeat:
    """The driver suite's matter (a dense record with a blank cover sheet, an
    invoice, a retainer) plus a scanned intake form in an odd folder, and any
    extra (id, name, bytes) the test names."""
    f1 = make_pdf([""] + [f"{PROSE} Page {i} of the record." for i in range(2, 8)])
    f2, f9, f5 = make_pdf(["Example Clinic\nTotal charges $1,800.00"]), make_pdf([PROSE]), make_pdf([""])
    docs = [
        doc_row("f1", "f1.pdf", "fold-med", len(f1)),
        doc_row("f2", "Example Clinic invoice.pdf", "fold-med", len(f2)),
        doc_row("f9", "Retainer signed.pdf", "fold-med", len(f9)),
        doc_row("f5", INTAKE, "fold-misc", len(f5)),
    ]
    blobs = {"f1": f1, "f2": f2, "f9": f9, "f5": f5}
    for fid, name, data in extra:
        docs.append(doc_row(fid, name, "fold-med", len(data)))
        blobs[fid] = data
    folders = [
        {"id": "fold-med", "name": "MEDICAL", "parentId": None, "path": "/MEDICAL"},
        {"id": "fold-misc", "name": "MISC SCANS", "parentId": None, "path": "/MISC SCANS"},
    ]
    return FakeSeat(docs, folders, blobs)


def _state(data_root: Path) -> RunState:
    return RunState.load_or_new(state_path(data_root, "example-matter", "alpha"), slug="x", unit="y")


def test_the_clean_path_delivers_with_no_orphan_or_coverage_hold_and_names_every_exclusion(
    job_dir: Path, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    # Two of Curtis's stops: a scanned file outside the client's medical
    # folder (owned by nobody until vision had read it), and a file the
    # composer read and drew no entry from (`entries: 0`, held for SMD).
    model = _Model(_map(f"=== FILE: {INTAKE} (fileId f5) === entries: 0"))
    o = _driver(job_dir, firm_config_path, pricing_path, client=model, seat_factory=_seat).run()[0]
    assert o.outcome == "delivered" and o.stage is None, o
    sd = data_root / "example-matter"
    # The scan was read by vision and joined the client's unit; no orphan row.
    unit = {r["id"]: r for r in json.loads((sd / "units" / "alpha.json").read_text())}
    assert "f5" in unit and Path(unit["f5"]["text_path"]).is_file()
    orphans = {x["name"]: x["reason"] for x in json.loads((sd / "orphans.json").read_text())["orphans"]}
    assert INTAKE not in orphans and orphans["Retainer signed.pdf"].startswith("engagement document")
    # The entries: 0 file is explained by rule and recorded as the rule's.
    rows = coverage_decide.rows(sd, "alpha")
    assert [(r["name"], r["by"], r["reason"]) for r in rows] == [
        (INTAKE, "rule", "read in full; it contains no dated medical encounter")
    ]
    # decide_orphans ran before the document, and nothing was rebuilt.
    st = _state(data_root)
    assert st.stage("decide_orphans").finished < st.stage("build_doc").finished
    assert st.stage("build_doc").attempts == 1 and not (sd / "runs" / "alpha" / "coverage_redo.json").exists()
    # Both are named to the firm, with the reason, in the delivered document.
    doc = (sd / "runs" / "alpha" / "final-chronology.md").read_text()
    assert '* "Retainer signed.pdf" - engagement document' in doc
    assert f'* "{INTAKE}" - read in full; it contains no dated medical encounter' in doc
    assert "—" not in doc[doc.index("## Records Reviewed and Limitations") :]


def test_a_file_the_composer_drew_entries_from_is_rebuilt_once_then_held_never_disclosed(
    job_dir: Path, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    # LOSS: the composer says it drew two entries from f3 and the document
    # cites none. Never a disclosure (the 08-27 rule); one rebuild from
    # build_doc, the same rebuild SMD ran by hand, then an integrity stop.
    f3 = make_pdf([PROSE, PROSE])
    model = _Model(
        _map(f"=== FILE: {INTAKE} (fileId f5) === entries: 0", "=== FILE: f3.pdf (fileId f3) === entries: 2")
    )
    seat = lambda: _seat([("f3", "f3.pdf", f3)])  # noqa: E731
    o = _driver(job_dir, firm_config_path, pricing_path, client=model, seat_factory=seat).run()[0]
    assert o.outcome == "held" and o.stage == "coverage_gate", o
    assert o.reason.startswith("coverage gate:") and "integrity" in o.reason
    assert holds.classify("medchron", o.reason)[0] == holds.SMD_FINISHING  # ours, never asked of the firm
    sd = data_root / "example-matter"
    st = _state(data_root)
    assert st.stage("build_doc").attempts == 2 and st.stage("strip_apply").attempts == 2
    assert st.stage("classify_scanned").attempts == 1  # the rebuild re-paid nothing
    assert json.loads((sd / "runs" / "alpha" / "coverage_redo.json").read_text())["lost"] == ["f3.pdf"]
    doc = (sd / "runs" / "alpha" / "final-chronology.md").read_text()
    assert '"f3.pdf"' not in doc  # the lost file is not named as a file nothing was drawn from
    assert "rebuilding from build_doc once" in " ".join(o.notes)


def _fake_extract_msg(attachment: bytes) -> types.ModuleType:
    mod = types.ModuleType("extract_msg")

    class _Att:
        longFilename = shortFilename = "records from another office.pdf"
        data = attachment

    class Message:
        def __init__(self, path: str, overrideEncoding: str | None = None) -> None:
            pass

        subject = "FW: records"
        attachments = [_Att()]

        def close(self) -> None:
            pass

    mod.Message = Message  # type: ignore[attr-defined]
    return mod


def test_an_attachment_naming_another_person_is_asked_of_the_firm(
    job_dir: Path, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The fold-time identity hold carried no "identity:" prefix, so it read as
    # SMD's (smd_finishing): the firm was never asked, and identity_decide
    # refused to record the firm's answer.
    # Over the index's 20 KB floor for an attachment (smaller is a signature).
    foreign = make_pdf(["Patient Name: Gamma Other\nDOB: 05/05/1990\nOffice visit for knee pain."] + [PROSE] * 40)
    assert len(foreign) > 20_000
    monkeypatch.setitem(sys.modules, "extract_msg", _fake_extract_msg(foreign))
    msg = b"not really ole2; the fake reader opens it"
    seat = lambda: _seat([("m1", "FW records.msg", msg)])  # noqa: E731
    o = _driver(job_dir, firm_config_path, pricing_path, client=_Model(_map()), seat_factory=seat).run()[0]
    assert o.outcome == "held" and o.stage == "decide_fold", o
    assert o.reason.startswith("identity: 1 emailed attachment(s) name a different person than the client")
    assert holds.classify("medchron", o.reason) == (holds.ANOTHER_PERSON, "identity_held")
    # Named the way the firm can find it; what it says about the other person
    # stays in SMD's detail file.
    assert '"records from another office.pdf" attached to the email "FW records.msg"' in o.reason
    assert "1990" not in o.reason
    detail = json.loads((data_root / "example-matter" / decisions.IDENTITY_HOLDS_FILE).read_text())
    assert any("1990" in f for f in detail[0]["found"])


def test_the_in_run_rebuild_reopens_exactly_what_smd_reopens_by_hand() -> None:
    assert list(dag.REBUILD_FROM_BUILD_DOC) == redo_doc.REDO
    assert dag.BY_NAME["coverage_gate"].rewind == {3: dag.REBUILD_FROM_BUILD_DOC}
    assert dag.ORDER.index("decide_orphans") < dag.ORDER.index("map") < dag.ORDER.index("build_doc")
    assert dag.validate_dag() == []
