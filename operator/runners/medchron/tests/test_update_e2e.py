"""An update into the delivered chronology, end to end through the driver:
the real stages, a fake seat holding a delivered package, and a scripted
model. The act: a Named Administrator's update on a matter with a delivered
chronology yields ONE complete document in the delivered chronology's own
folder, the delivered document retired by id only after the new one is
proven, and nothing else on the matter touched."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

import update_testkit as K
from medchron import driver as driver_mod
from medchron.update import body as B, docx_md
from medchron_testkit import FakeSeat, doc_row, job_yaml, make_pdf

CHRON_FOLDER, MEDICAL, PHOTOS = "fold-chron", "fold-med", "fold-photo"
OLD_DOCX = "Alpha Example - Medical Chronology 09-01-26.docx"
EX1 = "Exhibit 1 - Example Clinic - 12-01-2025 - 01-20-2026 (Medical Records).pdf"
EX1_PAGES = [
    "Example Clinic. Routine visit on 12/01/2025. Blood pressure 120/80 recorded at the visit by the nurse. "
    "The patient had no complaints and was told to return in one year for a routine physical examination.",
    "Example Clinic. Visit on 01/20/2026 after the collision. Cervical strain, pain rated 6 of 10 at intake. "
    "Return to clinic in four weeks for a recheck of the neck and continue home exercises as instructed.",
]
NEW_PAGES = [
    "Example Imaging. Cervical radiographs dated 11/03/2025 showed straightening of the lordosis at 11:40 in the "
    "morning. No fracture was seen and the alignment was otherwise normal on the lateral and oblique views.",
    "Example Imaging. MRI of the cervical spine dated 02/10/2026 showed a disc protrusion measuring 3 mm at the "
    "fifth and sixth level without cord compression. Impression was mild degenerative change at that level.",
]
NEW_TITLE = "Exhibit 2 - Example Imaging records (Medical Records).pdf"
MAP = (
    "## ENTRIES\n"
    "11/03/2025\nExample Imaging | Medical Diagnoses\n\nMedical Diagnoses\n"
    f"Cervical radiographs showed straightening of the lordosis at 11:40. (FILE: {NEW_TITLE}, p. 1)\n\n"
    "02/10/2026\nExample Imaging | Medical Diagnoses\n\nMedical Diagnoses\n"
    f"MRI showed a disc protrusion measuring 3 mm at the fifth and sixth level. (FILE: {NEW_TITLE}, p. 2)\n\n"
    "## INDEX\n2026-02-10 | Example Imaging | M50.20 | " + NEW_TITLE + "\n\n## BILLING-DATES\nnone in this chunk\n\n"
    "## CONFLICTS / REFERENCED-BUT-ABSENT\nnone observed\n\n## FILES-SEEN\n"
    f"=== FILE: {NEW_TITLE} (fileId n1) === entries: 2\n"
)
#: The summarize reply: the ADDED pre-incident entries only (the delivered
#: Prior Medical History is carried as delivered, never rewritten).
PMH_NEW = "Prior Medical History\nCervical radiographs before the incident showed straightening of the lordosis. (Exhibit 2 - p. 1)"


class _U:
    input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens = 10, 5, 0, 0


def _msg(content: list, stop: str):
    return type("M", (), {"content": content, "stop_reason": stop, "usage": _U()})()


def _text(t: str):
    return _msg([type("B", (), {"type": "text", "text": t})()], "end_turn")


class _Stream:
    def __init__(self, m) -> None:
        self.m = m

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.m


class _Model:
    """The compose (a stream), the review (a stream with its own system), the
    added prior history summary, and audit verdicts (a forced tool): all
    SUPPORTED. Anything else fails loudly."""

    def __init__(self) -> None:
        self.messages = self
        self.calls: list[dict] = []

    @staticmethod
    def _system(kw) -> str:
        s = kw.get("system")
        return s[0].get("text", "") if isinstance(s, list) else (s or "")

    def create(self, **kw):
        self.calls.append(kw)
        if kw.get("tool_choice"):
            verdict = {"verdict": "SUPPORTED", "unsupported_assertions": [], "contradictions": [], "note": "ok"}
            return _msg([type("B", (), {"type": "tool_use", "input": verdict})()], "tool_use")
        if "Prior Medical History section" in self._system(kw):
            return _text(PMH_NEW)
        raise AssertionError("unexpected model call shape")

    def stream(self, **kw):
        self.calls.append(kw)
        if "UPDATE REVIEW AND REPAIR" in self._system(kw):
            return _Stream(_text('{"findings": [], "complete": true}'))
        if "INDEPENDENT PRE-DELIVERY REVIEW" in self._system(kw):
            return _Stream(_text("VERDICT: 0 MUST-FIX, 0 SHOULD-FIX, 0 NOTE"))
        return _Stream(_text(MAP))


def _install(root: Path) -> None:
    icd = root / "controls" / "icd"
    icd.mkdir(parents=True, exist_ok=True)
    line = "{:<5} {:<7} {} {:<60} {}\n"
    (icd / "icd10cm_order.txt").write_text(
        line.format("00001", "S134XXA", "1", "Sprain", "Sprain of ligaments of cervical spine, initial encounter")
        + line.format(
            "00002",
            "M5020",
            "1",
            "Other cervical disc displacement",
            "Other cervical disc displacement, unspecified cervical region",
        )
    )
    (icd / "CMS32_DESC_LONG_DX.txt").write_text("7242 Lumbago\n")
    (icd / "VERSION.json").write_text("{}")


def _seat(tmp: Path, delivered_md: str, docx: bytes | None = None) -> FakeSeat:
    docx = docx if docx is not None else K.render(delivered_md, tmp / "delivered-src" / OLD_DOCX).read_bytes()
    ex1, n1 = make_pdf(EX1_PAGES), make_pdf(NEW_PAGES)
    photo = make_pdf(["scene"])
    notes = b"PK firm-authored notes"
    blobs = {"c1": docx, "e1": ex1, "a1": notes, "n1": n1, "n2": ex1, "n3": photo}
    docs = [
        doc_row("c1", OLD_DOCX, CHRON_FOLDER, len(docx)),
        doc_row("e1", EX1, CHRON_FOLDER, len(ex1)),
        doc_row("a1", "Attorney notes.docx", CHRON_FOLDER, len(notes)),
        doc_row("n1", "Example Imaging records.pdf", MEDICAL, len(n1)),
        doc_row("n2", "Example Clinic refiled.pdf", MEDICAL, len(ex1)),
        doc_row("n3", "scene photo.pdf", PHOTOS, len(photo)),
    ]
    folders = [
        {
            "id": CHRON_FOLDER,
            "name": "MEDICAL CHRONOLOGY - Alpha Example 09-01-26",
            "parentId": None,
            "path": "/MEDICAL CHRONOLOGY - Alpha Example 09-01-26",
        },
        {"id": MEDICAL, "name": "MEDICAL", "parentId": None, "path": "/MEDICAL"},
        {"id": PHOTOS, "name": "PHOTOS", "parentId": None, "path": "/PHOTOS"},
    ]
    return FakeSeat(docs, folders, blobs)


@pytest.fixture
def scene(tmp_path: Path, data_root: Path):
    install = tmp_path / "install"
    _install(install)
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    body = yaml.safe_load(job_yaml(data_root, install_root=install))
    body["selection"] = {"include_file_ids": ["n1", "n2", "n3"]}
    body["update"] = {"into": "delivered_chronology"}
    (job_dir / "job.yaml").write_text(yaml.safe_dump(body, sort_keys=False))
    return job_dir, _seat(tmp_path, K.base_md())


def _driver(job_dir: Path, firm: Path, pricing: Path, seat: FakeSeat, model: _Model) -> driver_mod.Driver:
    return driver_mod.Driver(
        job_dir,
        firm_config=str(firm),
        pricing=str(pricing),
        log=lambda *_: None,
        seat_factory=lambda: seat,
        client=model,
    )


def _upload_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    from medchron.stages import update_upload, upload

    monkeypatch.setattr(upload, "READBACK_PAUSE_SECONDS", 0.0)
    monkeypatch.setattr(update_upload.time, "sleep", lambda _s: None)
    monkeypatch.setattr(upload.time, "sleep", lambda _s: None)


def test_an_update_files_one_complete_document_into_the_delivered_folder(
    scene, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    job_dir, seat = scene
    _upload_pause(monkeypatch)
    o = _driver(job_dir, firm_config_path, pricing_path, seat, _Model()).run()[0]
    sd = data_root / "example-matter"
    assert o.outcome == "delivered" and o.stage is None, o
    # Filed INTO the delivered chronology's folder; no new folder.
    assert seat.created == [] and o.folder_id == CHRON_FOLDER
    sent = [s["name"] for s in seat.sent]
    final_ex = "Exhibit 2 - Example Imaging - 11-03-2025 - 02-10-2026 (Medical Records).pdf"
    assert sent[1:] == [final_ex] and sent[0].startswith("Alpha Example - Medical Chronology ")
    assert all(s["folderId"] == CHRON_FOLDER for s in seat.sent)
    # The delivered document retired BY ID after the new one was proven; the
    # delivered exhibit (still cited) and the firm's own file left alone.
    assert seat.deleted == ["c1"]
    assert o.replaced == {"file_id": "c1", "name": OLD_DOCX, "deleted": True, "confirmed_gone": True}
    assert o.proof["readback"] == "confirmed" and len(o.proof["files"]) == 2
    # The photo was named and never pulled; the re-filed copy of a delivered
    # exhibit was pulled and skipped by its bytes.
    assert not any("n3" in m for m in seat.mints)
    assert set(o.covered["covered"]) == {"n1", "n2", "n3"} and o.covered["uncovered"] == []
    # ONE complete document: delivered entries byte-identical, the new visits
    # before and after the incident, in date order.
    upd = (sd / "runs" / "alpha" / "update" / "updated-chronology.md").read_text()
    _, ents = B.entries_of(upd)
    assert ents[1:3] == [K.ENTRY_PRE, K.ENTRY_POST]
    assert ents[0].startswith("11/03/2025\nExample Imaging") and ents[3].startswith("02/10/2026\nExample Imaging")
    assert "(Exhibit 2 - p. 2)" in ents[3] and "Cervical radiographs before the incident" in upd
    assert "| M50.20 | Other cervical disc displacement, unspecified cervical region | 02/10/2026 |" in upd
    assert f"| 2 | {final_ex[len('Exhibit 2 - ') : -4]} |" in upd
    assert "Update of " in upd and "2 medical record document(s) filed on the matter after the earlier" in upd
    assert "1 was a copy and was not read again: Example Clinic refiled.pdf (duplicate copy of Exhibit 1)." in upd
    # The delivered Prior Medical History is carried as delivered; what the
    # new records add sits under it in a dated, audited paragraph.
    assert K.PMH.split("\n", 1)[1] in upd
    # The listing carries no filing date here, so the line carries none (never the run date).
    assert re.search(r"Additional prior history from records filed since the prior chronology\n", upd)
    # The new Diagnostic Highlights row was audited as a claim on its page.
    new_id = next(f["file_id"] for f in o.proof["files"] if f["name"] == sent[0])
    got = tmp_path / "got.docx"
    got.write_bytes(seat.blobs[new_id])
    lines = docx_md.summary_lines(got)
    for ln in (
        "Routine visit; blood pressure 120/80 recorded at the visit. (Exhibit 1 - p. 1)",
        "Return to clinic in four weeks for a recheck of the neck. (Exhibit 1 - p. 2)",
        K.PMH.split("\n", 1)[1],
    ):
        assert ln in lines
    # Only the NEW claims were audited.
    rows = [json.loads(x) for x in (sd / "out" / "alpha" / "audit-results.jsonl").read_text().splitlines()]
    claims = {r["claim"] for r in rows if r.get("kind") == "real"}
    assert not any("Return to clinic in four weeks" in c for c in claims)
    assert any("3 mm" in c for c in claims)
    assert any(c.startswith("Diagnosis code M50.20 recorded on 02/10/2026.") for c in claims)
    assert json.loads((sd / "runs" / "alpha" / "update" / "proofs.json").read_text())["fails"] == []
    # A chronology the seat rendered keeps the exact proof.
    assert json.loads((sd / "runs" / "alpha" / "update" / "update_plan.json").read_text())["renderer"] == "seat"


def test_a_dropped_value_fails_the_update_and_nothing_is_filed(
    scene, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proof 10 as the hard gate before upload: a value composed from a new
    record that does not reach the document fails the job; nothing is sent
    and the delivered chronology is untouched."""
    from medchron.stages import update_finish

    job_dir, seat = scene
    _upload_pause(monkeypatch)
    d = _driver(job_dir, firm_config_path, pricing_path, seat, _Model())
    real = update_finish.run_assemble

    def lossy(sr):
        rc = real(sr)
        p = sr.slug_dir / "runs" / sr.unit.unit / "update" / "updated-chronology.md"
        p.write_text(p.read_text().replace("measuring 3 mm ", ""))
        return rc

    d._runner_override = {"update_assemble": lossy}
    o = d.run()[0]
    assert o.outcome == "failed" and o.stage == "update_proofs", o
    assert seat.sent == [] and seat.deleted == []


