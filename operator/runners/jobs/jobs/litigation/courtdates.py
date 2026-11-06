"""`courtdates`: the court dates on the firm's own calendar, merged into the list.

WHY (2026-10-29). The list's "Next court date" came only from court papers a
model read (``baseline.py``, ``read.py``). A case whose trial and settlement
conference were set by an order never saved to the file showed no court date,
although both dates sat on the matter's Smokeball calendar, entered by
LawToolBox. The deadlines report already reads that calendar with no model
(``deadlines/pull.py``); the status list never did.

What this adds, $0 and read-only: every calendar entry on each listed matter
(``GET /events?MatterId=``, the only call that proves a matter link), sorted
into the dates an attorney asks about: trial, settlement conference,
mediation, case management conference, other hearings, the discovery cutoff
and the expert exchange. Each shown date names its source.

The rules, each from what the calendar actually holds:

* A deleted entry, a HIDDEN one, one marked OFF or cancelled, and a reminder
  are never a date.
* An ALL-DAY LawToolBox entry is a rule-computed deadline ("Request phone
  appearance at MSC", "Hold summary judgment hearing"), never the event
  itself, except the two rule dates that ARE the event (the discovery cutoff
  and the expert exchange) and an entry headed by the event's kind ("TRIAL-
  Trial date"). Otherwise the set event is the timed entry.
* A staff entry worded as a deadline ("LD to submit trial dates", "MSJ
  opposition due") is not the event either, unless a hearing word comes
  first: "Hearing on Motion to Compel (file reply 5 court days before)" is the
  hearing with its note, and words in a motion's own name ("Motion for Leave
  to File") are never an instruction.
* A hearing is named by the word "hearing" (hrg, OSC, ex parte) or by what is
  heard: a motion, a demurrer, an MSJ, a petition, a status conference
  (2026-11-02: a timed motion hearing on the calendar read as no court date,
  and the list flagged the papers' date as missing from it).

The merge never touches the stored values: ``shown`` returns copies whose
"Next court date" is the earliest upcoming court date from either source, and
the court papers' value stays in the state exactly as read. When the papers
give an upcoming date the calendar does not have, both are shown and the case
is flagged.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Callable

from .tools import dump

CALENDAR_NAME = "calendar.json"
LTB = "Smokeball calendar (LawToolBox)"
STAFF = "Smokeball calendar"
PAPERS_LABEL = "court papers"
KINDS = (
    ("trial", "Trial"),
    ("msc", "Settlement conference"),
    ("hearing", "Hearing"),
    ("cmc", "Case management conference"),
    ("mediation", "Mediation"),
    ("discovery_cutoff", "Discovery cutoff"),
    ("expert_exchange", "Expert exchange"),
)
LABEL = dict(KINDS)
#: The kinds a court sets and holds: the "Next court date" is the earliest of these.
COURT = ("trial", "msc", "hearing", "cmc")
DISAGREE = "Check: the court papers give a court date the calendar does not have"

_SKIP = re.compile(
    r"^\W*(HIDDEN|OFF\b|CANCEL)|\b(RMDR|REMINDERS?|REMIND|vacated|cancell?ed|possible latest|approx|suggested)\b"
    # Staff reminders about an event, never the event (2026-10-30, the calendar
    # read on 230 cases): "TWO MONTHS BEFORE TRIAL", "M&C ... Prior to MSC",
    # "L/D to Req Remote Appearance at MSC", "Check Tentative CMC Ruling".
    # ("N days before" and "prior to" are ``_BEFORE``: after a hearing word
    # they are its note, not a reminder.)
    # A tentative ruling posts the day before the hearing it precedes, and is
    # never that hearing (2026-11-02: "Tentative CMC ruling" printed the CMC a
    # day early): "Tentative ruling - CMC", "CMC tentative ruling".
    r"|\bcheck\s+tentative\b|\btentatives?\b.{0,40}\brulings?\b|\brulings?\b.{0,40}\btentative"
    r"|^\W*(\d+/\d+\W*)?L/D\b",
    re.I,
)
_CUTOFF = re.compile(r"discovery\s+cut-?\s?off", re.I)
_EXPERT = re.compile(r"exchange\s+(of\s+)?expert|expert\s+(witness\s+)?(exchange|designation|disclosure)", re.I)
#: "*Demand expert exchange" is the deadline to DEMAND one; "Exchange expert
#: info after demand" is the exchange itself.
_EXPERT_NOT = re.compile(r"^\W*demand\b|\b(supplement\w*|rebut\w*)\b", re.I)
_DEADLINE = re.compile(
    r"\bdeadline\b|\b(LD|DL)\b|\b(file|serve|submit|request|object|deposit|pay|prepare|make|give|"
    r"stipulat\w*|statement|brief|notice)\b"
    # The papers due around a motion, never its hearing (2026-11-02): "MSJ
    # Opposition due", "Reply to MTC due", "Last day to hear MSJ", "Motion cut-off".
    r"|\b(due|opp(osition|o)?|reply|last\s+day)\b|\bcut-?\s?off\b",
    re.I,
)
#: A staff reminder about an event (2026-10-30): "TWO MONTHS BEFORE TRIAL",
#: "L/D M&C w/Def Counsel Prior to MSC".
_BEFORE = re.compile(r"\b(months?|weeks?|days?)\s+before\b|\bprior\s+to\b", re.I)
#: A motion's own name carries deadline words that are not an instruction:
#: "Motion for Leave to File", "Notice of Motion", "OSC re Failure to File
#: Proof of Service", "Motion to Compel Responses to Requests for Production".
#: Masked before the deadline test.
_TITLE = re.compile(
    r"\b(leave|failure)\s+to\s+(file|serve)\b|\bnotice\s+of\s+(motion|hearing)\b|\brequests?\s+for\s+\w+",
    re.I,
)
#: The words that name a hearing itself. An entry that names one BEFORE any
#: deadline word is that hearing; the deadline words after it are its note
#: ("Hearing on Motion to Compel (file reply 5 court days before)").
_HEARING_WORD = re.compile(r"\bhearing\b|\bhrg\b|\bOSC\b|order\s+to\s+show\s+cause|\bex[\s-]+parte\b", re.I)
#: A court-set hearing named by what is heard, with no "hearing" word: a timed
#: "11/10 - Example. Motion to Compel dept 47", "MSJ", "Demurrer". The deadline
#: and reminder rules run first, so "File motion to compel" never reaches it.
_HEARD = r"|\b(motions?|mtn|mot)\b|\bdemurrers?\b|\b(MSJ|MSA|MTC|MJOP)\b|\bpetition\b|status\s+conf"
_RULES = (
    ("hearing", re.compile(r"trial\s+setting|\bTSC\b|issue\s+conf", re.I)),
    ("msc", re.compile(r"\bMSC\b|mandatory\s+settlement|settlement\s+conf", re.I)),
    ("cmc", re.compile(r"\bCMC\b|case\s+management", re.I)),
    # Before trial and mediation (2026-11-02): "Hearing re Motion to Continue
    # Trial" is a hearing, and "Post-Mediation Status Conference" a court date.
    ("hearing", re.compile(_HEARING_WORD.pattern + _HEARD + r"|arbitration", re.I)),
    ("trial", re.compile(r"\btrial\b", re.I)),
    ("mediation", re.compile(r"mediat", re.I)),
)
#: LawToolBox writes some SET events as all-day entries headed by the event's
#: kind: "TRIAL- Trial date", "**MSC- Mandatory Settlement Conference (MSC)",
#: "CMC- Initial case management conference". Its rule deadlines carry the same
#: kind as a lead-in to an instruction ("INITIAL CMC- *Pay CCP 631(b) jury fee"),
#: which the star and the deadline words keep out.
_LTB_EVENT = re.compile(r"^\W*(TRIAL|MSC|CMC|MEDIATION|HEARING)\s*-\s*(?!\*)", re.I)


def _ltb(e: dict[str, Any]) -> bool:
    return bool(e.get("ltb")) or "LawToolBox" in (e.get("additionalData") or {})


def kind(e: dict[str, Any]) -> str | None:
    """The date kind one calendar entry is, or None when it is not one."""
    subj = str(e.get("subject") or "").strip()
    if e.get("isDeleted") or not subj or _SKIP.search(subj) or _instruction(subj, _BEFORE):
        return None
    if _CUTOFF.search(subj):
        return "discovery_cutoff"
    if _EXPERT.search(subj) and not _EXPERT_NOT.search(subj):
        return "expert_exchange"
    if _instruction(subj):
        return None
    if e.get("allDay") and _ltb(e) and not _LTB_EVENT.search(subj):
        return None
    for k, rx in _RULES:
        if rx.search(subj):
            return k
    return None


def _instruction(subj: str, words: re.Pattern[str] = _DEADLINE) -> bool:
    """The entry is a deadline, task or reminder: one of ``words``, outside a
    motion's own name, that no hearing word comes before."""
    text = _TITLE.sub(" ", subj)
    d = words.search(text)
    if d is None:
        return False
    h = _HEARING_WORD.search(text)
    return h is None or h.start() > d.start()


