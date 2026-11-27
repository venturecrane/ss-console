"""The run's text model call, ``ask(stage, system, user, max_tokens) -> str``
over its own doorway, so every paid call is priced, capped and recorded, and a
replay answers it from recordings at $0.

One home for the first draft (``DraftingRun``) and every revision run that
inherits it. The firm-rules stage (``rulebook/hook.py``) takes its model call
from here: before 2026-11-26 only ``ReviseRun`` had it, so a first draft on a
firm whose ``phrase_book.stages`` names ``first_draft`` had no model call, and
every rule not already read failed the job ``rule_unchecked``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable


class DoorwayAsk:
    """Mixin of ``DraftingRun``: needs ``self.doorway``."""

    if TYPE_CHECKING:
        doorway: Any

    def _ask(self, model: str) -> Callable[[str, str, str, int], str]:
        def ask(stage: str, system: str, user: str, max_tokens: int) -> str:
            r = self.doorway.call(
                stage, model=model, system=system, messages=[{"role": "user", "content": user}], max_tokens=max_tokens
            )
            return r.text

        return ask
