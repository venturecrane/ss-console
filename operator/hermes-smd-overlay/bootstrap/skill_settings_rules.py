"""The value rules for a skill entry's ``settings`` (the on-box half; the
console's is ``sections-persona-skills.ts``). Split out of ``validate.py``,
which is at its module-size ceiling.

* ``recipients``: only ``roster`` (the scheduled-turn roster lane,
  ``shared.send_anchor.routine_turn_lane``); any other would author a lane
  nobody gets.
* ``audience``: only ``champion``, and only on the ``product-guide`` skill
  (``shared.champion_audience``).
"""

from __future__ import annotations

from typing import Any


def errors_for(skill: dict[str, Any], prefix: str) -> list[str]:
    """Every rule the entry's settings break, as ``validate.py`` error lines."""
    settings = skill.get("settings")
    if not isinstance(settings, dict):
        return []
    out = []
    if "recipients" in settings and settings["recipients"] != "roster":
        out.append(f"{prefix}.settings.recipients: must be 'roster' when present")
    if "audience" in settings and not (
        skill.get("name") == "product-guide" and settings["audience"] == "champion"
    ):
        out.append(
            f"{prefix}.settings.audience: must be 'champion', and only on the product-guide skill"
        )
    return out
