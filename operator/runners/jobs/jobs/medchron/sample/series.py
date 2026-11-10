"""A treatment series: the serial visits of a therapy course.

A twenty-visit course is most of a record set's pages and most of a
chronology's entries, and none of it is worth authoring by hand. The spec
states the course once (dates, the pain the patient reported at each visit,
the region); this module writes each visit's daily note and flowsheets, the
two-paragraph chronology entry each note supports, and the claim lines the
course was billed on. Visits the spec authors in full (an initial
evaluation, a re-evaluation, a discharge) are listed in ``authored`` and
skipped here.
"""

from __future__ import annotations

from typing import Any

from .model import Encounter, Page, Para, us_date
from .pages import Ctx, pt_flowsheet

_ASSESS = [
    "Tolerated session well with no increase in symptoms.",
    "Improved tolerance to loaded exercise this session.",
    "Mild soreness after lifting tasks, resolved with rest.",
    "Good carryover of home program reported.",
    "Requires cues for neutral spine with lifting.",
    "Symptoms centralize with repeated extension.",
]


def _visits(s: dict[str, Any], p: dict[str, Any]) -> list[tuple[int, str, int]]:
    """(visit number, ISO date, pain) for the visits this page block holds."""
    dates = [str(d) for d in s["dates"]]
    pain = [int(x) for x in s["pain"]]
    lo, hi = str(p.get("from", dates[0])), str(p.get("to", dates[-1]))
    authored = {str(d) for d in s.get("authored") or []}
    return [(i + 1, d, pain[i]) for i, d in enumerate(dates) if lo <= d <= hi and d not in authored]


def _note(s: dict[str, Any], visit: int, day: str, pain: int, cx: Ctx) -> Page:
    total = len(s["dates"])
    lines = [
        f"Date of service: {us_date(day)}        Visit {visit} of {total}",
        "",
        f"Subjective: Patient reports {s['region']} at {pain}/10 today.",
        f"  {cx.rng.choice(('Sleeping better.', 'Sitting tolerance about 30 minutes.', 'Worse after a long drive.', 'Doing home exercises daily.', 'No new complaints.'))}",
        "",
        f"Objective: {s['objective']}",
        "",
        f"Treatment: {s['treatment']}",
        "",
        f"Assessment: {cx.rng.choice(_ASSESS)}",
        "",
        "Plan: Continue plan of care. Progress loading as tolerated.",
        "",
        f"Therapist: {s['signer']}",
    ]
    return Page(cx.doc_id, "pt_daily", "Daily Treatment Note", lines, pid=f"{s['id']}-{day}", date=day)


def _encounter(s: dict[str, Any], day: str, pain: int) -> Encounter:
    label = f"{s['id']}-{day}"
    paras = [
        Para(
            "Patient Complaints & Limitations",
            f"Reported {s['region']} at {pain}/10.",
            label,
            0,
            f"{s['region']} at {pain}/10 today",
        ),
        Para(
            "Treatment Recommendations",
            f"{s['treatment_summary']} Plan to continue the plan of care.",
            label,
            0,
            "Continue plan of care",
        ),
    ]
    return Encounter(day, str(s["provider"]), str(s["signer"]), [str(c) for c in s.get("icd") or []], paras)


def expand(s: dict[str, Any], p: dict[str, Any], cx: Ctx, encounters: list[Encounter]) -> list[Page]:
    out: list[Page] = []
    for visit, day, pain in _visits(s, p):
        out.append(_note(s, visit, day, pain, cx))
        out += pt_flowsheet({"count": int(s.get("flowsheets", 1)), "date": day, "signer": s["signer"]}, cx)
        encounters.append(_encounter(s, day, pain))
    return out


def claims(s: dict[str, Any], p: dict[str, Any]) -> list[list[list[Any]]]:
    """One claim group per visit of the whole course (authored visits
    included: they were billed too). ``dos_shift`` moves a visit's billed date
    off the note's date: the deliberate date-of-service conflict."""
    shift = {str(k): str(v) for k, v in (s.get("dos_shift") or {}).items()}
    by_date = {str(k): v for k, v in (s.get("cpt_by_date") or {}).items()}
    groups = []
    for day in (str(d) for d in s["dates"]):
        billed = shift.get(day, day)
        groups.append([[billed, code, desc, amt] for code, desc, amt in by_date.get(day, s["cpt"])])
    return groups
