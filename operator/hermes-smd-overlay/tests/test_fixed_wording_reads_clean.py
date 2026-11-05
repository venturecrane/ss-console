"""Every reply the broker renders passes this seat's own reply checks.

Since 2026-11-04 a job's reply, held or delivered, is rendered by the broker
(``operator/workspace_broker/fixed_wording``) and the model may send no other
words. The overlay still runs its output checklist over that text on the way
out, with ``system_vocabulary`` on (a job's delivery reply). A rendered
sentence the checklist held would be a reply the model can neither send nor
change: the broker refuses any other words, and the seat holds these. So the
renderer's output, for a row of every lane and report, must read clean on both
surfaces a bound reply meets.

FALSIFIER: add a sentence that names "the job's files", an ISO timestamp or an
em dash to a renderer and this fails on that lane.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from shared import output_checklist as oc

OPERATOR = Path(__file__).resolve().parents[2]
if str(OPERATOR) not in sys.path:
    sys.path.insert(0, str(OPERATOR))

from workspace_broker import delivered_matter, delivered_wording, fixed_wording  # noqa: E402

READS = {
    "allowance": {
        "authored": True,
        "unit": "pages",
        "remaining": 4210,
        "month": "the cycle ending Dec 14",
    },
    "job_pages_credited": 0,
}


def _files(*pairs: tuple[str, str]) -> str:
    return json.dumps({"files": [{"role": r, "name": n} for r, n in pairs]})


ROWS = [
    (
        "litigation_job",
        {
            "state": "delivered",
            "folder_name": "Litigation Status",
            "matters_total": 64,
            "matters_reread": 7,
            "flags_new": 3,
            "delivery_json": _files(
                ("workbook", "Litigation status 11-04-26 (Operator 0000AA).xlsx"),
                ("per_case_workbook", "Litigation status by case 11-04-26 (Operator 0000AA).xlsx"),
                ("printout_pdf", "Court dates 11-04-26 (Operator 0000AA).pdf"),
            ),
        },
    ),
    (
        "litigation_job",
        {
            "state": "delivered",
            "report": "deadlines",
            "folder_name": "Deadlines",
            "counts_json": json.dumps(
                {key: 3 for key, _label in delivered_wording.DEADLINE_LABELS}
            ),
            "delivery_json": _files(
                ("workbook", "Litigation deadlines 10-15-26 (Operator 0000AA).xlsx"),
                ("statute", "Statute report 10-15-26 (Operator 0000AA).xlsx"),
            ),
        },
    ),
    (
        "litigation_job",
        {
            "state": "delivered",
            "report": "reassign",
            "reassign_json": json.dumps(
                {
                    "from_names": ["Pat Doe", "Sam"],
                    "to_name": "Dana Roe",
                    "attorney_names": ["Lee Example"],
                    "litigation_only": True,
                    "statuses": ["Open", "Pending"],
                }
            ),
            "counts_json": json.dumps(
                {"cases_moved": 4, "tasks_moved": 9, "cases_outside_scope": 2, "cases_skipped": 1}
            ),
        },
    ),
    (
        "litigation_job",
        {
            "state": "delivered",
            "report": "discovery12",
            "folder_name": "Litigation Status",
            "counts_json": json.dumps({"d12_declined": 2}),
            "delivery_json": _files(
                ("discovery_workbook", "Discovery.xlsx"), ("printout_pdf", "Discovery.pdf")
            ),
        },
    ),
    (
        "casefile_job",
        {
            "state": "delivered",
            "mode": "full",
            "matters_total": 40,
            "matters_read": 9,
            "matters_current": 31,
            "matters_held": 2,
            "lines_proposed": 4,
            "metrics_json": json.dumps({"billing_count": {"Open": 38, "Pending": 2}}),
            "delivery_json": _files(("workbook", "Case file review 11-04-26.xlsx")),
        },
    ),
    (
        "casefile_job",
        {
            "state": "delivered",
            "mode": "apply",
            "outcomes_json": json.dumps(
                [{"n": 1, "outcome": "completed"}, {"n": 2, "outcome": "x"}]
            ),
        },
    ),
    (
        "medchron_job",
        {
            "state": "delivered",
            "matter_number": "900201",
            "documents": 48,
            "pages": 790,
            "delivery_json": json.dumps({"folders": ["a", "b"]}),
        },
    ),
    (
        "medchron_job",
        {
            "state": "delivered",
            "matter_number": "900201",
            "delivery_json": json.dumps(
                {
                    "already_current": {
                        "requested": 5,
                        "files": 6,
                        "by_reason": {key: {"files": 1} for key in delivered_matter.CURRENT_KINDS},
                        "review_file": "Records review.docx",
                    }
                }
            ),
        },
    ),
    (
        "demand_job",
        {
            "state": "delivered",
            "matter_number": "900201",
            "matter_id": "m",
            "file_to_matter_id": "m",
            "delivery_json": _files(
                ("demand", "Demand.Doe.docx"),
                ("gap_audit", "Gap Audit.docx"),
                ("attorney_notes", "Notes.docx"),
            ),
        },
    ),
    (
        "demand_job",
        {
            "state": "delivered",
            "matter_number": "900201",
            "matter_id": "m",
            "file_to_matter_id": "x",
            "delivery_json": json.dumps(
                {
                    "files": [{"role": "coverage_report", "name": "Coverage.docx"}],
                    "coverage_report": True,
                }
            ),
        },
    ),
    (
        "demand_job",
        {
            "state": "held",
            "matter_number": "900201",
            "reason": "the file does not say whether this is a pre-suit or a litigation demand: x",
        },
    ),
    ("demand_job", {"state": "held", "matter_number": "900201", "reason": "filing refused: x"}),
    (
        "drafting_job",
        {
            "state": "delivered",
            "matter_number": "900201",
            "matter_id": "m",
            "file_to_matter_id": "m",
            "delivery_json": json.dumps(
                {
                    "files": [
                        {"role": "draft", "name": "Mediation Brief.docx"},
                        {"role": "attorney_notes", "name": "Attorney Notes.docx"},
                    ],
                    "markers": [
                        {"kind": "ATTORNEY", "text": "settlement authority"},
                        {"kind": "NOT IN RECORD", "text": "date of surgery"},
                        {"kind": "CLIENT", "text": "employer"},
                    ],
                    "caption_discrepancies": [
                        {
                            "field": "court",
                            "document_value": "SUPERIOR COURT OF ARIZONA",
                            "record_value": "Superior Court",
                        }
                    ],
                    "caption_corrections": [
                        {
                            "field": "case_number",
                            "from": "CV2023",
                            "to": "CV2023-0001",
                            "source_document": "Complaint.pdf",
                        }
                    ],
                }
            ),
        },
    ),
    ("drafting_job", {"state": "held", "matter_number": "900201", "reason": "gate_refused: x"}),
    (
        "drafting_job",
        {
            "state": "held",
            "matter_number": "900201",
            "reason": "request_incomplete: the request is missing what the draft needs: the deponent",
        },
    ),
]


@pytest.mark.parametrize(
    ("kind", "row"),
    ROWS,
    ids=[f"{k}-{r.get('report') or r.get('mode') or r['state']}" for k, r in ROWS],
)
@pytest.mark.parametrize("surface", [oc.STAFF_SEND, oc.EXTERNAL_REPLY])
def test_every_rendered_reply_reads_clean(kind: str, row: dict, surface: str) -> None:
    (text,) = fixed_wording.render(kind, row, READS)
    found = [
        v for v in oc.check(text, surface, job_reply=True) if v.rule not in oc.REPORT_ONLY_RULES
    ]
    assert found == [], [(v.rule, v.detail) for v in found]
