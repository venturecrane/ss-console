"""Whether recording a hand delivery may email the attorney (2026-11-24).

WHY. At 6:02 PM MST on 2026-11-24 the A&P Operator told an attorney his
responses were filed in the discovery folder. The hand draft was filed about
8:40 PM. The job had been recorded with ``jobs.drafting.deliver_by_smd``
without ``--quiet``, and that tool armed the lane's DELIVER wake by default:
the daemon dispatched it, and the Operator answered the attorney naming files
that were not on his matter yet.

So recording a delivery and telling the attorney are two acts, and the second
is never the default. Every ``*_by_smd`` recorder that could arm a DELIVER wake
records with NO wake unless it is run with ``--notify``, and ``--notify`` is
refused unless every file in the record is, right now:

(a) on the matter, read by its file id from Smokeball with the seat's own
    client (``seat.ClientSeat.list_files``, the listing the lanes' read-back
    uses, ``proof/readback.py``), at the size the record states; and
(b) filed or updated TODAY in the seat's timezone, by the vendor's own date on
    the file, or the record carries an explicit ``filed_at`` (ISO 8601) within
    the last 24 hours.

When a Smokeball read is not available from where the tool runs, ``--notify``
is refused outright: a check that could not run is never reported as passed.

Every refusal raises ``hand_delivery.Refused`` before anything is written.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .daemon import CHILD_ENV_PASS
from .hand_delivery import Refused

#: The seat's IANA timezone: bootstrap exports the customer.yaml timezone as
#: ``HERMES_TIMEZONE``; a plain ``TZ`` is the fallback.
TZ_ENV = ("HERMES_TIMEZONE", "TZ")
#: The Smokeball settings the runner daemon hands its jobs.
SMOKEBALL_ENV = tuple(k for k in CHILD_ENV_PASS if k.startswith("SMOKEBALL_"))
#: What ``--notify`` takes from the daemon's environment, by name only.
ENV_NAMES = (*SMOKEBALL_ENV, *TZ_ENV)
FILED_AT_WINDOW = timedelta(hours=24)
#: A ``filed_at`` this far ahead of the clock is a typo, never a filing.
FUTURE_SLACK = timedelta(minutes=5)
REQUEST_INDEX_ENV = "SMD_REQUEST_INDEX_DB_PATH"
DEFAULT_REQUEST_INDEX = "/opt/data/requests.db"

NO_EMAIL = "no email will be sent: the attorney is not told by this record (tell him by hand, or rerun with --notify)"
NO_WAKE = "recorded by SMD without --notify; no DELIVER wake, no email"


def _seat_client() -> Any:
    from .seat import ClientSeat

    return ClientSeat()


def matter_seat(factory: Callable[[], Any] = _seat_client) -> Any:
    """The seat's read-only Smokeball listing, or ``Refused``: no read, no notify."""
    try:
        return factory()
    except Exception as exc:  # "could not look" refuses; it never reads as "checked"
        raise Refused(
            "--notify needs a read of the matter in Smokeball and none is available here "
            f"({exc.__class__.__name__}); nothing was written. Record without --notify, and tell the attorney by hand"
        ) from exc


def matter_id_of(env: dict[str, Any]) -> str:
    """The matter the files were filed to: the drafting envelope's
    ``file_to_matter_id`` (the library matter) or ``matter_id``, or the
    chronology envelope's ``matter.id``."""
    m = env.get("matter")
    nested = m.get("id") if isinstance(m, dict) else None
    return str(env.get("file_to_matter_id") or env.get("matter_id") or nested or "").strip()


def seat_tz() -> ZoneInfo | None:
    for name in TZ_ENV:
        raw = (os.environ.get(name) or "").strip()
        if raw:
            try:
                return ZoneInfo(raw)
            except (ZoneInfoNotFoundError, ValueError):
                return None
    return None


def when(value: Any) -> datetime | None:
    """An ISO 8601 stamp as an aware UTC time (a naive one is read as UTC), or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        t = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def filed_at_ok(rec: dict[str, Any], now: datetime) -> bool:
    """The record's own ``filed_at`` is within the last 24 hours."""
    t = when(rec.get("filed_at"))
    return t is not None and now - FILED_AT_WINDOW <= t <= now + FUTURE_SLACK


