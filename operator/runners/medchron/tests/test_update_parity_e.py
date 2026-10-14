"""Parity E: the seat never READ a record the laptop read (offline run,
2026-10-13). Each test fails on f534d279 and passes after. Every value is
invented.

E1  a firm billing/lien name rule dropped "Medical and Billing Records";
E2  an uncited firm-numbered "Exhibit N-" file in the chronology folder was
    dropped as delivered material;
E3  a medical-legal expert report (a life care plan) was itemized as
    treatment and sent to vision in full.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import update_testkit as K
import yaml

from medchron import config as config_mod, decisions, job as job_mod
from medchron.stages import download as download_stage, extract as extract_stage
from medchron.stages.base import StageRun
from medchron_testkit import FakeSeat, doc_row, make_pdf, seed_seat_files

PROSE = "Patient seen in clinic for neck pain after the collision; plan to continue therapy. " * 8
MEDICAL, CHRON = "fold-med", "fold-chron"
FOLDERS = [
    {"id": MEDICAL, "name": "MEDICAL", "parentId": None, "path": "/MEDICAL"},
    {"id": CHRON, "name": "CHRONOLOGY", "parentId": None, "path": "/CHRONOLOGY"},
]
BILLING_RULE = {"match": r"(?i)\bbill", "reason": "billing or lien record"}


def _sr(job_dir: Path, firm: Path, data_root: Path, seat: FakeSeat, ids: list[str]) -> StageRun:
    body = yaml.safe_load((job_dir / "job.yaml").read_text())
    body["selection"] = {"include_file_ids": ids}
    (job_dir / "job.yaml").write_text(yaml.safe_dump(body, sort_keys=False))
    cfg = yaml.safe_load(firm.read_text())
    cfg["coverage"]["exclusions"].append(BILLING_RULE)
    firm.write_text(yaml.safe_dump(cfg, sort_keys=False))
    job = job_mod.load(job_dir)
    seed_seat_files(data_root, seat)
    sr = StageRun(
        job=job,
        cfg=config_mod.load(str(firm)),
        unit=job.units[0],
        slug_dir=data_root / "example-matter",
        decided={},
        log=lambda _m: None,
        seat_factory=lambda: seat,
    )
    decisions.selection(sr.job, sr.cfg, sr.slug_dir, dry_run=False)
    return sr


def _pulled(sr: StageRun) -> set[str]:
    rows = map(json.loads, (sr.slug_dir / "raw_manifest.jsonl").read_text().splitlines())
    return {r["id"] for r in rows if r.get("ok") and not r.get("duplicate_of")}


# ---- E1 ---------------------------------------------------------------------------
def test_a_medical_and_billing_records_file_is_read_and_a_billing_statement_is_not(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    rec, bill = make_pdf([PROSE]), make_pdf(["Statement of charges, balance due."])
    docs = [
        doc_row("mb", "Example Chiropractic Medical and Billing Records.pdf", MEDICAL, len(rec)),
        doc_row("bs", "Example Chiropractic Billing Statement.pdf", MEDICAL, len(bill)),
        doc_row("br", "Example Chiropractic Billing Records.pdf", MEDICAL, len(bill) + 1),
    ]
    seat = FakeSeat(docs, list(FOLDERS), {"mb": rec, "bs": bill, "br": bill + b" "})
    sr = _sr(job_dir, firm_config_path, data_root, seat, ["mb", "bs", "br"])
    assert download_stage.run(sr) == 0
    assert _pulled(sr) == {"mb"}  # the old code excluded "mb" as a billing or lien record
    excluded = {x["id"]: x["why"] for x in json.loads((sr.slug_dir / "named_excluded.json").read_text())}
    assert excluded == {"bs": "billing or lien record", "br": "billing or lien record"}


# ---- E2 ---------------------------------------------------------------------------
def _seed_plan(sr: StageRun, exhibits: list[dict]) -> None:
    ud = sr.slug_dir / "runs" / sr.unit.unit / "update"
    ud.mkdir(parents=True, exist_ok=True)
    (ud / "base.md").write_text(K.base_md())  # its Exhibit List names Exhibit 1 only
    plan = {"folder_id": CHRON, "exhibits": exhibits, "top_exhibit": 6}
    (ud / "update_plan.json").write_text(json.dumps(plan))


def test_an_uncited_firm_numbered_exhibit_in_the_chronology_folder_is_read_once(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    delivered, firm6 = make_pdf(["Delivered exhibit one. " * 20]), make_pdf([PROSE, PROSE])
    docs = [
        doc_row("e1", "Exhibit 1 - Example Clinic (Medical Records).pdf", CHRON, len(delivered)),
        doc_row("e6", "Exhibit 6- Example Rehab records.pdf", CHRON, len(firm6)),
        doc_row("tw", "Example Rehab records.pdf", MEDICAL, len(firm6)),  # its byte-twin
    ]
    seat = FakeSeat(docs, list(FOLDERS), {"e1": delivered, "e6": firm6, "tw": firm6})
    sr = _sr(job_dir, firm_config_path, data_root, seat, ["e1", "e6", "tw"])
    _seed_plan(
        sr,
        [
            {"exhibit": 1, "id": "e1", "name": "Exhibit 1 - Example Clinic (Medical Records).pdf"},
            {"exhibit": 6, "id": "e6", "name": "Exhibit 6- Example Rehab records.pdf"},
        ],
    )
    assert download_stage.run(sr) == 0
    pulled = _pulled(sr)
    assert len(pulled) == 1 and pulled <= {"e6", "tw"}  # the old code pulled only the twin, never e6 itself
    rows = [json.loads(x) for x in (sr.slug_dir / "raw_manifest.jsonl").read_text().splitlines()]
    assert {r["id"] for r in rows} == {"e6", "tw"}  # both pulled, one kept by content
    excluded = {x["id"]: x["why"] for x in json.loads((sr.slug_dir / "named_excluded.json").read_text())}
    assert excluded == {"e1": "in the delivered chronology's own folder"}


def test_an_uncited_firm_numbered_exhibit_named_alone_is_pulled(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    firm6 = make_pdf([PROSE])
    seat = FakeSeat(
        [doc_row("e6", "Exhibit 6- Example Rehab records.pdf", CHRON, len(firm6))], list(FOLDERS), {"e6": firm6}
    )
    sr = _sr(job_dir, firm_config_path, data_root, seat, ["e6"])
    _seed_plan(sr, [{"exhibit": 6, "id": "e6", "name": "Exhibit 6- Example Rehab records.pdf"}])
    assert download_stage.run(sr) == 0  # the old code held: every named document excluded
    assert _pulled(sr) == {"e6"}


# ---- E3 ---------------------------------------------------------------------------
LCP_PAGE1 = "Life Care Plan prepared for counsel. Future medical care needs and costs. " * 3


def _select_sr(tmp: Path) -> SimpleNamespace:
    (tmp / "text").mkdir(parents=True)
    (tmp / "c").mkdir()
    return SimpleNamespace(slug_dir=tmp, unit=SimpleNamespace(unit="alpha"))


def test_a_life_care_plan_is_reviewed_and_not_itemized(tmp_path: Path) -> None:
    from medchron.stages import update_finish as UF, update_select as US
    from medchron.update import select as S

    sr = _select_sr(tmp_path)
    for rid, name, text in (
        ("lc", "Exhibit 7- Example Future Care Assessment.pdf", PROSE),  # by the firm file's name
        ("lt", "scan 0042.pdf", LCP_PAGE1),  # by its first page
    ):
        pdf = tmp_path / f"{rid}.pdf"
        pdf.write_bytes(make_pdf([text, PROSE]))
        (tmp_path / "text" / f"{rid}.txt").write_text(f"[p.1]\n{text}\n[p.2]\n{PROSE}\n")
        r = {"id": rid, "path": str(pdf), "ext": ".pdf", "name": name.rsplit(".", 1)[0], "sha256": rid}
        firm = name if name.startswith("Exhibit") else None
        rec, unit_row = US._exhibit(sr, r, 7, set(), tmp_path / "c", S.Earlier(), firm)  # type: ignore[arg-type]
        assert rec["read"] == [] and unit_row["compose"] is False, rid  # the old code composed both pages
        assert rec.get("not_itemized")
        assert "reviewed and not itemized" in UF.uncited_name(rec)


def test_a_treatment_record_is_still_itemized(tmp_path: Path) -> None:
    from medchron.stages import update_select as US
    from medchron.update import select as S

    sr = _select_sr(tmp_path)
    pdf = tmp_path / "t.pdf"
    pdf.write_bytes(make_pdf([PROSE]))
    (tmp_path / "text" / "t.txt").write_text(f"[p.1]\n{PROSE}\n")
    r = {"id": "t", "path": str(pdf), "ext": ".pdf", "name": "Example Rehab visit notes", "sha256": "t"}
    rec, unit_row = US._exhibit(sr, r, 7, set(), tmp_path / "c", S.Earlier())  # type: ignore[arg-type]
    assert rec["read"] == [1] and unit_row["compose"] and not rec.get("not_itemized")


def test_on_an_update_vision_reads_only_page_one_of_a_scanned_life_care_plan(tmp_path: Path) -> None:
    d = tmp_path / "m"
    (d / "raw").mkdir(parents=True)
    rows = []
    for rid, name in (("lc", "Example Life Care Plan"), ("rx", "Example Rehab records")):
        p = d / "raw" / f"{rid}.pdf"
        p.write_bytes(make_pdf(["", "", ""]))  # three scanned pages
        rows.append({"id": rid, "name": name, "folder": "/MEDICAL", "ext": ".pdf", "path": str(p), "ok": True})
    (d / "raw_manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    sr = SimpleNamespace(slug_dir=d, job=SimpleNamespace(update=object()), log=lambda _m: None)
    assert extract_stage.run(sr) == 0  # type: ignore[arg-type]
    queue = {q["id"]: q for q in json.loads((d / "scan_queue.json").read_text())}
    assert queue["lc"].get("vision_pages") == [1]  # the old code queued all three pages
    assert not queue["rx"].get("vision_pages")  # a treatment record is read in full


# ---- billing pages inside a medical-and-billing file ------------------------------
CLINICAL_1 = (
    "Example Spine Works\nDate of visit 02/03/2026\nSubjective: neck pain 6 of 10 after the collision.\n"
    "Assessment: cervical strain. Plan: adjustment and home exercise, return in one week. " * 2
)
CLINICAL_2 = (
    "Example Spine Works\nDate of visit 02/10/2026\nSubjective: neck pain 4 of 10, improving.\n"
    "Exam: reduced rotation to the left. Plan: continue care twice weekly. " * 2
)
CMS_1500 = (
    "HEALTH INSURANCE CLAIM FORM\nAPPROVED BY NATIONAL UNIFORM CLAIM COMMITTEE (NUCC) CMS-1500\n"
    "Example Spine Works\n24A 02/03/2026 98941 65.00\n24A 02/10/2026 98941 65.00\nTOTAL CHARGE 130.00"
)
LEDGER = (
    "Example Spine Works\nPatient Ledger\nDate of Service Code Charges Payments Balance\n"
    "02/03/2026 98941 65.00 0.00 65.00\n02/10/2026 98941 65.00 0.00 130.00\n"
    "02/17/2026 98941 65.00 0.00 195.00\nBalance due 195.00"
)


def test_billing_pages_in_a_medical_and_billing_file_are_not_composed_but_stay_evidence(tmp_path: Path) -> None:
    from datetime import date

    from medchron.stages import update_collect as UC, update_select as US
    from medchron.update import coverage as UCov, handwritten as H, select as S

    sr = _select_sr(tmp_path)
    (tmp_path / "runs" / "alpha" / "update").mkdir(parents=True)
    pages = [CLINICAL_1, CLINICAL_2, CMS_1500, LEDGER]
    pdf = tmp_path / "mb.pdf"
    pdf.write_bytes(make_pdf(pages))
    (tmp_path / "text" / "mb.txt").write_text("".join(f"[p.{i}]\n{t}\n" for i, t in enumerate(pages, 1)))
    r = {"id": "mb", "path": str(pdf), "ext": ".pdf", "name": "Example Spine Works Medical and Billing Records"}
    rec, unit_row = US._exhibit(sr, {**r, "sha256": "mb"}, 2, set(), tmp_path / "c", S.Earlier())  # type: ignore[arg-type]
    assert rec["read"] == [1, 2]  # the old code composed the claim form and the ledger as visits
    assert rec["excluded"] == {"billing page": 2}
    composed = Path(unit_row["text_path"]).read_text()
    assert "CLAIM FORM" not in composed and "Ledger" not in composed
    # The handwritten-date check still reads the ledger's typed dates of service.
    _, pool = UC._page_texts(sr, [rec])  # type: ignore[arg-type]
    assert H.service_dates(pool, "Example Spine Works") >= {date(2026, 2, 3), date(2026, 2, 17)}
    # A document read in full is covered: the next update does not read it again.
    (tmp_path / "runs" / "alpha" / "update" / "selection.json").write_text(
        json.dumps({"exhibits": [{**rec, "read": [], "excluded": {"billing page": 4}}]})
    )
    assert UCov.covered_sets(tmp_path, "alpha")["covered"] == ["mb"]


def test_a_clinical_page_that_mentions_a_charge_is_still_read() -> None:
    from medchron.update import billing_page as BP

    assert not BP.is_billing(CLINICAL_1 + "\nCharges 65.00 payment 0.00 balance 65.00")
    assert BP.is_billing(CMS_1500) and BP.is_billing(LEDGER)
