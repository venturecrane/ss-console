"""What each broker job ledger golden records (``ledger_golden_kit.py`` says
why). One function per golden file; each returns the JSON document."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ledger_golden_kit import GOLDENS, LANES, Lane, Seat, module, normal

#: Every lane with a ledger golden set: the eight record lanes and the note ledger.
ALL = (*LANES, "note")
NOTE_TABLES = ("note_jobs",)
#: The Pacific month edges (PDT ends 2026-11-01; PST ends 2026-12-01).
EDGES = (
    datetime(2026, 11, 1, 6, 59, 59, tzinfo=timezone.utc),
    datetime(2026, 11, 1, 7, 0, 0, tzinfo=timezone.utc),
    datetime(2026, 12, 1, 7, 59, 59, tzinfo=timezone.utc),
    datetime(2026, 12, 1, 8, 0, 0, tzinfo=timezone.utc),
)


def tables_of(name: str) -> tuple[str, ...]:
    return NOTE_TABLES if name == "note" else LANES[name].tables


def open_today(name: str, db: Path, queue: Path) -> Any:
    """Today's ledger class of the lane, opened on ``db`` (its ``ensure_schema`` runs)."""
    from workspace_broker.casefile_store import CasefileLedger
    from workspace_broker.demand_ledger import DemandLedger
    from workspace_broker.drafting_ledger import DraftingLedger
    from workspace_broker.library_setup_ledger import SetupLedger
    from workspace_broker.litigation_ledger import LitigationLedger
    from workspace_broker.medchron_ledger import MedchronLedger
    from workspace_broker.negotiation_ledger import NegotiationLedger
    from workspace_broker.note_ledger import NoteLedger
    from workspace_broker.records_store import RecordsLedger

    classes: dict[str, Any] = {
        "medchron": MedchronLedger,
        "demand": DemandLedger,
        "drafting": DraftingLedger,
        "library_setup": SetupLedger,
        "litigation": LitigationLedger,
        "casefile": CasefileLedger,
        "negotiation": NegotiationLedger,
        "records": RecordsLedger,
    }
    if name == "note":
        return NoteLedger(str(db))
    return classes[name](str(db), queue)


def dump(db: Path, tables: tuple[str, ...]) -> dict[str, Any]:
    conn = sqlite3.connect(db)
    out: dict[str, Any] = {}
    for t in tables:
        master = conn.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE tbl_name = ? AND sql IS NOT NULL ORDER BY type, name", (t,)
        ).fetchall()
        info = conn.execute("SELECT * FROM pragma_table_info(?)", (t,)).fetchall()
        indexes = conn.execute(
            'SELECT name, "unique", origin, partial FROM pragma_index_list(?) ORDER BY name', (t,)
        ).fetchall()
        out[t] = {
            "sqlite_master": [list(r) for r in master],
            "table_info": [list(r) for r in info],
            "index_list": [
                [*r, [list(c) for c in conn.execute("SELECT * FROM pragma_index_info(?)", (r[0],)).fetchall()]]
                for r in indexes
            ],
        }
    conn.close()
    return out


def schema(name: str, tmp: Path, mp: Any) -> dict[str, Any]:
    tables = tables_of(name)
    fresh = tmp / "fresh"
    fresh.mkdir(parents=True)
    open_today(name, fresh / "audit.db", fresh / "queue")
    aged_doc = json.loads((GOLDENS / name / "aged.json").read_text(encoding="utf-8"))
    aged = []
    for i, step in enumerate(aged_doc["steps"]):
        d = tmp / f"aged-{i}"
        d.mkdir()
        conn = sqlite3.connect(d / "audit.db")
        rows = [s.split(": ", 1) for s in step["schema"]]
        for kind, sql in sorted(rows, key=lambda r: (not r[0].startswith("table"), r[0])):
            conn.execute(sql)
        conn.commit()
        conn.close()
        open_today(name, d / "audit.db", d / "queue")
        aged.append({"commit": step["commit"], "after_today_opened_it": dump(d / "audit.db", tables)})
    return {"fresh": dump(fresh / "audit.db", tables), "aged": aged}


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


