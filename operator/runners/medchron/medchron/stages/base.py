"""What an in-process stage receives, and the two ways it can end early.

A stage is `def run(sr: StageRun) -> int`. Its exit code goes through the
same `exit_map` a subprocess stage's would, so porting a stage in-process
changes nothing about how the driver reads it. Writes are the same artifacts
the frozen scripts wrote, in the same shapes, because every later stage
(ported or not) reads them.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .. import config as config_mod, job as job_mod
from ..seat import Seat
from ..ledger import Ledger
from ..llm import Doorway


class StageRefusal(RuntimeError):
    """The stage refuses to continue for a reason a person must act on. The
    driver maps it to the `refused` outcome with this message."""


@dataclass
class StageRun:
    job: job_mod.Job
    cfg: config_mod.FirmConfig
    unit: job_mod.Unit
    slug_dir: Path
    decided: dict[str, Any]
    log: Callable[[str], None]
    seat_factory: Callable[[], Seat]
    #: Builds (or returns) the run's one SDK client for paid stages; None means
    #: the doorway builds the real one. Tests inject a scripted client here, so
    #: no test touches the network.
    client_factory: Callable[[], Any] | None = None
    #: MM-DD-YY for the deliverable names; the driver stamps the run once.
    date_stamp: str = ""
    #: Called with the stage name before every paid model call in live mode.
    #: The driver binds it to the cost limits, so a limit reached inside a long
    #: stage stops the run at the next call rather than at the next stage.
    before_request: Callable[[str], None] | None = None
    #: The batch-mode twin: (stage, item count, request characters), called
    #: before a batch is submitted so the limits see the batch's projected cost.
    #: A batch has no per-call seam to stop at, so this is where it binds.
    before_batch: Callable[[str, int, int], None] | None = None
    _seat: Seat | None = field(default=None, repr=False)
    _doorway: Doorway | None = field(default=None, repr=False)

    @property
    def seat(self) -> Seat:
        if self._seat is None:
            self._seat = self.seat_factory()
        return self._seat

    @property
    def doorway(self) -> Doorway:
        """The paid doorway, writing this unit's ledger. Built once per stage
        run from the firm's levers."""
        if self._doorway is None:
            ledger = Ledger(self.slug_dir / "runs" / self.unit.unit / "usage-ledger.jsonl")
            client = self.client_factory() if self.client_factory is not None else None
            self._doorway = Doorway.from_config(self.cfg, ledger, client=client, log=self.log,
                                               before_request=self.before_request,
                                               before_batch=self.before_batch)
        return self._doorway

    @property
    def slug(self) -> str:
        return self.job.slug

    # ---- the matter's manifest and folder tree, as the stages read them -----
    def manifest(self) -> list[dict[str, Any]]:
        man = read_json(self.slug_dir / "manifest.json", {})
        return man["documents"] if isinstance(man, dict) else man

    def folder_paths(self) -> dict[str, str]:
        return {f["id"]: f["path"] for f in read_json(self.slug_dir / "folders.json", [])}


def read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
        fh.flush()
