"""The firm's authored attorney blocks: the top of every caption, by attorney.

WHY (2026-10-08). A caption was copied verbatim from the operative pleading,
attorney block included. A firm's pleadings often leave the block's email line
blank, so a draft could never carry the attorney's email: a live mediation brief
printed "Email: {{NOT IN RECORD ...}}". The attorney block is not a fact of the
case; it is the firm's own letterhead for one attorney, so the firm authors it
once, as data, in ``drafting-firm.yaml``:

    attorneys:
      - email: attorney@firm.example
        block:
          - Jane Q. Attorney, Esq. (SBN 123456)
          - EXAMPLE LAW, LLP
          - 100 Main Street
          - Springfield, California 90000
          - "Telephone: (555) 555-0100"
          - "Facsimile: (555) 555-0101"
          - "Email: attorney@firm.example"

A job uses the block of the matter's responsible attorney when the firm authored
one, else the requester's, else none (the caption's attorney block then comes
from the pleading, as before). Court, parties and case number always come from
the court record. The chosen block reaches compose and audit as a labeled source
and the drafting gate as one, so the final pass never strips an authored line.
"""

from __future__ import annotations

import re
from typing import Any

KEY = "attorneys"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate(value: Any) -> list[str]:
    """Problems with an ``attorneys:`` section, as ``attorneys...: message``."""
    if not isinstance(value, list) or not value:
        return [f"{KEY}: expected a non-empty list of {{email, block}}"]
    out: list[str] = []
    seen: set[str] = set()
    for i, entry in enumerate(value):
        where = f"{KEY}[{i}]"
        if not isinstance(entry, dict) or set(entry) != {"email", "block"}:
            out.append(f"{where}: expected exactly {{email, block}}")
            continue
        email = entry["email"]
        if not isinstance(email, str) or not _EMAIL.match(email.strip()):
            out.append(f"{where}.email: expected an email address")
        elif email.strip().lower() in seen:
            out.append(f"{where}.email: {email!r} appears twice")
        else:
            seen.add(email.strip().lower())
        block = entry["block"]
        if not isinstance(block, list) or not block or not all(isinstance(x, str) and x.strip() for x in block):
            out.append(f"{where}.block: expected a non-empty list of non-empty lines")
    return out


def pick(entries: list[dict[str, Any]], responsible: str | None, requester: str | None) -> dict[str, Any] | None:
    """The block for this job: the responsible attorney's, else the requester's, else None."""
    by_email = {str(e["email"]).strip().lower(): e for e in entries or []}
    for who in (responsible, requester):
        hit = by_email.get(str(who or "").strip().lower())
        if hit:
            return {"email": str(hit["email"]).strip().lower(), "lines": [str(x) for x in hit["block"]]}
    return None


def text(chosen: dict[str, Any]) -> str:
    """The block as compose and audit receive it."""
    return (
        f"THE ATTORNEY BLOCK (authored by the firm for {chosen['email']}; print these lines exactly, "
        "in this order, as the caption's attorney block, in place of any attorney block the court's "
        "paper carries; a valid source for each line):\n" + "\n".join(f"- {ln}" for ln in chosen["lines"])
    )
