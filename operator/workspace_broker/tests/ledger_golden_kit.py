"""The broker job ledger goldens: what each lane's ledger IS and DOES, run
through the real ledger classes and the real broker verbs
(``bin/lib/golden_broker.build_broker``), never read from their source.

Per lane (``goldens/ledgers/<lane>/``):

* ``schema.json``: ``sqlite_master`` and ``PRAGMA table_info`` / ``index_list``
  of every table the ledger owns, on a fresh file, and on each aged schema in
  ``aged.json`` (``bin/goldens/ledger-aged-schemas.py``) after today's class
  opened it;
* ``transitions.json``: every state x every target state, each on a fresh
  ledger driven to the state through the ``<lane>_job_record`` verb: the
  reply (or refusal text), the row after, every row of every table the lane
  owns, and the audit rows the verb appended; plus ``project()`` and
  ``recent()`` of the rows;
* ``reply_once.json``: ``mark_replied`` / ``unmark_replied`` truth table;
* ``month.json``: the lane's month read at Pacific month edges, with and
  without the job itself excluded;
* ``verbs.json``: every verb of the lane, called by root and by the gateway,
  with no payload and with a known job id.

The clock and the ids are fixed (``Clock``): every ``_iso_utc`` and ``_ulid``
in the broker reads it, and each verb's ``_now``.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

OPERATOR = Path(__file__).resolve().parents[2]
if str(OPERATOR) not in sys.path:
    sys.path.insert(0, str(OPERATOR))

from bin.lib.golden_broker import build_broker  # noqa: E402 - needs the tree root on sys.path, set just above
from bin.lib.golden_diff import diffs, load_allowed, unexplained  # noqa: E402 - needs the tree root on sys.path, set just above

GOLDENS = Path(__file__).resolve().parent / "goldens" / "ledgers"
QUEUED = ("medchron", "demand", "drafting", "litigation", "casefile", "negotiation", "records")
GATEWAY_PID = 999_999_999
START = datetime(2026, 10, 20, 17, 0, tzinfo=timezone.utc)
MATTER = "0f0f0f0f-0000-4000-8000-000000000001"
LIBRARY = "99999999-9999-4999-8999-999999999999"


class Clock:
    """One instant every ledger and verb reads; each ``_iso_utc`` read returns
    it and then moves it 1 s on (rows written one after another keep their order)."""

    def __init__(self) -> None:
        self.t = START
        self.n = 0

    def iso(self) -> str:
        out = self.t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{self.t.microsecond // 1000:03d}Z"
        self.t += timedelta(seconds=1)
        return out

    def ulid(self) -> str:
        self.n += 1
        return f"01KTJ0BX{self.n:018d}"

    def now(self) -> datetime:
        return self.t


def fix_clock(mp: Any, clock: Clock) -> None:
    # Every module that may read the clock is imported first, so none binds
    # the real ``_iso_utc`` / ``_ulid`` after the patch.
    import workspace_broker.server  # noqa: F401 - imported for its lane modules
    from workspace_broker import library_setup_ledger, note_ledger, records_store  # noqa: F401 - imported so no module binds the real clock after the patch

    for name, mod in list(sys.modules.items()):
        if not name.startswith("workspace_broker") or mod is None:
            continue
        if hasattr(mod, "_iso_utc"):
            mp.setattr(mod, "_iso_utc", clock.iso)
        if hasattr(mod, "_ulid"):
            mp.setattr(mod, "_ulid", clock.ulid)


# ---------------------------------------------------------------------------
# The lanes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Lane:
    name: str
    #: The broker attribute holding the lane's verbs (its ledger is ``._db``).
    verbs: str
    ledger_module: str
    tables: tuple[str, ...]
    submit: Callable[[Any, int], str]
    delivered: dict[str, Any]
    #: The month read the lane's host sends (``action``, extra payload).
    month: tuple[str, dict[str, Any]] | None
    flat_record: bool = False

    @property
    def record(self) -> str:
        return "drafting_job_record" if self.name == "library_setup" else f"{self.name}_job_record"


def _sub_medchron(b: Any, n: int) -> str:
    unit = {"unit": "alpha", "client_name": "Alpha Example", "name_token": "Example", "surname": "Example"}
    env = {
        "matter": {"id": f"m-{n}", "number": f"2026-PI-{100 + n}", "title": "Example v. Example"},
        "units": [{**unit, "dob": "01/02/1980"}],
        "incident": {"date": "2026-01-15", "source": "administrator_request"},
        "cap_usd": 25.0,
        "requested_by": "admin@firm.example",
        "request_ref": f"<req-{n}@firm.example>",
    }
    return b.medchron._db.submit(env, remaining=40)


def _sub_demand(b: Any, n: int) -> str:
    env = {
        "matter": {"id": f"0f0f0f0f-0000-4000-8000-{n:012d}", "number": f"1000{n:02d}"},
        "file_to": None,
        "requested_by": "admin@firm.example",
        "request_ref": f"<req-{n}@firm.example>",
        "request_text": "DELIVERABLE 1: gap audit. DELIVERABLE 2: draft demand.",
        "deliverables": ["gap_audit", "demand"],
    }
    return b.demand._db.submit(env)


def _sub_drafting(b: Any, n: int) -> str:
    env = {
        "document_class": "mediation_brief",
        "file_to": None,
        "matter": {"id": f"0f0f0f0f-0000-4000-8000-{n:012d}", "number": f"1000{n:02d}"},
        "request_ref": f"<req-{n}@firm.example>",
        "request_text": "Please draft the mediation brief.",
        "requested_by": "admin@firm.example",
    }
    return b.drafting._db.submit(env)


def _sub_library_setup(b: Any, n: int) -> str:
    from workspace_broker.library_setup_ledger import SetupLedger

    led = SetupLedger(b.audit_db_path, b.drafting._db.queue_dir)
    env = {
        "kind": "memo",
        "attorney": None,
        "requested_by": "admin@firm.example",
        "request_ref": f"<setup-{n}@firm.example>",
        "request_text": "Set up our memo",
    }
    return led.submit(env, library_number="OPS-LIBRARY", live=True)


def _sub_litigation(b: Any, n: int) -> str:
    return b.litigation._db.submit(
        {
            "trigger": "request",
            "requester": "admin@firm.example",
            "message_ref": f"<list-{n}@firm.example>",
            "request_text": "status list please",
            "scope": {"paralegal_staff_ids": [f"11111111-2222-3333-4444-5555555555{n:02d}"]},
            "file_to_matter_id": LIBRARY,
            "file_to_matter_number": "OPS-OPERATOR-LIBRARY",
            "folder_name": "Litigation Status",
        }
    )


def _sub_casefile(b: Any, n: int) -> str:
    return b.casefile._db.submit(
        {
            "trigger": "request",
            "mode": "full",
            "requester": "admin@firm.example",
            "message_ref": f"<req-{n}@firm.example>",
            "request_text": "Please review Pat Example's files.",
            "scope": {"assisting": {"names": [f"Pat Example{n}"]}},
            "file_to_matter_id": LIBRARY,
            "file_to_matter_number": "OPS-LIBRARY",
            "folder_name": "Case File Manager",
        }
    )


def _sub_negotiation(b: Any, n: int) -> str:
    return b.negotiation._db.submit(
        {
            "trigger": "scheduled",
            "requester": "admin@firm.example",
            "message_ref": f"scheduled:2026-10-{10 + n}T08",
            "request_text": "negotiation watch",
            "matter_statuses": ["Open"],
            "negotiation_design": "",
            "firm_words": ["example"],
            "seed_saved_before": "",
            "per_job_cap_usd": 8,
            "monthly_budget_usd": 25,
        }
    )


def _sub_records(b: Any, n: int) -> str:
    return b.records._db.submit(
        {
            "trigger": "request",
            "requester": "pat.lane@example-firm.test",
            "message_ref": f"AAMkAGraphMessageId000{n}",
            "request_text": "the clinic visit",
            "matter_id": "11111111-2222-3333-4444-555555555555",
            "ack": "turn",
        }
    )


FILE = {"name": "Out.xlsx", "role": "report", "size": 900, "sha256": "b" * 64}
LANES: dict[str, Lane] = {
    "medchron": Lane("medchron", "medchron", "medchron_ledger", ("medchron_jobs", "medchron_page_credits"),
                     _sub_medchron, {"cents": 500, "documents": 4, "pages": 30, "folder_id": "folder-9",
                                     "delivery": {"files": [{"bytes": 10, "confirmed": True, "name": "A.docx", "sha256": "ab"}]}},
                     ("medchron_allowance", {})),
    "demand": Lane("demand", "demand", "demand_ledger", ("demand_jobs", "demand_spend"), _sub_demand,
                   {"cents": 500, "folder_id": "folder-7",
                    "delivery": {"cents": 500, "coverage_report": False, "files": [{"name": "Out.docx", "role": "demand", "size": 41000}]}},
                   ("demand_allowance", {})),
    "drafting": Lane("drafting", "drafting", "drafting_ledger", ("drafting_jobs", "drafting_spend"), _sub_drafting,
                     {"cents": 500, "folder_id": "folder-7", "caption_corrections": [], "caption_discrepancies": [], "markers": [],
                      "delivery": {"cents": 500, "files": [{"name": "Out.docx", "role": "drafting", "size": 41000}]}},
                     ("drafting_allowance", {})),
    "library_setup": Lane("library_setup", "drafting", "library_setup_ledger", ("library_setup_jobs",), _sub_library_setup,
                          {"cents": 0, "report": {"counts": {"found": 2}, "setup_outcome": "proposed"}}, None),
    "litigation": Lane("litigation", "litigation", "litigation_ledger", ("litigation_jobs", "litigation_spend"), _sub_litigation,
                       {"cents": 500, "files": [FILE], "flags_new": 0, "folder_id": "folder-7", "matters_reread": 0,
                        "matters_total": 9, "stage": "report"}, ("litigation_job_status", {})),
    "casefile": Lane("casefile", "casefile", "casefile_ledger", ("casefile_jobs", "casefile_lines", "casefile_spend"), _sub_casefile,
                     {"cents": 500, "files": [FILE], "folder_id": "folder-7", "lines_proposed": 1, "matters_current": 0,
                      "matters_held": 0, "matters_read": 1, "matters_total": 9, "stage": "report",
                      "lines": [{"n": 1, "kind": "task", "matter_id": MATTER, "source_id": "src1", "sha256": "e" * 64}]},
                     ("casefile_job_status", {})),
    "negotiation": Lane("negotiation", "negotiation", "negotiation_ledger",
                        ("negotiation_jobs", "negotiation_notices", "negotiation_spend"), _sub_negotiation,
                        {"cents": 500, "docs_failed": 0, "docs_read": 2, "matters_seeded": 0, "matters_total": 9, "stage": "report",
                         "notices": [{"matter_id": "m", "matter_number": "1", "status": "entered", "text": "x"}]},
                        ("negotiation_job_status", {})),
    "records": Lane("records", "records", "records_ledger", ("records_jobs", "records_lines", "records_spend"), _sub_records,
                    {"cents": 500, "stage": "verdict", "verdict": "delivered", "items": [{"files": 1, "n": 1, "outcome": "attached"}],
                     "delivery": {"digest": "c" * 64, "review_id": "d" * 24}},
                    ("records_job_status", {}), flat_record=True),
}  # fmt: skip
#: Every table a lane's ledger owns (the only names ``Seat.tables`` reads).
OWNED = frozenset(t for lane in LANES.values() for t in lane.tables) | {"note_jobs"}


def module(lane: Lane) -> Any:
    """The lane's ledger module, from a fixed table."""
    from workspace_broker import (
        casefile_ledger,
        demand_ledger,
        drafting_ledger,
        library_setup_ledger,
        litigation_ledger,
        medchron_ledger,
        negotiation_ledger,
        note_ledger,
        records_ledger,
    )

    return {
        "medchron_ledger": medchron_ledger,
        "demand_ledger": demand_ledger,
        "drafting_ledger": drafting_ledger,
        "library_setup_ledger": library_setup_ledger,
        "litigation_ledger": litigation_ledger,
        "casefile_ledger": casefile_ledger,
        "negotiation_ledger": negotiation_ledger,
        "records_ledger": records_ledger,
        "note_ledger": note_ledger,
    }[lane.ledger_module]


