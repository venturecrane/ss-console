"""A chronology never leaves out a dated event the records state (2026-11-21).

Two causes, both on delivered chronologies the firm corrected by hand:

1. The writer's map rule wrote a history printout off whole as "no dated
   encounter" when its header carried only a print date, though its rows (a
   vaccine given, a TB skin test placed, a skin test read) state their dates.
2. A visit only a bill showed reached no chronology: the date-of-service
   check named it on the billing worksheet and nowhere else.

The map pass is paid, so its reply here is scripted in the shape the rule asks
for; what is tested at $0 is the instruction the writer and checkers get and
every step after the map. The billed rows come from the billing extraction
``dos_check`` reads, never from the writer's BILLING-DATES block.

Every name, date and file here is invented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from jobs.medchron import billed_rows as BR, config as config_mod, job as job_mod, prompts
from jobs.medchron.dos_check import check as dos_check
from jobs.medchron.update import collect as C
from jobs.stages import assemble as assemble_stage, group as group_stage
from jobs.stages.base import StageRun
from jobs_testkit import FIRM_CONFIG, FakeSeat, job_yaml

HEADINGS = ["Patient Complaints & Limitations", "Medical Diagnoses", "All Other Information"]
BILL = "billing statement"
NAMES = ("history printout", "clinic visit", BILL, "med list")

#: The writer's reply, in the shape the new rule asks for, over one chunk: an
#: immunization history printed 10/01/2026 whose rows date a Tdap given and a
#: PPD placed on 03/05/2026 and the PPD read on 03/07/2026, a visit dated
#: 04/20/2026, a billing statement, and a medication list with no dated row.
MAP_OUT = """## ENTRIES
03/05/2026
R. Ames, RN | All Other Information

Tetanus (Tdap) vaccine given by R. Ames, RN. (FILE: history printout.pdf, p. 1)

Tuberculin skin test (PPD) placed by R. Ames, RN. (FILE: history printout.pdf, p. 1)

03/07/2026
R. Ames, RN | All Other Information

Tuberculin skin test (PPD) read as negative. (FILE: history printout.pdf, p. 1)

04/20/2026
Mesquite Family Clinic | Patient Complaints & Limitations

The patient reports low back pain since the Subject Incident. (FILE: clinic visit.pdf, p. 1)

## INDEX
2026-03-05 | R. Ames, RN | -- | history printout.pdf
2026-03-07 | R. Ames, RN | -- | history printout.pdf
2026-04-20 | Mesquite Family Clinic | -- | clinic visit.pdf

## BILLING-DATES
2026-04-20 | Mesquite Family Clinic | (FILE: billing statement.pdf, p. 1)
2026-06-02 | Saguaro Therapy Group | (FILE: billing statement.pdf, p. 3)

## CONFLICTS / REFERENCED-BUT-ABSENT
none observed

## FILES-SEEN
=== FILE: history printout.pdf (fileId history_printout) === entries: 2
=== FILE: clinic visit.pdf (fileId clinic_visit) === entries: 1
=== FILE: billing statement.pdf (fileId billing_statement) === billing-dates: 2
=== FILE: med list.pdf (fileId med_list) === nothing extractable: no dated encounter on pp. 1-1
"""

EMPTY_MAP = """## ENTRIES
none in this chunk

## INDEX
none in this chunk

## BILLING-DATES
none in this chunk

## CONFLICTS / REFERENCED-BUT-ABSENT
none observed

