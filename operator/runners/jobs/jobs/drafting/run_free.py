"""``DraftingRun``'s free stages, before anything is paid: the matter record,
the filing destination, the preflight count, the estimate against the caps,
and a response job's scope. Split from ``run.py`` at the module-size ceiling;
``DraftingRun`` inherits it, so every caller is unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from .. import limits as limits_mod
from ..demand import facts as facts_mod, preflight
from . import evidence_cost, response_cost
from .outcome import DraftingFailed, DraftingHold

#: Scoped and priced over what its sets read (response_scope.py, response_cost.py).
RESPONSE = "discovery_response"


class FreeStages:
    """Mixin of ``DraftingRun``: what it supplies is below."""

    if TYPE_CHECKING:
        data: Path
        job: Any
        firm: Any
        cls: str
        dview: Any
        pricing: Any
        budget: Any
        limits: Any
        evidence: Any
        log: Callable[[str], None]
        _units: Any

        @property
        def seat(self) -> Any: ...
        def _state(self) -> dict[str, Any]: ...
        def _put(self, key: str, value: Any) -> None: ...
        def _json(self, name: str) -> Any: ...
        def _write(self, name: str, obj: Any) -> None: ...

    # ---- free stages -----------------------------------------------------------------
    def _facts(self) -> None:
        self._write("facts.json", facts_mod.read(self.seat, self.job.matter_id))
        self._check_facts()

    def _check_facts(self) -> None:
        errs = facts_mod.blocking_errors(self._json("facts.json"))
        if errs:
            raise DraftingFailed(
                "record_unreadable: the matter record could not be read in full: " + "; ".join(errs)[:400]
            )

    def _destination(self) -> None:
        job = self.job
        if facts_mod.matter_number(self.seat, job.matter_id) != job.matter_number:
            raise DraftingHold(
                f"destination_mismatch: the matter id does not carry matter number {job.matter_number}; nothing was read"
            )
        if job.file_to_id == job.matter_id:
            return
        if job.file_to_number not in self.firm.rehearsal_matters:
            raise DraftingHold(
                f"destination_unauthored: the job files to matter {job.file_to_number}, which is neither the matter it reads nor an "
                "authored rehearsal matter; refused"
            )
        if facts_mod.matter_number(self.seat, job.file_to_id) != job.file_to_number:
            raise DraftingHold(
                f"destination_mismatch: the filing destination's id does not carry matter number {job.file_to_number}"
            )

    def _preflight(self) -> None:
        # The ledger path is priced stage by stage from the file's real pages
        # (evidence_cost.py), never by the digest path's flat tail.
        tail = (
            (lambda rows: evidence_cost.project(rows, self.firm.data, self.pricing, self.cls))
            if self.evidence.ledger
            else None
        )
        report = preflight.run(self.data, self.dview, self._json("facts.json"), self.log, tail=tail)
        if not report["documents"]:
            raise DraftingHold(
                "no_readable_documents: the matter file has no readable documents to draft from; nothing was spent"
            )

    def _estimate(self) -> None:
        if self.job.allowance_remaining is not None and self.job.allowance_remaining <= 0 and not self.budget.refresh():
            raise limits_mod.LimitHold(
                "drafting_allowance_per_cycle",
                "drafting_allowance_per_cycle: this cycle's drafting jobs are used; nothing was spent",
            )
        # PR3: a response job is priced over its scoped corpus (response_cost.py).
        est = self._json(response_cost.FILE) if self.cls == RESPONSE else self._json("preflight.json")["estimate"]
        spent = self.budget.refresh()
        try:
            self.limits.check_before_paid(
                projected_usd=max(0.0, float(est["usd"]) - spent), spent_usd=spent, stage="the first paid stage"
            )
        except limits_mod.LimitHold as hold:
            raise limits_mod.LimitHold(
                hold.setting,
                f"{hold.reason} (estimate before anything was paid: {est['pages']:,} pages, "
                f"{est['characters']:,} characters, {est['transcription_pages']:,} pages to transcribe, "
                f"{est['usd']:.2f} USD)",
            ) from None

    def _scope(self) -> None:
        """PR3 hook: a response job reads only what its sets need; the estimate
        over that, its finish time, and the units cheapest first (response_cost.py)."""
        plan = self._state()["units"]
        est = response_cost.scope_and_estimate(
            self.data, self.firm.data, self.pricing, plan, self._json("facts.json"),
            self.firm.model("transcription"), self.log,
        )  # fmt: skip
        self._put("units", response_cost.cheapest_first(plan, est))
        self._units = None
        self._put("expected_finish", est["expected_finish"])
