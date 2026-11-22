"""A chronology never leaves out a dated event the records state (2026-11-21).

Two causes, both on delivered chronologies the firm corrected by hand:

1. The writer's map rule wrote a printout off whole as "no dated encounter"
   when its header carried only a print date, though each of its rows
   (a vaccine given, a skin test placed, a skin test read) states its own date.
2. A date of service only a bill showed was listed in BILLING-DATES and never
   written: no code read the block.

The map pass is paid, so its reply here is scripted in the shape the rule now
asks for; what is tested at $0 is the instruction every writer and checker is
given, and every step after the map: assemble carries both kinds of row to the
entries, the print date keys nothing, a billed date an entry already covers is
not added, and a page with only a print date stays `no dated encounter`.

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
from jobs_testkit import FIRM_CONFIG, FakeSeat

HEADINGS = ["Patient Complaints & Limitations", "Medical Diagnoses", "All Other Information"]

#: The writer's reply, scripted in the shape the new rule asks for, over one
#: chunk holding: an immunization history printed 10/01/2026 whose rows date a
#: Tdap given and a PPD placed on 03/05/2026 and the PPD read on 03/07/2026; a
#: visit dated 04/20/2026; a billing statement billing that visit and a therapy
#: session on 05/11/2026 that no visit note records; and a medication list
#: printed 10/02/2026 with no dated row at all.
MAP_OUT = """## ENTRIES
03/05/2026
R. Ames, RN | All Other Information

Tetanus (Tdap) vaccine given by R. Ames, RN, lot X1234. (FILE: history printout.pdf, p. 1)

Tuberculin skin test (PPD) placed by R. Ames, RN. (FILE: history printout.pdf, p. 1)

03/07/2026
R. Ames, RN | All Other Information

Tuberculin skin test (PPD) read as negative. (FILE: history printout.pdf, p. 1)

04/20/2026
Example Family Clinic | Patient Complaints & Limitations

The patient reports low back pain since the Subject Incident. (FILE: clinic visit.pdf, p. 1)

## INDEX
2026-03-05 | R. Ames, RN | -- | history printout.pdf
2026-03-07 | R. Ames, RN | -- | history printout.pdf
2026-04-20 | Example Family Clinic | -- | clinic visit.pdf

## BILLING-DATES
2026-04-20 | Example Family Clinic | Office visit 99213 $150.00 | (FILE: billing statement.pdf, p. 1)
2026-05-11 | Example Therapy Group | Therapeutic exercise 97110 $95.00 | (FILE: billing statement.pdf, p. 2)

## CONFLICTS / REFERENCED-BUT-ABSENT
none observed

