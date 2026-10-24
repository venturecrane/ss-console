"""The self-improving help guide, constrained (ss-console#3423, increment 3).

THE INTENT. The Operator should get better at helping people work with it, by
noticing where they struggle and leading its help answer with the tip that
fits. It must never write its own advice: every word a person reads is one SMD
authored in ``operator-help/references/tips.yaml``, reviewed by PR. So the only
thing the Operator may change is the ORDER of emphasis: which SMD tips lead the
help answer. This module owns that one list, its history, its undo, and the
review that decides when a change is earned.

WHAT THE OPERATOR MAY DO, enforced here and not in a prompt:

* ``promote`` a tip id to the head of the emphasis list, only when the tip's
  ``when`` kind is ACTIONABLE in a fresh review (it recurred at least
  :data:`ACTIONABLE_MIN_OCCURRENCES` times from at least
  :data:`ACTIONABLE_MIN_SENDERS` distinct people in :data:`REVIEW_WINDOW_DAYS`);
* ``demote`` (remove) an emphasized tip, or ``reorder`` one within the list;
* at most :data:`MAX_CHANGES` changes in any rolling :data:`CHANGE_WINDOW_DAYS`.

An unknown tip id is refused. No free text is accepted anywhere: the tool's only
inputs are an action enum, a tip id checked against the authored tips, and a
position integer. Every change writes a snapshot of the list before it, the
list after it, and the counts that justified it (``help_snapshots``). SMD's
``undo`` restores the newest snapshot not yet undone; an undo is not a change
and does not spend the weekly allowance.

WHERE THE SIGNALS COME FROM (``review``), all counts, never content:

* ``requests.db`` (``shared.request_index.read_requests``): requests in the
  window, and REPEAT ASKS: a thread in which one person sent at least
  ``help_state.REPEAT_ASK_PRIOR + 1`` emails (the same bar as the struggle
  line). Kind ``repeat_ask``.
* the job ledgers, through the broker's read-only ``help_job_signals`` verb
  (``operator/workspace_broker/help_signals.py``): held and failed jobs by lane
  and hold class. A hold whose answer is the firm's (a record that names
  someone else, two possible chronologies, a person the staff list cannot
  settle) is kind ``held_identity_check``; a list request that matched no open
  matter is kind ``nothing_in_scope``. A hold or failure that is OURS (the
  firm-safe ``smd_finishing`` family, a failed run, a lane awaiting proof) is
  counted and reported, and never treated as a person struggling: no tip can
  fix our own fault. Distinct people for a job kind come from joining the job's
  request ref to the request index's ``internet_message_id``.
* the model's own notes (``help_state.NOTE_STRUGGLE_KINDS``), from
  ``help_events``, distinct people by salted hash.

A READ THAT FAILS IS NEVER AN EMPTY RESULT. When any source cannot be read the
review returns ``read: false`` and the list of ``unreadable`` sources, and
carries no ``kinds`` and no ``actionable`` at all, so a model can never read
"could not look" as "nothing to do"; a promotion then has no evidence and is
refused.

THE WEEKLY NOTE reads what this module stores (the snapshots and the last
review, ``help_meta`` key ``last_review``) from ``operator-help/pre_run.py``,
read-only. That is why the last review is stored as counts only.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from shared import request_index

from . import help_state

logger = logging.getLogger(__name__)

REVIEW_WINDOW_DAYS = 7
ACTIONABLE_MIN_OCCURRENCES = 3
ACTIONABLE_MIN_SENDERS = 2
MAX_CHANGES = 3
CHANGE_WINDOW_DAYS = 7
#: The longest emphasis list. A promotion past it drops the tail.
EMPHASIS_MAX = 3

ACTION_PROMOTE = "promote"
ACTION_DEMOTE = "demote"
ACTION_REORDER = "reorder"
ACTIONS: tuple[str, ...] = (ACTION_PROMOTE, ACTION_DEMOTE, ACTION_REORDER)

KIND_REPEAT_ASK = "repeat_ask"
KIND_HELD_IDENTITY = "held_identity_check"
KIND_NOTHING_IN_SCOPE = "nothing_in_scope"
#: Hold classes (``workspace_broker/hold_class.py``) whose answer is the
#: firm's, mapped to the struggle kind they evidence. Every other class is ours.
HOLD_CLASS_KINDS: dict[str, str] = {
    "another_person": KIND_HELD_IDENTITY,
    "which_chronology": KIND_HELD_IDENTITY,
    "staff_unresolved": KIND_HELD_IDENTITY,
    "requester_unresolved": KIND_HELD_IDENTITY,
    "nothing_in_scope": KIND_NOTHING_IN_SCOPE,
}

_EMPHASIS_KEY = "emphasis"
_LAST_REVIEW_KEY = "last_review"
_TIP_ID_MAX = 64

#: The sources a review reads; any one unreadable makes the review unreadable.
SOURCE_REQUESTS = "requests"
SOURCE_JOBS = "jobs"
SOURCE_NOTES = "notes"
SOURCE_TIPS = "tips"


# --------------------------------------------------------------------------- #
# Emphasis
# --------------------------------------------------------------------------- #


def _meta_get(conn: sqlite3.Connection, key: str) -> Any:
    row = conn.execute("SELECT value FROM help_meta WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row is not None else None


def _meta_set(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO help_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value, separators=(",", ":"))),
    )


def _clean_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item and item not in out:
            out.append(item)
    return out


def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.execute("PRAGMA busy_timeout=2000")
    return conn


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def read_emphasis(*, path: Path | None = None) -> dict[str, Any]:
    """``{read, emphasis}``. A file or table not there yet is no emphasis
    (``read: true``); a file that cannot be read is ``read: false``. Never raises."""
    target = path or help_state.db_path()
    if not target.exists():
        return {"read": True, "emphasis": []}
    try:
        conn = _ro(target)
    except Exception:  # noqa: BLE001
        logger.warning("help_review: cannot open %s", target, exc_info=True)
        return {"read": False, "emphasis": []}
    try:
        if "help_meta" not in _tables(conn):
            return {"read": True, "emphasis": []}
        return {"read": True, "emphasis": _clean_ids(_meta_get(conn, _EMPHASIS_KEY))}
    except Exception:  # noqa: BLE001 - "could not look" is reported, never raised
        logger.warning("help_review: emphasis read failed", exc_info=True)
        return {"read": False, "emphasis": []}
    finally:
        conn.close()


def _changes_in_window(conn: sqlite3.Connection, now: float) -> int:
    cutoff = now - CHANGE_WINDOW_DAYS * 86400.0
    row = conn.execute(
        "SELECT COUNT(*) FROM help_snapshots WHERE ts >= ? AND action IN (?, ?, ?)",
        (cutoff, *ACTIONS),
    ).fetchone()
    return int(row[0])


def _next_emphasis(
    action: str, tip_id: str, current: list[str], position: int | None
) -> tuple[list[str] | None, str | None]:
    """The list after one action, or ``(None, refusal)``."""
    if action == ACTION_PROMOTE:
        if current[:1] == [tip_id]:
            return None, "already first"
        after = [tip_id, *[t for t in current if t != tip_id]]
        return after[:EMPHASIS_MAX], None
    if tip_id not in current:
        return None, "tip is not emphasized"
    rest = [t for t in current if t != tip_id]
    if action == ACTION_DEMOTE:
        return rest, None
    if isinstance(position, bool) or not isinstance(position, int):
        return None, "reorder needs a position"
    if not 0 <= position < len(current):
        return None, f"position must be 0 to {len(current) - 1}"
    after = [*rest[:position], tip_id, *rest[position:]]
    if after == current:
        return None, "no change"
    return after, None


def update(
    action: Any,
    tip_id: Any,
    *,
    tips: list[dict[str, str]],
    review: dict[str, Any] | None,
    position: Any = None,
    path: Path | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Apply one emphasis change, or refuse it. Never raises.

    ``tips`` is the authored set (id, when, text); ``review`` is a FRESH
    :func:`review` result, the evidence a promotion must cite."""
    ts = time.time() if now is None else now
    if action not in ACTIONS:
        return {"changed": False, "refused": f"action must be one of {list(ACTIONS)}"}
    known = {t["id"]: t for t in tips}
    if not isinstance(tip_id, str) or len(tip_id) > _TIP_ID_MAX or tip_id not in known:
        return {"changed": False, "refused": "unknown tip id"}
    reason: dict[str, Any] = {}
    if action == ACTION_PROMOTE:
        if not review or review.get("read") is not True:
            return {"changed": False, "refused": "no readable review to justify a promotion"}
        kind = known[tip_id]["when"]
        if kind not in (review.get("actionable") or []):
            return {"changed": False, "refused": "this tip's kind is not actionable this week"}
        counts = (review.get("kinds") or {}).get(kind) or {}
        reason = {
            "kind": kind,
            "occurrences": int(counts.get("occurrences") or 0),
            "senders": int(counts.get("senders") or 0),
        }
    try:
        conn = help_state._connect(path or help_state.db_path())
    except Exception:  # noqa: BLE001
        logger.warning("help_review: cannot open the help state", exc_info=True)
        return {"changed": False, "refused": "help state unreadable"}
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            used = _changes_in_window(conn, ts)
            if used >= MAX_CHANGES:
                conn.execute("ROLLBACK")
                return {
                    "changed": False,
                    "refused": "weekly change limit reached",
                    "changes_left": 0,
                }
            current = [t for t in _clean_ids(_meta_get(conn, _EMPHASIS_KEY)) if t in known]
            after, refusal = _next_emphasis(action, tip_id, current, position)
            if after is None:
                conn.execute("ROLLBACK")
                return {"changed": False, "refused": refusal, "changes_left": MAX_CHANGES - used}
            cur = conn.execute(
                "INSERT INTO help_snapshots (ts, action, tip_id, prior_json, after_json, reason_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ts, action, tip_id, json.dumps(current), json.dumps(after), json.dumps(reason)),
            )
            _meta_set(conn, _EMPHASIS_KEY, after)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        return {
            "changed": True,
            "emphasis": after,
            "snapshot_id": cur.lastrowid,
            "changes_left": MAX_CHANGES - used - 1,
        }
    except Exception:  # noqa: BLE001
        logger.warning("help_review: update failed", exc_info=True)
        return {"changed": False, "refused": "help state unreadable"}
    finally:
        conn.close()