def _listing(seat: Any, matter_id: str) -> dict[str, dict[str, Any]]:
    try:
        rows = seat.list_files(matter_id)
    except Exception as exc:  # an unreadable matter is a refusal, never "present"
        raise Refused(
            f"could not read matter {matter_id} from Smokeball ({exc.__class__.__name__}); nothing was written"
        ) from exc
    return {str(r.get("id")): r for r in rows or [] if isinstance(r, dict) and r.get("id")}


def check_files(
    seat: Any, matter_id: str, files: list[dict[str, Any]], rec: dict[str, Any], *, now: datetime, tz: ZoneInfo | None
) -> list[dict[str, Any]]:
    """Every file on the matter now, at its size, dated today or covered by
    ``filed_at``; else ``Refused``. Returns what was read, for the plan."""
    rows = _listing(seat, matter_id)
    recent = filed_at_ok(rec, now)
    today = now.astimezone(tz).date() if tz else None
    out = []
    for f in files:
        row = rows.get(str(f["file_id"]))
        label = f"file {f['file_id']} ({f.get('name') or 'unnamed'})"
        if row is None or row.get("deleted"):
            raise Refused(f"{label} is not on matter {matter_id} in Smokeball now; nothing was written")
        if row.get("size") is not None and int(row.get("size") or 0) != int(f["bytes"]):
            raise Refused(
                f"{label} is {row.get('size')} bytes on the matter, not the record's {f['bytes']}; nothing was written"
            )
        stamp = when(row.get("modified")) or when(row.get("created"))
        dated_today = bool(today and stamp and stamp.astimezone(tz).date() == today)
        if not dated_today and not recent:
            seen = stamp.isoformat() if stamp else "no date"
            zone = str(tz) if tz else "unknown here"
            raise Refused(
                f"{label} was last filed or updated {seen}, not today in the seat's timezone ({zone}), and the "
                "record carries no filed_at within the last 24 hours; nothing was written"
            )
        out.append({"file_id": str(f["file_id"]), "on_matter": True, "dated": stamp.isoformat() if stamp else None})
    return out


def subject_of(ref: str) -> str:
    """The reply's subject: "Re: " and the request email's subject from the
    seat's request index (read only), or a plain stand-in naming the email."""
    path = os.environ.get(REQUEST_INDEX_ENV) or DEFAULT_REQUEST_INDEX
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
        try:
            row = conn.execute(
                "SELECT subject FROM requests WHERE internet_message_id = ? OR vendor_message_id = ? LIMIT 1",
                (ref, ref),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        row = None
    subject = str((row or [""])[0] or "").strip()
    if not subject:
        return f"a reply in the request thread (email {ref}; its subject is not in the request index)"
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


def gate(
    job_dir: Path,
    rec: dict[str, Any],
    files: list[dict[str, Any]],
    *,
    notify: bool,
    ref_key: str,
    seat_factory: Callable[[], Any] = _seat_client,
    now: datetime | None = None,
) -> dict[str, Any]:
    """``{"email": <what happens>, "checked": [...]}``. Without ``notify``
    nothing is read and no email is sent. With it, every check above or
    ``Refused``. ``files`` are ``hand_delivery.checked_file`` rows."""
    if not notify:
        return {"email": NO_EMAIL}
    try:
        env = json.loads((Path(job_dir) / "envelope.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused(f"--notify needs the job's envelope ({exc}); nothing was written") from exc
    matter_id = matter_id_of(env)
    if not matter_id:
        raise Refused("--notify needs the matter the files were filed to, and the envelope names none")
    seat = matter_seat(seat_factory)
    checked = check_files(seat, matter_id, files, rec, now=now or datetime.now(timezone.utc), tz=seat_tz())
    subject = subject_of(str(env.get(ref_key) or ""))
    return {"email": f"the Operator WILL email the attorney: {subject}", "checked": checked}


def wake(task: str, notify: bool) -> dict[str, Any]:
    """The job's terminal wake: pending only with ``--notify``."""
    if notify:
        return {"pending": True, "attempts": 0, "task": task}
    return {"pending": False, "attempts": 0, "task": task, "quiet": NO_WAKE}


__all__ = [
    "ENV_NAMES",
    "NO_EMAIL",
    "NO_WAKE",
    "check_files",
    "filed_at_ok",
    "gate",
    "matter_id_of",
    "matter_seat",
    "seat_tz",
    "subject_of",
    "wake",
]
