"""Broker-owned ledger for chronology-package jobs (routine 11, ss#2614).

One row per job the Operator (or root on the box) submits; the row is the small
durable record the console reads and the monthly allowance is counted from:
states, counts, cents, the delivery folder. Never document content, never a
page of text. The envelope the runner consumes is written beside it as
``queue/<id>.json``; the run's working files live under the root-owned jobs
dir and are wiped after delivery.

WHY this table lives in the audit db file (the ``jobs`` ledger's precedent,
``job_ledger.py``): the broker holds the only RW handle on that file, it sits
on the volume behind the ``/run/smd-audit`` bind, and the month's delivered
document count must survive a restart or a reprovision. The broker's own
``/var/lib/smd-workspace-broker`` is rootfs, recreated every boot; nothing
durable belongs there.

WHY the runner reports through the broker instead of writing here: the runner
daemon is root and cannot append audit rows through the generic verb (gateway
PID gate) nor the agent-uid verbs (uid 0 is never the agent). So every state
transition arrives as ``medchron_job_record`` (root-only) and the broker writes
the ledger row AND the audit row under its own uid, the way the establishment
verbs write ``ACT_PROPOSED``. One writer, one chain.

States and the audit type each transition pins:

    submitted  MEDCHRON_JOB_SUBMITTED
    running    MEDCHRON_JOB_RUNNING
    held       MEDCHRON_JOB_HELD       (also carries a refusal: reason 'refused: …')
    delivered  MEDCHRON_JOB_DELIVERED
    failed     MEDCHRON_JOB_FAILED

Transitions are monotonic except held -> running (a seat pause lifting, or a
hold the firm resolved and resubmitted through a fresh run of the same job).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .audit_ledger import _iso_utc, _ulid
from .cycle_window import AnchorInvalid, Window, cycle_window, resolve_anchor, resolve_effective_from

STATES = ("submitted", "running", "held", "delivered", "failed")
TERMINAL = frozenset({"delivered", "failed"})
AUDIT_TYPE = {
    "submitted": "MEDCHRON_JOB_SUBMITTED",
    "running": "MEDCHRON_JOB_RUNNING",
    "held": "MEDCHRON_JOB_HELD",
    "delivered": "MEDCHRON_JOB_DELIVERED",
    "failed": "MEDCHRON_JOB_FAILED",
}
_ALLOWED_NEXT = {
    "submitted": {"running", "held", "failed"},
    "running": {"held", "delivered", "failed"},
    "held": {"running", "failed"},
    "delivered": set(),
    "failed": set(),
}

SKILL_NAME = "medical-chronology-maintainer"
# 2026-09-09: the allowance is metered in PAGES, not documents. A document is
# not a unit of work -- the delivered packages ranged from a one-page bill to a
# 700-page hospital chart -- and the cost of a package tracks its pages. The
# old key is deliberately NOT read as a fallback: a seat still carrying only
# the document key reads as unauthored and submits nothing, which is the
# fail-closed answer, and the refusal names the key the firm has to author.
ALLOWANCE_KEY = "chronology_package_page_allowance_per_month"
INCIDENT_SOURCES = frozenset({"matter_layout", "intake_document", "administrator_request", "record_citation"})
_DOB_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_UNIT_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")

CREATE_SQL = (
    "CREATE TABLE IF NOT EXISTS medchron_jobs ("
    "id TEXT PRIMARY KEY, "
    "created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL, "
    "state TEXT NOT NULL, "
    "matter_id TEXT NOT NULL, "
    "matter_number TEXT NOT NULL, "
    "requester TEXT, "
    "request_ref TEXT, "
    "envelope_digest TEXT NOT NULL, "
    "documents INTEGER NOT NULL DEFAULT 0, "
    "pages INTEGER NOT NULL DEFAULT 0, "
    "cents INTEGER NOT NULL DEFAULT 0, "
    "reason TEXT, "
    "folder_id TEXT, "
    "delivery_json TEXT"
    ")"
)
CREATE_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_medchron_jobs_created ON medchron_jobs(created_at)"

# ONE debit rule for pages, documents and cents: a job DEBITS THE MONTH IT WAS
# CREATED IN whenever it recorded cents, in whatever state it ended. Two halves:
#
# * `cents > 0`, any state. Counting delivered jobs only (the rule before
#   2026-09-09) let a run that read thousands of pages and spent real money
#   leave no mark, because it held or failed after the money had moved. A hold
#   at zero cents is not a debit: nothing was read and nothing was spent.
# * Keyed on `created_at`, NOT on the period the cents landed in. A period-of-
#   charge key was tried first and reverted the same day: `month_charged` is not
#   in PROJECTION, and PROJECTION's shape is pinned by the overlay's
#   `_MEDCHRON_JOBS_COLUMNS` this release, so the console could never see it.
#   A job created on the last day of a period whose cents land on the first day
#   of the next would then be debited to the new period on the seat and shown in
#   the old one on the console -- the two surfaces disagreeing about the same
#   period, which is the one thing this rule exists to prevent. Created-time
#   keying is a figure every surface can compute from a column every surface
#   already has. Moving to period-of-charge is the next OVERLAY_REF bump's
#   business (ADR 0087 amendment).
#
# Since 2026-09-11 the period is the firm's BILLING CYCLE when one is authored
# (`cycle_window.py`), and the calendar month when none is. The predicate is a
# half-open range rather than a `substr(...)` prefix, which also lets it use
# `idx_medchron_jobs_created` -- the prefix form could not.
#
# Both forms are written out in full rather than composed: a query built by
# concatenation reads as an injection risk to every scanner and every reviewer,
# even when every part is a literal.
_DEBITS_SQL = (
    "SELECT COALESCE(SUM(pages), 0) AS pages, COALESCE(SUM(documents), 0) AS documents, "
    "COALESCE(SUM(cents), 0) AS cents FROM medchron_jobs "
    "WHERE cents > 0 AND created_at >= ? AND created_at < ?"
)
_DEBITS_SQL_EXCLUDING = (
    "SELECT COALESCE(SUM(pages), 0) AS pages, COALESCE(SUM(documents), 0) AS documents, "
    "COALESCE(SUM(cents), 0) AS cents FROM medchron_jobs "
    "WHERE cents > 0 AND created_at >= ? AND created_at < ? AND id <> ?"
)

# The console projection (the ``medchron_jobs`` runtime-read kind and the
# agent's status verb both read this): counts and states, never the envelope.
PROJECTION = (
    "id",
    "created_at",
    "updated_at",
    "state",
    "matter_number",
    "documents",
    "pages",
    "cents",
    "reason",
    "folder_id",
)


class EnvelopeError(ValueError):
    """The submission is missing something the run needs. Never filled in."""


def digest(obj: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_envelope(req: dict[str, Any]) -> dict[str, Any]:
    """The fields the broker owns, checked the way ``medchron.job.parse`` will
    check them again on the other side. Returns the envelope to queue (only
    known keys, in a fixed shape)."""
    matter = req.get("matter")
    if (
        not isinstance(matter, dict)
        or not str(matter.get("id") or "").strip()
        or not str(matter.get("number") or "").strip()
    ):
        raise EnvelopeError("matter.id and matter.number are required")
    units = req.get("units")
    if not isinstance(units, list) or not units:
        raise EnvelopeError("at least one unit (client_name, surname, dob) is required")
    out_units = []
    seen: set[str] = set()
    for u in units:
        if not isinstance(u, dict):
            raise EnvelopeError("each unit is an object")
        name = str(u.get("client_name") or "").strip()
        surname = str(u.get("surname") or "").strip()
        dob = str(u.get("dob") or "").strip()
        if not name or not surname:
            raise EnvelopeError("unit.client_name and unit.surname are required")
        if not _DOB_RE.match(dob):
            raise EnvelopeError("unit.dob must be MM/DD/YYYY")
        unit = str(u.get("unit") or surname.lower().replace(" ", "-"))
        if not _UNIT_RE.match(unit) or unit in seen:
            raise EnvelopeError(f"unit slug {unit!r} is invalid or repeated")
        seen.add(unit)
        row = {
            "unit": unit,
            "client_name": name,
            "name_token": str(u.get("name_token") or surname),
            "surname": surname,
            "dob": dob,
        }
        if u.get("folder_prefix"):
            row["folder_prefix"] = str(u["folder_prefix"])
        out_units.append(row)
    if len(out_units) > 1 and any("folder_prefix" not in u for u in out_units):
        raise EnvelopeError("a joint matter needs folder_prefix on every unit")
    incident = req.get("incident")
    if not isinstance(incident, dict) or not _DATE_RE.match(str(incident.get("date") or "")):
        raise EnvelopeError("incident.date (YYYY-MM-DD) is required")
    if str(incident.get("source") or "") not in INCIDENT_SOURCES:
        raise EnvelopeError(f"incident.source must be one of {sorted(INCIDENT_SOURCES)}")
    env: dict[str, Any] = {
        "matter": {
            "id": str(matter["id"]).strip(),
            "number": str(matter["number"]).strip(),
            "title": str(matter.get("title") or ""),
        },
        "units": out_units,
        "incident": {"date": str(incident["date"]), "source": str(incident["source"])},
    }
    if req.get("injuries"):
        env["injuries"] = str(req["injuries"])[:2000]
    if isinstance(req.get("selection"), dict):
        # Known keys only, string lists only (ss#2616: include_file_ids is the
        # append grain; the folder-grain overrides remain for SMD-side use).
        sel = {}
        for key in ("include_prefixes", "exclude_substrings", "include_file_ids"):
            value = req["selection"].get(key)
            if isinstance(value, list) and value:
                sel[key] = [str(v)[:200] for v in value[:500]]
        if sel:
            env["selection"] = sel
    cap = req.get("cap_usd")
    if cap is not None:
        if not isinstance(cap, (int, float)) or isinstance(cap, bool) or cap <= 0:
            raise EnvelopeError("cap_usd must be a positive number when present")
        env["cap_usd"] = float(cap)
    for key in ("requested_by", "request_ref"):
        if req.get(key):
            env[key] = str(req[key])[:200]
    return env


def _medchron_skill_settings(path: str | Path) -> dict[str, Any] | None:
    """The authored settings map for this skill, or None when the skill is
    absent or disabled. One walk, so the allowance and the cycle keys can never
    be read off different skills."""
    try:
        import yaml

        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - an unreadable seat config is "nothing authored"
        return None
    for persona in doc.get("personas") or []:
        for skill in (persona or {}).get("skills") or []:
            if not isinstance(skill, dict) or skill.get("name") != SKILL_NAME:
                continue
            if skill.get("enabled") is False:
                return None
            settings = skill.get("settings")
            return settings if isinstance(settings, dict) else {}
    return None


def cycle_from_customer_yaml(path: str | Path) -> tuple[int | None, str | None]:
    """The firm's authored billing-cycle anchor and its effective-from date.

    Raises ``AnchorInvalid`` when either key is present but unreadable. Absent
    is NOT an error -- it means no cycle is authored and the window is the
    calendar month, which is what every seat used before 2026-09-11. Invalid is
    an error, because an authored control the seat cannot honour would
    otherwise silently meter the firm on a different window than it believes.
    """
    settings = _medchron_skill_settings(path)
    if settings is None:
        return (None, None)
    return (resolve_anchor(settings), resolve_effective_from(settings))


def allowance_from_customer_yaml(path: str | Path) -> int | None:
    """The firm's authored monthly document allowance, or None when the skill
    is absent, disabled, or carries no such key (fail closed: no allowance, no
    submission)."""
    try:
        import yaml

        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - an unreadable seat config is "no allowance"
        return None
    for persona in doc.get("personas") or []:
        for skill in (persona or {}).get("skills") or []:
            if not isinstance(skill, dict) or skill.get("name") != SKILL_NAME:
                continue
            if skill.get("enabled") is False:
                return None
            value = (skill.get("settings") or {}).get(ALLOWANCE_KEY)
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    return None


class MedchronLedger:
    """Per-operation sqlite connections, ``journal_mode=DELETE`` — the same
    discipline as ``JobLedgerWriter`` and for the same cross-uid reasons."""

    def __init__(self, db_path: str, queue_dir: str | Path) -> None:
        self._db_path = db_path
        self.queue_dir = Path(queue_dir)
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def ensure_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute(CREATE_SQL)
            conn.execute(CREATE_INDEX_SQL)
            conn.commit()
        finally:
            conn.close()

    # -- the month's debits ------------------------------------------------
    # ONE rule for pages, documents and cents: a job DEBITS THE MONTH when it
    # recorded cents, whatever state it ended in. Counting delivered jobs only
    # (the rule before 2026-09-09) let a run that read 3,000 pages and spent
    # real money against the vendor leave no mark, because it held or failed
    # after the money moved. Held-at-zero jobs are not debits: nothing was read
    # and nothing was spent.
    def debits(self, window: Window, exclude_job_id: str | None = None) -> dict[str, int]:
        """The window's debited pages, documents and cents, in one read so the
        three can never disagree about which rows they counted. The window is
        half-open on a job's CREATED time, and the console and the laptop
        pipeline compute it from the same fixture-pinned algorithm
        (`cycle_window.py`), so no two surfaces can disagree about which rows a
        period holds."""
        conn = self._connect()
        try:
            if exclude_job_id:
                row = conn.execute(_DEBITS_SQL_EXCLUDING, (window.start, window.end, exclude_job_id)).fetchone()
            else:
                row = conn.execute(_DEBITS_SQL, (window.start, window.end)).fetchone()
            return {"pages": int(row["pages"]), "documents": int(row["documents"]), "cents": int(row["cents"])}
        finally:
            conn.close()

    def pages_used(self, window: Window, exclude_job_id: str | None = None) -> int:
        return self.debits(window, exclude_job_id)["pages"]

    def cents_used(self, window: Window, exclude_job_id: str | None = None) -> int:
        return self.debits(window, exclude_job_id)["cents"]

    def documents_used(self, window: Window, exclude_job_id: str | None = None) -> int:
        return self.debits(window, exclude_job_id)["documents"]

    def allowance(
        self,
        allowance: int | None,
        now: str | None = None,
        exclude_job_id: str | None = None,
        anchor_day: int | None = None,
        effective_from: str | None = None,
    ) -> dict[str, Any]:
        """The cycle's allowance state, in PAGES.

        `used`/`remaining` are the allowance's own unit and `unit` says which
        it is, so a caller can never read a page count as a document count.
        The document and cents figures ride alongside for the console and the
        runner's cost limits. `exclude_job_id` leaves one job's own row out,
        which is what a resume needs so it is not metered against itself.

        `anchor_day` is the firm's billing-cycle day; without one the window is
        the calendar month, which is what an unauthored seat has always used.
        The anchor arrives as an argument rather than being read here for the
        same reason the allowance does: `MedchronLedger` has no path to
        `customer.yaml` (`medchron_verbs` owns that read).

        `month` keeps its key -- the overlay's pinned tool whitelists it by name
        -- but carries PROSE for a human ("the cycle ending Oct 14"). The machine
        range rides `cycle_start`/`cycle_end`; never parse `month`.
        """
        window = cycle_window(now or _iso_utc(), anchor_day, effective_from)
        pages = self.pages_used(window, exclude_job_id)
        extra: dict[str, Any] = {
            "unit": "pages",
            "pages_used": pages,
            "documents_used": self.documents_used(window, exclude_job_id),
            "cents_used": self.cents_used(window, exclude_job_id),
            "cycle_start": window.start,
            "cycle_end": window.end,
            "cycle_anchored": window.anchored,
        }
        if allowance is None:
            return {
                "month": window.label,
                "allowance": None,
                "used": pages,
                "remaining": 0,
                "authored": False,
                "pages_remaining": 0,
                **extra,
            }
        remaining = max(0, allowance - pages)
        return {
            "month": window.label,
            "allowance": allowance,
            "used": pages,
            "remaining": remaining,
            "authored": True,
            "pages_remaining": remaining,
            **extra,
        }

    # -- intake ------------------------------------------------------------
    def submit(self, envelope: dict[str, Any], *, remaining: int) -> str:
        """Row + queue file, in that order, so an envelope on disk always has
        its row (the daemon quarantines the other way round)."""
        job_id = _ulid()
        now = _iso_utc()
        queued = dict(envelope)
        queued["job_id"] = job_id
        # Both keys, the same integer, for ONE release. The allowance is in
        # pages now; `allowance_remaining_documents` stays because the overlay's
        # pinned tool relays that key by name and an old broker restarting
        # during the rollout window still reads it. It goes when the pin moves
        # (ADR 0087 amendment, first item of the next overlay bump).
        queued["allowance_remaining_pages"] = int(remaining)
        queued["allowance_remaining_documents"] = int(remaining)
        queued["submitted_at"] = now
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO medchron_jobs (id, created_at, updated_at, state, matter_id, matter_number, requester, "
                "request_ref, envelope_digest) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    now,
                    now,
                    "submitted",
                    envelope["matter"]["id"],
                    envelope["matter"]["number"],
                    envelope.get("requested_by"),
                    envelope.get("request_ref"),
                    digest(envelope),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.queue_dir / f".{job_id}.json.tmp"
        tmp.write_text(json.dumps(queued, indent=1, sort_keys=True), encoding="utf-8")
        tmp.chmod(0o640)
        tmp.replace(self.queue_dir / f"{job_id}.json")
        return job_id

    # -- read --------------------------------------------------------------
    def read(self, job_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            r = conn.execute("SELECT * FROM medchron_jobs WHERE id=?", (job_id,)).fetchone()
            return dict(r) if r is not None else None
        finally:
            conn.close()

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM medchron_jobs ORDER BY created_at DESC LIMIT ?", (max(1, min(int(limit), 200)),)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def project(row: dict[str, Any]) -> dict[str, Any]:
        return {k: row.get(k) for k in PROJECTION}

    # -- the runner's report ----------------------------------------------
    def record(self, job_id: str, state: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Apply one transition. Returns the row after. Raises ValueError on an
        unknown job or an illegal transition; the caller writes the audit row."""
        if state not in STATES:
            raise ValueError(f"unknown state {state!r}")
        conn = self._connect()
        try:
            cur = conn.execute("SELECT state FROM medchron_jobs WHERE id=?", (job_id,)).fetchone()
            if cur is None:
                raise ValueError(f"no such job {job_id}")
            # ss#2616: a same-state re-record is a NOTE (a lost deliver wake,
            # for instance) — legal, merges fields, writes one audit row with
            # the current state's type. Transitions stay monotonic otherwise.
            if state != cur["state"] and state not in _ALLOWED_NEXT[cur["state"]]:
                raise ValueError(f"illegal transition {cur['state']} -> {state}")
            sets = ["state=?", "updated_at=?"]
            vals: list[Any] = [state, _iso_utc()]
            for col in ("documents", "pages", "cents"):
                if col in fields:
                    v = fields[col]
                    if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                        raise ValueError(f"{col} must be a non-negative int")
                    sets.append(f"{col}=?")
                    vals.append(v)
            if "reason" in fields:
                sets.append("reason=?")
                vals.append(str(fields["reason"] or "")[:500] or None)
            if "folder_id" in fields:
                sets.append("folder_id=?")
                vals.append(str(fields["folder_id"] or "") or None)
            if "delivery" in fields and isinstance(fields["delivery"], dict):
                sets.append("delivery_json=?")
                vals.append(json.dumps(fields["delivery"], sort_keys=True))
            vals.append(job_id)
            conn.execute(f"UPDATE medchron_jobs SET {', '.join(sets)} WHERE id=?", vals)
            conn.commit()
            row = conn.execute("SELECT * FROM medchron_jobs WHERE id=?", (job_id,)).fetchone()
            return dict(row)
        finally:
            conn.close()


def now_utc() -> datetime:
    return datetime.now(UTC)