def undo(*, path: Path | None = None, now: float | None = None) -> dict[str, Any]:
    """Restore the list before the newest change not yet undone. Never raises."""
    ts = time.time() if now is None else now
    target = path or help_state.db_path()
    if not target.exists():
        return {"restored": False, "reason": "nothing to undo"}
    try:
        conn = help_state._connect(target)
    except Exception:  # noqa: BLE001
        logger.warning("help_review: cannot open the help state", exc_info=True)
        return {"restored": False, "reason": "help state unreadable"}
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT id, prior_json FROM help_snapshots WHERE undone_at IS NULL "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return {"restored": False, "reason": "nothing to undo"}
            prior = _clean_ids(json.loads(row[1]))
            _meta_set(conn, _EMPHASIS_KEY, prior)
            conn.execute("UPDATE help_snapshots SET undone_at = ? WHERE id = ?", (ts, row[0]))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        return {"restored": True, "emphasis": prior, "snapshot_id": row[0]}
    except Exception:  # noqa: BLE001
        logger.warning("help_review: undo failed", exc_info=True)
        return {"restored": False, "reason": "help state unreadable"}
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Review
# --------------------------------------------------------------------------- #


def _read_events(path: Path, since: float) -> list[tuple[str, str]]:
    """``(kind, sender_hash)`` since ``since``. ``[]`` when nothing was ever
    recorded; RAISES when the file exists and cannot be read."""
    if not path.exists():
        return []
    conn = _ro(path)
    try:
        if "help_events" not in _tables(conn):
            return []
        return [
            (str(k), str(h))
            for k, h in conn.execute(
                "SELECT kind, sender_hash FROM help_events WHERE ts >= ?", (since,)
            ).fetchall()
        ]
    finally:
        conn.close()