## FILES-SEEN
=== FILE: history printout.pdf (fileId history_printout) === entries: 2
=== FILE: clinic visit.pdf (fileId clinic_visit) === entries: 1
=== FILE: billing statement.pdf (fileId billing_statement) === billing-dates: 2
=== FILE: med list.pdf (fileId med_list) === nothing extractable: no dated encounter on pp. 1-1
"""


@pytest.fixture
def firm(tmp_path: Path) -> Path:
    cfg = json.loads(json.dumps(FIRM_CONFIG))
    cfg["format"]["subsections"] = HEADINGS
    p = tmp_path / "firm.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return p


def _sr(job_dir: Path, firm: Path, data_root: Path) -> StageRun:
    job = job_mod.load(job_dir)
    return StageRun(
        job=job,
        cfg=config_mod.load(str(firm)),
        unit=job.units[0],
        slug_dir=data_root / "example-matter",
        decided={},
        log=lambda _m: None,
        seat_factory=lambda: FakeSeat([], [], {}),
        client_factory=lambda: None,
    )


def _assembled(job_dir: Path, firm: Path, data_root: Path, text: str = MAP_OUT) -> Path:
    sr = _sr(job_dir, firm, data_root)
    d = sr.slug_dir / "runs" / "alpha"
    d.mkdir(parents=True)
    (d / "map-01.md").write_text(text)
    (d / "usage.jsonl").write_text(json.dumps({"chunk": 1, "stop": "end_turn"}) + "\n")
    (sr.slug_dir / "units").mkdir(exist_ok=True)
    names = ("history printout", "clinic visit", "billing statement", "med list")
    (sr.slug_dir / "units" / "alpha.json").write_text(
        json.dumps([{"id": n.replace(" ", "_"), "name": n, "ext": ".pdf"} for n in names])
    )
    assert assemble_stage.run(sr) == 0
    return d


def _dates(entries_md: str) -> list[str]:
    return [b.splitlines()[0] for b in entries_md.split("\n\n") if b[:2].isdigit() and b[2:3] == "/"]


# ---- the instruction every writer and checker is given -------------------------------
def test_the_writer_is_told_a_dated_printout_row_is_an_event_and_its_print_date_is_not(firm: Path) -> None:
    text = prompts.load("map-system", config_mod.load(str(firm)))
    assert "is never a date of service and never keys an entry" in text  # the print date still keys nothing
    assert "a vaccine given, a skin test placed or read" in text
    assert "IS a dated event, whether or not the page's header carries a date" in text
    assert "on that row's own date" in text and "cited to the page the row is on" in text
    # only a page with no dated row at all is written off
    assert "Only a page with no dated row and no dated encounter at all is listed in FILES-SEEN" in text
    assert "When the page dates no encounter, write no entry for it" not in text


def test_the_writer_lists_the_service_on_every_billed_date(firm: Path) -> None:
    text = prompts.load("map-system", config_mod.load(str(firm)))
    assert "`YYYY-MM-DD | <provider> | <service as billed> | (FILE: <file name>, p. <x>)`" in text
    assert "written into the chronology later as a date billed with no visit note" in text


def test_the_update_review_and_finisher_agree_a_printout_row_is_an_event(firm: Path) -> None:
    cfg = config_mod.load(str(firm))
    review = prompts.load("update-review-system", cfg)
    assert "is an event on that row's own date, not a print date" in review
    assert "presents a billing line as what it is, not as a visit" in review
    assert "a dated row of a printout such as a vaccine given" in prompts.load("update-finish-system", cfg)


# ---- the exact shape, through assemble ----------------------------------------------
def test_printout_rows_keep_their_own_dates_and_the_print_date_keys_nothing(
    job_dir: Path, firm: Path, data_root: Path
) -> None:
    d = _assembled(job_dir, firm, data_root)
    entries = (d / "entries.md").read_text()
    dates = _dates(entries)
    assert "03/05/2026" in dates and "03/07/2026" in dates
    assert "10/01/2026" not in entries and "10/02/2026" not in entries
    ex = json.loads((d / "exhibit_map.json").read_text())["history printout.pdf"]
    assert f"Tetanus (Tdap) vaccine given by R. Ames, RN, lot X1234. (Exhibit {ex} - p. 1)" in entries
    assert f"Tuberculin skin test (PPD) placed by R. Ames, RN. (Exhibit {ex} - p. 1)" in entries
    assert f"Tuberculin skin test (PPD) read as negative. (Exhibit {ex} - p. 1)" in entries
    # the control page with only a print date stays written off
    assert (
        "=== FILE: med list.pdf (fileId med_list) === nothing extractable: no dated encounter on pp. 1-1"
        in (d / "files_seen.md").read_text()
    )


def test_a_billed_date_no_entry_covers_becomes_a_dated_row_cited_to_the_bill(
    job_dir: Path, firm: Path, data_root: Path
) -> None:
    d = _assembled(job_dir, firm, data_root)
    entries = (d / "entries.md").read_text()
    bill = json.loads((d / "exhibit_map.json").read_text())["billing statement.pdf"]
    row = (
        "05/11/2026\nExample Therapy Group | All Other Information\n\n"
        f"Therapeutic exercise billed on 05/11/2026. (Exhibit {bill} - p. 2)"
    )
    assert row in entries
    assert "$" not in entries and "97110" not in entries  # no amounts, no billing codes
    assert BR.is_billed_row(row)
    # the bill's provider and date reach the exhibit titling (group.index_rows)
    _dates_by_file, provs = group_stage.index_rows(d)
    assert provs["billing statement.pdf"] == ["Example Therapy Group"]


def test_a_billed_date_an_entry_already_covers_is_not_added(job_dir: Path, firm: Path, data_root: Path) -> None:
    d = _assembled(job_dir, firm, data_root)
    entries = (d / "entries.md").read_text()
    assert _dates(entries).count("04/20/2026") == 1
    assert "billed on 04/20/2026" not in entries
    assert (d / assemble_stage.BILLED_ROWS).read_text().count("billed on") == 1


def test_a_saved_map_without_the_service_column_still_carries_its_billed_date(
    job_dir: Path, firm: Path, data_root: Path
) -> None:
    """Maps saved before 2026-11-21 list `date | provider | cite`: a $0 replay
    of them must carry the billed date too."""
    old = MAP_OUT.replace(" | Therapeutic exercise 97110 $95.00 |", " |").replace(
        " | Office visit 99213 $150.00 |", " |"
    )
    d = _assembled(job_dir, firm, data_root, old)
    assert "Services billed on 05/11/2026." in (d / "entries.md").read_text()


def test_a_page_with_only_a_print_date_carries_no_row(job_dir: Path, firm: Path, data_root: Path) -> None:
    only_print = """## ENTRIES
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
    d = _assembled(job_dir, firm, data_root, only_print)
    assert (d / "entries.md").read_text() == "" and (d / assemble_stage.BILLED_ROWS).read_text() == ""
    assert "no dated encounter on pp. 1-1" in (d / "files_seen.md").read_text()


