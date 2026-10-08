"""What the litigation lane's submit reads from authored config, held apart from
the verbs so each reader is one small, testable function.

Three sources, all authored, none from the model:

* ``customer.yaml`` (the seat): the skill entry (listed, enabled, its
  settings), ``scope.admins``, the library matter number, and whether the
  weekday cron row is live (uncommented) for the skill.
* the firm's ``litigation-firm.yaml`` (private, staged from the engagements
  repo; the runner reads ``$MEDCHRON_LITIGATION_INPUTS``, root:medchron, which
  the broker's uid cannot open, so entrypoint-litigation.sh installs a copy
  for the broker at ``$SMD_LITIGATION_FIRM_CONFIG``, root:workspace-broker
  0640): the monthly budget the broker
  enforces at submit, and the ``attorneys`` map (display name -> Smokeball
  staff id) the broker resolves a requester's "for <attorney>" against. The
  broker has no Smokeball access, so the map is the authored roster.

``operator_library_number`` is this lane's copy of
``drafting_intake.operator_library_number``; ``tests/test_litigation_intake.py``
drives both through the same cases, so a divergence is a visible failure.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

SKILL_NAME = "litigation-status"
FIRM_FILE = "litigation-firm.yaml"
FIRM_CONFIG_ENV = "SMD_LITIGATION_FIRM_CONFIG"
DEFAULT_FIRM_CONFIG = "/var/lib/smd-config/litigation-broker-firm.yaml"
#: The skill settings this lane reads (scalars, ADR 0075).
FOLDER_KEY = "folder_name"
FILE_TO_KEY = "file_to_matter_id"
RECIPIENTS_KEY = "scheduled_recipients"


def _load(path: str | Path) -> dict[str, Any]:
    import yaml

    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return doc if isinstance(doc, dict) else {}


def skill_entry(path: str | Path) -> dict[str, Any] | None:
    """The skill's entry, or None unless the seat LISTS it and does not
    disable it. Omission is refusal (ADR 0035)."""
    try:
        doc = _load(path)
    except Exception:  # noqa: BLE001 - an unreadable seat config enables nothing
        return None
    for persona in doc.get("personas") or []:
        for skill in (persona or {}).get("skills") or []:
            if not isinstance(skill, dict) or skill.get("name") != SKILL_NAME:
                continue
            if skill.get("enabled") is False:
                return None
            return skill
    return None


def settings_of(entry: dict[str, Any] | None) -> dict[str, Any]:
    settings = (entry or {}).get("settings")
    return settings if isinstance(settings, dict) else {}


def initiation_allows(entry: dict[str, Any] | None, trigger: str) -> bool:
    """``initiation.manual`` for a request, ``initiation.scheduled`` for a
    scheduled run. Unauthored is no."""
    initiation = (entry or {}).get("initiation")
    if not isinstance(initiation, dict):
        return False
    return initiation.get("manual" if trigger == "request" else "scheduled") is True


def scheduled_recipients(settings: dict[str, Any]) -> list[str]:
    """The authored recipients of a scheduled run, normalized. A
    comma-separated string (settings are scalars); a list is read the same."""
    value = settings.get(RECIPIENTS_KEY)
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return []
    return [v.strip().lower() for v in value if isinstance(v, str) and v.strip()]


def cron_row_live(path: str | Path) -> bool:
    """Whether a persona's ``cron`` list targets the skill. The row ships
    commented out, so this is False until the firm's enable act."""
    try:
        doc = _load(path)
    except Exception:  # noqa: BLE001 - unreadable is not live
        return False
    for persona in doc.get("personas") or []:
        for row in (persona or {}).get("cron") or []:
            if isinstance(row, dict) and row.get("skill") == SKILL_NAME:
                return True
    return False


def operator_library_number(path: str | Path) -> str | None:
    """The firm's own authored library matter number (``self_initiation.
    document_library.operator_matter.number``). None when unauthored."""
    try:
        number = _load(path)["self_initiation"]["document_library"]["operator_matter"]["number"]
    except Exception:  # noqa: BLE001 - unauthored or unreadable authorizes nothing
        return None
    return number.strip() if isinstance(number, str) and number.strip() else None


def firm_config(path: str | Path | None = None) -> dict[str, Any] | None:
    """The broker's copy of ``litigation-firm.yaml``, or None when absent or unreadable."""
    try:
        return _load(Path(path or os.environ.get(FIRM_CONFIG_ENV) or DEFAULT_FIRM_CONFIG))
    except Exception:  # noqa: BLE001 - absent config enables nothing
        return None


def monthly_budget_cents(firm: dict[str, Any] | None) -> int | None:
    value = (firm or {}).get("monthly_budget_usd")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return int(round(value * 100))


def attorney_roster(firm: dict[str, Any] | None) -> dict[str, str]:
    """name -> staff id, both stripped; the id lowercased."""
    raw = (firm or {}).get("attorneys")
    if not isinstance(raw, dict):
        return {}
    return {
        str(name).strip(): str(sid).strip().lower()
        for name, sid in raw.items()
        if isinstance(name, str) and name.strip() and isinstance(sid, str) and sid.strip()
    }


def resolve_attorneys(names: list[str], roster: dict[str, str]) -> tuple[list[str], str | None]:
    """(staff ids, None) or ([], the refusal sentence). A name matches a
    roster name exactly (case-insensitive), or else one roster entry by a
    single word of it (a first or last name). Ambiguous or unknown refuses."""
    ids: list[str] = []
    for raw in names:
        name = " ".join(str(raw).split()).lower()
        if not name:
            continue
        exact = [sid for full, sid in roster.items() if " ".join(full.split()).lower() == name]
        hits = exact or [sid for full, sid in roster.items() if name in full.lower().split()]
        if len(set(hits)) != 1:
            known = ", ".join(sorted(roster)) or "none authored"
            why = "matches more than one attorney" if hits else "is not an attorney on the firm's list"
            return [], f'"{raw}" {why} (the list: {known}); nothing was queued'
        ids.append(hits[0])
    if not ids:
        return [], "no attorney was named; nothing was queued"
    return sorted(set(ids)), None


__all__ = [
    "FIRM_CONFIG_ENV",
    "FIRM_FILE",
    "SKILL_NAME",
    "attorney_roster",
    "cron_row_live",
    "firm_config",
    "initiation_allows",
    "monthly_budget_cents",
    "operator_library_number",
    "resolve_attorneys",
    "scheduled_recipients",
    "settings_of",
    "skill_entry",
]
