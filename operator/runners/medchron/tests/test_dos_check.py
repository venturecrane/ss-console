"""The date-of-service check: every billed visit is in the chronology, before
the incident, explained, billed with no record in the file, or a MISSED VISIT.
Each case builds the three artifacts the check reads (billing_extract.jsonl,
extracted.jsonl + text/, the entries file) and asserts one class."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from medchron import __main__ as cli, config as config_mod, dag, dos_check, job as job_mod
from medchron.stages import dos_check as stage
from medchron.stages.base import StageRun
from medchron_testkit import FIRM_CONFIG, FakeSeat

INCIDENT = "2026-01-15"  # medchron_testkit.job_yaml


def _slug(data_root: Path) -> Path:
    d = data_root / "example-matter"
    (d / "text").mkdir(parents=True, exist_ok=True)
    return d


def _bills(d: Path, chunks: list[dict]) -> None:
    (d / "billing_extract.jsonl").write_text(json.dumps({"file": "Bill.pdf", "chunks": chunks}) + "\n")


def _records(d: Path, pages: dict[int, str]) -> None:
    (d / "extracted.jsonl").write_text(json.dumps({"id": "r1", "name": "Clinic Records.pdf"}) + "\n")
    (d / "text" / "r1.txt").write_text("".join(f"[p.{n}]\n{t}\n" for n, t in pages.items()))


def _entries(run: Path, dates: list[str]) -> None:
    run.mkdir(parents=True, exist_ok=True)
    body = "\n\n".join(f"{d}\nExample Clinic | Office visit\nSeen for back pain. (Exhibit 1 - p. 1)" for d in dates)
    (run / "entries_scoped_final.md").write_text(body + "\n")


def _bill(date: str, *, kind: str = "MEDICAL_BILL", desc: str = "Office visit", charge: str = "150.00") -> dict:
    return {
        "doc_type": kind,
        "provider": "Example Clinic",
        "patient": "",
        "line_items": [{"date": date, "description": desc, "charge": charge, "page": 1}],
    }


def _check(data_root: Path, run: Path) -> dict:
    return dos_check.check(_slug(data_root), run, INCIDENT, None, (run / "entries_scoped_final.md").read_text())


@pytest.fixture
def run(data_root: Path) -> Path:
    return _slug(data_root) / "runs" / "alpha"


def test_billed_date_in_the_chronology(data_root: Path, run: Path) -> None:
    _bills(_slug(data_root), [_bill("03/02/2026")])
    _records(_slug(data_root), {1: "Visit 03/02/2026"})
    _entries(run, ["03/02/2026"])
    assert _check(data_root, run)["classes"]["in_chronology"] == 1


def test_missed_visit_when_a_record_page_carries_the_date(data_root: Path, run: Path) -> None:
    _bills(_slug(data_root), [_bill("03/09/2026")])
    _records(_slug(data_root), {4: "Date of service: 3/9/2026 follow-up"})
    _entries(run, ["03/02/2026"])
    rep = _check(data_root, run)
    assert rep["classes"]["missed_visit"] == 1
    assert rep["missed_visits"] == [
        {"date": "2026-03-09", "record_file": "Clinic Records.pdf", "record_page": 4, "nearest_entry_days": 7}
    ]


def test_billed_with_no_record_is_a_records_gap_not_a_miss(data_root: Path, run: Path) -> None:
    _bills(_slug(data_root), [_bill("03/09/2026")])
    _records(_slug(data_root), {1: "Visit 03/02/2026"})
    _entries(run, ["03/02/2026"])
    rep = _check(data_root, run)
    assert rep["classes"]["billed_no_record"] == 1 and rep["classes"]["missed_visit"] == 0


def test_date_match_is_bounded(data_root: Path, run: Path) -> None:
    # "2/3/26" must not be found inside "12/3/26" or "2/3/2601": that would
    # report a record page for a date the record never names.
    _bills(_slug(data_root), [_bill("02/03/2026")])
    _records(_slug(data_root), {1: "Seen 12/3/26 and 2/3/2601"})
    _entries(run, ["03/02/2026"])
    assert _check(data_root, run)["classes"]["billed_no_record"] == 1


def test_pre_incident_date_is_by_design(data_root: Path, run: Path) -> None:
    _bills(_slug(data_root), [_bill("01/03/2026")])
    _records(_slug(data_root), {1: "Visit 01/03/2026"})
    _entries(run, ["03/02/2026"])
    assert _check(data_root, run)["classes"]["pre_incident"] == 1


def test_explained_date(data_root: Path, run: Path) -> None:
    _bills(_slug(data_root), [_bill("03/09/2026")])
    _records(_slug(data_root), {1: "Visit 03/09/2026"})
    _entries(run, ["03/02/2026"])
    (run / dos_check.EXPLAINED_FILE).write_text(json.dumps([{"date": "2026-03-09", "reason": "no-show"}]))
    assert _check(data_root, run)["classes"]["explained"] == 1


@pytest.mark.parametrize(
    "kind,desc,charge",
    [
        ("VENDOR_INVOICE", "Records copy", "25.00"),
        ("MEDICAL_BILL", "Payment - insurance", "150.00"),
        ("MEDICAL_BILL", "Contractual adjustment", "150.00"),
        ("MEDICAL_BILL", "Office visit", "0.00"),
        ("MEDICAL_BILL", "Office visit", "(150.00)"),
    ],
)
def test_non_visits_are_not_billed_dates(data_root: Path, run: Path, kind: str, desc: str, charge: str) -> None:
    _bills(_slug(data_root), [_bill("03/09/2026", kind=kind, desc=desc, charge=charge)])
    _records(_slug(data_root), {1: "Visit 03/09/2026"})
    _entries(run, ["03/02/2026"])
    assert _check(data_root, run)["billed_dates"] == 0


def test_itemless_bill_contributes_its_printed_dates(data_root: Path, run: Path) -> None:
    chunk = {
        "doc_type": "LEDGER",
        "provider": "Example PT",
        "patient": "",
        "line_items": [],
        "line_items_omitted": True,
        "date_first": "03/02/2026",
        "date_last": "03/20/2026",
        "page_first": 1,
    }
    _bills(_slug(data_root), [chunk])
    _records(_slug(data_root), {2: "PT visit 03/20/2026"})
    _entries(run, ["03/02/2026"])
    rep = _check(data_root, run)
    assert rep["quality"]["itemless_bills"] == 1
    assert rep["classes"] == {**dict.fromkeys(dos_check.CLASSES, 0), "in_chronology": 1, "missed_visit": 1}


def test_duplicate_visit_counts_once_and_undated_items_are_counted(data_root: Path, run: Path) -> None:
    items = [
        {"date": "03/02/2026", "description": "Visit", "charge": "150", "page": 1},
        {"date": "03/02/2026", "description": "X-ray", "charge": "90", "page": 1},
        {"date": None, "description": "Visit", "charge": "150", "page": 1},
        {"date": "13/45/2026", "description": "Visit", "charge": "150", "page": 1},
    ]
    _bills(
        _slug(data_root),
        [{"doc_type": "MEDICAL_BILL", "provider": "Example Clinic", "patient": "", "line_items": items}],
    )
    _records(_slug(data_root), {1: "Visit 03/02/2026"})
    _entries(run, ["03/02/2026"])
    rep = _check(data_root, run)
    assert rep["billed_dates"] == 1
    assert rep["quality"]["undated_items"] == 1 and rep["quality"]["unreadable_dates"] == 1


def test_billing_pages_are_not_record_pages(data_root: Path, run: Path) -> None:
    # The bill's own page carries the date; that is not a medical record.
    d = _slug(data_root)
    (d / "billing_extract.jsonl").write_text(
        json.dumps({"file": "Clinic Records.pdf", "chunks": [_bill("03/09/2026")]}) + "\n"
    )
    _records(d, {1: "Charge 03/09/2026 office visit $150"})
    _entries(run, ["03/02/2026"])
    assert _check(data_root, run)["classes"]["billed_no_record"] == 1


def _two_providers_one_day(d: Path) -> None:
    pt = _bill("03/09/2026")
    img = {**_bill("03/09/2026"), "provider": "Valley Radiology Partners"}
    _bills(d, [pt, img])
    _records(d, {1: "Visit 03/09/2026", 2: "Valley Radiology Partners MRI report 03/09/2026"})


def test_second_provider_on_a_covered_date_is_not_hidden_by_the_first(data_root: Path, run: Path) -> None:
    # The chronology names the clinic visit that day; the imaging visit billed
    # the same day must not ride on it into in_chronology.
    _two_providers_one_day(_slug(data_root))
    _entries(run, ["03/09/2026"])
    rep = _check(data_root, run)
    assert rep["classes"]["in_chronology"] == 1 and rep["classes"]["provider_unmatched"] == 1
    assert rep["provider_unmatched"][0]["billing_provider"] == "Valley Radiology Partners"


def test_authored_provider_match_names_the_billing_label(data_root: Path, run: Path) -> None:
    _two_providers_one_day(_slug(data_root))
    _entries(run, ["03/09/2026"])
    rd = _slug(data_root)
    rep = dos_check.check(
        rd,
        run,
        INCIDENT,
        None,
        (run / "entries_scoped_final.md").read_text(),
        {"Example Clinic": ["Valley Radiology Partners"]},
    )
    assert rep["classes"]["in_chronology"] == 2


def test_a_billing_name_no_record_uses_rides_on_the_covered_date(data_root: Path, run: Path) -> None:
    # A physician group billing for hospital care under a name the records
    # never print: the chronology cannot name it, so it is not a miss.
    d = _slug(data_root)
    _bills(d, [_bill("03/09/2026"), {**_bill("03/09/2026"), "provider": "Desert Emergency Physicians"}])
    _records(d, {1: "Visit 03/09/2026"})
    _entries(run, ["03/09/2026"])
    assert _check(data_root, run)["classes"]["in_chronology"] == 2


def test_no_billing_extraction_is_flagged_not_a_clean_pass(data_root: Path, run: Path) -> None:
    _records(_slug(data_root), {1: "Visit 03/09/2026"})
    _entries(run, ["03/02/2026"])
    q = _check(data_root, run)["quality"]
    assert q["billing_extract_missing"] == 1 and q["bill_chunks"] == 0
    _bills(_slug(data_root), [_bill("03/02/2026")])
    q = _check(data_root, run)["quality"]
    assert q["billing_extract_missing"] == 0 and q["bill_chunks"] == 1


# ---- the stage and its lever ----------------------------------------------
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


def _firm(tmp_path: Path, mode: str | None) -> Path:
    data = copy.deepcopy(FIRM_CONFIG)
    if mode:
        data["levers"]["dos_check"] = mode
    p = tmp_path / f"firm-{mode}.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


def _missed_scene(data_root: Path, job_dir: Path) -> Path:
    unit = job_mod.load(job_dir).units[0].unit
    rd = _slug(data_root) / "runs" / unit
    _bills(_slug(data_root), [_bill("03/09/2026")])
    _records(_slug(data_root), {4: "Date of service 03/09/2026"})
    _entries(rd, ["03/02/2026"])
    return rd


@pytest.mark.parametrize("mode", [None, "report"])
def test_report_mode_never_holds_but_writes_the_report(tmp_path, job_dir, data_root, mode) -> None:
    rd = _missed_scene(data_root, job_dir)
    log: list[str] = []
    assert stage.run(_sr(job_dir, _firm(tmp_path, mode), data_root, log)) == 0
    assert json.loads((rd / "dos_report.json").read_text())["classes"]["missed_visit"] == 1
    assert any("MISSED VISIT 2026-03-09" in line for line in log)


def test_hold_mode_holds_on_a_missed_visit_and_explain_date_releases_it(tmp_path, job_dir, data_root) -> None:
    rd = _missed_scene(data_root, job_dir)
    firm = _firm(tmp_path, "hold")
    log: list[str] = []
    assert stage.run(_sr(job_dir, firm, data_root, log)) == 1
    unit = rd.name
    assert cli.main(["explain-date", str(job_dir), unit, "2026-03-09", "patient did not attend"]) == 0
    assert stage.run(_sr(job_dir, firm, data_root, [])) == 0


def test_rehearse_reports_without_writing(tmp_path, job_dir, data_root) -> None:
    rd = _missed_scene(data_root, job_dir)
    lines = stage.rehearse(_sr(job_dir, _firm(tmp_path, None), data_root, []))
    assert any("MISSED VISIT 2026-03-09" in line for line in lines)
    assert not (rd / "dos_report.json").exists()


def test_not_measured_is_said_out_loud(tmp_path, job_dir, data_root) -> None:
    rd = _missed_scene(data_root, job_dir)
    (_slug(data_root) / "billing_extract.jsonl").unlink()
    log: list[str] = []
    assert stage.run(_sr(job_dir, _firm(tmp_path, "hold"), data_root, log)) == 0
    assert any("NOT MEASURED" in line for line in log) and (rd / "dos_report.json").is_file()


def test_no_entries_file_is_refused_not_a_missed_visit(tmp_path, job_dir, data_root) -> None:
    rd = _missed_scene(data_root, job_dir)
    (rd / "entries_scoped_final.md").unlink()
    assert stage.run(_sr(job_dir, _firm(tmp_path, "hold"), data_root, [])) == 2
    assert dag.BY_NAME["dos_check"].exit_map[2][0] != dag.BY_NAME["dos_check"].exit_map[1][0]


def test_explain_date_refuses_a_non_iso_date_without_a_trace(job_dir, data_root, capsys) -> None:
    unit = job_mod.load(job_dir).units[0].unit
    (_slug(data_root) / "runs" / unit).mkdir(parents=True, exist_ok=True)
    assert cli.main(["explain-date", str(job_dir), unit, "03/09/2026", "no-show"]) == 2
    assert "YYYY-MM-DD" in capsys.readouterr().out


def test_lever_accepts_only_report_or_hold() -> None:
    data = copy.deepcopy(FIRM_CONFIG)
    data["levers"]["dos_check"] = "block"
    assert any("levers.dos_check" in p for p in config_mod.validate(data))
    data["levers"]["dos_check"] = "hold"
    assert not [p for p in config_mod.validate(data) if "dos_check" in p]


def test_dag_runs_dos_check_after_coverage_and_build_doc_reopens_it() -> None:
    names = [s.name for s in dag.STAGES]
    assert names.index("coverage_gate") < names.index("dos_check") < names.index("billing_chart")
    build = next(s for s in dag.STAGES if s.name == "build_doc")
    assert "dos_check" in build.invalidates
    assert dag.BY_NAME["dos_check"].rehearse is stage.rehearse
