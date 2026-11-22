"""A dated row on a history printout reaches the chronology (2026-11-21).

On delivered chronologies the firm corrected by hand, the writer's map rule
had written a history printout off whole as "no dated encounter" when its
header carried only a print date, though its rows (a vaccine given, a TB skin
test placed, a skin test read) state their own dates.

The map pass is paid, so its reply here is scripted in the shape the rule asks
for; what is tested at $0 is the instruction the writer and the update's
checkers are given, and that assemble carries the rows on their own dates.

Every name, date and file here is invented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from jobs.medchron import config as config_mod, job as job_mod, prompts
from jobs.stages import assemble as assemble_stage
from jobs.stages.base import StageRun
from jobs_testkit import FIRM_CONFIG, FakeSeat

HEADINGS = ["Patient Complaints & Limitations", "Medical Diagnoses", "All Other Information"]
NAMES = ("history printout", "clinic visit", "med list")
#: The one wording the writer and both update checkers carry.
EVENTS = "a vaccine given, a TB skin test placed, a TB skin test read with its result, or a single dated order or prescription"
NOT_EVENTS = (
    "A due, next-dose, recommended, expiration, lot, entered or recorded date, or a medication's start date on a "
    "medication history list, is never an event date."
)
PROMPTS = ("map-system", "update-review-system", "update-finish-system")

#: The writer's reply, in the shape the rule asks for, over one chunk: an
#: immunization history printed 10/01/2026 whose rows date a Tdap given and a
#: PPD placed on 03/05/2026 and the PPD read on 03/07/2026, a visit dated
#: 04/20/2026, and a medication list with no dated row.
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
none in this chunk

## CONFLICTS / REFERENCED-BUT-ABSENT
none observed

## FILES-SEEN
=== FILE: history printout.pdf (fileId history_printout) === entries: 2
=== FILE: clinic visit.pdf (fileId clinic_visit) === entries: 1
=== FILE: med list.pdf (fileId med_list) === nothing extractable: no dated encounter on pp. 1-1
"""


@pytest.fixture
def firm(tmp_path: Path) -> Path:
    cfg = json.loads(json.dumps(FIRM_CONFIG))
    cfg["format"]["subsections"] = HEADINGS
    p = tmp_path / "firm.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return p


def _assembled(job_dir: Path, firm: Path, data_root: Path, maps: dict[str, str]) -> Path:
    job = job_mod.load(job_dir)
    sr = StageRun(
        job=job,
        cfg=config_mod.load(str(firm)),
        unit=job.units[0],
        slug_dir=data_root / "example-matter",
        decided={},
        log=lambda _m: None,
        seat_factory=lambda: FakeSeat([], [], {}),
        client_factory=lambda: None,
    )
    d = sr.slug_dir / "runs" / "alpha"
    d.mkdir(parents=True)
    for name, body in maps.items():
        (d / name).write_text(body)
    (d / "usage.jsonl").write_text(
        "".join(json.dumps({"chunk": k, "stop": "end_turn"}) + "\n" for k in range(1, len(maps) + 1))
    )
    (sr.slug_dir / "units").mkdir(exist_ok=True)
    (sr.slug_dir / "units" / "alpha.json").write_text(
        json.dumps([{"id": n.replace(" ", "_"), "name": n, "ext": ".pdf"} for n in NAMES])
    )
    assert assemble_stage.run(sr) == 0
    return d


def _dates(entries_md: str) -> list[str]:
    return [b.splitlines()[0] for b in entries_md.split("\n\n") if b[:2].isdigit() and b[2:3] == "/"]


# ---- the instruction ------------------------------------------------------------------
@pytest.mark.parametrize("name", PROMPTS)
def test_the_writer_and_both_update_checkers_carry_one_event_list_and_one_exclusion(firm: Path, name: str) -> None:
    text = prompts.load(name, config_mod.load(str(firm)))
    assert text.count(EVENTS) == 1
    assert text.count(NOT_EVENTS) == 1


def test_the_writer_keys_a_printout_row_to_its_own_date_and_the_print_date_to_nothing(firm: Path) -> None:
    text = prompts.load("map-system", config_mod.load(str(firm)))
    assert "is never a date of service and never keys an entry" in text
    assert "IS a dated event, whether or not the page's header carries a date" in text
    assert "Only a page with no such dated row and no dated encounter is listed in FILES-SEEN" in text
    assert "When the page dates no encounter, write no entry for it" not in text


def test_medication_administration_records_and_lab_tables_stay_in_the_days_entry(firm: Path) -> None:
    text = prompts.load("map-system", config_mod.load(str(firm)))
    assert "Medication administration records and lab result tables are not this rule" in text
    assert "never a paragraph per row" in text


def test_a_printout_vaccine_also_in_a_visit_note_is_one_entry_keyed_to_who_gave_it(firm: Path) -> None:
    text = prompts.load("map-system", config_mod.load(str(firm)))
    assert "never the registry or system that printed the list" in text
    assert "it is a paragraph of that visit's entry, not an entry of its own" in text


def test_the_update_review_says_a_print_date_or_an_excluded_date_is_wrong(firm: Path) -> None:
    review = prompts.load("update-review-system", config_mod.load(str(firm)))
    assert "is an event on that row's own date, not a print date" in review
    assert "or to a date that is never an event date, is wrong" in review


def test_the_billing_rules_are_unchanged(firm: Path) -> None:
    text = prompts.load("map-system", config_mod.load(str(firm)))
    assert "`YYYY-MM-DD | <provider> | (FILE: <file name>, p. <x>)`" in text
    assert "written into the chronology later" not in text


# ---- through assemble -----------------------------------------------------------------
def test_printout_rows_keep_their_own_dates_and_the_print_date_keys_nothing(
    job_dir: Path, firm: Path, data_root: Path
) -> None:
    d = _assembled(job_dir, firm, data_root, {"map-01.md": MAP_OUT})
    entries = (d / "entries.md").read_text()
    assert {"03/05/2026", "03/07/2026"} <= set(_dates(entries))
    assert "10/01/2026" not in entries
    ex = json.loads((d / "exhibit_map.json").read_text())["history printout.pdf"]
    assert f"Tetanus (Tdap) vaccine given by R. Ames, RN. (Exhibit {ex} - p. 1)" in entries
    assert f"Tuberculin skin test (PPD) placed by R. Ames, RN. (Exhibit {ex} - p. 1)" in entries
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
    d = _assembled(job_dir, firm, data_root, {"map-01.md": printout, "map-02.md": note})
    clusters = (d / "clusters.md").read_text()
    assert clusters.count("##### CLUSTER 2026-03-05") == 1 and "(2 fragments)" in clusters
    assert "03/05/2026" not in (d / "entries.md").read_text()
