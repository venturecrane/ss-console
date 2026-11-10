"""One listed sender cannot spend the seat's whole day (``sender_enrollment.daily_limit``).

WHY (ss-console #3946, gate 16). A guide seat's only brake on volume is the
seat-wide inbound limit, which counts every delivery before the sender is
known: one representative practising a pitch thirty times starves everyone
else, and every one of those emails is a model turn SMD pays for. A guide seat
that authors ``sender_enrollment.daily_limit: N`` answers each listed sender who
is not an administrator at most N emails a day. This runs in the webhook
router, where the sender is already verified, and before any model turn:

* within the limit, the delivery flows exactly as before, and is counted;
* over it, the delivery is dropped (no turn, no request card), a ``REPLY_HELD``
  row with reason ``sender_daily_limit`` is written so the hourly work-health
  triage sees it, and the sender gets ONE fixed notice that day, from code:

      You have reached today's limit of messages to this address. Please write
      again tomorrow.

  A second email over the limit the same day gets nothing more.

Administrators (``scope.admins``) are never limited. A day is a UTC calendar
day. The shared inbound gate (``shared/gate_inbound_cap.py``) and the cost meter
are not changed. A seat that does not author the key never reaches this
module: it is read only where enrollment is switched on (a guide seat or SMD's
demo seat, ``shared/sender_enrollment.py`` ``enabled_in``), and even there it
is off unless ``daily_limit`` itself is authored.

THE STORE. ``/opt/data/smd/sender_daily.db`` (``SMD_SENDER_DAILY_DB_PATH``):
one row per sender and day, keyed by ``sender_key`` (the sha256 of the address,
``shared/audit_contract.py``), holding a count and whether the notice went.
Rows older than three days are deleted on every write. A person's purge
(``workspace_broker/person_purge.py``) deletes their rows by the same key.

FAIL-OPEN, deliberately. A store that cannot be opened lets the email through
and logs an error: the limit protects spend, and a broken counter must not
silence a person who is allowed to write. The seat-wide limit still holds.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DB_ENV = "SMD_SENDER_DAILY_DB_PATH"
DEFAULT_DB_PATH = "/opt/data/smd/sender_daily.db"
KEY = "daily_limit"
REASON = "sender_daily_limit"

#: The one notice an over-limit sender gets each day. Fixed; no model writes it.
NOTICE = "You have reached today's limit of messages to this address. Please write again tomorrow."

_KEEP_DAYS = 3
_CREATE = (
    "CREATE TABLE IF NOT EXISTS sender_daily ("
    " sender_hash TEXT NOT NULL,"
    " day TEXT NOT NULL,"
    " count INTEGER NOT NULL DEFAULT 0,"
    " noticed INTEGER NOT NULL DEFAULT 0,"
    " PRIMARY KEY (sender_hash, day))"
)


def db_path() -> Path:
    return Path(os.environ.get("SMD_SENDER_DAILY_DB_PATH") or DEFAULT_DB_PATH)


def authored_limit(raw_config: Any) -> int | None:
    """The authored limit, or ``None`` (no limit: today's behaviour). Read only
    where enrollment is switched on (a guide seat or the demo seat), the one
    block it lives in; absent there, there is no limit."""
    from shared import sender_enrollment

    if not sender_enrollment.enabled_in(raw_config):
        return None
    value = raw_config.get(sender_enrollment.BLOCK, {}).get(KEY)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


@dataclass(frozen=True)
class Decision:
    """``admit``: run the delivery. ``notify``: over the limit, and this is the
    first time today, so the sender gets the notice."""

    admit: bool
    notify: bool = False
    count: int = 0


ADMIT = Decision(admit=True)


def _day(now: float) -> str:
    return datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")


def decide(
    sender_hash: str, limit: int, *, now: float | None = None, path: Path | None = None
) -> Decision:
    """Count this delivery against ``sender_hash``'s day, or refuse it.

    Admitted deliveries are counted; refused ones are not (the count is what
    was answered). ``notify`` is True exactly once per sender and day. Never
    raises: a store fault admits (see the module note)."""
    ts = time.time() if now is None else now
    day = _day(ts)
    target = path or db_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(target), timeout=5)
    except (OSError, sqlite3.Error):
        logger.error("daily_limit: the per-sender store cannot be opened; admitting", exc_info=True)
        return ADMIT
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(_CREATE)
            cutoff = _day(ts - _KEEP_DAYS * 86400)
            conn.execute("DELETE FROM sender_daily WHERE day < ?", (cutoff,))
            row = conn.execute(
                "SELECT count, noticed FROM sender_daily WHERE sender_hash = ? AND day = ?",
                (sender_hash, day),
            ).fetchone()
            count, noticed = (row[0], row[1]) if row else (0, 0)
            if count < limit:
                conn.execute(
                    "INSERT INTO sender_daily (sender_hash, day, count, noticed) VALUES (?, ?, 1, 0)"
                    " ON CONFLICT(sender_hash, day) DO UPDATE SET count = count + 1",
                    (sender_hash, day),
                )
                return Decision(admit=True, count=count + 1)
            if not noticed:
                conn.execute(
                    "UPDATE sender_daily SET noticed = 1 WHERE sender_hash = ? AND day = ?",
                    (sender_hash, day),
                )
                return Decision(admit=False, notify=True, count=count)
            return Decision(admit=False, notify=False, count=count)
    except sqlite3.Error:
        logger.error("daily_limit: the per-sender store failed; admitting", exc_info=True)
        return ADMIT
    finally:
        conn.close()


__all__ = [
    "ADMIT",
    "DB_ENV",
    "DEFAULT_DB_PATH",
    "Decision",
    "NOTICE",
    "REASON",
    "authored_limit",
    "db_path",
    "decide",
]