# ---------------------------------------------------------------------------
# A seat
# ---------------------------------------------------------------------------


class Seat:
    """A fresh broker on a fresh audit file, with the clock fixed."""

    def __init__(self, root: Path, mp: Any) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.clock = Clock()
        self.db = root / "audit" / "audit.db"
        queues = {q: root / "run" / f"{q}-queue" for q in QUEUED}
        fix_clock(mp, self.clock)
        self.broker = build_broker(root, self.db, queues)
        for lane in QUEUED:
            v = getattr(self.broker, lane)
            if hasattr(v, "_now"):
                mp.setattr(v, "_now", self.clock.now)
        self.audit_mark = 0

    def call(self, request: dict[str, Any], *, gateway: bool = False) -> dict[str, Any]:
        from workspace_broker.canon import canonical
        from workspace_broker.request_errors import error_response_for

        try:
            reply = self.broker.handle(request, GATEWAY_PID if gateway else 1, None if gateway else 0)
        except Exception as exc:  # noqa: BLE001 - the server's own mapping of every exception
            reply = error_response_for(exc)
        return json.loads(canonical(reply))

    def audit(self) -> list[dict[str, Any]]:
        conn = sqlite3.connect(self.db)
        rows = conn.execute(
            "SELECT rowid, action_type, matter_ref, metadata FROM audit_log WHERE rowid > ? ORDER BY rowid",
            (self.audit_mark,),
        ).fetchall()
        conn.close()
        if rows:
            self.audit_mark = rows[-1][0]
        return [{"action_type": a, "matter_ref": m, "metadata": json.loads(md or "null")} for _r, a, m, md in rows]

    def tables(self, names: tuple[str, ...]) -> dict[str, list[dict[str, Any]]]:
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        out: dict[str, list[dict[str, Any]]] = {}
        for t in names:
            if t not in OWNED:
                raise ValueError(f"not a ledger table: {t}")
            try:
                # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query, python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query - t is checked against OWNED, the fixed set of ledger table names above; no input reaches it
                out[t] = [dict(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY rowid")]  # noqa: S608 - t is one of OWNED, the fixed ledger table names
            except sqlite3.Error as exc:
                out[t] = [{"error": str(exc)}]
        conn.close()
        return out


def normal(doc: Any, root: Path) -> Any:
    text = json.dumps(doc, sort_keys=True, default=str)
    return json.loads(text.replace(str(root), "<TMP>"))


__all__ = [
    "GOLDENS",
    "LANES",
    "Clock",
    "Lane",
    "Seat",
    "diffs",
    "load_allowed",
    "module",
    "normal",
    "unexplained",
]