# ---- the rule in code ----------------------------------------------------------------
def test_parse_reads_old_and_new_lines_and_skips_prose() -> None:
    got = BR.parse(
        "2026-05-11 | Example Therapy Group | Therapeutic exercise | (FILE: bill.pdf, p. 2)\n"
        "- 2026-05-12 | Example Therapy Group | (FILE: bill.pdf, p. 3)\n"
        "none in this chunk\n"
        "2026-05-13 | no citation here\n"
    )
    assert [(g["iso"], g["service"], g["cite"]) for g in got] == [
        ("2026-05-11", "Therapeutic exercise", "(FILE: bill.pdf, p. 2)"),
        ("2026-05-12", "", "(FILE: bill.pdf, p. 3)"),
    ]


def test_one_row_per_date_and_provider_and_none_for_a_covered_date() -> None:
    billed = BR.parse(
        "2026-05-11 | Example Therapy Group | Therapeutic exercise | (FILE: bill.pdf, p. 2)\n"
        "2026-05-11 | Example Therapy Group | Manual therapy | (FILE: bill.pdf, p. 2)\n"
        "2026-05-11 | Example Imaging | X-ray of the lumbar spine | (FILE: bill.pdf, p. 3)\n"
        "2026-04-20 | Example Family Clinic | Office visit | (FILE: bill.pdf, p. 1)\n"
    )
    rows = BR.carry(billed, {"2026-04-20"}, "All Other Information")
    assert [(r["iso"], r["provider"]) for r in rows] == [
        ("2026-05-11", "Example Imaging"),
        ("2026-05-11", "Example Therapy Group"),
    ]
    assert "Therapeutic exercise; Manual therapy billed on 05/11/2026. (FILE: bill.pdf, p. 2)" in rows[1]["text"]


def test_an_entry_with_care_in_it_is_not_a_billed_row() -> None:
    row = BR.carry(
        BR.parse("2026-05-11 | Example Therapy Group | Therapeutic exercise | (FILE: bill.pdf, p. 2)"), set(), "X"
    )[0]["text"]
    assert BR.is_billed_row(row)
    assert not BR.is_billed_row(row + "\n\nThe patient reports pain rated 4 of 10. (FILE: note.pdf, p. 1)")
    assert not BR.is_billed_row(
        "05/11/2026\nExample Therapy Group | X\n\nThe patient reports pain. (FILE: n.pdf, p. 1)"
    )


