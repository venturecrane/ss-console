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

from . import amended, challenge, parties, served_find, sets
from .forms import lane_hook, read_stage
from .outcome import DraftingFailed
from .served import PLAN_STOPS, PLANNED_CLASSES, clients_of, frozen, has_plan, outcomes, plan_units, review_map
from .served import stop_note
from .served_find import other_side


class DiscoveryPlan:
    """Mixin: ``DraftingRun`` supplies cls, data, job, firm and the state
    helpers; ``finish.Finish`` supplies units, _names and _served_on."""

    if TYPE_CHECKING:  # what DraftingRun (run.py) and Finish (finish.py) supply
        cls: str
        data: Path
        job: Any
        firm: Any
        doorway: Any
        budget: Any
        pricing: Any
        _title: str
        log: Callable[[str], None]

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
            # The served sets found in the matter file first; SMD's hand
            # served.json, when there, is the override (served_find.py).
            self._stage("find", self._find_served)
            self._stage("plan", self.units)
            lane_hook.before_spend(self)  # a served form set's policy, rules and boxes (forms/lane_hook.py)
        except DraftingFailed as exc:
            code = str(exc).split(":", 1)[0]
            if code in PLAN_STOPS or code in lane_hook.STOPS or code == "challenge_unread":
                stop_note(self.job.job_dir, self.data, str(exc), served_shape=code not in lane_hook.POLICY_STOPS)
            raise

    def _find_served(self) -> None:
        """The ``find`` stage: one capped call per served candidate through the
        job's own doorway and spend ledger (stage ``served-find``)."""
        model = self.firm.model("transcription")
        rate = self.pricing.rate_for(model)

        def ask(text: str) -> str:
            messages: list[dict[str, Any]] = [{"role": "user", "content": text}]
            return self.doorway.call(
                served_find.STAGE, model=model, system=served_find.SYSTEM, messages=messages,
                max_tokens=served_find.MAX_TOKENS, effort="",
            ).text  # fmt: skip

        def spent() -> float:
            self.budget.refresh()
            return float(self.budget.by_stage().get(served_find.STAGE, 0.0))

        def next_cost(chars: int) -> float:
            # About 3 characters a token for the input (an over-estimate), every output token asked for.
            cents = chars / 3 * rate.input_per_million_cents + served_find.MAX_TOKENS * rate.output_per_million_cents
            return cents / 1e8

        served_find.find(
            self.data,
            self.job.job_dir,
            self.job.request_text,
            served_find.Reader(ask, spent, next_cost),
            clients=clients_of(self.data),
            other_side=other_side(self.data),
            title=self._title,
            form_by_operator=lane_hook.by_operator(self.firm.data),
        )
        # Each served form set the Operator drafts: its boxes read before the plan
        # (forms/read_stage.py); a firm with no form policy reads and spends nothing.
        read_stage.read_found(
            self.data, self.job.job_dir, self.doorway, model,
            by_operator=lane_hook.by_operator(self.firm.data), log=self.log,
        )  # fmt: skip
        if amended.active(self.data):
            # Amended responses: the job is the amended lane's from here (its proof
            # key), and the other side's letters are read before the plan.
            amended.mark_envelope(self.job.job_dir)
            amended.challenge_stage(self.data, self.job.job_dir, amended.asker(self))

    def _freeze_plan(self, plan: list[dict[str, Any]]) -> None:
        self._put("units", plan)

    def _plan(self) -> list[dict[str, Any]]:
        """A fresh unit plan (``finish.units`` freezes it). With no default
        sets authored, a propounded set plans exactly as it always did."""
        if self.cls == "discovery_response":
            served = frozen(self.data, self.job.job_dir, lane_hook.by_operator(self.firm.data))
            if amended.active(self.data):
                ch = challenge.load(self.data)
                if ch is None:
                    raise challenge.Unread("the letters were not read before the plan")
                return amended.plan_units(served, ch)
            return plan_units(served)
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
        return amended.relabel(
            outcomes(frozen(self.data, self.job.job_dir, lane_hook.by_operator(self.firm.data)), plan, names), self.data
        )

    def served_review(self) -> dict[str, dict[str, Any]] | None:
        """What the review's response checks count against; None without a served plan."""
        plan = self._state().get("units") or []
        if not has_plan(plan):
            return None
        return review_map(frozen(self.data, self.job.job_dir, lane_hook.by_operator(self.firm.data)), plan)
