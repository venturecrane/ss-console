"""A chronology runs from one start to delivery without an SMD-only hold, end to
end through the real driver, a fake seat and a scripted model.

WHY (2026-10-23). The Curtis chronology (A&P matter 200530) stopped eight
times in 22 hours; seven of the stops were holds only SMD could clear, each
cleared by a merge in the middle of the job: a residue file at the orphans
decision, a file the composer read and drew nothing from at the coverage gate,
a fold-time identity hold that read as ours, so the firm was never asked. The
rule since 2026-10-24 (the Captain): a job stops only for the authored cost
limits or an integrity falsifier, and SMD finishes it; the firm hears about a
chronology only when it is delivered. Everything else, a record that names a
person other than the client included, is decided by rule and named to the
firm in Records Reviewed and Limitations, and no record the map drew entries
from is ever disclosed as "not cited".

Each test runs the whole DAG and names the stop it stands against.
"""

from __future__ import annotations

import functools
import json
import sys
import types
from pathlib import Path

import pytest

from medchron import coverage_decide, dag, decisions
from medchron.gates import foreign_identity as FI
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


def test_a_file_the_composer_drew_entries_from_is_held_at_once_never_disclosed(
    job_dir: Path, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    # LOSS: the composer says it drew two entries from f3, no filter rule
    # removed them, and the document cites none. Never a disclosure (the 08-27
    # rule); an integrity stop. Since 2026-10-24 there is no rebuild first: it
    # rebuilt from build_doc, which could not change what the gate reads.
    f3 = make_pdf([PROSE, PROSE])
    model = _Model(
        _map(f"=== FILE: {INTAKE} (fileId f5) === entries: 0", "=== FILE: f3.pdf (fileId f3) === entries: 2")
    )
    seat = functools.partial(_seat, [("f3", "f3.pdf", f3)])
    o = _driver(job_dir, firm_config_path, pricing_path, client=model, seat_factory=seat).run()[0]
    assert o.outcome == "held" and o.stage == "coverage_gate", o
    assert o.reason.startswith("coverage gate:") and "integrity" in o.reason
    assert holds.classify("medchron", o.reason)[0] == holds.SMD_FINISHING  # ours, never asked of the firm
    sd = data_root / "example-matter"
    st = _state(data_root)
    assert st.stage("build_doc").attempts == 1 and st.stage("strip_apply").attempts == 1
    assert json.loads((sd / "runs" / "alpha" / "coverage_unexplained.json").read_text()) == ["f3.pdf"]
    doc = (sd / "runs" / "alpha" / "final-chronology.md").read_text()
    assert '"f3.pdf"' not in doc  # the lost file is not named as a file nothing was drawn from


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


INTAKE_SEEN = f"=== FILE: {INTAKE} (fileId f5) === entries: 0"
RULE_WORDS = "names a person other than the client, so it was left out of this chronology"


def _limitations(sd: Path) -> str:
    doc = (sd / "runs" / "alpha" / "final-chronology.md").read_text()
    return doc[doc.index("## Records Reviewed and Limitations") :]


def test_an_attachment_naming_another_person_is_left_out_by_rule_and_the_job_delivers(
    job_dir: Path, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 2026-10-24: the fold-time identity check (the attachment's own text
    # layer) used to hold the job and ask the firm whose the record was. It is
    # now withheld by rule and the job delivers, naming the file and why.
    # FALSIFIER: restore the fold's hold and this stops at decide_fold.
    # Over the index's 20 KB floor for an attachment (smaller is a signature).
    foreign = make_pdf(["Patient Name: Gamma Other\nDOB: 05/05/1990\nOffice visit for knee pain."] + [PROSE] * 40)
    assert len(foreign) > 20_000
    monkeypatch.setitem(sys.modules, "extract_msg", _fake_extract_msg(foreign))
    msg = b"not really ole2; the fake reader opens it"
    seat = functools.partial(_seat, [("m1", "FW records.msg", msg)])
    o = _driver(job_dir, firm_config_path, pricing_path, client=_Model(_map(INTAKE_SEEN)), seat_factory=seat).run()[0]
    assert o.outcome == "delivered" and o.stage is None, o
    sd = data_root / "example-matter"
    assert FI.withheld_reason(sd, json.loads((sd / decisions.IDENTITY_HOLDS_FILE).read_text())[0]["id"]) == RULE_WORDS
    # Named the way the firm can find it, with the rule's reason; what it says
    # about the other person stays in SMD's detail file.
    lim = _limitations(sd)
    assert f'* "records from another office.pdf" - {RULE_WORDS}' in lim
    assert "1990" not in lim and "Gamma" not in lim and "—" not in lim
    # The email paragraph no longer says the left-out attachment was read.
    assert "1 were new to it; it was left out, for the reason given below." in lim
    detail = json.loads((sd / decisions.IDENTITY_HOLDS_FILE).read_text())
    assert any("1990" in f for f in detail[0]["found"])


def _scan_pdf() -> bytes:
    """A PDF with no text layer: one page carrying a noise image (incompressible,
    so the file clears the email index's 20 KB attachment floor)."""
    import os

    import pymupdf

    pix = pymupdf.Pixmap(pymupdf.csRGB, 100, 100, os.urandom(100 * 100 * 3), 0)
    doc = pymupdf.open()
    doc.new_page().insert_image(pymupdf.Rect(72, 72, 372, 372), stream=pix.tobytes("png"))
    data = doc.tobytes()
    doc.close()
    return data


FOREIGN_SCAN = "Patient Name: Gamma Other. DOB: 05/05/1990. Office visit for knee pain."


class _ForeignScanModel(_Model):
    """Every scanned page transcribes as another person's record: the email
    attachment below has no text layer, so only the identity stage, after
    vision, can see whose it is."""

    def create(self, **kw):
        if "transcribe one scanned page" in self._system(kw):
            return _text_msg(FOREIGN_SCAN)
        return super().create(**kw)

    def stream(self, **kw):
        if "transcribe one scanned page" in self._system(kw):
            self.calls.append(kw)
            from test_decisions_driver import _Stream

            return _Stream(_text_msg(FOREIGN_SCAN))
        return super().stream(**kw)


def test_a_scanned_attachment_the_identity_stage_finds_is_left_out_by_rule_and_the_job_delivers(
    job_dir: Path, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The identity stage (after vision) used to hold with exit 2. It now
    # withholds by rule, takes the attachment out of the unit build_units
    # wrote, and the job delivers. FALSIFIER: restore the identity stage's
    # exit 2 and this stops at identity.
    scan = _scan_pdf()
    assert len(scan) > 20_000  # over the index's floor for an attachment
    monkeypatch.setitem(sys.modules, "extract_msg", _fake_extract_msg(scan))
    seat = functools.partial(_seat, [("m1", "FW records.msg", b"not really ole2; the fake reader opens it")])
    o = _driver(
        job_dir, firm_config_path, pricing_path, client=_ForeignScanModel(_map(INTAKE_SEEN)), seat_factory=seat
    ).run()[0]
    assert o.outcome == "delivered" and o.stage is None, o
    sd = data_root / "example-matter"
    held = json.loads((sd / "identity_holds.json").read_text())
    assert len(held) == 1 and FI.withheld_reason(sd, held[0]["id"]) == RULE_WORDS
    unit_ids = [r["id"] for r in json.loads((sd / "units" / "alpha.json").read_text())]
    assert held[0]["id"] not in unit_ids
    lim = _limitations(sd)
    assert f'* "records from another office.pdf" - {RULE_WORDS}' in lim  # shown once, never "x.pdf.pdf"
    assert "1990" not in lim and "Gamma" not in lim
    assert "it was left out, for the reason given below." in lim


class _JointModel(_Model):
    """Each unit's map cites that unit's own record."""

    def stream(self, **kw):
        sysm = self._system(kw)
        if "transcribe one scanned page" in sysm or "INDEPENDENT PRE-DELIVERY REVIEW" in sysm:
            return super().stream(**kw)
        self.calls.append(kw)
        from test_decisions_driver import _Stream

        text = json.dumps(kw.get("messages") or [])
        body = self.map_text.replace("f1.pdf", "g1.pdf").replace("fileId f1", "fileId g1")
        return _Stream(_text_msg(body if "g1.pdf" in text else self.map_text))


def _joint_seat() -> FakeSeat:
    f1 = make_pdf([""] + [f"{PROSE} Page {i} of the record." for i in range(2, 8)])
    g1 = make_pdf([""] + [f"{PROSE} Page {i} of the record." for i in range(2, 8)])
    u1 = make_pdf([PROSE, PROSE])
    docs = [
        doc_row("f1", "f1.pdf", "fold-a", len(f1)),
        doc_row("g1", "g1.pdf", "fold-b", len(g1)),
        doc_row("u1", "Example Health Records 6.22.26.pdf", "fold-s", len(u1)),
    ]
    folders = [
        {"id": "fold-a", "name": "Alpha_Example", "parentId": None, "path": "/Alpha_Example"},
        {"id": "fold-b", "name": "Beta_Example", "parentId": None, "path": "/Beta_Example"},
        {"id": "fold-s", "name": "Emails", "parentId": None, "path": "/Emails"},
    ]
    return FakeSeat(docs, folders, {"f1": f1, "g1": g1, "u1": u1})


def test_a_joint_matters_file_no_client_owns_is_left_out_by_rule_and_the_job_delivers(
    tmp_path: Path, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    # build_units used to hold a joint matter's unowned file and ask the firm
    # whose it was. It is now withheld by rule (it could not be placed with
    # one client), and every unit delivers, naming it.
    # FALSIFIER: restore build_units' IDENTITY_HELD exit and this stops there.
    from medchron_testkit import job_yaml

    jd = tmp_path / "joint-job"
    jd.mkdir()
    (jd / "job.yaml").write_text(job_yaml(data_root, joint=True))
    outs = _driver(jd, firm_config_path, pricing_path, client=_JointModel(_map()), seat_factory=_joint_seat).run()
    assert [o.outcome for o in outs] == ["delivered", "delivered"], outs
    sd = data_root / "example-matter"
    words = "could not be placed with one client on this matter, so it was left out of this chronology"
    assert FI.withheld_reason(sd, "u1") == words
    for unit in ("alpha", "beta"):
        doc = (sd / "runs" / unit / "final-chronology.md").read_text()
        lim = doc[doc.index("## Records Reviewed and Limitations") :]
        assert f'* "Example Health Records 6.22.26.pdf" - {words}' in lim, lim


def test_the_coverage_gate_holds_at_once_and_never_asks_for_a_no_op_rebuild() -> None:
    # 2026-10-24: the rebuild from build_doc could not change the gate's answer
    # (it reads entries_scoped_final.md and page_map.json, both written before
    # build_doc), so it was removed rather than left to look like a remedy.
    assert dag.BY_NAME["coverage_gate"].rewind == {}
    assert dag.ORDER.index("decide_orphans") < dag.ORDER.index("map") < dag.ORDER.index("build_doc")
    assert dag.validate_dag() == []
