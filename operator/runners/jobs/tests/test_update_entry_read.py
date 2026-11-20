"""The UPDATE lane's audit is the entry read (2026-11-20, ``stages/entry_audit.py``,
``stages/entry_audit_update.py``): the per-claim audit and the update review
are gone, and code acts on the read's verdict. A line the read finds wrong
and cannot correct holds the job; a corrected line files; a diagnosis claim
found wrong gets no table row; a provider-name claim is read against the
pages of the entries naming that person. Through the real StageRun, Doorway,
planner, parsers and DAG rows, with a scripted SDK client. Every name, date,
code and value is invented."""

from __future__ import annotations

import json
from dataclasses import replace as dc_replace
from pathlib import Path
from typing import Any

import pytest

from jobs.medchron import dag
from jobs.medchron.gates import claim_audit
from jobs.medchron.job import UPDATE_INTO, UpdateSpec
from jobs.medchron.update import body as B, entry_confirm as EC, entry_read as ER, icd, proofs as P
from jobs.stages import entry_audit as EA, entry_audit_io as IO, entry_audit_update as EU
from test_audit import _exhibit_set, _sr
from test_entry_audit import ALL_OK, FILLER, PAGES, VALUE, YES, Client

E1 = (
    "01/05/2025\nExample Clinic | Medical Diagnoses\n\nMedical Diagnoses\n"
    "The pulse was 72 at triage during the visit. (Exhibit 1 - p. 2)\n\n"
    "All Other Information\nTherapy twice weekly was planned at the visit. (Exhibit 1 - p. 3)"
)
E2 = (
    "02/10/2025\nSample Therapy | All Other Information\n\nAll Other Information\n"
    "Jane Exampel, MD reviewed the home exercise plan with the patient. (Exhibit 1 - p. 5)"
)
#: Enough sentences that one correction stays under the edit tripwire.
E3 = f"03/01/2025\nSample Therapy | All Other Information\n\nAll Other Information\n{FILLER} (Exhibit 1 - p. 6)"
DX = "Diagnosis code M54.50 recorded on 01/05/2025. (Exhibit 1 - p. 2)"
NAME = "Provider named on the record: Jane Exampel, MD. (Exhibit 1 - p. 5)"
#: The audit document ``update_collect`` writes: the new entries only, ahead of
#: them the diagnosis and provider-name claims; never a delivered entry.
DOC = (
    "Alpha Example - Medical Chronology\n\n## Treatment Timeline\n\n## Diagnostic Highlights\n\n"
    f"## Medical Chronology\n\n{DX}\n\n{NAME}\n\n{E1}\n\n{E2}\n\n{E3}\n\n## Exhibit List\n"
)
#: A delivered entry: in base.md, never in the audit document.
DELIVERED = "12/01/2024\nExample Clinic | Medical Diagnoses\n\nMedical Diagnoses\nIntake visit. (Exhibit 1 - p. 1)"
UNSUPPORTED = {**VALUE, "class": "unsupported", "problem": "the page does not say this"}
NOT_SUPPORTED = {**YES, "corrected_supported": False}


def _reply(*items: dict[str, Any]) -> str:
    return json.dumps({"lines": ALL_OK, "findings": list(items), "complete": True})


def _on(unit: str, *items: dict[str, Any]):
    """A read that finds `items` in the unit headed `unit` and nothing elsewhere."""
    return lambda text: _reply(*items) if f" {unit}:" in text or f" {unit} (" in text else _reply()


@pytest.fixture
def scene(job_dir: Path, firm_config_path: Path, data_root: Path):
    def make(client: Client | None, doc: str = DOC, update: bool = True, fresh: bool = True):
        log: list[str] = []
        sr = _sr(job_dir, firm_config_path, data_root, client, log)
        if update:
            sr.job = dc_replace(sr.job, update=UpdateSpec(into=UPDATE_INTO))
        rd = sr.slug_dir / "runs" / "alpha"
        if fresh:
            _exhibit_set(sr, PAGES)
            rd.mkdir(parents=True, exist_ok=True)
            (rd / "final-chronology.md").write_text(doc, encoding="utf-8")
        sr.doorway.sleep = lambda _s: None
        return sr, rd / "final-chronology.md", log

    return make


