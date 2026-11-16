"""The mailbox keeper's state on the seat volume: one directory, the broker's.

``$SMD_MAILBOX_KEEPER_DIR`` (``/run/smd-mailbox-keeper`` on a seat, a bind
mount of ``/opt/data/mailbox-keeper``, ``operator/templates/entrypoint-mailbox-
keeper.sh``). Every file is written by the broker uid only, 0600. A layer of the
gone-means-gone inventory (CLAUDE.md):

``listed.json``      what has been listed already (alert thread ids, waiting
                     message ids, SMD Ops message ids), the SMD Ops lines labelled
                     out of the inbox and not yet listed (their subject text),
                     the date of the last list, and whether the one-time label
                     backfill ran.
``classified.json``  per thread id: the newest message id the model read, the
                     kind code kept, the date it was first listed. No content.
``expenses.jsonl``   one row per filed receipt, keyed by thread id: date,
                     vendor (the From display name), the amount as written,
                     cents and currency.
``spend.json``       the keeper's model spend today (cents), the broker-side
                     daily fence beside the seat's cost breaker.
``trashed.jsonl``    one line per trashed thread (time, thread id, rule).
``.lock``            held while a run that writes is in flight.
"""

from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

STATE_DIR_ENV = "SMD_MAILBOX_KEEPER_DIR"
DEFAULT_STATE_DIR = "/opt/data/mailbox-keeper"
#: SMD Ops message ids kept as listed; older ones fall off (their threads left
#: the inbox long ago, and a thread back in the inbox brings a new message id).
OPS_LISTED_KEEP = 5_000


class Busy(RuntimeError):
    """Another run holds the lock."""


def state_dir() -> Path:
    return Path(os.environ.get(STATE_DIR_ENV) or DEFAULT_STATE_DIR)


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def _write(directory: Path, name: str, doc: Any) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / (name + ".tmp")
    tmp.write_text(json.dumps(doc, sort_keys=True, indent=1), encoding="utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, directory / name)


def load_state(directory: Path) -> dict[str, Any]:
    data = _read(directory / "listed.json", {})
    if not isinstance(data, dict):
        raise ValueError("listed.json is not an object")
    pending = [p for p in data.get("ops_pending") or [] if isinstance(p, dict)]
    return {
        "alerts": [str(x) for x in data.get("alerts") or []],
        "waiting": [str(x) for x in data.get("waiting") or []],
        "digest_sent_on": str(data.get("digest_sent_on") or ""),
        "ops_listed": [str(x) for x in data.get("ops_listed") or []],
        "ops_pending": pending,
        "backfill_done": data.get("backfill_done") is True,
    }


def save_state(directory: Path, state: dict[str, Any]) -> None:
    doc = dict(state)
    doc["ops_listed"] = list(doc.get("ops_listed") or [])[-OPS_LISTED_KEEP:]
    _write(directory, "listed.json", doc)


def load_classified(directory: Path) -> dict[str, dict[str, str]]:
    data = _read(directory / "classified.json", {})
    if not isinstance(data, dict):
        raise ValueError("classified.json is not an object")
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def save_classified(directory: Path, rows: dict[str, dict[str, str]]) -> None:
    _write(directory, "classified.json", rows)


def spent_today(directory: Path, today: str) -> float:
    data = _read(directory / "spend.json", {})
    if not isinstance(data, dict) or data.get("date") != today:
        return 0.0
    try:
        return float(data.get("cents") or 0)
    except (TypeError, ValueError):
        return 0.0


def add_spend(directory: Path, today: str, cents: float) -> None:
    if cents <= 0:
        return
    _write(directory, "spend.json", {"date": today, "cents": round(spent_today(directory, today) + cents, 4)})


def _append(directory: Path, name: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    path.chmod(0o600)


def expense_thread_ids(directory: Path) -> set[str]:
    try:
        lines = (directory / "expenses.jsonl").read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return set()
    found: set[str] = set()
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("thread_id"):
            found.add(str(row["thread_id"]))
    return found


def append_expenses(directory: Path, rows: list[dict[str, Any]]) -> int:
    """Append the rows whose thread id is not already filed. Returns how many."""
    known = expense_thread_ids(directory)
    fresh = [r for r in rows if str(r.get("thread_id")) not in known]
    _append(directory, "expenses.jsonl", fresh)
    return len(fresh)


def log_trashed(directory: Path, thread_id: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _append(directory, "trashed.jsonl", [{"ts": stamp, "thread_id": thread_id, "rule": "settled"}])


class RunLock:
    """An exclusive, non-blocking lock on the state directory for one run."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._handle: Any = None

    def __enter__(self) -> "RunLock":
        self._directory.mkdir(parents=True, exist_ok=True)
        self._handle = (self._directory / ".lock").open("a")
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._handle.close()
            raise Busy("another mailbox keeper run is in flight") from exc
        return self

    def __exit__(self, *_exc: object) -> None:
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()


def iter_lines(path: Path) -> Iterator[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            yield row


__all__ = [
    "DEFAULT_STATE_DIR",
    "STATE_DIR_ENV",
    "Busy",
    "RunLock",
    "add_spend",
    "append_expenses",
    "expense_thread_ids",
    "iter_lines",
    "load_classified",
    "load_state",
    "log_trashed",
    "save_classified",
    "save_state",
    "spent_today",
    "state_dir",
]
