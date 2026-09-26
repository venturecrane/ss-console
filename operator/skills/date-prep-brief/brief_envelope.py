"""date-prep-brief: recipients, the three files the gate writes, and the facts line.

Files, all under ``$HERMES_HOME/.smd/pre_run/`` (0700 dir, 0600 files, atomic
rename, ``O_EXCL`` temp: the dispatch-envelope writer discipline):

* ``date-prep-brief.brief.json`` - the envelope the overlay's
  ``casework_brief`` takes once (it refuses a stale or second one). It
  carries WHO receives the brief and WHICH steps may be offered; the turn
  supplies only the done lines and at most two questions.
* ``date-prep-brief.json`` - the provenance handoff: the dates this pull read,
  and ``(matterNumber, dates)`` records, so the identifier gate accepts a date
  the brief renders only when a read produced it for that matter.
* ``date-prep-brief.woken.json`` - the dates this seat already woke for today,
  so a date whose only steps run at ``handles`` (and leave no decision) wakes
  once a day at most, not on every hourly tick.

Recipients are ``matter_staff`` routing only (case-manager spec §5 rule 1):
the matter's responsible attorney, with its assisting staff copied, each
address covered by an authored roster grant. No fallback and no central leg:
a brief is the matter owner's, and a matter with no routable owner is left to
the escalator, which has its own fallback and hold rules.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

WOKEN_KEEP_DAYS = 14
_HANDOFF_MAX_DATES = 200


def _directory() -> Path:
    return Path(os.environ.get("HERMES_HOME") or "/opt/data") / ".smd" / "pre_run"


def _write(name: str, payload: dict) -> bool:
    try:
        directory = _directory()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = directory / ("." + name + ".tmp")
        tmp.unlink(missing_ok=True)
        handle = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, directory / name)
        return True
    except Exception as exc:  # noqa: BLE001 - the caller suppresses on a failed write
        sys.stderr.write("[pre_run] " + name + " write failed: " + type(exc).__name__ + "\n")
        return False


def _mapping(value: object) -> dict:
    """``value`` when it is a mapping, else an empty one: config read as authored or absent."""
    return value if isinstance(value, dict) else {}


def recipients(routing, cfg: dict, staff: dict | None) -> dict | None:
    """``{"to": [...], "cc": [...], "leg": ...}`` for one matter, or None."""
    mode = _mapping(_mapping(cfg.get("escalation")).get("case_alert_routing")).get("mode")
    if mode != "matter_staff" or not isinstance(staff, dict):
        return None
    scope = _mapping(cfg.get("scope"))
    grants = [g for g in (scope.get("inbound_allow_from") or []) if isinstance(g, str)]
    responsible = routing._usable_staff_email(staff.get("responsible"))
    assisting = [
        email
        for record in (staff.get("assisting") or [])
        if (email := routing._usable_staff_email(record)) is not None and routing._granted(email, grants)
    ]
    if responsible is not None and routing._granted(responsible, grants):
        cc = [a for a in dict.fromkeys(assisting) if a.lower() != responsible.lower()]
        return {"to": [responsible], "cc": cc, "leg": routing.LEG_RESPONSIBLE}
    if responsible is None and assisting:
        return {"to": list(dict.fromkeys(assisting)), "cc": [], "leg": routing.LEG_ASSISTING}
    return None


def subject_label(candidate: dict) -> str:
    day = date.fromisoformat(candidate["date"])
    number = candidate.get("matter_number") or "Matter"
    return (number + ": " + candidate.get("subject", "").strip() + ", " + day.strftime("%b") + " " + str(day.day))[:200]


def _dates(plan: dict) -> list[str]:
    status = plan["status"]
    days = [plan["candidate"]["date"]]
    days += [f["date"] for f in status.get("files") or [] if f.get("date")]
    days += list((status.get("memo_markers") or {}).values())
    days += [c["last_chased"] for c in plan["chases"].values() if c.get("last_chased")]
    days += list(plan.get("seed_days") or [])
    return list(dict.fromkeys(days))[:_HANDOFF_MAX_DATES]


def read_woken() -> dict:
    try:
        with open(_directory() / "date-prep-brief.woken.json", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _woken_after(plan: dict) -> dict:
    today = date.fromisoformat(plan["today"])
    kept = {}
    for key, day in read_woken().items():
        try:
            if date.fromisoformat(day) >= today - timedelta(days=WOKEN_KEEP_DAYS):
                kept[key] = day
        except (TypeError, ValueError):
            continue
    kept[plan["candidate"]["item_key"]] = plan["today"]
    return kept


def write_all(plan: dict, skill: str, started_at: str, catalog: list[dict]) -> bool:
    """Envelope, handoff, woken marker. False when the envelope or handoff failed."""
    candidate = plan["candidate"]
    envelope = {
        "skill": skill,
        "started_at": started_at,
        "matter_id": candidate["matter_id"],
        "matter_number": candidate.get("matter_number"),
        "event_id": candidate["event_id"],
        "item_key": candidate["item_key"],
        "subject_label": subject_label(candidate),
        "recipients": plan["recipients"]["to"],
        "cc": plan["recipients"]["cc"],
        "routing_leg": "matter_staff",
        "catalog": catalog,
    }
    if plan.get("done_since"):
        envelope["done_since"] = plan["done_since"]
    dates = _dates(plan)
    handoff = {
        "skill": skill,
        "started_at": started_at,
        "dates": dates,
        "matter_ids": [candidate["matter_id"]],
        "records": [{"matterNumber": candidate["matter_number"], "dates": dates}]
        if candidate.get("matter_number")
        else [],
    }
    ok = _write(skill + ".brief.json", envelope) and _write(skill + ".json", handoff)
    if ok:
        _write(skill + ".woken.json", _woken_after(plan))
    return ok


def facts(plan: dict) -> dict:
    """What the woken turn sees on the gate line: the date, the file status,
    and the catalog with each entry's basis. No addresses: the tool holds them."""
    candidate, status = plan["candidate"], plan["status"]
    records = [{**r, **plan["chases"].get(r["roster_task_id"], {})} for r in status.get("records") or []]
    return {
        "matter_id": candidate["matter_id"],
        "matter_number": candidate.get("matter_number"),
        "event": {k: candidate[k] for k in ("event_id", "date", "subject", "days_out")},
        "file_status": {
            "files": status.get("files") or [],
            "files_truncated": bool(status.get("files_truncated")),
            "memo_markers": status.get("memo_markers") or {},
            "records": records,
            "unread": status.get("unread") or [],
        },
        "catalog": plan["catalog"],
        "recipients": {
            "to": "responsible attorney" if plan["recipients"]["leg"].endswith("responsible") else "assisting staff",
            "cc": len(plan["recipients"]["cc"]),
        },
    }
