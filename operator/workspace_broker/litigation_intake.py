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
  staff id) the broker resolves a requester's "for <attorney>" against, and
  the ``paralegals`` map the same for "<paralegal>'s files". A name on one
  side alone becomes a people scope (any staff role, by the roster's name);
  both sides named keep the ids (responsible AND Person Assisting). The
  broker has no Smokeball access, so the maps are the authored roster.

``operator_library_number`` is this lane's copy of
``drafting_intake.operator_library_number``; ``tests/test_litigation_intake.py``
drives both through the same cases, so a divergence is a visible failure.
"""

from __future__ import annotations

import os
import re
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
#: Where the deadlines report is filed (the same library matter, its own folder).
DEADLINES_FOLDER_KEY = "deadlines_folder_name"

#: THE DEADLINES REPORT, told from the status list by the request's OWN words
#: (2026-10-15). Code decides, never the model, and the overlay's tool needs
#: no new field: the broker already holds the email's words verbatim. A
#: request naming deadlines, a calendar of dates, or a statute report asks
#: for the deadlines report, unless it names the status list (or a case
#: review list) and no deadline or statute REPORT, which stays the list.
_STATUS_WORDS = re.compile(r"(?i)\bstatus (list|report)\b|\bcase review\b|\blitigation (case )?list\b")
_DEADLINE_WORDS = re.compile(r"(?i)\bdeadlines?\b|\bcalendar(ed)? dates\b|\bupcoming (court )?dates\b")
_STATUTE_WORDS = re.compile(r"(?i)\bstatutes?\b|\bSOLs?\b")
_NAMED_REPORT = re.compile(
    r"(?i)\b(deadlines?|statutes?( of limitations?)?|SOLs?)( and \w+)? (report|list|spreadsheet|workbook|sheet)s?\b"
)


def requested_report(text: str) -> tuple[str, tuple[str, ...]]:
    """``("status", ())`` or ``("deadlines", workbooks)`` from the request's own
    words. Workbooks: ``deadlines`` when the words name deadlines or dates,
    ``statute`` when they name a statute report; a request naming only the
    one gets only it."""
    text = str(text or "")
    deadlines, statute = bool(_DEADLINE_WORDS.search(text)), bool(_STATUTE_WORDS.search(text))
    if not (deadlines or statute):
        return "status", ()
    if _STATUS_WORDS.search(text) and not _NAMED_REPORT.search(text):
        return "status", ()
    return "deadlines", tuple(w for w, on in (("deadlines", deadlines), ("statute", statute)) if on)


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


def _roster(firm: dict[str, Any] | None, key: str) -> dict[str, str]:
    raw = (firm or {}).get(key)
    if not isinstance(raw, dict):
        return {}
    return {
        str(name).strip(): str(sid).strip().lower()
        for name, sid in raw.items()
        if isinstance(name, str) and name.strip() and isinstance(sid, str) and sid.strip()
    }


def attorney_roster(firm: dict[str, Any] | None) -> dict[str, str]:
    """name -> staff id, both stripped; the id lowercased."""
    return _roster(firm, "attorneys")


def paralegal_roster(firm: dict[str, Any] | None) -> dict[str, str]:
    """The ``paralegals`` map (name -> staff id; the id a matter carries as its
    Person Assisting when both sides are named), read the same way as the attorneys'."""
    return _roster(firm, "paralegals")


def _resolve_named(
    names: list[str], roster: dict[str, str], noun: str, article: str
) -> tuple[list[tuple[str, str]], str | None]:
    """([(the roster's own name, staff id)], None) or ([], the refusal sentence).
    The roster's name is what a people scope carries to the runner, which finds
    every Smokeball record of that person by it (``litigation/people.py``)."""
    found: list[tuple[str, str]] = []
    for raw in names:
        name = " ".join(str(raw).split()).lower()
        name = re.sub(r"['’]s$", "", name)  # "Irma's" is Irma
        if not name:
            continue
        exact = [(full, sid) for full, sid in roster.items() if " ".join(full.split()).lower() == name]
        hits = exact or [(full, sid) for full, sid in roster.items() if name in full.lower().split()]
        if len({sid for _full, sid in hits}) != 1:
            known = ", ".join(sorted(roster)) or "none authored"
            why = f"matches more than one {noun}" if hits else f"is not {article} {noun} on the firm's list"
            return [], f'"{raw}" {why} (the list: {known}); nothing was queued'
        found.append((" ".join(hits[0][0].split()), hits[0][1]))
    if not found:
        return [], f"no {noun} was named; nothing was queued"
    return sorted(set(found)), None


def _resolve(names: list[str], roster: dict[str, str], noun: str, article: str) -> tuple[list[str], str | None]:
    found, why = _resolve_named(names, roster, noun, article)
    return sorted({sid for _full, sid in found}), why


def named_attorneys(names: list[str], roster: dict[str, str]) -> tuple[list[tuple[str, str]], str | None]:
    """``resolve_attorneys`` keeping each match's roster name."""
    return _resolve_named(names, roster, "attorney", "an")


def named_paralegals(names: list[str], roster: dict[str, str]) -> tuple[list[tuple[str, str]], str | None]:
    """``resolve_paralegals`` keeping each match's roster name."""
    return _resolve_named(names, roster, "paralegal", "a")


def resolve_attorneys(names: list[str], roster: dict[str, str]) -> tuple[list[str], str | None]:
    """(staff ids, None) or ([], the refusal sentence). A name matches a
    roster name exactly (case-insensitive), or else one roster entry by a
    single word of it (a first or last name). Ambiguous or unknown refuses."""
    return _resolve(names, roster, "attorney", "an")


def resolve_paralegals(names: list[str], roster: dict[str, str]) -> tuple[list[str], str | None]:
    """The same rules against the firm's paralegal list."""
    return _resolve(names, roster, "paralegal", "a")


__all__ = [
    "DEADLINES_FOLDER_KEY",
    "FIRM_CONFIG_ENV",
    "FIRM_FILE",
    "SKILL_NAME",
    "attorney_roster",
    "cron_row_live",
    "firm_config",
    "initiation_allows",
    "monthly_budget_cents",
    "named_attorneys",
    "named_paralegals",
    "operator_library_number",
    "paralegal_roster",
    "requested_report",
    "resolve_attorneys",
    "resolve_paralegals",
    "scheduled_recipients",
    "settings_of",
    "skill_entry",
]
