"""A billing name rule never drops a treatment record whose name says it is
one (2026-11-16).

WHY. An update named "Dr. Example Bill and Report.pdf" (a billing statement,
then a signed follow-up examination report) and the firm's billing rule
excluded it by its name: the visit never reached the chronology, and the reply
said none of the new records added a visit. The rescue knew only "medical",
"chart", "clinical", "notes", "visits", "treatment" and "records". The other
readers of the same rules (the coverage record, the orphan decision, the
limitations) applied them with no rescue at all. Every name here is one of the
real shapes, with invented values.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from jobs.stages import coverage, download as download_stage
from jobs.stages.name_rules import excluded_by_name
from jobs_testkit import FakeSeat, doc_row, make_pdf
import test_update_parity_e as PE
from test_update_parity_e import FOLDERS, MEDICAL, PROSE, _pulled, _sr

#: the firm's billing rule as the seat carries it (its fixed part)
RULE = (re.compile(r"(?i)\bbill|ledger|invoice|statement|charges|\blien|\bEOB\b|payment"), "billing or lien record")
REPORT = "Dr. Example Bill and Report.pdf"
IMAGING_BILL = "Example Diagn Imaging Bill.pdf"
LEDGER = "Example Ledger 09.30.2026.pdf"


@pytest.mark.parametrize(
    ("name", "why"),
    [
        (REPORT, None),
        (IMAGING_BILL, "billing or lien record"),
        (LEDGER, "billing or lien record"),
        ("Example Chiropractic Medical and Billing Records.pdf", None),
        ("Example Chiropractic Billing Records.pdf", "billing or lien record"),
        ("Example Ortho Billing Report.pdf", "billing or lien record"),
        ("Example Ortho Lien Summary.pdf", "billing or lien record"),
        ("Example Surgery Center Operative Report and Bill.pdf", None),
        ("Example Clinic H&P and Charges.pdf", None),
        ("Example PT Re-evaluation Statement.pdf", None),
        ("Dr Example Bill and Report 4.21.26.pdf", None),
        # Never rescued: words that can describe a bill or a form.
        ("Example Hospital Discharge Bill.pdf", "billing or lien record"),
        ("Example Ortho Progress Billing.pdf", "billing or lien record"),
        ("Example Imaging Summary of Charges.pdf", "billing or lien record"),
    ],
)
def test_every_reader_of_the_name_rules_agrees(name: str, why: str | None) -> None:
    assert excluded_by_name(name, [RULE]) == why
    assert coverage.classify_name(name, [RULE]) == why  # the coverage record and gate read the same answer


def test_the_limitations_never_call_a_pulled_report_excluded(firm_config_path: Path) -> None:
    """Records Reviewed re-derives an orphan's reason from the current rules
    (``limitations._orphan_reason``): through the same function, a pulled Bill
    and Report keeps its stored reason; a ledger is still the billing rule's."""
    import yaml

    from jobs.medchron import config as config_mod
    from jobs.stages import limitations

    body = yaml.safe_load(firm_config_path.read_text())
    body["coverage"]["exclusions"].append({"match": RULE[0].pattern, "reason": RULE[1]})
    firm_config_path.write_text(yaml.safe_dump(body, sort_keys=False))
    cfg = config_mod.load(str(firm_config_path))
    stored = "no unit adopted it"
    assert limitations._orphan_reason(REPORT, stored, {}, cfg) == stored
    assert limitations._orphan_reason(LEDGER, stored, {}, cfg) == "billing or lien record"


def test_an_update_pulls_a_bill_and_report_and_leaves_the_bills_and_the_ledger_out(
    job_dir: Path, firm_config_path: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(PE, "BILLING_RULE", {"match": RULE[0].pattern, "reason": RULE[1]})
    rec, claim, ledger = make_pdf([PROSE]), make_pdf(["CMS-1500 claim form"] * 3), make_pdf(["Ledger"])
    docs = [
        doc_row("kr", REPORT, MEDICAL, len(rec)),
        doc_row("im", IMAGING_BILL, MEDICAL, len(claim)),
        doc_row("lg", LEDGER, MEDICAL, len(ledger)),
    ]
    seat = FakeSeat(docs, list(FOLDERS), {"kr": rec, "im": claim, "lg": ledger})
    sr = _sr(job_dir, firm_config_path, data_root, seat, ["kr", "im", "lg"])
    assert download_stage.run(sr) == 0
    assert _pulled(sr) == {"kr"}
    excluded = {x["id"]: x["why"] for x in json.loads((sr.slug_dir / "named_excluded.json").read_text())}
    assert excluded == {"im": "billing or lien record", "lg": "billing or lien record"}