def _day(e: dict[str, Any]) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(e.get("startTime") or "")[:10])
    except ValueError:
        return None


def _time(e: dict[str, Any]) -> str:
    """The entry's time as written (startTime is local to its own zone)."""
    s = str(e.get("startTime") or "")
    if e.get("allDay") or len(s) < 16 or s[11:16] == "00:00":
        return ""
    h, mi = int(s[11:13]), s[14:16]
    return f"{(h - 1) % 12 + 1}:{mi} {'AM' if h < 12 else 'PM'}"


#: Kinds whose set event is a timed entry: when the matter's calendar has a
#: timed one, an all-day entry of the same kind is a task or reminder about it,
#: never the event (2026-10-30: a case printed an all-day CMC task on 11-03 as
#: its next court date; the hearing was the timed entry on 11-17).
TIMED_FIRST = (*COURT, "mediation")


def upcoming(events: list[dict[str, Any]], today: dt.date) -> dict[str, dict[str, Any]]:
    """Per kind, the earliest entry on or after ``today``, a timed entry
    outranking every all-day one of a ``TIMED_FIRST`` kind:
    ``{kind: {date, time, what, source}}``."""
    cands: dict[str, list[dict[str, Any]]] = {}
    for e in events:
        k, day = kind(e), _day(e)
        if k is None or day is None or day < today:
            continue
        cands.setdefault(k, []).append(
            {
                "date": day.isoformat(),
                "time": _time(e),
                "what": " ".join(str(e.get("subject") or "").split()),
                "source": LTB if _ltb(e) else STAFF,
            }
        )
    out: dict[str, dict[str, Any]] = {}
    for k, rows in cands.items():
        if k in TIMED_FIRST and any(r["time"] for r in rows):
            rows = [r for r in rows if r["time"]]
        out[k] = min(rows, key=lambda r: (r["date"], r["time"] or "99"))
    return out


