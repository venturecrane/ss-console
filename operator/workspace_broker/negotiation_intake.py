"""What the negotiation lane's submit reads from the seat's customer.yaml, each
reader one small, testable function. All authored, none from the model:

* the skill entry ``negotiation-watch`` (listed, enabled, ``initiation``), its
  settings (scalars, ADR 0075): ``scheduled_recipients`` (who is emailed about
  each new offer; the first entry, a Named Administrator),
  ``monthly_budget_usd`` and ``per_job_cap_usd`` (runaway guards),
  ``matter_statuses`` and ``firm_words`` (comma-separated);
* whether a persona's cron list targets the skill (the firm's enable act);
* ``smokeball_layouts.settlement_negotiations_design``, the firm's authored
  Negotiation Details design, which finds a tab that has no rows yet.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

SKILL_NAME = "negotiation-watch"
RECIPIENTS_KEY = "scheduled_recipients"


def _load(path: str | Path) -> dict[str, Any]:
    import yaml

    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return doc if isinstance(doc, dict) else {}


def skill_entry(path: str | Path) -> dict[str, Any] | None:
    """The skill's entry, or None unless the seat LISTS it and does not disable
    it. Omission is refusal (ADR 0035)."""
    try:
        doc = _load(path)
    except Exception:  # noqa: BLE001 - an unreadable seat config enables nothing
        return None
    for persona in doc.get("personas") or []:
        for skill in (persona or {}).get("skills") or []:
            if isinstance(skill, dict) and skill.get("name") == SKILL_NAME:
                return None if skill.get("enabled") is False else skill
    return None


def settings_of(entry: dict[str, Any] | None) -> dict[str, Any]:
    settings = (entry or {}).get("settings")
    return settings if isinstance(settings, dict) else {}


def scheduled_allowed(entry: dict[str, Any] | None) -> bool:
    initiation = (entry or {}).get("initiation")
    return isinstance(initiation, dict) and initiation.get("scheduled") is True


def _csv(settings: dict[str, Any], key: str) -> list[str]:
    value = settings.get(key)
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return []
    return [v.strip() for v in value if isinstance(v, str) and v.strip()]


def scheduled_recipients(settings: dict[str, Any]) -> list[str]:
    return [v.lower() for v in _csv(settings, RECIPIENTS_KEY)]


def matter_statuses(settings: dict[str, Any]) -> list[str]:
    return _csv(settings, "matter_statuses") or ["Open"]


def firm_words(settings: dict[str, Any]) -> list[str]:
    return [w.lower() for w in _csv(settings, "firm_words") if w.isalpha() and 3 <= len(w) <= 40]


def usd(settings: dict[str, Any], key: str) -> float | None:
    value = settings.get(key)
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return float(value)


def cron_row_live(path: str | Path) -> bool:
    try:
        doc = _load(path)
    except Exception:  # noqa: BLE001 - unreadable is not live
        return False
    return any(
        isinstance(row, dict) and row.get("skill") == SKILL_NAME
        for persona in doc.get("personas") or []
        for row in (persona or {}).get("cron") or []
    )


def negotiation_design(path: str | Path) -> str:
    try:
        block = _load(path).get("smokeball_layouts") or {}
    except Exception:  # noqa: BLE001 - unauthored is empty; the runner falls back to the tab's keys
        return ""
    value = block.get("settlement_negotiations_design") if isinstance(block, dict) else None
    return value.strip().lower() if isinstance(value, str) else ""


__all__ = [
    "SKILL_NAME",
    "cron_row_live",
    "firm_words",
    "matter_statuses",
    "negotiation_design",
    "scheduled_allowed",
    "scheduled_recipients",
    "settings_of",
    "skill_entry",
    "usd",
]
