"""The demand job's verdict and the exceptions that decide it.

Split from ``run.py`` at the module-size ceiling (2026-10-13, when the
verdict gained its ``proof`` block); ``run.py`` re-exports every name, so
``from medchron.demand.run import DemandHold`` keeps working.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class GateQuotesLeft(RuntimeError):
    """Quote findings left after the deterministic repair: recorded failed, so a
    resume re-runs only repair and gate (the paid stages are on disk)."""


class DemandHold(RuntimeError):
    """Something about the FILE or the REQUEST that a person at the firm must
    decide (premise, variant, an unmapped attorney, a letter that contradicts
    the matter record, privileged text the wall caught): ``held``, final, and
    the firm is told. Nothing is filed."""


class DemandFailed(RuntimeError):
    """Something about OUR machinery (a read that did not complete, a draft
    the audit loop could not clean, a gate or format refusal of our own
    output): ``failed``, resumable, no client message, SMD
    alerted (ss#3085). A held job cannot resume, so a machinery fault must
    never hold."""


@dataclass
class Verdict:
    outcome: str
    stage: str | None = None
    reason: str | None = None
    dollars: float = 0.0
    documents: int = 0
    pages: int = 0
    folder_id: str | None = None
    files: list[dict[str, Any]] = field(default_factory=list)
    coverage_report: bool = False
    notes: list[str] = field(default_factory=list)
    #: The daemon's proof block (``proof.gate``), on a delivered verdict only.
    proof: dict[str, Any] | None = None

    def to_list(self) -> list[dict[str, Any]]:
        d = dict(self.__dict__)
        if d.get("proof") is None:
            d.pop("proof", None)
        d["unit"] = "demand"
        d["kind"] = "demand"
        return [d]


__all__ = ["DemandFailed", "DemandHold", "GateQuotesLeft", "Verdict"]