def test_a_delivered_chronology_the_seat_cannot_reproduce_holds_before_anything_is_read(
    tmp_path: Path, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    import docx

    install = tmp_path / "install"
    _install(install)
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    body = yaml.safe_load(job_yaml(data_root, install_root=install))
    body.update(selection={"include_file_ids": ["n1"]}, update={"into": "delivered_chronology"})
    (job_dir / "job.yaml").write_text(yaml.safe_dump(body, sort_keys=False))
    seat = _seat(tmp_path, K.base_md())
    # A firm edit after delivery: a paragraph the rendered markdown never had.
    src = tmp_path / "hand.docx"
    src.write_bytes(seat.blobs["c1"])
    d = docx.Document(str(src))
    d.add_paragraph("A note the firm typed into the document.")
    d.save(str(src))
    seat.blobs["c1"] = src.read_bytes()
    seat.docs[0]["size"] = len(seat.blobs["c1"])
    o = _driver(job_dir, firm_config_path, pricing_path, seat, _Model()).run()[0]
    assert o.outcome == "held" and o.stage == "update_base", o
    assert not any("n1" in m for m in seat.mints) and seat.sent == [] and seat.deleted == []
    # The reason reaches the job record and people: it never quotes the document.
    assert "firm typed" not in (o.reason or "") and "Exhibit 1" not in (o.reason or "")


def test_a_named_record_of_another_person_is_left_out_before_any_paid_read(
    scene, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    """The record naming another person is left out by rule (2026-10-24);
    the other two named files are a copy of a delivered exhibit and a photo
    the firm's rules exclude, so nothing new is left: the update ends
    delivered already current (2026-10-25), nothing paid, nothing filed."""
    job_dir, seat = scene
    other = make_pdf(["Patient Name: Gamma Other\nDOB: 05/05/1990\n" + NEW_PAGES[0], NEW_PAGES[1]])
    seat.blobs["n1"] = other
    seat.docs[3]["size"] = len(other)
    model = _Model()
    o = _driver(job_dir, firm_config_path, pricing_path, seat, model).run()[0]
    assert o.outcome == "delivered" and o.stage == "update_select" and o.current is not None, o
    assert o.current["by_reason"]["left_out_by_rule"]["files"] == 1
    assert model.calls == [] and seat.sent == [] and seat.deleted == []


def _job(tmp_path: Path, data_root: Path, ids: list[str]) -> Path:
    install = tmp_path / "install"
    _install(install)
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    body = yaml.safe_load(job_yaml(data_root, install_root=install))
    body.update(selection={"include_file_ids": ids}, update={"into": "delivered_chronology"})
    (job_dir / "job.yaml").write_text(yaml.safe_dump(body, sort_keys=False))
    return job_dir


def test_a_chronology_another_renderer_made_is_updated_and_its_scope_note_carried(
    tmp_path: Path,
    data_root: Path,
    firm_config_path: Path,
    pricing_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The firm's real case: the delivered chronology was rendered on the
    laptop. Before, update_base held (the seat renderer could not reproduce
    its styles). Now its words are proven, the plan records renderer foreign,
    the delivered entries are carried, the note that pre-incident encounters
    were not itemized is in the filed document verbatim, and one complete
    document is filed."""
    _upload_pause(monkeypatch)
    job_dir = _job(tmp_path, data_root, ["n1", "n2", "n3"])
    foreign = K.foreign_docx(tmp_path / "laptop" / OLD_DOCX, note=K.NOTE, labels=True).read_bytes()
    seat = _seat(tmp_path, K.base_md(note=K.NOTE), docx=foreign)
    o = _driver(job_dir, firm_config_path, pricing_path, seat, _Model()).run()[0]
    sd = data_root / "example-matter"
    ud = sd / "runs" / "alpha" / "update"
    assert o.outcome == "delivered" and o.stage is None, o
    plan = json.loads((ud / "update_plan.json").read_text())
    assert plan["renderer"] == "foreign"
    assert json.loads((ud / "base_proof.json").read_text())["renderer"] == "foreign"
    _, ents = B.entries_of((ud / "updated-chronology.md").read_text())
    assert ents[1:3] == [K.ENTRY_PRE, K.ENTRY_POST]
    assert seat.deleted == ["c1"] and seat.sent[0]["name"].startswith("Alpha Example - Medical Chronology ")
    proofs = json.loads((ud / "proofs.json").read_text())
    assert proofs["fails"] == [] and any(
        ln.startswith("2. pre-incident note: carried verbatim") for ln in proofs["lines"]
    )
    new_id = next(f["file_id"] for f in o.proof["files"] if f["name"] == seat.sent[0]["name"])
    got = tmp_path / "got.docx"
    got.write_bytes(seat.blobs[new_id])
    assert K.NOTE in docx_md.summary_lines(got)
    # The laptop document's formatting is carried, not re-rendered plain: the
    # review note's bold label and the title's own bold run read as delivered.
    runs = K.runs_of(got)
    assert [(K.LIMITS_LABEL[0], True), (K.LIMITS_LABEL[1], False)] in runs
    assert runs[0] == [("Alpha Example - Medical Chronology", True)]


def test_a_laptop_chronology_the_firm_edited_still_holds_and_the_reason_quotes_no_chronology_text(
    tmp_path: Path, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    job_dir = _job(tmp_path, data_root, ["n1"])
    typed = "A note the firm typed under the entries."
    foreign = K.foreign_docx(tmp_path / "laptop" / OLD_DOCX, typed=typed).read_bytes()
    seat = _seat(tmp_path, K.base_md(), docx=foreign)
    o = _driver(job_dir, firm_config_path, pricing_path, seat, _Model()).run()[0]
    assert o.outcome == "held" and o.stage == "update_base", o
    assert not any("n1" in m for m in seat.mints) and seat.sent == [] and seat.deleted == []
    reason = o.reason or ""
    assert "differing paragraph" in reason
    for words in (typed, "blood pressure", "Cervical strain", "Example Clinic", "Prior Medical History"):
        assert words not in reason, words
    proof = json.loads((data_root / "example-matter" / "runs" / "alpha" / "update" / "base_proof.json").read_text())
    assert proof["proven"] is False and proof["renderer"] == "foreign" and proof["differing_paragraphs"] >= 1


def test_a_firm_numbered_exhibit_in_the_folder_takes_its_number_and_the_list_has_no_gap(
    scene, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Change 3. The firm filed its own "Exhibit 2- ..." in the chronology
    folder after delivery. Before, the update could not see it and numbered
    its own first new exhibit 2 too: two Exhibit 2 files, one list row. Now
    the new exhibit is 3, the firm's file is listed under 2 by its name, and
    nothing of the firm's is sent again."""
    job_dir, seat = scene
    _upload_pause(monkeypatch)
    firm = make_pdf(["Example Radiology report filed by the firm. Lumbar views were unremarkable in every plane."])
    seat.blobs["f2"] = firm
    seat.docs.append(doc_row("f2", "Exhibit 2- Example Radiology.pdf", CHRON_FOLDER, len(firm)))
    monkeypatch.setitem(globals(), "PMH_NEW", PMH_NEW.replace("Exhibit 2", "Exhibit 3"))
    o = _driver(job_dir, firm_config_path, pricing_path, seat, _Model()).run()[0]
    assert o.outcome == "delivered" and o.stage is None, o
    sent = [s["name"] for s in seat.sent]
    assert sent[1:] == ["Exhibit 3 - Example Imaging - 11-03-2025 - 02-10-2026 (Medical Records).pdf"]
    upd = (data_root / "example-matter" / "runs" / "alpha" / "update" / "updated-chronology.md").read_text()
    listed = B.table_rows(upd.split("## Exhibit List", 1)[1].split("## Records Reviewed", 1)[0])
    assert listed[1:] == [
        "| 1 | Example Clinic - 12-01-2025 - 01-20-2026 (Medical Records) |",
        "| 2 | Exhibit 2- Example Radiology.pdf |",
        "| 3 | Example Imaging - 11-03-2025 - 02-10-2026 (Medical Records) |",
    ]
    assert "(Exhibit 3 - p. 2)" in upd and seat.deleted == ["c1"]


def test_a_new_record_identical_to_a_firm_numbered_file_reuses_its_number_and_is_not_filed_again(
    scene, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Change 3, the reuse case. The firm filed the imaging records itself as
    "Exhibit 2- Example Radiology.pdf"; the update is asked to read the same
    bytes. Before, they became a second Exhibit 2 and were uploaded again.
    Now the record is read and cited as Exhibit 2, the list names the firm's
    file, and only the chronology is filed."""
    job_dir, seat = scene
    _upload_pause(monkeypatch)
    seat.blobs["f2"] = seat.blobs["n1"]
    seat.docs.append(doc_row("f2", "Exhibit 2- Example Radiology.pdf", CHRON_FOLDER, len(seat.blobs["n1"])))
    o = _driver(job_dir, firm_config_path, pricing_path, seat, _Model()).run()[0]
    assert o.outcome == "delivered" and o.stage is None, o
    assert [s["name"] for s in seat.sent] == [seat.sent[0]["name"]]  # the chronology only
    assert seat.sent[0]["name"].startswith("Alpha Example - Medical Chronology ")
    upd = (data_root / "example-matter" / "runs" / "alpha" / "update" / "updated-chronology.md").read_text()
    listed = B.table_rows(upd.split("## Exhibit List", 1)[1].split("## Records Reviewed", 1)[0])
    assert listed[1:] == [
        "| 1 | Example Clinic - 12-01-2025 - 01-20-2026 (Medical Records) |",
        "| 2 | Exhibit 2- Example Radiology.pdf |",
    ]
    assert "(Exhibit 2 - p. 2)" in upd and "Exhibit 3" not in upd and seat.deleted == ["c1"]


def test_a_refiled_copy_of_a_new_record_is_not_read_twice_and_is_named_in_records_reviewed(
    scene, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Change 1 wired, and change 7. A second new document repeats the first
    one's pages under another fax header. Before, both were composed (the
    check looked only at the delivered exhibits). Now its pages are excluded
    as repeats, nothing cites it, and Records Reviewed names it with its page
    count."""
    job_dir, seat = scene
    _upload_pause(monkeypatch)
    copy = make_pdf(["FAX 555-0177 06/09/2026 16:40 PAGE 7 OF 30\n" + p for p in NEW_PAGES])
    seat.blobs["n4"] = copy
    seat.docs.append(doc_row("n4", "Imaging refax.pdf", MEDICAL, len(copy)))
    body = yaml.safe_load((job_dir / "job.yaml").read_text())
    body["selection"]["include_file_ids"].append("n4")
    (job_dir / "job.yaml").write_text(yaml.safe_dump(body, sort_keys=False))
    o = _driver(job_dir, firm_config_path, pricing_path, seat, _Model()).run()[0]
    assert o.outcome == "delivered" and o.stage is None, o
    ud = data_root / "example-matter" / "runs" / "alpha" / "update"
    raw = json.loads((ud / "selection.json").read_text())
    sel = {r["source_name"]: r for r in raw["exhibits"]}
    # Parity O: a copy every page of which repeats a new record is not an
    # exhibit at all; it is cited in the record it copies (selection "subsets").
    assert "Imaging refax.pdf" not in sel
    (sub,) = [s for s in raw["subsets"] if s["source_name"] == "Imaging refax.pdf"]
    assert sub["pages"] == 2 and sub["matched"] == 2 and sub["subset_of"]["id"] == "n1"
    upd = (ud / "updated-chronology.md").read_text()
    assert f"Imaging refax.pdf (re-filed pages of Exhibit {sub['subset_of']['exhibit']})" in upd