def fields(lane: Lane, target: str) -> dict[str, Any]:
    if target == "delivered":
        return dict(lane.delivered)
    return {
        "held": {"reason": "limit_held: a matrix hold", "cents": 100},
        "failed": {"reason": "unexpected: a matrix failure", "cents": 100},
        "review": {"cents": 100, "delivery": {"digest": "c" * 64, "review_id": "d" * 24}},
        "observed": {"cents": 0, "report": {"counts": {"found": 0}}},
    }.get(target, {})


def request(lane: Lane, job_id: str, target: str) -> dict[str, Any]:
    f = fields(lane, target)
    if lane.flat_record:
        return {"action": lane.record, "job_id": job_id, "state": target, **f}
    return {"action": lane.record, "job_id": job_id, "state": target, "fields": f}


def ledger(seat: Seat, lane: Lane) -> Any:
    if lane.name == "library_setup":
        from workspace_broker.library_setup_ledger import SetupLedger

        return SetupLedger(str(seat.db), seat.broker.drafting._db.queue_dir)
    return getattr(seat.broker, lane.verbs)._db


def _state(seat: Seat, lane: Lane, job_id: str) -> str:
    return str((ledger(seat, lane).read(job_id) or {}).get("state"))


def _attempt(lane: Lane, root: Path, mp: Any, path: list[str], target: str) -> tuple[dict[str, Any], str]:
    seat = Seat(root, mp)
    jid = lane.submit(seat.broker, 1)
    for step in path:
        assert seat.call(request(lane, jid, step))["ok"], (lane.name, path, step)
    seat.audit()
    reply = seat.call(request(lane, jid, target))
    led = ledger(seat, lane)
    row = led.read(jid)
    recent = getattr(led, "recent", None) or getattr(led, "list_recent", None)
    got = {
        "reply": reply,
        "row": row,
        "project": type(led).project(row) if row is not None and hasattr(type(led), "project") else None,
        "recent": recent() if recent is not None else None,
        "tables": seat.tables(lane.tables),
        "audit": seat.audit(),
    }
    return normal(got, root), _state(seat, lane, jid)


def transitions(name: str, tmp: Path, mp: Any) -> dict[str, Any]:
    lane = LANES[name]
    states = tuple(module(lane).STATES)
    seat = Seat(tmp / "start", mp)
    start = _state(seat, lane, lane.submit(seat.broker, 1))
    paths: dict[str, list[str]] = {start: []}
    queue = [start]
    matrix: dict[str, dict[str, Any]] = {}
    n = 0
    while queue:
        s = queue.pop(0)
        matrix[s] = {}
        for t in states:
            n += 1
            got, after = _attempt(lane, tmp / f"a{n}", mp, paths[s], t)
            matrix[s][t] = got
            if got["reply"].get("ok") and after not in paths:
                paths[after] = [*paths[s], t]
                queue.append(after)
    return {
        "states": list(states),
        "start": start,
        "reached_by": paths,
        "never_reached": [s for s in states if s not in paths],
        "matrix": matrix,
    }


# ---------------------------------------------------------------------------
# Reply once, the month, the verbs
# ---------------------------------------------------------------------------


def _try(fn: Any, *a: Any) -> Any:
    try:
        return {"returned": fn(*a)}
    except Exception as exc:  # noqa: BLE001 - the refusal IS the golden
        return {"raised": type(exc).__name__, "message": str(exc)}


def _note_job(seat: Seat) -> tuple[Any, str]:
    from workspace_broker.note_ledger import NoteLedger

    led = NoteLedger(str(seat.db))
    jid = led.create(
        requester="admin@firm.example", request_ref="<req-1@firm.example>", text="A note.",
        approved_by="smd@example.test", recorded_by="smd@example.test",
    )  # fmt: skip
    return led, jid