def _record(sr: Any) -> dict[str, Any]:
    return json.loads((sr.slug_dir / "runs" / "alpha" / IO.RECORD).read_text(encoding="utf-8"))


def _forget(sr: Any) -> None:
    """A new scene in the same job dir: no answer of the last one is reused."""
    import shutil

    shutil.rmtree(sr.slug_dir / "runs" / "alpha" / IO.DIR, ignore_errors=True)


def _audit_row() -> dag.Stage:
    return dag.by_name(update=True)["audit"]


# ---- code acts on the verdict --------------------------------------------------------
def test_an_unsupported_line_with_no_correction_holds_and_nothing_is_written(scene) -> None:
    sr, doc, log = scene(Client(_on("N1", UNSUPPORTED), NOT_SUPPORTED))
    rc = EA.run(sr)
    assert rc == EA.EXIT_HELD == 1
    outcome, reason = _audit_row().exit_map[rc]
    assert outcome == dag.HELD and reason.startswith("audit coverage:")  # the broker resumes by this prefix
    assert "a line found wrong was not corrected" in reason
    assert doc.read_text(encoding="utf-8") == DOC  # held with the document untouched
    rec = _record(sr)
    assert rec["unresolved"] == 1 and rec["unresolved_by_class"] == {"unsupported": 1}
    assert rec["held"] == "1 line(s) found wrong by two readers with no correction applied (unsupported 1)"
    assert "pulse" not in json.dumps(rec).lower()  # counts by class, never text
    assert not claim_audit.check(sr.slug_dir, "alpha", update=True)[0]


def test_a_corrected_line_files_and_its_removed_number_excuses_proof_10(scene) -> None:
    sr, doc, log = scene(Client(_on("N1", VALUE)))
    assert EA.run(sr) == 0, log
    out = doc.read_text(encoding="utf-8")
    assert "The pulse was 78 at triage during the visit. (Exhibit 1 - p. 2)" in out
    rec = _record(sr)
    assert rec["unresolved"] == 0 and rec["applied"] == 1
    assert EU.removed(sr.slug_dir, "alpha") == {"72"}  # numbers only
    assert claim_audit.check(sr.slug_dir, "alpha", update=True)[0]


def test_a_clean_document_files_as_written(scene) -> None:
    sr, doc, log = scene(Client(lambda _t: _reply()))
    assert EA.run(sr) == 0, log
    assert doc.read_text(encoding="utf-8") == DOC
    assert _record(sr)["unresolved"] == 0 and EU.removed(sr.slug_dir, "alpha") == set()


def test_a_line_the_second_reader_finds_not_wrong_is_settled(scene) -> None:
    sr, doc, log = scene(Client(_on("N1", UNSUPPORTED), {**YES, "original_wrong": False}))
    assert EA.run(sr) == 0, log
    assert _record(sr)["recorded_by_reason"] == {EC.NOT_WRONG: 1} and _record(sr)["unresolved"] == 0


def test_another_persons_line_holds_only_when_the_second_reader_confirms_it(scene) -> None:
    other = {**VALUE, "class": "another_person", "problem": "this visit is another patient's"}
    sr, doc, log = scene(Client(_on("N1", other)))  # the confirm answers wrong
    assert EA.run(sr) == EA.EXIT_HELD
    assert _record(sr)["unresolved_by_class"] == {"another_person": 1} and doc.read_text(encoding="utf-8") == DOC
    _forget(sr)
    sr2, doc2, log2 = scene(Client(_on("N1", other), {**YES, "original_wrong": False}))
    assert EA.run(sr2) == 0, log2  # one reader's word alone never holds
    assert _record(sr2)["confirmed_not_wrong_by_class"] == {"another_person": 1}


def test_falsifier_the_full_chronology_records_the_same_line_and_files(scene) -> None:
    """The hold is the update lane's: the full chronology's behaviour is unchanged."""
    sr, doc, log = scene(Client(_on("N1", UNSUPPORTED), NOT_SUPPORTED), update=False)
    assert EA.run(sr) == 0, log
    rec = _record(sr)
    assert "unresolved" not in rec and rec["recorded_by_reason"] == {EC.NOT_SUPPORTED: 1}
    assert not EU.path(sr.slug_dir, "alpha").exists()


