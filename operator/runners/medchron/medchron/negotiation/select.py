"""Which newly saved documents the negotiation watch reads: an offer-related
file NAME, in a format it can read.

COVERAGE GAP (named on purpose, in the skill doc too): selection is by name. An
offer saved as "Scan_0042.pdf" or "Correspondence.pdf" is not read, and its
offer is not entered or announced. The firm's own offer letters and the
carriers' emails are named by the staff who save them, which is what the
2026-10-09 backfill measured; a generic name is the residue.
"""

from __future__ import annotations

import re
from typing import Any

NAME = re.compile(r"offer|demand|998|tender|counter|settle|evaluat|policy[\s_-]*limit", re.IGNORECASE)
EXTENSIONS = frozenset({".pdf", ".docx", ".doc", ".msg", ".eml"})


def extension(f: dict[str, Any]) -> str:
    ext = str(f.get("ext") or "").strip().lower()
    if ext and not ext.startswith("."):
        ext = "." + ext
    if ext:
        return ext
    name = str(f.get("name") or "")
    return ("." + name.rsplit(".", 1)[1].lower()) if "." in name else ""


def full_name(f: dict[str, Any]) -> str:
    name = str(f.get("name") or "").strip()
    ext = extension(f)
    return name if not ext or name.lower().endswith(ext) else name + ext


def select(f: dict[str, Any]) -> bool:
    """True for a live file whose name is offer-related and whose format the
    reader handles."""
    if f.get("deleted"):
        return False
    return extension(f) in EXTENSIONS and bool(NAME.search(str(f.get("name") or "")))


__all__ = ["EXTENSIONS", "NAME", "extension", "full_name", "select"]
