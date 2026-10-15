"""`book`: the two workbooks from the pull, the hand kit's ``build.py`` rule
for rule (so the counts equal the kit's on the same data).

* **In litigation:** at least ``min_court_hits`` file names matching the
  firm's ``court_paper_patterns``, or a Filed date, or a Case number.
* **Deadlines workbook:** non-deleted calendar entries on litigation matters
  from 14 days back through 167 days out; times as written (an event's
  startTime is LOCAL in its own zone, never converted); entries whose subject
  starts HIDDEN sorted last in gray. Tabs: Next 14 days, Last 14 days, then
  one per category by subject words (mediation, CMC, discovery, trial and
  MSC, other, in that order), OPEN TASKS (due up to 30 days out, overdue
  included), CASES.
* **Statute workbook:** a statute date set, no Filed date and no Case number;
  upcoming through 182 days, and passed on Open matters. The court-named
  document count is shown because a file can hold filed papers never
  entered.

Every value is read from the pull; nothing is computed but day counts.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from pathlib import Path
from typing import Any

HORIZON_DAYS, LOOKBACK_DAYS, STATUTE_DAYS, TASK_DAYS = 167, 14, 182, 30
CATS = [
    ("MEDIATION", re.compile(r"mediat", re.I)),
    ("CMC", re.compile(r"\bCMC\b|case management|\bCMP\b", re.I)),
    (
        "DISCOVERY",
        re.compile(
            r"discover|interrog|\bS?ROGS?\b|\bRFPs?\b|\bRFAs?\b|request(s)? for (production|admission)|demand for (production|inspection|exchange|expert)"
            r"|expert (exchange|witness|designation)|\bdepo|subpoena|\bSDT\b|motion to compel|meet and confer|\bIME\b|\b2034\b|\b2030\b|\b2031\b|\b2033\b|\b2025\b",
            re.I,
        ),
    ),
    (
        "TRIAL & MSC",
        re.compile(r"trial|\bMSC\b|\bTSC\b|settlement conf|issue conf|\bjury\b|\bin limine\b|peremptory", re.I),
    ),
]
TABS = ["Next 14 days", "Last 14 days", "DISCOVERY", "CMC", "MEDIATION", "TRIAL & MSC", "OTHER"]
HDR = ["Date", "Time", "Day of week", "Case", "Matter #", "What", "Attendees", "Hidden", "Status",
       "Responsible attorney", "Paralegal"]  # fmt: skip
WIDTH = [11, 7, 11, 34, 11, 80, 40, 7, 9, 20, 20]
TASK_HDR = ["Due", "Days overdue", "Case", "Matter #", "Task", "Assigned to", "Status", "Responsible attorney",
            "Paralegal"]  # fmt: skip
STATUTE_HDR = ["Statute date", "Days left", "Case", "Matter #", "Status", "Responsible attorney", "Paralegal",
               "Court-named documents"]  # fmt: skip


def stamp(day: dt.date) -> str:
    return f"{day.month}-{day.day}-{day.strftime('%y')}"


def file_names(today: dt.date, suffix: str) -> dict[str, str]:
    """The two files' names: the kit's, plus this job's suffix so a second run
    the same day is a different file (the filing stage refuses a name it did
    not send)."""
    return {
        "deadlines": f"Litigation deadlines {stamp(today)} (Operator {suffix}).xlsx",
        "statute": f"Statute report {stamp(today)} (Operator {suffix}).xlsx",
    }


class Rules:
    def __init__(self, staff: dict[str, str], court_rx: list[re.Pattern[str]], min_hits: int, today: dt.date) -> None:
        self.staff, self.court_rx, self.min_hits, self.today = staff, court_rx, int(min_hits), today
        self.horizon = today + dt.timedelta(days=HORIZON_DAYS)
        self.lookback = today - dt.timedelta(days=LOOKBACK_DAYS)
        self.statute_horizon = today + dt.timedelta(days=STATUTE_DAYS)

    def name(self, sid: Any) -> str:
        return self.staff.get(sid, "") if sid else ""

    def court_hits(self, m: dict[str, Any]) -> int:
        return sum(1 for n in m.get("fileNames") or [] if any(p.search(n) for p in self.court_rx))

    def is_lit(self, m: dict[str, Any]) -> bool:
        return bool(m.get("caseNumber") or m.get("filed") or self.court_hits(m) >= self.min_hits)


def case_of(m: dict[str, Any]) -> str:
    t = str(m.get("title") or "")
    return re.sub(r"^\s*\d+\s*-\s*", "", t).strip() or t


def ev_date(e: dict[str, Any]) -> dt.date | None:
    s = str(e.get("startTime") or "")
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def ev_time(e: dict[str, Any]) -> str:
    if e.get("allDay"):
        return ""
    s = str(e.get("startTime") or "")
    if len(s) >= 16 and s[11:16] != "00:00":
        h, mi = int(s[11:13]), s[14:16]
        return f"{(h - 1) % 12 + 1}:{mi}{'a' if h < 12 else 'p'}"
    return ""


def category(subject: str) -> str:
    for cat, rx in CATS:
        if rx.search(subject):
            return cat
    return "OTHER"


def event_rows(lit: list[dict[str, Any]], r: Rules) -> dict[str, list[list[Any]]]:
    rows: dict[str, list[list[Any]]] = {t: [] for t in TABS}
    for m in lit:
        for e in m.get("events") or []:
            day = ev_date(e)
            if e.get("isDeleted") or day is None or day < r.lookback or day > r.horizon:
                continue
            subj = str(e.get("subject") or "").strip()
            att = ", ".join(sorted({r.name(a.get("id")) for a in e.get("attendees") or [] if r.name(a.get("id"))}))
            row = [day, ev_time(e), day.strftime("%A"), case_of(m), m.get("number"), subj, att,
                   "Y" if subj.upper().startswith("HIDDEN") else "N", m["status"], r.name(m.get("resp")),
                   r.name(m.get("assist"))]  # fmt: skip
            if day < r.today:
                rows["Last 14 days"].append(row)
                continue
            if day <= r.today + dt.timedelta(days=13):
                rows["Next 14 days"].append(row + [category(subj)])
            rows[category(subj)].append(row)
    return rows


def task_rows(lit: list[dict[str, Any]], r: Rules) -> list[list[Any]]:
    out = []
    for m in lit:
        for t in m.get("tasks") or []:
            try:
                dd = dt.date.fromisoformat(str(t.get("dueDateOnly") or t.get("dueDate") or "")[:10])
            except ValueError:
                continue
            if dd > r.today + dt.timedelta(days=TASK_DAYS):
                continue
            who = ", ".join(sorted({r.name(a.get("id")) for a in t.get("assignees") or [] if r.name(a.get("id"))}))
            out.append([dd, (r.today - dd).days if dd < r.today else 0, case_of(m), m.get("number"),
                        str(t.get("subject") or "").strip(), who, m["status"], r.name(m.get("resp")),
                        r.name(m.get("assist"))])  # fmt: skip
    return out


def case_rows(lit: list[dict[str, Any]], r: Rules) -> list[list[Any]]:
    out = []
    for m in lit:
        dated = [(ev_date(e), e) for e in m.get("events") or [] if not e.get("isDeleted")]
        nxt: list[tuple[dt.date, str]] = sorted(
            (day, str(e.get("subject") or "").strip())
            for day, e in dated
            if day is not None and day >= r.today and not str(e.get("subject") or "").upper().startswith("HIDDEN")
        )
        out.append([case_of(m), m.get("number"), m["status"], r.name(m.get("resp")), r.name(m.get("assist")),
                    m.get("caseNumber") or "", str(m.get("filed") or "")[:10], nxt[0][0] if nxt else None,
                    nxt[0][1] if nxt else "No future date on the calendar",
                    sum(1 for x in nxt if x[0] <= r.horizon)])  # fmt: skip
    return out


def statute_rows(matters: list[dict[str, Any]], r: Rules) -> tuple[list[list[Any]], list[list[Any]]]:
    up, past = [], []
    for m in matters:
        if m.get("caseNumber") or m.get("filed") or not m.get("statute"):
            continue
        try:
            sd = dt.date.fromisoformat(str(m["statute"])[:10])
        except ValueError:
            continue
        row = [sd, (sd - r.today).days, case_of(m), m.get("number"), m["status"], r.name(m.get("resp")),
               r.name(m.get("assist")), r.court_hits(m)]  # fmt: skip
        if r.today <= sd <= r.statute_horizon:
            up.append(row)
        elif sd < r.today and m["status"] == "Open":
            past.append(row)
    return up, past


def coverage_misses(matters: list[dict[str, Any]], r: Rules) -> list[str]:
    """The kit's falsifier (``check_coverage.py``): a matter the litigation
    test excludes that still carries a future LawToolBox entry is a rule date
    the deadlines workbook would leave out. Expected empty; matter numbers."""
    today = r.today.isoformat()
    out = []
    for m in matters:
        if r.is_lit(m):
            continue
        fut = [e for e in m.get("events") or [] if not e.get("isDeleted") and str(e.get("startTime")) >= today]
        if any("LawToolBox" in (e.get("additionalData") or {}) for e in fut):
            out.append(str(m.get("number") or m.get("id")))
    return out


def counts(d: dict[str, Any], r: Rules, rows: Any, tasks: Any, cases: Any, up: Any, past: Any) -> dict[str, int]:
    lit = [m for m in d["matters"] if r.is_lit(m)]
    return {
        "matters_read": len(d["matters"]),
        "litigation": len(lit),
        "dates_next_14": len(rows["Next 14 days"]),
        "dates_last_14": len(rows["Last 14 days"]),
        "dates_ahead": sum(len(rows[t]) for t in ("DISCOVERY", "CMC", "MEDIATION", "TRIAL & MSC", "OTHER")),
        "tasks_due": len(tasks),
        "tasks_overdue": sum(1 for x in tasks if x[1] > 0),
        "cases_without_date": sum(1 for x in cases if x[7] is None),
        "statute_upcoming": len(up),
        "statute_passed_open": len(past),
    }


def summary(d: dict[str, Any], r: Rules) -> dict[str, Any]:
    """The kit's printed counts, as data (the parity test compares these)."""
    lit = [m for m in d["matters"] if r.is_lit(m)]
    rows, tasks = event_rows(lit, r), task_rows(lit, r)
    up, past = statute_rows(d["matters"], r)
    return {
        "litigation_by_attorney": dict(Counter(r.name(m["resp"]) for m in lit)),
        "litigation_by_status": dict(Counter(m["status"] for m in lit)),
        "by_fields": sum(1 for m in lit if m.get("caseNumber") or m.get("filed")),
        "tabs": {t: len(v) for t, v in rows.items()},
        "hidden": sum(1 for t in TABS for x in rows[t] if x[7] == "Y"),
        "overdue_90": sum(1 for x in tasks if x[1] > 90),
        "statute_within_30": sum(1 for x in up if x[1] <= 30),
        "statute_with_court_docs": sum(1 for x in up if x[7] > 0),
    }


def build(d: dict[str, Any], r: Rules, out_dir: Path, names: dict[str, str], want: tuple[str, ...]) -> dict[str, Any]:
    """Writes the workbooks ``want`` names; returns ``{"files": {key: path},
    "sheets": {key: {sheet: data rows}}, "counts": {...}}``."""
    from .render import deadlines_workbook, statute_workbook

    lit = [m for m in d["matters"] if r.is_lit(m)]
    rows, tasks, cases = event_rows(lit, r), task_rows(lit, r), case_rows(lit, r)
    up, past = statute_rows(d["matters"], r)
    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}
    sheets: dict[str, dict[str, int]] = {}
    if "deadlines" in want:
        files["deadlines"] = out_dir / names["deadlines"]
        sheets["deadlines"] = deadlines_workbook(files["deadlines"], rows, tasks, cases, r)
    if "statute" in want:
        files["statute"] = out_dir / names["statute"]
        sheets["statute"] = statute_workbook(files["statute"], up, past, r)
    return {"files": files, "sheets": sheets, "counts": counts(d, r, rows, tasks, cases, up, past)}