# ---- the diagnosis claim -------------------------------------------------------------
DX_WRONG = {"line": "S1", "class": "value", "exhibit": 1, "page": 2, "quote": "M54.50", "action": "replace",
            "replacement": "M54.59", "problem": "the page records another code"}  # fmt: skip


def test_a_diagnosis_claim_found_wrong_does_not_hold_and_gets_no_table_row(scene) -> None:
    sr, doc, log = scene(Client(_on("PRE", DX_WRONG)))
    assert EA.run(sr) == 0, log
    rec = _record(sr)
    assert rec["unresolved"] == 0 and rec["diagnosis_rows_withheld"] == 1
    md = doc.read_text(encoding="utf-8")
    assert md == DOC  # the read never edits the claim
    stripped, n = EU.strip_withheld(md, EU.withheld(sr.slug_dir, "alpha"))
    assert n == 1 and DX not in stripped and NAME in stripped
    assert B.entries_of(stripped)[1] == B.entries_of(md)[1]  # every entry byte for byte

    def resolve(code: str, _on: str, _text: str) -> tuple[str, str | None, str]:
        return code, "Low back pain", "table"

    def rows(text: str) -> list[str]:
        return icd.rows([], icd.split_claims(B.entries_of(text)[0])[1], B.entries_of(text)[1], resolve)[0]

    assert any("M54.50" in r for r in rows(md))  # unflagged, the claim is a row
    assert not any("M54.50" in r for r in rows(stripped))  # flagged and not corrected: no row


def test_falsifier_a_clean_diagnosis_claim_keeps_its_row(scene) -> None:
    sr, doc, log = scene(Client(lambda _t: _reply()))
    assert EA.run(sr) == 0, log
    assert EU.withheld(sr.slug_dir, "alpha") == set()
    assert EU.strip_withheld(doc.read_text(encoding="utf-8"), set()) == (DOC, 0)


# ---- the provider-name claim ---------------------------------------------------------
def _units(doc: str = DOC, neighbours: bool = True) -> list[ER.Unit]:
    return ER.plan(doc, {1}, {"Medical Diagnoses", "All Other Information"}, lambda _x: 6, neighbours=neighbours)


def test_a_provider_name_claim_is_its_own_unit_shown_the_pages_of_the_entries_naming_that_person() -> None:
    units = {u.key: u for u in _units()}
    assert set(units) == {"PRE", "PN1", "N1", "N2", "N3"}
    pn = units["PN1"]
    assert pn.record_only and pn.text == NAME and pn.cited == [(1, 5)]  # E2 names the person, on p. 5
    assert units["PRE"].text == DX  # the name claim left the prior-history block
    # A full chronology carries no name claim: its units are as before.
    full = DOC.replace(f"{NAME}\n\n", "")
    assert [u.key for u in _units(full)] == ["PRE", "N1", "N2", "N3"] and _units(full)[0].text == DX


SPELLED = {"line": "S1", "class": "provider", "exhibit": 1, "page": 5, "quote": "Exampel", "action": "replace",
           "replacement": "Example", "problem": "the page spells the name otherwise"}  # fmt: skip


def _delivered(sr: Any, names: list[str]) -> None:
    ud = sr.slug_dir / "runs" / "alpha" / "update"
    ud.mkdir(parents=True, exist_ok=True)
    rows = [{"composed": "Jane Exampel, MD", "likely_same_person": names}]
    (ud / "compose_report.json").write_text(json.dumps({"provider_spelling": rows}))


def test_a_name_claim_is_record_only_and_never_holds_or_asks_a_second_reader(scene) -> None:
    client = Client(_on("PN1", SPELLED))
    sr, doc, log = scene(client)
    _delivered(sr, ["Jane Exemplar, MD"])  # the read's spelling is not the delivered one
    assert EA.run(sr) == 0, log
    assert client.calls.count("verify") == 0 and EU.spellings(sr.slug_dir, "alpha") == {}


def test_a_name_spelled_as_delivered_is_applied_through_respell_at_assemble(scene) -> None:
    from jobs.medchron.update import providers as PV

    sr, doc, log = scene(Client(_on("PN1", SPELLED)))
    _delivered(sr, ["Jane Example, MD"])
    assert EA.run(sr) == 0, log
    md, n = EU.respell_claims(doc.read_text(encoding="utf-8"), EU.spellings(sr.slug_dir, "alpha"))
    assert n == 1 and "Provider named on the record: Jane Example, MD. (Exhibit 1 - p. 5)" in md
    names = PV.split_claims(B.entries_of(md)[0])[1]
    ents, change = PV.respell([E2], names, {"Exhibit 1 - p. 5": "Jane Exampel, MD"})
    assert change == {"Jane Exampel, MD": "Jane Example, MD"} and "Jane Example, MD reviewed" in ents[0]


