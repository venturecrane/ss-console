"""The run-level state file: `<data_root>/<slug>/runs/<unit>/state.json`.

Nothing in the pipeline records which stage completed; every stage infers its
own state from artifacts and the ordering invariants are enforced only as
refusals. A supervisor that restarts a killed run therefore had to replay every
command and trust each to no-op. This file is the missing record: per stage,
the status, attempt count, exit code, the sha of its inputs, and the dollars
and pages at completion, plus the pipeline sha the run was made with.

Writes are atomic (temp file + os.replace) so a kill between two stages never
leaves a half-written state.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STATUSES = ("pending", "running", "done", "refused", "held", "failed", "skipped")
TERMINAL_OUTCOMES = ("delivered", "held", "refused", "failed")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class StageRecord:
    status: str = "pending"
    attempts: int = 0
    started: str | None = None
    finished: str | None = None
    exit_code: int | None = None
    input_sha: str | None = None
    dollars_after: float | None = None
    pages_after: int | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StageRecord":
        rec = cls()
        for k, v in d.items():
            if k in rec.__dict__:
                setattr(rec, k, v)
        return rec


@dataclass
class RunState:
    path: Path
    slug: str
    unit: str
    pipeline_sha: str | None = None
    runner_version: str | None = None
    created: str = field(default_factory=_now)
    updated: str = field(default_factory=_now)
    outcome: str | None = None  # one of TERMINAL_OUTCOMES when the run ends
    outcome_reason: str | None = None
    stages: dict[str, StageRecord] = field(default_factory=dict)

    # ---- persistence ------------------------------------------------------
    @classmethod
    def load_or_new(cls, path: Path, *, slug: str, unit: str) -> "RunState":
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            st = cls(path=path, slug=data.get("slug", slug), unit=data.get("unit", unit))
            st.pipeline_sha = data.get("pipeline_sha")
            st.runner_version = data.get("runner_version")
            st.created = data.get("created", st.created)
            st.updated = data.get("updated", st.updated)
            st.outcome = data.get("outcome")
            st.outcome_reason = data.get("outcome_reason")
            st.stages = {k: StageRecord.from_dict(v) for k, v in (data.get("stages") or {}).items()}
            return st
        return cls(path=path, slug=slug, unit=unit)

    def save(self) -> None:
        self.updated = _now()
        payload = {
            "slug": self.slug,
            "unit": self.unit,
            "pipeline_sha": self.pipeline_sha,
            "runner_version": self.runner_version,
            "created": self.created,
            "updated": self.updated,
            "outcome": self.outcome,
            "outcome_reason": self.outcome_reason,
            "stages": {k: v.to_dict() for k, v in self.stages.items()},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    # ---- transitions ------------------------------------------------------
    def stage(self, name: str) -> StageRecord:
        return self.stages.setdefault(name, StageRecord())

    def start(self, name: str, *, input_sha: str | None) -> None:
        rec = self.stage(name)
        rec.status = "running"
        rec.attempts += 1
        rec.started = _now()
        rec.finished = None
        rec.exit_code = None
        rec.input_sha = input_sha
        self.save()

    def finish(
        self,
        name: str,
        *,
        status: str,
        exit_code: int | None,
        dollars: float | None,
        pages: int | None,
        note: str | None = None,
    ) -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown stage status {status!r}")
        rec = self.stage(name)
        rec.status = status
        rec.finished = _now()
        rec.exit_code = exit_code
        rec.dollars_after = dollars
        rec.pages_after = pages
        rec.note = note
        self.save()

    def invalidate(self, names: list[str]) -> None:
        """Reopen stages whose inputs changed (build_doc rerun reopens the audit)."""
        for n in names:
            rec = self.stages.get(n)
            if rec and rec.status == "done":
                rec.status = "pending"
                rec.note = "invalidated: an upstream stage re-ran"
        self.save()

    def end(self, outcome: str, reason: str | None = None) -> None:
        if outcome not in TERMINAL_OUTCOMES:
            raise ValueError(f"unknown outcome {outcome!r}")
        self.outcome = outcome
        self.outcome_reason = reason
        self.save()

    def is_done(self, name: str) -> bool:
        rec = self.stages.get(name)
        return bool(rec and rec.status == "done")

    def dollars(self) -> float:
        vals = [r.dollars_after for r in self.stages.values() if r.dollars_after is not None]
        return max(vals) if vals else 0.0


def state_path(data_root: Path, slug: str, unit: str) -> Path:
    return Path(data_root) / slug / "runs" / unit / "state.json"