def _dates_on(events: list[dict[str, Any]]) -> set[str]:
    """The days the calendar holds a court date or mediation on (a rule
    deadline on the same day is not agreement)."""
    return {
        d.isoformat()
        for e in events
        if (kind(e) in (*COURT, "mediation") or _headed_event(e) or _timed_named(e)) and (d := _day(e)) is not None
    }


#: The words that make an entry naming a court date a task about it: the
#: paper due, filed or served before it, or a staff last-day.
_TASK = re.compile(r"\b(due|file[ds]?|filing|serve[ds]?|service)\b|\bL/?D\b", re.I)


def _timed_named(e: dict[str, Any]) -> bool:
    """A timed entry that names a court date, with no due, file, serve or
    last-day word, is that court date on that day. The deadline words that keep
    it out of ``kind`` ("CMC Statement", "DEADLINE - CMC DEPT 28") do not make
    a set time a deadline (2026-11-02: two false "calendar does not have it"
    flags). It counts only as agreement with the papers; the picker never
    takes it."""
    subj = str(e.get("subject") or "")
    if e.get("isDeleted") or not _time(e) or _SKIP.search(subj) or _TASK.search(subj):
        return False
    return any(k in COURT and rx.search(subj) for k, rx in _RULES)


def _headed_event(e: dict[str, Any]) -> bool:
    """A LawToolBox entry headed by its event's kind ("**CMC- Additional case
    management conference (Need to serve ... and file POS before)") is that
    event on that day, though its note carries deadline words that keep it out
    of ``kind`` (2026-10-30: a false "calendar does not have it" flag)."""
    subj = str(e.get("subject") or "")
    return _ltb(e) and not e.get("isDeleted") and not _SKIP.search(subj) and bool(_LTB_EVENT.search(subj))


