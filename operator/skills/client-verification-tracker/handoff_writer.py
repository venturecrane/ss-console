"""Pre-run provenance handoff writer for the chase gate (ss#2547 + WS-RENDER).

Split out of ``pre_run.py`` as a sibling (module-size ratchet), loaded by the
same candidates walk as the vendored ledger. Projects the emitted wake payload
down to date atoms + matter ids + per-matter ``(matterNumber, dates)`` records
and hands it to the overlay's ``shared/pre_run_handoff.take_handoff``, which
seeds the session's provenance register — the seam that lets the rendered
alert's matter numbers verify as READ instead of composed (ss #2390; the
escalator's records block, vendored here now that the CVT alert names
numbers).

Best-effort by construction: a handoff that cannot be written costs the
seeding — the identifier gate refuses the full body and the skeleton fallback
ships (paged as degraded). Never the wake.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_RECORD_DATE_KEYS = ("authored_date", "next_chase_due")


def _values(node, key: str, out: list) -> list:
    """Every ``key`` string in a nested payload, deduped, first-seen order."""
    if isinstance(node, dict):
        value = node.get(key)
        if isinstance(value, str) and value and value not in out:
            out.append(value)
        for child in node.values():
            _values(child, key, out)
    elif isinstance(node, list):
        for child in node:
            _values(child, key, out)
    return out


def _is_iso_day(value: str) -> bool:
    return len(value) == 10 and value[4] == "-" and value[7] == "-" and value.replace("-", "").isdigit()


def _records(node, out: dict) -> dict:
    """Group every ``(matter_number, date)`` co-occurrence into per-matter
    records — the association half of provenance (the escalator's block,
    reading this skill's date fields)."""
    if isinstance(node, dict):
        number = node.get("matter_number")
        if isinstance(number, str) and number:
            for key in _RECORD_DATE_KEYS:
                value = node.get(key)
                if not isinstance(value, str):
                    continue
                day = value[:10]
                if not _is_iso_day(day):
                    continue
                dates = out.setdefault(number, [])
                if day not in dates:
                    dates.append(day)
        for child in node.values():
            _records(child, out)
    elif isinstance(node, list):
        for child in node:
            _records(child, out)
    return out


def write_pre_run_handoff(payload: dict, *, skill: str, started_at: str) -> None:
    """Project + write, atomically, 0600. Same writer pattern as the
    escalator's (O_EXCL against a pre-planted symlink; unlink against a
    wedged temp)."""
    try:
        grouped = _records(payload, {})
        days: list[str] = []
        for key in _RECORD_DATE_KEYS:
            for value in _values(payload, key, []):
                day = value[:10]
                if _is_iso_day(day) and day not in days:
                    days.append(day)
        record = {
            "skill": skill,
            "started_at": started_at,
            "dates": days,
            "matter_ids": _values(payload, "matter_id", []),
            "records": [{"matterNumber": number, "dates": dates} for number, dates in grouped.items()],
        }
        directory = Path(os.environ.get("HERMES_HOME") or "/opt/data") / ".smd" / "pre_run"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = directory / ("." + skill + ".json.tmp")
        tmp.unlink(missing_ok=True)
        handle = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(record, fh)
        os.replace(tmp, directory / (skill + ".json"))
    except Exception as exc:  # noqa: BLE001 — never change stdout or the wake
        sys.stderr.write("[pre_run] handoff write failed (" + str(exc) + ")\n")
