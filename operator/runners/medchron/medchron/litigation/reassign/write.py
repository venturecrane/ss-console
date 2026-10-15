"""The reassignment's writes: each journaled before it is sent, and each read
back until it shows, in a bounded loop.

THE JOURNAL (``data/writes.jsonl``). The runner runs as the ``medchron`` uid
under the daemon, which the connector exempts from its per-write audit row
(``smokeball_connector/write_record.py``) because the job's own rows record
the job. A reassignment's effect is a hundred scattered writes, so "the job
row names the job" is not enough: every write is appended here (intent before
the request, result after, flushed to disk), and the verdict carries the list
(method, path, status) so the broker writes it onto the job's audit row
(``litigation_job_record`` ``writes``). Nothing in it names a client: a method,
``/matters/<id>`` or ``/tasks/<id>``, a status, and a digest of the body.

CONVERGENCE. Smokeball applies a write some seconds after it answers, and one
task PUT on 2026-10-15 showed no change on two reads and then took. So one read
is never a verdict: ``converge`` writes every pending item, polls them all a
few times, re-writes what still differs, and returns only what never showed.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

JOURNAL = "writes.jsonl"
#: Bounded: ATTEMPTS writes per item, each followed by up to POLLS reads.
ATTEMPTS = 3
POLLS = 6
POLL_SECONDS = 10.0
WRITE_PAUSE_SECONDS = 0.25


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Journal:
    def __init__(self, data: Path) -> None:
        self.path = data / JOURNAL

    def append(self, row: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": _now(), **row}, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def rows(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def results(self) -> list[dict[str, Any]]:
        """The verdict's write list: one entry per sent write, in order."""
        return [
            {"method": r["method"], "path": r["path"], "status": r.get("status"), "ok": r.get("ok") is True}
            for r in self.rows()
            if r.get("phase") == "result"
        ]


class Writer:
    def __init__(self, client: Any, journal: Journal, pause: float = WRITE_PAUSE_SECONDS) -> None:
        self.client, self.journal, self.pause = client, journal, pause

    def send(self, method: str, path: str, body: dict[str, Any]) -> bool:
        """One write, journaled both sides. True when the vendor accepted it
        (a 2xx); whether it TOOK is the read-back's question, not this one's."""
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
        self.journal.append({"phase": "intent", "method": method, "path": path, "body_sha256": digest})
        status: int | None = None
        try:
            self.client.request(method, path, json=body)
            status, ok = 200, True
        except Exception as exc:  # noqa: BLE001 - a refused write is journaled and retried, never raised mid-sweep
            status, ok = getattr(exc, "status", None), False
        self.journal.append({"phase": "result", "method": method, "path": path, "status": status, "ok": ok})
        if self.pause:
            time.sleep(self.pause)
        return ok


def converge(
    keys: list[str],
    write: Callable[[str], None],
    check: Callable[[str], bool],
    *,
    attempts: int = ATTEMPTS,
    polls: int = POLLS,
    pause: float = POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[str], int]:
    """(the keys that never showed the change, reads made). ``check`` reads
    the far end; a read that raises counts as not yet."""
    pending, reads = list(dict.fromkeys(keys)), 0

    def shows(k: str) -> bool:
        nonlocal reads
        reads += 1
        try:
            return bool(check(k))
        except Exception:  # noqa: BLE001 - a failed read is "not yet", never "done"
            return False

    pending = [k for k in pending if not shows(k)]
    for _ in range(attempts):
        if not pending:
            break
        for k in pending:
            write(k)
        for _ in range(polls):
            sleep(pause)
            pending = [k for k in pending if not shows(k)]
            if not pending:
                break
    return pending, reads


__all__ = ["ATTEMPTS", "JOURNAL", "POLLS", "POLL_SECONDS", "Journal", "Writer", "converge"]
