"""The note lane: the daemon's wake-and-watch half for SMD notes (``note_by_smd.py``).

A note has no runner and no queue: SMD records it through the broker's root-only
``note_record``, and ``note_by_smd`` writes its job dir with a PENDING wake. This
lane is the ONLY sender of that wake (it holds the wake secret; the command
holds none), so there is exactly one DELIVER turn per note and one writer of
``daemon.json`` after the command's single atomic write:

* job dirs ``<run_dir>/note-jobs/<note id>`` (root-only, made by the
  entrypoint): an ``envelope.json`` naming the requester and a terminal
  ``daemon.json`` carrying the pending wake;
* each tick: wipe expired dirs, dispatch every pending wake (the daemon's own
  at-most-once dispatch: consumed on a 2xx or a timeout after send, retried
  otherwise, ``wake_lost`` paged after the last attempt), which arms the reply
  watch; then settle every open reply watch from ``delivery_reply_status``
  (kind ``note_job``), paging SMD when the reply is not proven in time;
* the wake's handoff id is ``note-<id>``, the daemon's own ``<LANE>-<id>``.

It claims nothing and runs no child, so it costs the Machine no memory slot.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .broker_client import BrokerClient
from .daemon import Daemon
from .skill_load import load_line

logger = logging.getLogger("medchron.note")

SKILL = "smd-note"
REPLY_KIND = "note_job"


def wake_task(note_id: str, *, new_message: bool = False) -> str:
    """Code-authored only: the id and the kind. The words are the ledger's. A
    new-message note (no request email) carries ``Trigger: scheduled.``, the
    overlay's only mark that a wake may bind one new email
    (``shared/turn_sources.WAKE_TRIGGER``; the negotiation lane's line)."""
    task = f"Run the {SKILL} skill's DELIVER mode for note job {note_id}.\n{load_line(SKILL)}\nKind: note."
    return task + "\nTrigger: scheduled." if new_message else task


class NoteBroker:
    """The client, plus the one call the base daemon makes on a lost wake (a
    same-state ledger note): a note's ledger has no record verb, and the loss is
    paged by ``proof_watch.wake_lost`` regardless."""

    def __init__(self, client: BrokerClient) -> None:
        self.client = client

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.client._request(payload)

    def record(self, _job_id: str, _state: str, _fields: dict[str, Any]) -> dict[str, Any]:
        return {}


@dataclass
class NoteLane(Daemon):
    LANE = "note"
    HEARTBEAT = "note-heartbeat"

    @property
    def queue(self) -> Path:
        # Never written: a note is recorded by root, not queued.
        return self.run_dir / "note-queue"

    @property
    def jobs(self) -> Path:
        return self.run_dir / "note-jobs"

    def tick(self) -> str | None:
        self.wipe_expired()
        self.dispatch_wakes()
        self.heartbeat(running=None)
        return None


def build_lane(d: Daemon) -> NoteLane:
    client = d.broker.client if hasattr(d.broker, "client") else d.broker
    return NoteLane(
        run_dir=d.run_dir,
        broker=NoteBroker(client),
        runner_cmd=[],
        customer_slug=d.customer_slug,
        sticky_db=d.sticky_db,
        cgroup_root=d.cgroup_root,
        wipe_hours=d.wipe_hours,
        held_wipe_hours=d.held_wipe_hours,
        child_uid=d.child_uid,
        gate_url=d.gate_url,
        wake_secret=d.wake_secret,
    )


def start_lane(d: Daemon, *, stop: Callable[[], bool], poll_seconds: float) -> threading.Thread | None:
    lane = build_lane(d)
    t = threading.Thread(target=lane.run_forever, args=(stop, poll_seconds), name="note-lane", daemon=True)
    t.start()
    logger.info("note lane up: jobs=%s", lane.jobs)
    return t


__all__ = ["REPLY_KIND", "SKILL", "NoteBroker", "NoteLane", "build_lane", "start_lane", "wake_task"]