def _bump(kinds: dict[str, dict[str, Any]], kind: str, who: str | None) -> None:
    entry = kinds.setdefault(kind, {"occurrences": 0, "_who": set()})
    entry["occurrences"] += 1
    if who:
        entry["_who"].add(who)


def aggregate(
    requests: Iterable[dict[str, Any]],
    job_rows: Iterable[dict[str, Any]],
    events: Iterable[tuple[str, str]],
    tips: list[dict[str, str]],
) -> dict[str, Any]:
    """Counts by source and by struggle kind. Pure; carries no address, text,
    matter or request id."""
    reqs = list(requests)
    senders_by_ref: dict[str, str] = {}
    per_thread: dict[tuple[str, str], int] = {}
    people: set[str] = set()
    for r in reqs:
        who = request_index.bare_sender(r.get("sender"))
        if who:
            people.add(who)
            ref = r.get("internet_message_id")
            if isinstance(ref, str) and ref:
                senders_by_ref[ref] = who
            thread = r.get("thread_ref")
            if isinstance(thread, str) and thread:
                per_thread[(who, thread)] = per_thread.get((who, thread), 0) + 1

    kinds: dict[str, dict[str, Any]] = {}
    repeat_threads = 0
    for (who, _thread), n in per_thread.items():
        if n >= help_state.REPEAT_ASK_PRIOR + 1:
            repeat_threads += 1
            _bump(kinds, KIND_REPEAT_ASK, who)

    held: dict[str, int] = {}
    failed: dict[str, int] = {}
    classes: dict[str, int] = {}
    for row in job_rows:
        lane = str(row.get("lane") or "unknown")
        bucket = failed if row.get("state") == "failed" else held
        bucket[lane] = bucket.get(lane, 0) + 1
        cls = row.get("hold_class")
        if isinstance(cls, str) and cls:
            classes[cls] = classes.get(cls, 0) + 1
            kind = HOLD_CLASS_KINDS.get(cls)
            if kind:
                ref = row.get("request_ref")
                _bump(kinds, kind, senders_by_ref.get(ref) if isinstance(ref, str) else None)

    noted: dict[str, int] = {}
    for kind, who in events:
        if kind in help_state.NOTE_STRUGGLE_KINDS:
            noted[kind] = noted.get(kind, 0) + 1
            _bump(kinds, kind, "h:" + who)
        elif kind in help_state.KINDS:
            noted[kind] = noted.get(kind, 0) + 1

    tips_by_kind: dict[str, list[str]] = {}
    for tip in tips:
        tips_by_kind.setdefault(tip["when"], []).append(tip["id"])
    shaped: dict[str, dict[str, Any]] = {}
    for kind, entry in sorted(kinds.items()):
        senders = len(entry["_who"])
        shaped[kind] = {
            "occurrences": entry["occurrences"],
            "senders": senders,
            "tips": tips_by_kind.get(kind, []),
            "actionable": entry["occurrences"] >= ACTIONABLE_MIN_OCCURRENCES
            and senders >= ACTIONABLE_MIN_SENDERS,
        }
    actionable = [k for k, v in shaped.items() if v["actionable"]]
    return {
        "read": True,
        "window_days": REVIEW_WINDOW_DAYS,
        "requests": {
            "total": len(reqs),
            "senders": len(people),
            "repeat_ask_threads": repeat_threads,
        },
        "jobs": {"held": held, "failed": failed, "hold_classes": classes},
        "noted": noted,
        "kinds": shaped,
        "actionable": actionable,
        "promotable": [k for k in actionable if shaped[k]["tips"]],
        "proposals": [k for k in actionable if not shaped[k]["tips"]],
        "thresholds": {
            "occurrences": ACTIONABLE_MIN_OCCURRENCES,
            "senders": ACTIONABLE_MIN_SENDERS,
        },
    }


