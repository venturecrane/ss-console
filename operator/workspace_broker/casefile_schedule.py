"""The Case File Manager's standing schedule: the broker's half of the
weekday cron (``operator/skills/case-file-manager/pre_run.py``).

WHAT THE CRON DOES. Each fire submits ``{"trigger": "scheduled",
"smokeball_token_age_seconds": <int or null>}``. The broker, never the
caller, decides everything else, in the FIRM'S time zone (``timezone`` in
``casefile-firm.yaml``, the runner's own key):

* a weekday that is not on the firm's ``holidays`` list is a business day;
  anything else queues nothing (``not_business_day``);
* the FIRST business day of the month is a ``full`` read of every file, any
  other business day a ``recheck`` (only what changed since the last run);
* one job per authored caseload per day: the job's ``request_ref`` is
  ``scheduled:<firm date>:<caseload key>``, and the ledger's unique index on
  ``request_ref`` (failed scheduled rows included) makes a second fire the same
  day queue nothing;
* a caseload whose previous job is still queued or running is DEFERRED, never
  skipped: the cron fires hourly and the next fire tries again, until the
  firm's ``scheduled_cutoff_hour``. A fire at or after the cutoff queues
  nothing; for each caseload with no job that day it pages SMD once
  (``missed``), so a scheduled day never ends silently.

THE SMOKEBALL CONNECTION GATE. The cron reads the age of the seat's durable
Smokeball token with the overlay's own reader (``shared.connector_check.
token_ages``, the heartbeat's field) and passes it. Under
``TOKEN_FLOOR_DAYS`` left of ``smokeball_token_lifetime_days`` (the skill's
setting; ``DEFAULT_LIFETIME_DAYS`` otherwise, the fleet pager's unproven
Smokeball lifetime) nothing is queued, and an unknown age is refused the same
way. SMD is paged ONCE per episode (``delivery_proof`` shortfall, lane
``casefile``, code ``stage_refused``; the detail says which), and the
cron's own SUPPRESSED_WAKE row on every later fire is the daily digest line
(``work_health_sources.casefile_schedule_items``). A fire that passes the
gate ends the episode. The same gate holds an approved apply (the casework
reply path passes the age it read).

HOLIDAYS are the firm's authored list only (``holidays`` in the firm config);
nothing here knows a public holiday calendar.

Every audit row and page detail carries ids, dates, counts and reason tokens,
never a name: a caseload is named in the ledger by its key (the first 12 hex of
the sha256 of the authored name).
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

SKILL_NAME = "case-file-manager"
SCHEDULED_TEXT = "scheduled case file review"
CASELOADS_KEY = "scheduled_caseloads"
RECIPIENTS_KEY = "scheduled_recipients"
LIFETIME_KEY = "smokeball_token_lifetime_days"
#: workers/fleet-alerts/src/token-expiry.ts VENDOR_LIFETIME_RULES.smokeball:
#: the lifetime every Smokeball token is judged by until a longer one is proven.
DEFAULT_LIFETIME_DAYS = 30
TOKEN_FLOOR_DAYS = 7
DEFAULT_TIMEZONE = "America/Los_Angeles"
DEFAULT_CUTOFF_HOUR = 14
#: A raised line older than this answers "this list was replaced".
STALE_DAYS = 10
STALE_SENTENCE = "this list was replaced; reply to today's list with the numbers"
_REF = re.compile(r"^scheduled:(\d{4}-\d{2}-\d{2}):([0-9a-f]{12})$")
MAX_CASELOADS = 20

PAGES_SQL = (
    "CREATE TABLE IF NOT EXISTS casefile_schedule_pages ("
    "code TEXT NOT NULL, key TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY (code, key))"
)


class ScheduleError(ValueError):
    """The schedule cannot be read; the message is the refusal sentence."""


# ---- authored inputs -------------------------------------------------------------
def caseloads(settings: dict[str, Any]) -> list[str]:
    """The authored caseloads, each one person's name as Smokeball shows it.
    A scalar (settings are scalars, ADR 0075), semicolon separated. Empty or
    unauthored is no caseload: nothing is queued."""
    raw = settings.get(CASELOADS_KEY)
    if not isinstance(raw, str):
        return []
    names = list(dict.fromkeys(" ".join(p.split()) for p in raw.split(";")))
    return [n for n in names if n and len(n) <= 100][:MAX_CASELOADS]


def caseload_key(name: str) -> str:
    return hashlib.sha256(" ".join(name.split()).lower().encode("utf-8")).hexdigest()[:12]


def scheduled_ref(day: date, name: str) -> str:
    return f"scheduled:{day.isoformat()}:{caseload_key(name)}"


def is_scheduled_ref(ref: str) -> bool:
    return bool(_REF.match(str(ref or "")))


def ref_day(ref: str) -> date | None:
    m = _REF.match(str(ref or ""))
    return date.fromisoformat(m.group(1)) if m else None


def lifetime_days(settings: dict[str, Any]) -> int:
    v = settings.get(LIFETIME_KEY)
    if isinstance(v, str) and v.strip().isdigit():
        v = int(v.strip())
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else DEFAULT_LIFETIME_DAYS


def cron_row_live(path: str | Path) -> bool:
    """Whether a persona's ``cron`` list targets this skill: the firm's enable
    act. False until that row is authored."""
    try:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - unreadable is not live
        return False
    personas = doc.get("personas") if isinstance(doc, dict) else None
    for persona in personas or []:
        for row in (persona or {}).get("cron") or []:
            if isinstance(row, dict) and row.get("skill") == SKILL_NAME:
                return True
    return False


# ---- the firm's calendar -----------------------------------------------------------
class Calendar:
    def __init__(self, tz: ZoneInfo, holidays: frozenset[date], cutoff_hour: int) -> None:
        self.tz, self.holidays, self.cutoff_hour = tz, holidays, cutoff_hour

    @classmethod
    def from_firm(cls, firm: dict[str, Any] | None) -> Calendar:
        firm = firm or {}
        try:
            tz = ZoneInfo(str(firm.get("timezone") or DEFAULT_TIMEZONE))
        except Exception as exc:
            raise ScheduleError("the firm's time zone cannot be read, so today's date is unknown") from exc
        days: set[date] = set()
        for h in firm.get("holidays") or []:
            if isinstance(h, datetime):
                days.add(h.date())
            elif isinstance(h, date):
                days.add(h)
            elif isinstance(h, str):
                try:
                    days.add(date.fromisoformat(h.strip()))
                except ValueError as exc:
                    raise ScheduleError("the firm's holiday list has a date that cannot be read") from exc
            else:
                raise ScheduleError("the firm's holiday list has a date that cannot be read")
        cutoff = firm.get("scheduled_cutoff_hour", DEFAULT_CUTOFF_HOUR)
        if not (isinstance(cutoff, int) and not isinstance(cutoff, bool) and 1 <= cutoff <= 23):
            raise ScheduleError("the firm's scheduled cutoff hour cannot be read")
        return cls(tz, frozenset(days), cutoff)

    def local(self, now: datetime) -> datetime:
        return now.astimezone(self.tz)

    def is_business_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays

    def first_business_day(self, year: int, month: int) -> date:
        day = date(year, month, 1)
        while not self.is_business_day(day):
            day += timedelta(days=1)
        return day

    def mode_for(self, day: date) -> str | None:
        """``full`` on the month's first business day, ``recheck`` on any
        other business day, None on a weekend or a firm holiday."""
        if not self.is_business_day(day):
            return None
        return "full" if day == self.first_business_day(day.year, day.month) else "recheck"


# ---- the token gate ----------------------------------------------------------------
def token_refusal(age: Any, lifetime: int, now: datetime) -> tuple[str, str] | None:
    """None when the connection has at least ``TOKEN_FLOOR_DAYS`` left, else
    (the page-once key of this episode, the refusal sentence)."""
    if not isinstance(age, int) or isinstance(age, bool) or age < 0:
        return "unknown", (
            "the Smokeball connection's age is not known on this seat, so a scheduled case file run was not "
            "queued (fail closed); nothing was queued"
        )
    left = lifetime * 86400 - age
    if left >= TOKEN_FLOOR_DAYS * 86400:
        return None
    issued = (now - timedelta(seconds=age)).date().isoformat()
    return f"issued:{issued}", (
        f"the Smokeball connection needs renewing ({max(left, 0) // 86400} days left of {lifetime}); nothing was queued"
    )


# ---- page once -------------------------------------------------------------------------
def _pages(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute(PAGES_SQL)
    return conn


def claim_page(db_path: str, code: str, key: str) -> bool:
    """True the first time ``(code, key)`` is claimed: that caller pages."""
    conn = _pages(db_path)
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO casefile_schedule_pages (code, key, at) VALUES (?,?,?)",
            (code, key, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def clear_pages(db_path: str, code: str) -> None:
    """An episode ended (the gate passed): the next one pages again."""
    conn = _pages(db_path)
    try:
        conn.execute("DELETE FROM casefile_schedule_pages WHERE code=?", (code,))
        conn.commit()
    finally:
        conn.close()


def job_for_ref(conn: sqlite3.Connection, ref: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, state FROM casefile_jobs WHERE request_ref=? ORDER BY created_at DESC LIMIT 1", (ref,)
    ).fetchone()
    return {"id": row[0], "state": row[1]} if row else None


__all__ = [
    "CASELOADS_KEY",
    "Calendar",
    "DEFAULT_LIFETIME_DAYS",
    "LIFETIME_KEY",
    "RECIPIENTS_KEY",
    "SCHEDULED_TEXT",
    "STALE_DAYS",
    "STALE_SENTENCE",
    "ScheduleError",
    "TOKEN_FLOOR_DAYS",
    "caseload_key",
    "caseloads",
    "claim_page",
    "clear_pages",
    "cron_row_live",
    "is_scheduled_ref",
    "job_for_ref",
    "lifetime_days",
    "ref_day",
    "scheduled_ref",
    "token_refusal",
]
