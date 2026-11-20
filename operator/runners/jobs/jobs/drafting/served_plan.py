"""The discovery unit plan (``DraftingRun`` mixes it in).

Split from ``served.py`` at the module-size ceiling. A propounded set plans
exactly where it always did, after the transcription and the caption stage
(``finish.units``): the sets the request names, the firm's default sets when
it authored them and the request names none or asks for "the usual", one unit
per (party, set) when it serves several (``parties.py``). A response job plans
from SMD's served sets (``served.py``), and only that check runs before
anything is paid (``_plan_before_spend``): it reads the hand file alone, never
a caption.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from . import parties, sets
from .outcome import DraftingFailed
from .served import PLAN_STOPS, PLANNED_CLASSES, frozen, has_plan, outcomes, plan_units, review_map, stop_note


class DiscoveryPlan:
    """Mixin: ``DraftingRun`` supplies cls, data, job, firm and the state
    helpers; ``finish.Finish`` supplies units, _names and _served_on."""

    if TYPE_CHECKING:  # what DraftingRun (run.py) and Finish (finish.py) supply
        cls: str
        data: Path
        job: Any
        firm: Any

        def _state(self) -> dict[str, Any]: ...
        def _put(self, key: str, value: Any) -> None: ...
        def _stage(self, name: str, fn: Callable[[], Any]) -> None: ...
        def units(self) -> list[Any]: ...
        def _names(self, u: Any = None) -> dict[str, str]: ...
        def _served_on(self) -> list[str]: ...

    def _plan_before_spend(self) -> None:
        """A response job's served sets, after the estimate and before the
        transcription. Its stops (``served_*``) end the job with nothing paid
        and leave SMD ``smd-needs.json`` with the job's frozen wording rules.
        A plan already in state.json is reused. Nothing else plans early: a
        propounded set plans after its caption stage, as it always has."""
        if self.cls not in PLANNED_CLASSES:
            return
        try:
            self._stage("plan", self.units)
        except DraftingFailed as exc:
            code = str(exc).split(":", 1)[0]
            if code in PLAN_STOPS:
                stop_note(self.job.job_dir, self.data, str(exc), served_shape=True)
            raise

    def _freeze_plan(self, plan: list[dict[str, Any]]) -> None:
        self._put("units", plan)

    def _plan(self) -> list[dict[str, Any]]:
        """A fresh unit plan (``finish.units`` freezes it). With no default
        sets authored, a propounded set plans exactly as it always did."""
        if self.cls == "discovery_response":
            return plan_units(frozen(self.data, self.job.job_dir))
        if self.cls != "discovery_set":
            return parties.plan_units([], [])
        req = self.job.request_text
        found, keyed = sets.variants(req), False
        defaults = sets.default_variants(self.firm.data) or []
        if defaults and (sets.wants_defaults(req) or not (found or sets.names_any_set(req))):
            found = [*found, *(u for u in defaults if u.key not in {f.key for f in found})]
            keyed = True
        return parties.plan_units(found, self._served_on() if found else [], keyed=keyed)

    def served_outcomes(self) -> list[dict[str, Any]] | None:
        """Every served set as filed or being prepared by us; None without a served plan."""
        plan = self._state().get("units") or []
        if not has_plan(plan):
            return None
        names = {u.key: self._names(u)["draft"] for u in self.units()}
        return outcomes(frozen(self.data, self.job.job_dir), plan, names)

    def served_review(self) -> dict[str, dict[str, Any]] | None:
        """What the review's response checks count against; None without a served plan."""
        plan = self._state().get("units") or []
        if not has_plan(plan):
            return None
        return review_map(frozen(self.data, self.job.job_dir), plan)