def test_a_name_unit_is_capped_at_one_part() -> None:
    many = "\n\n".join(
        f"0{(k % 9) + 1}/1{k % 10}/2025\nSample Therapy | All Other Information\n\nAll Other Information\n"
        f"Jane Exampel, MD saw the patient. (Exhibit 1 - p. {k + 1})"
        for k in range(40)
    )
    doc = DOC.replace(E3, many)
    units = ER.plan(doc, {1}, {"All Other Information", "Medical Diagnoses"}, lambda _x: 60)
    pn = [u for u in units if u.anchor == "PN1"]
    assert len(pn) == 1 and pn[0].parts == 1 and len(pn[0].pages) <= ER.MAX_SHOWN


def test_a_provider_finding_on_an_entry_holds_only_once_confirmed(scene) -> None:
    prov = {"line": "P", "class": "provider", "exhibit": 1, "page": 2, "quote": "Example Clinic", "action": "none"}
    sr, doc, log = scene(Client(_on("N1", prov), {**YES, "original_wrong": False}))
    assert EA.run(sr) == 0, log
    assert _record(sr)["confirmed_not_wrong_by_class"] == {"provider": 1}
    _forget(sr)
    sr2, _d, log2 = scene(Client(_on("N1", prov)))
    assert EA.run(sr2) == EA.EXIT_HELD and _record(sr2)["unresolved_by_class"] == {"provider": 1}


# ---- delivered entries are never read ------------------------------------------------
def test_no_delivered_entry_is_ever_shown_or_edited(scene) -> None:
    """The document the read plans from is the one collect writes
    (``update/collect.audit_document``) over a delivered chronology: the
    delivered entry is in no request and in no unit, and the base is untouched."""
    from jobs.medchron.update import collect as C

    base = (
        "Alpha Example - Medical Chronology\n\n## Treatment Timeline\n\n## Diagnostic Highlights\n\n"
        f"## Medical Chronology\n\n{DELIVERED}\n\n## Exhibit List\n\n| Exhibit | Title |\n|---|---|\n"
        "| 1 | Exhibit 1 - Example Clinic |\n"
    )
    built = C.audit_document(base, [E1, E2, E3], [], icd_claims=[DX, NAME])
    client = Client(_on("N1", VALUE))
    sr, doc, log = scene(client, doc=built)
    assert EA.run(sr) == 0, log
    assert client.texts and not any("Intake visit" in t for t in client.texts)
    after = doc.read_text(encoding="utf-8")
    assert "pulse was 78" in after and DELIVERED not in after
    assert not any("Intake visit" in u.text for u in _units(after))
    assert "Intake visit" in base and base.count(DELIVERED) == 1  # the delivered document is never the read's


# ---- a resume of an update the per-claim audit ran -----------------------------------
def test_an_update_with_old_audit_rows_and_no_read_record_reads_again(tmp_path: Path) -> None:
    from jobs.medchron.state import RunState

    st = RunState.load_or_new(tmp_path / "state.json", slug="example-matter", unit="alpha")
    st.finish("audit", status="done", exit_code=0, dollars=0.0, pages=0)
    out = tmp_path / "out" / "alpha"
    out.mkdir(parents=True)
    (out / "audit-results.jsonl").write_text(json.dumps({"key": "k", "kind": "real", "verdict": "SUPPORTED"}) + "\n")
    assert claim_audit.stale_update_audit(tmp_path, "alpha", st)
    assert not claim_audit.check(tmp_path, "alpha", update=True)[0]  # never the old coverage rule
    (tmp_path / "runs" / "alpha").mkdir(parents=True)
    (tmp_path / "runs" / "alpha" / IO.RECORD).write_text(json.dumps({"complete": True, "held": "", "rows": []}))
    assert not claim_audit.stale_update_audit(tmp_path, "alpha", st)  # the read ran: nothing to redo
    (tmp_path / "runs" / "alpha" / IO.RECORD).unlink()
    st.finish("upload", status="done", exit_code=0, dollars=0.0, pages=0)
    assert not claim_audit.stale_update_audit(tmp_path, "alpha", st)  # already filed: never rebuilt