## FILES-SEEN
=== FILE: med list.pdf (fileId med_list) === nothing extractable: no dated encounter on pp. 1-1
"""


def item(date: str, desc: str, charge: str = "$95.00", page: int = 2) -> dict:
    return {"date": date, "description": desc, "charge": charge, "page": page}


def chunk(prov: str, items: list[dict], *, doc: str = "LEDGER", patient: str | None = None) -> dict:
    return {"doc_type": doc, "provider": prov, "patient": patient, "line_items": items}


#: The billed visits: the 04/20 visit (a record exists) and a therapy session
#: on 05/11/2026 that no record on file dates.
THERAPY = chunk("Saguaro Therapy Group", [item("05/11/2026", "97110-GP Therapeutic exercise x4 units $380.00")])
CLINIC = chunk("Mesquite Family Clinic", [item("04/20/2026", "99213 Office visit", "$150.00", page=1)])


@pytest.fixture
def firm(tmp_path: Path) -> Path:
    cfg = json.loads(json.dumps(FIRM_CONFIG))
    cfg["format"]["subsections"] = HEADINGS
    p = tmp_path / "firm.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return p


def _sr(job_dir: Path, firm: Path, data_root: Path, log: list[str]) -> StageRun:
    job = job_mod.load(job_dir)
    return StageRun(
        job=job,
        cfg=config_mod.load(str(firm)),
        unit=job.units[0],
        slug_dir=data_root / "example-matter",
        decided={},
        log=log.append,
        seat_factory=lambda: FakeSeat([], [], {}),
        client_factory=lambda: None,
    )


def _seed(slug: Path, chunks: list[dict], record_text: dict[str, str] | None = None, bill: str = BILL) -> None:
    (slug / "billing_extract.jsonl").write_text(json.dumps({"file": bill + ".pdf", "chunks": chunks}) + "\n")
    texts = record_text if record_text is not None else {"clinic visit": "[p.1] Office visit 04/20/2026 back pain"}
    (slug / "text").mkdir(exist_ok=True)
    rows = []
    for name, text in texts.items():
        fid = name.replace(" ", "_")
        (slug / "text" / f"{fid}.txt").write_text(text)
        rows.append({"id": fid, "name": name + ".pdf"})
    (slug / "extracted.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def _assembled(
    job_dir: Path,
    firm: Path,
    data_root: Path,
    *,
    text: str = MAP_OUT,
    chunks: list[dict] | None = None,
    names: tuple[str, ...] = NAMES,
    record_text: dict[str, str] | None = None,
    log: list[str] | None = None,
    extra_maps: dict[str, str] | None = None,
) -> Path:
    lines: list[str] = log if log is not None else []
    sr = _sr(job_dir, firm, data_root, lines)
    d = sr.slug_dir / "runs" / "alpha"
    d.mkdir(parents=True)
    (d / "map-01.md").write_text(text)
    usage = [{"chunk": 1, "stop": "end_turn"}]
    for k, (name, body) in enumerate((extra_maps or {}).items(), 2):
        (d / name).write_text(body)
        usage.append({"chunk": k, "stop": "end_turn"})
    (d / "usage.jsonl").write_text("".join(json.dumps(u) + "\n" for u in usage))
    (sr.slug_dir / "units").mkdir(exist_ok=True)
    (sr.slug_dir / "units" / "alpha.json").write_text(
        json.dumps([{"id": n.replace(" ", "_"), "name": n, "ext": ".pdf"} for n in names])
    )
    _seed(sr.slug_dir, [CLINIC, THERAPY] if chunks is None else chunks, record_text)
    assert assemble_stage.run(sr) == 0
    return d


def _dates(entries_md: str) -> list[str]:
    return [b.splitlines()[0] for b in entries_md.split("\n\n") if b[:2].isdigit() and b[2:3] == "/"]


def _bill_ex(d: Path) -> int:
    return json.loads((d / "exhibit_map.json").read_text())[BILL + ".pdf"]


# ---- the instruction the writer and the checkers are given ---------------------------
def test_the_writer_keys_a_history_printout_row_to_its_own_date_and_the_print_date_to_nothing(firm: Path) -> None:
    text = prompts.load("map-system", config_mod.load(str(firm)))
    assert "is never a date of service and never keys an entry" in text
    for event in ("a vaccine or immunization given", "a TB skin test placed", "a TB skin test read with its result",
                  "a single dated order or prescription"):  # fmt: skip
        assert event in text
    assert "IS a dated event, whether or not the page's header carries a date" in text
    assert "Only a page with no such dated row and no dated encounter is listed in FILES-SEEN" in text
    assert "When the page dates no encounter, write no entry for it" not in text


def test_the_writer_is_told_which_printout_dates_are_never_events(firm: Path) -> None:
    text = prompts.load("map-system", config_mod.load(str(firm)))
    assert "A due, next-dose, recommended, expiration, lot, entered or recorded date is never an event date" in text


def test_medication_administration_records_and_lab_tables_stay_in_the_days_entry(firm: Path) -> None:
    text = prompts.load("map-system", config_mod.load(str(firm)))
    assert "Medication administration records and lab result tables are not this rule" in text
    assert "never a paragraph per row" in text


def test_a_printout_vaccine_also_in_a_visit_note_is_one_entry_keyed_to_who_gave_it(firm: Path) -> None:
    text = prompts.load("map-system", config_mod.load(str(firm)))
    assert "never the registry or system that printed the list" in text
    assert "it is a paragraph of that visit's entry, not an entry of its own" in text


def test_the_writer_is_no_longer_told_billing_dates_become_entries(firm: Path) -> None:
    text = prompts.load("map-system", config_mod.load(str(firm)))
    assert "written into the chronology later" not in text
    assert "`YYYY-MM-DD | <provider> | (FILE: <file name>, p. <x>)`" in text  # the block as it was


def test_the_update_review_and_finisher_agree_with_the_writer(firm: Path) -> None:
    cfg = config_mod.load(str(firm))
    review = prompts.load("update-review-system", cfg)
    assert "is an event on that row's own date, not a print date" in review
    assert "due, next-dose, expiration, lot, entered or recorded date, is wrong" in review
    assert "date of service per the bill" in review
    assert "a history printout's row dating a vaccine given" in prompts.load("update-finish-system", cfg)


# ---- the printout shape, through assemble --------------------------------------------
def test_printout_rows_keep_their_own_dates_and_the_print_date_keys_nothing(
    job_dir: Path, firm: Path, data_root: Path
) -> None:
    d = _assembled(job_dir, firm, data_root)
    entries = (d / "entries.md").read_text()
    assert {"03/05/2026", "03/07/2026"} <= set(_dates(entries))
    assert "10/01/2026" not in entries
    ex = json.loads((d / "exhibit_map.json").read_text())["history printout.pdf"]
    assert f"Tetanus (Tdap) vaccine given by R. Ames, RN. (Exhibit {ex} - p. 1)" in entries
    assert f"Tuberculin skin test (PPD) read as negative. (Exhibit {ex} - p. 1)" in entries
    seen = (d / "files_seen.md").read_text()
    assert "=== FILE: med list.pdf (fileId med_list) === nothing extractable: no dated encounter on pp. 1-1" in seen


def test_the_same_vaccine_from_a_printout_and_a_visit_note_in_two_chunks_is_merged_into_one_entry(
    job_dir: Path, firm: Path, data_root: Path
) -> None:
    """Keyed to who gave it (the clinic, not the registry), the printout row and
    the visit note of the same date and provider are one cluster: merge folds
    them into one entry, keeping both citations (its falsifier)."""
    printout = MAP_OUT.split("03/07/2026")[0].replace("R. Ames, RN |", "Mesquite Family Clinic |") + "\n## INDEX\n"
    note = (
        "## ENTRIES\n03/05/2026\nMesquite Family Clinic | All Other Information\n\n"
        "Tetanus (Tdap) vaccine given by R. Ames, RN. (FILE: clinic visit.pdf, p. 2)\n\n## INDEX\n"
    )
    d = _assembled(job_dir, firm, data_root, text=printout, chunks=[], extra_maps={"map-02.md": note})
    clusters = (d / "clusters.md").read_text()
    assert clusters.count("##### CLUSTER 2026-03-05") == 1 and "(2 fragments)" in clusters
    assert "03/05/2026" not in (d / "entries.md").read_text()


# ---- billed rows: the source is the billing extraction --------------------------------
def test_a_billed_visit_with_no_record_becomes_a_row_cited_to_the_page_that_shows_it(
    job_dir: Path, firm: Path, data_root: Path
) -> None:
    d = _assembled(job_dir, firm, data_root)
    entries = (d / "entries.md").read_text()
    row = (
        "05/11/2026\nSaguaro Therapy Group | All Other Information\n\n"
        f"Therapeutic exercise on 05/11/2026, date of service per the bill. (Exhibit {_bill_ex(d)} - p. 2)"
    )
    assert row in entries and BR.is_billed_row(row)
    assert "$" not in entries and "97110" not in entries and "units" not in entries and "billed on" not in entries
    _dates_by_file, provs = group_stage.index_rows(d)
    assert provs[BILL + ".pdf"] == ["Saguaro Therapy Group"]


def test_the_writers_billing_dates_block_makes_no_row(job_dir: Path, firm: Path, data_root: Path) -> None:
    """MAP_OUT lists 06/02/2026 in BILLING-DATES; the extraction has no such
    item, so nothing is carried for it."""
    d = _assembled(job_dir, firm, data_root)
    assert "06/02/2026" not in (d / "entries.md").read_text()
    assert "06/02/2026" not in (d / assemble_stage.BILLED_ROWS).read_text()


def test_a_billed_date_with_an_entry_is_not_added(job_dir: Path, firm: Path, data_root: Path) -> None:
    d = _assembled(job_dir, firm, data_root)
    entries = (d / "entries.md").read_text()
    assert _dates(entries).count("04/20/2026") == 1 and "Office visit on 04/20/2026" not in entries


@pytest.mark.parametrize(
    "bad",
    [
        item("05/11/2026", "Payment received", "$95.00"),
        item("05/11/2026", "Insurance adjustment", "($40.00)"),
        item("05/11/2026", "Therapeutic exercise", "$0.00"),
        item("05/11/2026", "Therapeutic exercise", "-95.00"),
    ],
)
def test_a_payment_adjustment_or_non_positive_charge_is_no_visit(
    job_dir: Path, firm: Path, data_root: Path, bad: dict
) -> None:
    d = _assembled(job_dir, firm, data_root, chunks=[chunk("Saguaro Therapy Group", [bad])])
    assert (d / assemble_stage.BILLED_ROWS).read_text() == ""


def test_a_vendor_invoice_or_eob_line_is_no_visit(job_dir: Path, firm: Path, data_root: Path) -> None:
    eob = chunk("Example Health Plan", [item("05/11/2026", "Therapeutic exercise")], doc="EOB")
    d = _assembled(job_dir, firm, data_root, chunks=[eob])
    assert (d / assemble_stage.BILLED_ROWS).read_text() == ""


def test_another_patients_bill_on_a_joint_matter_is_quarantined(tmp_path: Path, firm: Path) -> None:
    data_root = tmp_path / "data"
    (data_root / "example-matter").mkdir(parents=True)
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "job.yaml").write_text(job_yaml(data_root, joint=True), encoding="utf-8")
    other = chunk("Saguaro Therapy Group", [item("05/11/2026", "Therapeutic exercise")], patient="Beta Example")
    d = _assembled(job_dir, firm, data_root, chunks=[other])
    assert (d / assemble_stage.BILLED_ROWS).read_text() == ""


def test_a_billed_date_a_record_page_carries_is_a_missed_visit_not_a_row(
    job_dir: Path, firm: Path, data_root: Path
) -> None:
    """dos_check calls it a missed visit (the writer's miss, reported there);
    the bill never stands in for the record."""
    pages = {"clinic visit": "[p.1] Office visit 04/20/2026\n[p.2] Therapy note 05/11/2026 exercises"}
    d = _assembled(job_dir, firm, data_root, record_text=pages)
    assert (d / assemble_stage.BILLED_ROWS).read_text() == ""


def test_a_pre_incident_billed_date_is_not_carried(job_dir: Path, firm: Path, data_root: Path) -> None:
    pre = chunk("Saguaro Therapy Group", [item("01/10/2026", "Therapeutic exercise")])
    d = _assembled(job_dir, firm, data_root, chunks=[pre])
    assert (d / assemble_stage.BILLED_ROWS).read_text() == ""


def test_the_row_takes_the_run_providers_name_dos_check_matches_the_bill_to(
    job_dir: Path, firm: Path, data_root: Path
) -> None:
    """The bill names "Mesquite Family Clinic LLC"; every distinctive word of
    it is in the run's "Mesquite Family Clinic": no new lane. A bill sharing
    one word only keeps its own name."""
    bill = chunk("Mesquite Family Clinic LLC", [item("05/11/2026", "Office visit")])
    d = _assembled(job_dir, firm, data_root, chunks=[CLINIC, bill])
    assert "05/11/2026\nMesquite Family Clinic | All Other Information" in (d / "entries.md").read_text()
    assert BR.row_provider("Mesquite Imaging", ["Mesquite Family Clinic"], {}) == "Mesquite Imaging"
    assert BR.row_provider("Desert Spine", ["Dr. Lee"], {"Dr. Lee": ["Desert Spine"]}) == "Dr. Lee"


def test_one_sentence_per_service_each_cited_to_its_own_page_never_pooled(
    job_dir: Path, firm: Path, data_root: Path
) -> None:
    two = chunk(
        "Saguaro Therapy Group",
        [item("05/11/2026", "Therapeutic exercise", page=2), item("05/11/2026", "Manual therapy", page=3)],
    )
    again = chunk("Saguaro Therapy Group", [item("05/11/2026", "Therapeutic exercise", page=7)], doc="MEDICAL_BILL")
    d = _assembled(job_dir, firm, data_root, chunks=[two, again])
    rows = (d / assemble_stage.BILLED_ROWS).read_text()
    assert (
        "Therapeutic exercise on 05/11/2026, date of service per the bill. (FILE: billing statement.pdf, p. 2)" in rows
    )
    assert "Manual therapy on 05/11/2026, date of service per the bill. (FILE: billing statement.pdf, p. 3)" in rows
    assert "p. 7" not in rows and ";" not in rows  # a repeat on another page adds nothing; nothing pooled


def test_a_bill_that_is_not_a_unit_file_is_dropped_and_logged_never_left_unresolved(
    job_dir: Path, firm: Path, data_root: Path
) -> None:
    log: list[str] = []
    d = _assembled(job_dir, firm, data_root, names=("history printout", "clinic visit", "med list"), log=log)
    assert (d / assemble_stage.BILLED_ROWS).read_text() == ""
    assert "(FILE:" not in (d / "entries.md").read_text()
    assert any("billed date 2026-05-11 not carried" in line for line in log)


def test_a_bill_dated_a_day_off_its_visit_note_keeps_both_dates(job_dir: Path, firm: Path, data_root: Path) -> None:
    """The note is dated 05/10, the bill says 05/11: no record page carries
    05/11, so the bill's date is carried beside the note's (house style)."""
    pages = {"clinic visit": "[p.1] Office visit 04/20/2026\n[p.2] Therapy re-evaluation 05/10/2026"}
    d = _assembled(job_dir, firm, data_root, record_text=pages)
    assert "Therapeutic exercise on 05/11/2026, date of service per the bill." in (d / "entries.md").read_text()


# ---- the service text ----------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("97110-GP Therapeutic exercise x4 units $380.00", "Therapeutic exercise"),
        ("97110GP Therapeutic exercise", "Therapeutic exercise"),
        ("CPT: 97140-59 Manual therapy Qty 2", "Manual therapy"),
        ("0420 Physical therapy 4 units 960.00", "Physical therapy"),
        ("Rev 0450 Emergency room", "Emergency room"),
        ("J1100 Dexamethasone NDC 12345-6789-01", "Dexamethasone"),
        ("Lab draw 36415 00071015527", "Lab draw"),
        ("99213 Office visit M54.16 S13.4XXA", "Office visit"),
        ("Therapeutic exercise 1,240.00", "Therapeutic exercise"),
        ("Tdap vaccine 90715 x1", "Tdap vaccine"),
        ("COVID-19 vaccine 91300", "COVID-19 vaccine"),
        ("Vitamin B12 injection J3420", "Vitamin B12 injection"),
        ("97164 PT re-evaluation", "PT re-evaluation"),
        ("99213", ""),
    ],
)
def test_the_service_keeps_its_words_and_loses_codes_amounts_and_units(raw: str, want: str) -> None:
    assert BR.service(raw) == want


def test_an_entry_with_care_in_it_is_not_a_billed_row() -> None:
    row = "05/11/2026\nSaguaro Therapy Group | X\n\nTherapeutic exercise on 05/11/2026, date of service per the bill. (Exhibit 3 - p. 2)"
    assert BR.is_billed_row(row)
    assert not BR.is_billed_row(row + "\n\nThe patient reports pain rated 4 of 10. (Exhibit 2 - p. 1)")


# ---- the checkers that read entries after assemble -----------------------------------
ROW = (
    "05/11/2026\nSaguaro Therapy Group | All Other Information\n\n"
    "Therapeutic exercise on 05/11/2026, date of service per the bill. (Exhibit 7 - p. 2)"
)


def test_an_update_does_not_add_a_billed_row_for_a_date_the_delivered_chronology_carries() -> None:
    delivered = ["05/11/2026\nSaguaro Therapy Group | All Other Information\n\nTherapy visit. (Exhibit 2 - p. 4)"]
    kept, rep = C.collect([ROW], delivered, "2026-01-01", lambda p: p.lower())
    assert kept == [] and rep[C.BILLED_DELIVERED] == 1
    later = ROW.replace("05/11/2026", "05/18/2026")
    kept, _ = C.collect([later], delivered, "2026-01-01", lambda p: p.lower())
    assert kept == [later]


def test_the_billed_date_check_does_not_count_a_billed_row_as_a_record_of_the_visit(tmp_path: Path) -> None:
    slug = tmp_path / "matter"
    run = slug / "runs" / "alpha"
    run.mkdir(parents=True)
    _seed(slug, [THERAPY], record_text={})
    rep = dos_check(slug, run, "2026-01-01", None, ROW)
    assert rep["billed_dates"] == 1 and rep["classes"]["in_chronology"] == 0
    assert rep["classes"]["billed_no_record"] == 1 and rep["billed_no_record"][0]["date"] == "2026-05-11"
