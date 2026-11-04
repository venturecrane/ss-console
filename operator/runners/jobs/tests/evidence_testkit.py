"""Synthetic record for the evidence-first drafting tests.

Every name, figure and sentence is invented (Example / Exampletown). Nothing
here is a firm's, a client's, or a matter's.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from jobs.drafting.evidence.shapes import Candidate, DocPages

#: The repo's gate checker; the runner is installed into its own venv in CI,
#: so the checker is named the way the seat names it (record_check's env).
CHECKER = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"

BILL = "Exampletown Surgical bill.pdf"
CHART = "Exampletown Clinic chart.pdf"
DEPO = "Deposition of Pat Example.pdf"
FORM = "Plaintiff verification.pdf"

TRANSCRIPT_P1 = """Page 12
 1   Q.  Where were you going that morning?
 2   A.  To work, at the bakery on Main.
 3   Q.  Did you feel pain right after the crash?
 4   A.  Not right away, but by that night my
 5   neck was stiff and I could not turn my head.
 6   Q.  Did you see a doctor?
 7   A.  The next day.
"""
TRANSCRIPT_P2 = """Page 13
 1   Q.  Isn't it true your bills were only $1,200?
 2   A.  I don't know the total.
 3   Q.  Did you miss work?
 4   A.  Two weeks.
"""


def corpus() -> dict[str, DocPages]:
    return {
        BILL: DocPages(
            BILL,
            {
                1: "Exampletown Surgical\nStatement of account\nPatient: Pat Example",
                2: "Date of service 02/01/2026. Arthroscopic repair, left knee. Total charges 8300 00\n"
                "Payment received from Example Health Plan: $4,100.00 on 03/15/2026.",
            },
            source_path=None,
        ),
        CHART: DocPages(
            CHART,
            {
                1: "Visit of 01/20/2026. Patient reports neck pain since the collision.",
                2: "Assessment: the patient was doing well, but her knee remained swollen after therapy. "
                "Plan: follow up in four weeks. Home exercise program reviewed with the patient, who "
                "demonstrated each exercise and asked about returning to work at the bakery.",
                3: "Dr. Example states the future care will cost about $25,000.00 over five years.",
            },
        ),
        DEPO: DocPages(DEPO, {1: TRANSCRIPT_P1, 2: TRANSCRIPT_P2}),
        FORM: DocPages(
            FORM,
            {
                1: "VERIFICATION\nI declare under penalty of perjury that the foregoing is true.\nSignature: ________\nDate: ______"
            },
            source_path="/nonexistent/verification.pdf",
        ),
    }


def candidates() -> list[Candidate]:
    return [
        Candidate(BILL, 2, "figure", "Total charges 8300 00", "Surgery charges", role="billed", value="8300 00"),
        Candidate(
            BILL,
            2,
            "figure",
            "Payment received from Example Health Plan: $4,100.00",
            "Plan paid",
            role="paid",
            value="$4,100.00",
        ),
        Candidate(BILL, 2, "date", "Date of service 02/01/2026", "Surgery date", value="02/01/2026"),
        Candidate(CHART, 1, "date", "Visit of 01/20/2026", "First clinic visit", value="01/20/2026"),
        # The model typed a semicolon where the page prints a comma, and cited the wrong page.
        Candidate(CHART, 1, "quote", "the patient was doing well; but her knee remained swollen", "Knee still swollen"),
        Candidate(
            CHART,
            3,
            "figure",
            "future care will cost about $25,000.00",
            "Future care",
            role="expert_stated",
            value="$25,000.00",
        ),
        Candidate(
            DEPO,
            1,
            "testimony",
            "Not right away, but by that night my neck was stiff",
            "Neck stiff that night",
            tpage=12,
            lines=(4, 5),
        ),
        Candidate(
            DEPO,
            2,
            "figure",
            "Isn't it true your bills were only $1,200?",
            "Defense figure",
            role="opposing_question",
            value="$1,200",
        ),
        Candidate(
            FORM,
            1,
            "fact",
            "I declare under penalty of perjury that the foregoing is true.",
            "Plaintiff verified the responses",
        ),
        Candidate(
            CHART, 2, "fact", "the patient reported no prior injuries to the knee", "No prior knee injury"
        ),  # not in the record
    ]


class ScriptedDoorway:
    """Answers in order, one per call; records every call. No network."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.calls: list[dict[str, Any]] = []

    def call(self, stage: str, **kw: Any) -> SimpleNamespace:
        self.calls.append({"stage": stage, **kw})
        a = self.answers[len(self.calls) - 1] if len(self.calls) <= len(self.answers) else self.answers[-1]
        if callable(a):
            a = a(stage, kw)
        if isinstance(a, tuple):
            return SimpleNamespace(text=a[0], stop_reason=a[1])
        return SimpleNamespace(text=a, stop_reason="end_turn")