def reply_once(name: str, tmp: Path, mp: Any) -> dict[str, Any]:
    seat = Seat(tmp, mp)
    if name == "note":
        led, jid = _note_job(seat)
    else:
        lane = LANES[name]
        jid = lane.submit(seat.broker, 1)
        for step in ("running", "delivered"):
            seat.call(request(lane, jid, step))
        led = ledger(seat, lane)
    if not hasattr(led, "mark_replied"):
        return {"not_applicable": f"{type(led).__name__} has no reply-once mark (no mark_replied)"}
    rows = [
        ("mark reply", _try(led.mark_replied, jid)),
        ("mark reply again", _try(led.mark_replied, jid)),
        ("mark another key", _try(led.mark_replied, jid, "second")),
        ("unmark reply", _try(led.unmark_replied, jid, "reply")),
        ("mark reply after unmark", _try(led.mark_replied, jid)),
        ("unmark a key never marked", _try(led.unmark_replied, jid, "never")),
        ("mark an unknown job", _try(led.mark_replied, "01KTJ0BX999999999999999999")),
    ]
    return normal({"rows": [{"call": c, "result": r} for c, r in rows], "row": led.read(jid)}, tmp)


def month(name: str, tmp: Path, mp: Any) -> dict[str, Any]:
    lane = LANES[name]
    if lane.month is None:
        return {"not_applicable": "this lane has no month read"}
    seat = Seat(tmp, mp)
    action, extra = lane.month
    ids = []
    for n, at in ((1, EDGES[0]), (2, EDGES[1])):
        jid = lane.submit(seat.broker, n)
        seat.call(request(lane, jid, "running"))
        seat.clock.t = at
        seat.call(request(lane, jid, "failed"))
        ids.append(jid)
    reads = []
    for now in (EDGES[0] - timedelta(days=5), *EDGES, EDGES[1] + timedelta(days=14)):
        for exclude in ("", ids[0]):
            seat.clock.t = now
            req = {"action": action, **extra, **({"exclude_job_id": exclude} if exclude else {})}
            reads.append({"now": now.isoformat(), "exclude": exclude, "reply": seat.call(req)})
    return normal({"spend_at": [EDGES[0].isoformat(), EDGES[1].isoformat()], "jobs": ids, "reads": reads}, tmp)


def _verb_names(name: str) -> list[str]:
    from workspace_broker import (
        casefile_verbs,
        demand_verbs,
        drafting_verbs,
        library_setup_verbs,
        litigation_verbs,
        medchron_verbs,
        negotiation_verbs,
        note_verbs,
        records_verbs,
    )

    table = {
        "medchron": medchron_verbs.VERBS,
        "demand": demand_verbs.VERBS,
        "drafting": drafting_verbs.VERBS,
        "library_setup": library_setup_verbs.VERBS,
        "litigation": litigation_verbs.VERBS,
        "casefile": casefile_verbs.VERBS,
        "negotiation": negotiation_verbs.VERBS,
        "records": records_verbs.VERBS,
        "note": note_verbs.VERBS,
    }
    return sorted(table[name])


def verbs(name: str, tmp: Path, mp: Any) -> dict[str, Any]:
    seat = Seat(tmp, mp)
    if name == "note":
        _led, jid = _note_job(seat)
    else:
        jid = LANES[name].submit(seat.broker, 1)
    out: dict[str, Any] = {}
    for verb in _verb_names(name):
        for caller in ("root", "gateway"):
            for label, payload in (("empty", {}), ("known job", {"job_id": jid})):
                seat.clock.t = datetime(2026, 11, 12, 18, 0, tzinfo=timezone.utc)
                out[f"{verb} | {caller} | {label}"] = seat.call(
                    {"action": verb, **payload}, gateway=caller == "gateway"
                )
    return normal(out, tmp)


CAPTURES = {
    "schema": schema,
    "transitions": transitions,
    "reply_once": reply_once,
    "month": month,
    "verbs": verbs,
}

__all__ = ["ALL", "CAPTURES", "Path"]