def merge(m: dict[str, Any], events: list[dict[str, Any]] | None, today: dt.date) -> dict[str, Any]:
    """One matter's court dates from both sources:
    ``{next, papers, calendar, rows, flag, read}``. ``events`` None means the
    calendar was not read for this matter (the papers' value stands alone)."""
    papers = m.get("next_court_date") if isinstance(m.get("next_court_date"), dict) else {}
    pd = str((papers or {}).get("date") or "")
    p_up = bool(pd) and pd >= today.isoformat()
    cal = upcoming(events or [], today)
    court = sorted((cal[k] for k in COURT if k in cal), key=lambda r: (r["date"], r["time"] or "99"))
    c_next = court[0] if court else None
    flag = ""
    if p_up and events is not None and pd not in _dates_on(events):
        flag = DISAGREE
    nxt: dict[str, Any] | None = None
    if c_next and (not p_up or c_next["date"] <= pd):
        both = p_up and pd == c_next["date"]
        what = c_next["what"] if not both else ((papers or {}).get("event") or c_next["what"])
        src = c_next["source"] + (f"; {_papers_src(papers)}" if both else "")
        nxt = {"date": c_next["date"], "event": _when(c_next["time"], what), "source": {"name": src}}
    elif p_up:
        nxt = copy.deepcopy(papers)
    rows = [(LABEL[k], cal[k]) for k, _l in KINDS if k in cal]
    return {
        "next": nxt,
        "papers": papers if p_up else None,
        "calendar": c_next,
        "rows": rows,
        "flag": flag,
        "read": events is not None,
    }


def _when(t: str, what: str) -> str:
    return ", ".join(x for x in (t, what) if x)


def _papers_src(papers: dict[str, Any] | None) -> str:
    s = (papers or {}).get("source") or {}
    return ", ".join(x for x in (s.get("name"), s.get("doc_date")) if x) or PAPERS_LABEL


def shown(
    ms: list[dict[str, Any]], calendar: dict[str, list[dict[str, Any]]] | None, today: dt.date
) -> list[dict[str, Any]]:
    """Copies of the final values as the list shows them: "Next court date"
    is the merged one, ``court_dates`` carries the merge, and a disagreement
    becomes a matter flag. ``calendar`` None (not read) changes nothing."""
    if calendar is None:
        return ms
    out = []
    for m in ms:
        c = copy.deepcopy(m)
        got = merge(m, calendar.get(str(m.get("matter_id"))), today)
        c["court_dates"] = got
        c["next_court_date"] = got["next"] or {"date": None, "event": None, "source": None}
        if got["flag"]:
            c["matter_flags"] = [*(c.get("matter_flags") or []), _disagree_text(got)]
        out.append(c)
    return out


def _disagree_text(got: dict[str, Any]) -> str:
    p, c = got["papers"] or {}, got["calendar"]
    cal = f"{c['date']} ({_when(c['time'], c['what'])})" if c else "no upcoming court date"
    return f"{DISAGREE}: court papers {p.get('date')} ({p.get('event') or ''}); calendar {cal}"


# ---- the read ----------------------------------------------------------------------------------
def pull(seat: Any, matter_ids: list[str], data: Path, log: Callable[[str], None]) -> dict[str, list[dict[str, Any]]]:
    """Every calendar entry on each matter, read-only and resumable: a matter
    already in ``data/calendar.json`` is not read again. A matter whose read
    raised is left out (its papers' value stands, and ``unread`` counts it)."""
    from ..arrivals import getter, paged

    path = data / CALENDAR_NAME
    have: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    try:
        get = getter(seat) if seat is not None else None
    except Exception as exc:  # noqa: BLE001 - an offline replay's seat cannot list: every case shows "not read"
        log(f"[calendar] this seat cannot read the calendar ({type(exc).__name__})")
        get = None
    if get is None:
        return have
    keep = ("startTime", "endTime", "allDay", "subject", "isDeleted")
    for i, mid in enumerate(matter_ids, 1):
        if mid in have:
            continue
        try:
            rows = paged(get, "/events", MatterId=mid)
        except Exception as exc:  # noqa: BLE001 - counted; the list still builds from the papers
            log(f"[calendar] a matter's calendar read raised {type(exc).__name__}")
            continue
        have[mid] = [
            {**{k: e.get(k) for k in keep}, "ltb": "LawToolBox" in (e.get("additionalData") or {})}
            for e in rows
            if isinstance(e, dict)
        ]
        if i % 25 == 0:
            dump(path, have)
            log(f"[calendar] {i}/{len(matter_ids)} matters read")
    dump(path, have)
    return have


def unread(calendar: dict[str, Any], matter_ids: list[str]) -> int:
    return sum(1 for mid in matter_ids if mid not in calendar)