# ---- the checkers that read entries after assemble -----------------------------------
def test_an_update_does_not_add_a_billed_row_for_a_date_the_delivered_chronology_carries() -> None:
    delivered = ["05/11/2026\nExample Therapy Group | All Other Information\n\nTherapy visit. (Exhibit 2 - p. 4)"]
    row = (
        "05/11/2026\nExample Therapy Group | All Other Information\n\n"
        "Therapeutic exercise billed on 05/11/2026. (Exhibit 7 - p. 2)"
    )
    kept, rep = C.collect([row], delivered, "2026-01-01", lambda p: p.lower())
    assert kept == [] and rep[C.BILLED_DELIVERED] == 1
    later = row.replace("05/11/2026", "05/18/2026")
    kept, _ = C.collect([later], delivered, "2026-01-01", lambda p: p.lower())
    assert kept == [later]


def test_the_billed_date_check_does_not_count_a_billed_row_as_a_record_of_the_visit(tmp_path: Path) -> None:
    slug = tmp_path / "matter"
    run = slug / "runs" / "alpha"
    run.mkdir(parents=True)
    (slug / "billing_extract.jsonl").write_text(
        json.dumps(
            {
                "file": "billing statement.pdf",
                "chunks": [
                    {
                        "doc_type": "MEDICAL_BILL",
                        "provider": "Example Therapy Group",
                        "line_items": [
                            {"date": "05/11/2026", "description": "Therapeutic exercise", "charge": "95.00", "page": 2}
                        ],
                    }
                ],
            }
        )
        + "\n"
    )
    row = (
        "05/11/2026\nExample Therapy Group | All Other Information\n\n"
        "Therapeutic exercise billed on 05/11/2026. (Exhibit 3 - p. 2)"
    )
    rep = dos_check(slug, run, "2026-01-01", None, row)
    assert rep["billed_dates"] == 1 and rep["classes"]["in_chronology"] == 0
    assert rep["classes"]["billed_no_record"] == 1 and rep["billed_no_record"][0]["date"] == "2026-05-11"


def test_one_file_makes_one_paragraph_cited_to_its_first_page_showing_the_date() -> None:
    """A ledger, its claim form and an EOB print the same billed line: one
    paragraph per file (the house rule), never one per page."""
    billed = BR.parse(
        "2026-05-11 | Example Therapy Group | Therapeutic exercise | (FILE: ledger.pdf, p. 12)\n"
        "2026-05-11 | Example Therapy Group | Therapeutic exercise | (FILE: ledger.pdf, p. 1)\n"
        "2026-05-11 | Example Therapy Group | Therapeutic exercise | (FILE: payer statement.pdf, p. 3)\n"
    )
    text = BR.carry(billed, set(), "All Other Information")[0]["text"]
    assert text.count("Therapeutic exercise billed on 05/11/2026.") == 2
    assert "(FILE: ledger.pdf, p. 1)" in text and "p. 12" not in text
    assert "(FILE: payer statement.pdf, p. 3)" in text


def test_a_bill_dated_a_day_off_its_visit_note_keeps_both_dates() -> None:
    """A note dated 05/10 billed on 05/11: the house style keeps both dates
    (the writer's CONFLICTS block names the disagreement), and the row says
    only what the bill prints."""
    billed = BR.parse("2026-05-11 | Example Therapy Group | Re-evaluation | (FILE: ledger.pdf, p. 1)")
    rows = BR.carry(billed, {"2026-05-10"}, "All Other Information")
    assert [r["iso"] for r in rows] == ["2026-05-11"]
    assert rows[0]["text"].endswith("Re-evaluation billed on 05/11/2026. (FILE: ledger.pdf, p. 1)")
