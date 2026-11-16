"""A seat whose product-guide answers are for champions only (``settings.audience: champion``).

A client seat can enable the product-guide skill next to its own work. The
first is a hosted-agent seat whose own skill captures and recalls open-house
visitors. There, nobody is enrolled with a code. The product library is still
answered to people inside a client business, so what it may read and cite is
limited by PATH, for everyone on the seat, and never by who the sender is:

* the seat authors ``settings: {audience: champion}`` on its ``product-guide``
  skill entry (both customer.yaml validators accept that one value there);
* reads under ``product-guide/references/`` reach only the champion allowlist
  and the index (``hermes-smd-initiation`` ``champion_fence``);
* a reply's ``Source:`` lines that name a library document must name an
  allowlisted document and a ``##`` heading it has (:func:`source_reason`).
  Lines that name no library document (the seat's own skill writes none) are
  not checked, and the representative markers are not checked, because nobody
  on this seat is a representative;
* once a turn has read ``product-guide/SKILL.md`` (marked in
  :data:`GUIDE_READ` at ``post_tool_call``, cleared at the next turn's start),
  its reply must carry at least one valid library ``Source:`` line
  (:data:`SOURCE_MISSING`). An answer from the product library cites the
  library; anything else is held and the model is told why.

None of the champion predicate applies here: no record-store or handler
refusals, no role line, no daily cap, no change to the seat's held line.
The registry is in memory; nothing is written to disk.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from shared import champion

logger = logging.getLogger(__name__)

KEY = "audience"
VALUE = "champion"
#: A reply that answered from the product library but cites none of it.
SOURCE_MISSING = "source_missing"
#: What the model is told when a reply on such a seat is held.
MODEL_DETAIL = (
    "This reply answers from the product library on a seat where every library answer "
    "cites an open library document. Cite only documents the index opens to champions, "
    'each with an exact section heading, in the line Source: <file>, "<heading>"; or '
    "answer without the library."
)

#: Sessions whose current turn read product-guide's procedure.
GUIDE_READ = champion.InFlight()

_GUIDE_SKILL_MD = re.compile(r"(?:^|/)skills/+product-guide/+SKILL\.md$")


def seat_has_audience(raw: Any) -> bool:
    """Whether this seat authors ``audience: champion`` on its product-guide entry."""
    if not isinstance(raw, dict):
        return False
    for persona in raw.get("personas") or []:
        skills = persona.get("skills") if isinstance(persona, dict) else None
        for skill in skills if isinstance(skills, list) else []:
            if not isinstance(skill, dict) or skill.get("name") != champion.SKILL:
                continue
            settings = skill.get("settings")
            if isinstance(settings, dict) and settings.get(KEY) == VALUE:
                return skill.get("enabled") is not False
    return False


def live_seat_has_audience() -> bool:
    """:func:`seat_has_audience` over the live config; a fault reads as off."""
    try:
        from shared.customer_config import CustomerConfig

        return seat_has_audience(CustomerConfig.from_volume().raw)
    except Exception:  # noqa: BLE001 - an unreadable config changes nothing
        return False


def read_guide_procedure(tool_name: Any, args: Any) -> bool:
    """Whether this tool call read product-guide's own SKILL.md."""
    if not isinstance(args, dict):
        return False
    if tool_name == "skill_view":
        name = args.get("name") or args.get("skill") or args.get("slug")
        sub = args.get("file_path")
        return name == champion.SKILL and (not sub or str(sub).strip() == "SKILL.md")
    if tool_name != "read_file":
        return False
    for key in ("path", "file_path", "filename"):
        value = args.get(key)
        if isinstance(value, str) and _GUIDE_SKILL_MD.search(os.path.normpath(value.strip())):
            return True
    return False


def _library_lines(text: str, refs: Path | None) -> list[tuple[str, str | None]]:
    """``(document, heading or None)`` for each ``Source:`` line naming a library file."""
    library = {p.name for p in refs.glob("*.md")} if refs is not None else set()
    out: list[tuple[str, str | None]] = []
    for line in champion._SOURCE_LINE.findall(text):
        for found in champion._SOURCE_FILE.finditer(line):
            name = os.path.basename(found.group(1))
            if name in library:
                heading = champion._SOURCE_HEADING.search(line[found.end() :])
                out.append((name, heading.group(1).strip() if heading else None))
                break
    return out


def _valid(doc: str, heading: str | None, allowed: frozenset[str], refs: Path) -> str:
    if doc not in allowed:
        return champion.SOURCE_NOT_ALLOWED
    if heading is None:
        return ""
    have = champion.headings(refs / doc)
    return "" if have is not None and heading in have else champion.HEADING_NOT_FOUND


def source_reason(
    text: str, *, guide_read: bool, allowed: frozenset[str] | None = None, refs: Path | None = None
) -> str:
    """Why a reply on an audience seat may not go, or ``""``. Never raises."""
    try:
        body = text if isinstance(text, str) else ""
        refs = refs if refs is not None else champion.references_dir()
        allowed = allowed if allowed is not None else champion.allowlist(refs)
        cited = _library_lines(body, refs)
        for doc, heading in cited:
            problem = _valid(doc, heading, allowed, refs) if refs is not None else "unreadable"
            if problem:
                return problem if problem != "unreadable" else champion.SOURCE_UNREADABLE
        if guide_read and not any(heading is not None for _d, heading in cited):
            return SOURCE_MISSING
        return ""
    except Exception:  # noqa: BLE001 - a wall that cannot decide holds
        logger.warning("champion_audience: the wall could not decide; holding", exc_info=True)
        return champion.SOURCE_UNREADABLE


__all__ = [
    "GUIDE_READ",
    "KEY",
    "MODEL_DETAIL",
    "SOURCE_MISSING",
    "VALUE",
    "live_seat_has_audience",
    "read_guide_procedure",
    "seat_has_audience",
    "source_reason",
]