def _store_review(path: Path, result: dict[str, Any], now: float) -> bool:
    """The last review, counts only, for the weekly note. Never raises."""
    stored = {
        k: result[k] for k in ("requests", "jobs", "noted", "kinds", "actionable", "proposals")
    }
    stored["at"] = now
    try:
        conn = help_state._connect(path)
    except Exception:  # noqa: BLE001
        return False
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            help_state._salt(conn, create=True)
            _meta_set(conn, _LAST_REVIEW_KEY, stored)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        return True
    except Exception:  # noqa: BLE001
        logger.warning("help_review: last review not stored", exc_info=True)
        return False
    finally:
        conn.close()


def review(
    *,
    tips: list[dict[str, str]] | None,
    job_reader: Callable[[int], dict[str, Any]],
    path: Path | None = None,
    requests_path: str | None = None,
    now: float | None = None,
    store: bool = True,
) -> dict[str, Any]:
    """The seat's struggle signals for the last week, or ``read: false`` with
    the sources that could not be read. Never raises."""
    ts = time.time() if now is None else now
    since = ts - REVIEW_WINDOW_DAYS * 86400.0
    target = path or help_state.db_path()
    unreadable: list[str] = []
    requests: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    events: list[tuple[str, str]] = []
    try:
        requests = request_index.read_requests(since, path=requests_path)
    except Exception:  # noqa: BLE001 - "could not look" is not "none"
        logger.warning("help_review: request index unreadable", exc_info=True)
        unreadable.append(SOURCE_REQUESTS)
    try:
        answer = job_reader(REVIEW_WINDOW_DAYS)
        found = answer.get("rows") if isinstance(answer, dict) else None
        if not isinstance(found, list):
            raise ValueError("job signals carried no rows list")
        rows = [r for r in found if isinstance(r, dict)]
    except Exception:  # noqa: BLE001
        logger.warning("help_review: job signals unreadable", exc_info=True)
        unreadable.append(SOURCE_JOBS)
    try:
        events = _read_events(target, since)
    except Exception:  # noqa: BLE001
        logger.warning("help_review: help events unreadable", exc_info=True)
        unreadable.append(SOURCE_NOTES)
    if tips is None:
        unreadable.append(SOURCE_TIPS)
    emphasis = read_emphasis(path=target)
    if unreadable:
        return {
            "read": False,
            "unreadable": unreadable,
            "window_days": REVIEW_WINDOW_DAYS,
            "emphasis": emphasis["emphasis"],
        }
    assert tips is not None
    result = aggregate(requests, rows, events, tips)
    result["emphasis"] = [t for t in emphasis["emphasis"] if t in {x["id"] for x in tips}]
    result["changes_left"] = changes_left(path=target, now=ts)
    if store:
        result["stored"] = _store_review(target, result, ts)
    return result


def changes_left(*, path: Path | None = None, now: float | None = None) -> int:
    """Changes still allowed in the rolling window; 0 when it cannot be read."""
    ts = time.time() if now is None else now
    target = path or help_state.db_path()
    if not target.exists():
        return MAX_CHANGES
    try:
        conn = _ro(target)
    except Exception:  # noqa: BLE001
        return 0
    try:
        if "help_snapshots" not in _tables(conn):
            return MAX_CHANGES
        return max(0, MAX_CHANGES - _changes_in_window(conn, ts))
    except Exception:  # noqa: BLE001
        return 0
    finally:
        conn.close()


__all__ = [
    "ACTIONABLE_MIN_OCCURRENCES",
    "ACTIONABLE_MIN_SENDERS",
    "ACTIONS",
    "CHANGE_WINDOW_DAYS",
    "EMPHASIS_MAX",
    "HOLD_CLASS_KINDS",
    "KIND_HELD_IDENTITY",
    "KIND_NOTHING_IN_SCOPE",
    "KIND_REPEAT_ASK",
    "MAX_CHANGES",
    "REVIEW_WINDOW_DAYS",
    "aggregate",
    "changes_left",
    "read_emphasis",
    "review",
    "undo",
    "update",
]
