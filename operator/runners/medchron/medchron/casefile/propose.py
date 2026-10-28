"""`propose`: the numbered changes a person may approve, decided in code.

A line is proposed only when every rule already holds, so an approval never
has to be second-guessed:

* a TASK CLOSURE: the reader's ``mark_complete`` passed the validator (proof
  documents read, no money or court words, not a statute reminder) AND the
  second reader ``confirmed`` it;
* a MEDICALS ROW: a new row (not already on the tab) on a file whose posture
  the firm writes rows on (``write_postures``), whose charge appears in its
  source bill's text, whose source is not a scan, and which carries both
  service dates;
* a CALENDAR ENTRY: a specialty appointment the reader found in the file,
  dated today or later, with no event on the matter's calendar that day naming
  the provider. A time becomes a timed entry only in the time zone the document
  states; with none it is an all-day entry on that date. Two documents naming
  one appointment are one line.

Each line carries the facts its apply needs and a snapshot of the record it
changes (the task as read, the Medicals tab as read): at apply time the record
is read again and a line whose snapshot no longer matches is refused, never
written. The facts live in the lane's state (``state.save_proposals``); the
broker holds the line's ids and the hash of its facts, so an approval binds to
exactly what was proposed.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from . import state as state_mod
from .validate import scanned_bill

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def task_snapshot(t: dict[str, Any]) -> str:
    """What a closure was proposed against: changed means refused at apply."""
    return state_mod.digest(
        {
            "subject": str(t.get("subject") or ""),
            "isCompleted": bool(t.get("isCompleted")),
            "due": str(t.get("dueDate") or t.get("dueDateOnly") or "")[:10],
            "note": str(t.get("note") or ""),
        }
    )


def medicals_snapshot(inv: dict[str, Any], design: str) -> tuple[str, str] | None:
    """(item id, hash of its values) of the matter's ONE Medicals tab; None
    when the matter has none or several (one per claimant: a person picks)."""
    tabs = [
        (k, v)
        for k, v in (inv.get("layouts") or {}).items()
        if design
        in str(
            ((v.get("row") or {}).get("layoutDesign") or {}).get("id")
            or (v.get("row") or {}).get("layoutDesignId")
            or ""
        )
    ]
    if len(tabs) != 1:
        return None
    return tabs[0][0], state_mod.digest(tabs[0][1].get("values"))


def task_lines(rec: dict[str, Any], inv: dict[str, Any], meta: dict[str, Any], verdicts: dict[str, Any]) -> list[dict]:
    by_id = {str(t.get("id")): t for t in inv.get("tasks_open") or []}
    out = []
    for t in rec.get("open_tasks") or []:
        tid = str(t.get("task_id") or "")
        if (
            t.get("action") != "mark_complete"
            or (verdicts.get(tid) or {}).get("verdict") != "confirmed"
            or tid not in by_id
        ):
            continue
        names = [
            str((meta.get(str(d).lower()) or meta.get(str(d)) or {}).get("name") or d)
            for d in t.get("proof_doc_ids") or []
        ]
        out.append(
            {
                "kind": "task",
                "source_id": tid,
                "facts": {
                    "number": rec.get("number"),
                    "task_id": tid,
                    "subject": str(by_id[tid].get("subject") or ""),
                    "snapshot": task_snapshot(by_id[tid]),
                    "proof_doc_ids": [str(d) for d in t.get("proof_doc_ids") or []],
                    "proof_names": names,
                    "why": str(t.get("why") or ""),
                    "audit_reason": str((verdicts.get(tid) or {}).get("reason") or ""),
                },
            }
        )
    return out


def medicals_lines(
    rec: dict[str, Any], inv: dict[str, Any], meta: dict[str, Any], firm: Any, text_of: Any
) -> tuple[list[dict[str, Any]], list[str]]:
    """(lines, why each other new row was not proposed)."""
    if rec.get("posture") not in set(firm.get("write_postures")):
        return [], []
    tab = medicals_snapshot(inv, str(firm.get("medicals_layout_design")))
    out, skipped = [], []
    for i, m in enumerate(rec.get("medicals") or []):
        if not isinstance(m, dict) or m.get("existing_row"):
            continue
        src = str(m.get("source_doc_id") or "")
        start, end = str(m.get("service_start") or ""), str(m.get("service_end") or m.get("service_start") or "")
        if tab is None:
            skipped.append("the matter has no single Medicals tab")
        elif scanned_bill(meta, text_of, src):
            skipped.append("the bill is a scan, not read in this run")
        elif not (_DATE.match(start) and _DATE.match(end)):
            skipped.append("the bill's dates of service were not read")
        else:
            src_meta = meta.get(src.lower()) or meta.get(src) or {}
            out.append(
                {
                    "kind": "medicals",
                    "row_index": i,
                    "facts": {
                        "number": rec.get("number"),
                        "provider": str(m.get("provider")),
                        "charge": f"{float(m['charge']):.2f}",
                        "service_start": start,
                        "service_end": end,
                        "source_doc_id": src,
                        "source_name": str(src_meta.get("name") or src),
                        "page": m.get("page"),
                        "item_id": tab[0],
                        "snapshot": tab[1],
                        "why": str(m.get("note") or ""),
                    },
                }
            )
    for ln in out:
        ln["source_id"] = (
            "med-"
            + state_mod.digest([ln["facts"][k] for k in ("provider", "charge", "service_start", "source_doc_id")])[:24]
        )
    return out, skipped


def _plain(v: Any) -> str:
    """Letters and digits only, lowercased: "Dr. A. Example, MD" -> "draexamplemd"."""
    return re.sub(r"[^0-9a-z]", "", str(v or "").casefold())


def appointment_key(provider: Any, date: Any) -> str:
    return "appt-" + state_mod.digest([_plain(provider), str(date or "")])[:24]


def appointments(rec: dict[str, Any]) -> list[dict[str, Any]]:
    """The record's specialty appointments, one per provider and date: two
    documents naming the same appointment are one entry citing both."""
    out: dict[str, dict[str, Any]] = {}
    for a in rec.get("specialty_appointments") or []:
        if not isinstance(a, dict) or not str(a.get("provider") or "").strip():
            continue
        key = appointment_key(a.get("provider"), a.get("date"))
        if key in out:
            got = out[key]
            got["source_doc_ids"] += [
                str(d) for d in a.get("source_doc_ids") or [] if str(d) not in got["source_doc_ids"]
            ]
            for k in ("time", "time_zone", "note"):
                got[k] = got.get(k) or a.get(k)
            continue
        out[key] = {**a, "key": key, "source_doc_ids": [str(d) for d in a.get("source_doc_ids") or []]}
    return list(out.values())


def event_subject(a: dict[str, Any]) -> str:
    return f"{str(a.get('specialty')).strip()} appointment: {str(a.get('provider')).strip()}"


def on_calendar(events: list[dict[str, Any]], provider: Any, date: str) -> bool:
    """An event on that local date whose subject or description names the provider."""
    want = _plain(provider)
    for e in events:
        when = str(e.get("startTime") or e.get("start") or "")[:10]
        text = _plain(f"{e.get('subject') or ''} {e.get('description') or ''}")
        if when == date and want and want in text:
            return True
    return False


def _clock(a: dict[str, Any]) -> tuple[str, str, bool, str, str]:
    """(start, end, all_day, zone, how) for the entry. A time is written as a
    timed entry only in the zone the document states (one hour held: no
    document states an end); with no stated zone it is an all-day entry that
    names the stated time, never a guess at the zone."""
    date, time, zone = str(a["date"]), a.get("time"), a.get("time_zone")
    if time and zone:
        start = dt.datetime.fromisoformat(f"{date}T{time}:00")
        end = start + dt.timedelta(hours=1)
        return start.isoformat(), end.isoformat(), False, str(zone), f"{time} {zone}, one hour held"
    how = f"all day; the file gives {time} with no time zone" if time else "all day; the file gives no time"
    return f"{date}T00:00:00", f"{date}T00:00:00", True, "", how


def appointment_lines(
    rec: dict[str, Any], inv: dict[str, Any], meta: dict[str, Any], firm: Any, today: dt.date
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """(calendar lines, why each other appointment was not proposed, by key)."""
    out, skipped = [], {}
    events = [e for e in inv.get("events") or [] if isinstance(e, dict)]
    for a in appointments(rec):
        date = str(a.get("date") or "")
        if not _DATE.match(date):
            skipped[a["key"]] = "No date in the file"
            continue
        if date < today.isoformat():
            skipped[a["key"]] = "Date passed"
            continue
        if on_calendar(events, a.get("provider"), date):
            skipped[a["key"]] = "Already on the calendar"
            continue
        start, end, all_day, zone, how = _clock(a)
        names = [str((meta.get(d.lower()) or meta.get(d) or {}).get("name") or d) for d in a["source_doc_ids"]]
        out.append(
            {
                "kind": "event",
                "source_id": a["key"],
                "facts": {
                    "number": rec.get("number"),
                    "provider": str(a.get("provider")),
                    "specialty": str(a.get("specialty")),
                    "subject": event_subject(a),
                    "date": date,
                    "start": start,
                    "end": end,
                    "all_day": all_day,
                    # A timed entry carries the document's zone; an all-day one
                    # the firm's (Smokeball requires one; the day is the same).
                    "time_zone": zone or str(firm.get("timezone")),
                    "how": how,
                    "source_doc_ids": a["source_doc_ids"],
                    "source_names": names,
                    "why": str(a.get("note") or ""),
                },
            }
        )
    return out, skipped


def number_lines(per_matter: list[tuple[str, list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    """Lines numbered 1..N in the workbook's matter order, each with the hash
    of its facts (what the broker binds an approval to)."""
    out, n = [], 0
    for mid, lines in per_matter:
        for ln in lines:
            n += 1
            out.append({**ln, "n": n, "matter_id": mid, "sha256": state_mod.digest(ln["facts"])})
    return out


def broker_view(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What the broker records: ids and hashes, never a fact from a file."""
    return [{k: ln[k] for k in ("n", "kind", "matter_id", "source_id", "sha256")} for ln in lines]


__all__ = [
    "appointment_lines",
    "appointments",
    "broker_view",
    "event_subject",
    "medicals_lines",
    "medicals_snapshot",
    "number_lines",
    "on_calendar",
    "task_lines",
    "task_snapshot",
]