# ---- proof 4 ---------------------------------------------------------------------------
def _proofs(tmp_path: Path, rec: dict[str, Any] | None) -> P.Result:
    rd = tmp_path / "runs" / "alpha"
    rd.mkdir(parents=True, exist_ok=True)
    if rec is not None:
        (rd / IO.RECORD).write_text(json.dumps(rec))
    gate, read = claim_audit.check(tmp_path, "alpha", update=True)
    x = P.Inputs([], [], DOC, DOC, [], set(), gate_passed=gate, entry_read=read)
    return P.run(x)


def test_proof_4_fails_an_update_with_a_wrong_line_left_uncorrected(tmp_path: Path) -> None:
    rows = [{"key": "N1", "status": "read"}]
    clean = {"complete": True, "held": "", "rows": rows, "unresolved": 0}
    assert "4" not in _proofs(tmp_path, clean).fails
    left = _proofs(tmp_path, {**clean, "unresolved": 1})
    assert "4" in left.fails and any(ln.startswith("4. ") and "not corrected 1" in ln for ln in left.lines)
    assert "4" in _proofs(tmp_path / "none", None).fails  # no read record: the update cannot file
    assert not {"3", "5"} & {ln.split(".")[0] for ln in left.lines}  # went with the per-claim audit


# ---- pages shown ------------------------------------------------------------------------
def test_pages_shown_default_to_neighbours_and_the_update_switch_shows_cited_only(monkeypatch) -> None:
    n1 = next(u for u in _units() if u.key == "N1")
    assert n1.pages == [(1, p) for p in (1, 2, 3, 4)]  # today's: cited 2 and 3, one either side
    n1c = next(u for u in _units(neighbours=False) if u.key == "N1")
    assert n1c.pages == [(1, 2), (1, 3)]
    assert IO.neighbours(update=True) and IO.neighbours(update=False)
    monkeypatch.setenv(IO.PAGES_ENV, "cited")
    assert not IO.neighbours(update=True) and IO.neighbours(update=False)  # the full lane ignores it


# ---- the $0 replay ---------------------------------------------------------------------
def test_the_update_audit_honours_the_replay_switch(scene, monkeypatch) -> None:
    monkeypatch.setenv(IO.REPLAY_ENV, "1")
    client = Client()
    sr, doc, log = scene(client)
    assert EA.run(sr) == EA.EXIT_PENDING and client.calls == []
    d = sr.slug_dir / "runs" / "alpha" / IO.DIR
    reqs = sorted(p.name for p in d.glob("*.request.json"))
    assert reqs == [f"{k}.read1.request.json" for k in ("N1", "N2", "N3", "PN1", "PRE")]
    for p in d.glob("*.request.json"):
        req = json.loads(p.read_text())
        (d / p.name.replace(".request", "")).write_text(json.dumps({"hash": req["hash"], "text": _reply()}))
    assert EA.run(sr) == 0, log
    assert client.calls == [] and _record(sr)["unresolved"] == 0


# ---- the hold rate, counted on every run (the lead's $0 replay reads it) ---------------
def test_the_lines_that_would_hold_are_counted_when_the_tripwire_held_first(scene, monkeypatch) -> None:
    monkeypatch.setattr(ER, "EDIT_SHARE", 0.0)
    sr, doc, log = scene(Client(_on("N1", VALUE)))
    assert EA.run(sr) == EA.EXIT_HELD and doc.read_text(encoding="utf-8") == DOC
    rec = _record(sr)
    assert rec["held"].startswith("tripwire") and rec["unresolved_by_class"] == {"value": 1}


def test_the_hold_classes_are_one_constant_a_replay_can_narrow(scene, monkeypatch) -> None:
    monkeypatch.setattr(EU, "HOLD_CLASSES", ("another_person",))
    sr, doc, log = scene(Client(_on("N1", UNSUPPORTED), NOT_SUPPORTED))
    assert EA.run(sr) == 0, log  # unsupported no longer holds once narrowed
    rec = _record(sr)
    assert rec["unresolved"] == 0 and rec["unresolved_by_class"] == {"unsupported": 1}  # still counted
