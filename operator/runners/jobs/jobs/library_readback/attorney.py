"""Test (b): the filed document is signed for the attorney its record names.

WHAT IS EXPECTED, from the roster (``operator_connector_sdk.library.roster``)
when one is installed, else from the list each lane read before the roster:

* a drafting document: the attorney's ``caption_block``, every line, in order.
  With no roster, the firm's authored block the job chose (``caption.json``
  ``attorney_block``: the drafting inputs' ``attorneys:`` entry for the
  matter's attorney email). That list keys attorneys by email and the record
  keys them by name, so without a roster code cannot tie the block to the
  record's attorney; the result says so (``tie``);
* a demand: the roster's ``signs.demand`` signer and title lines (a title's
  ``{client}`` matches any words), or with no roster the demand inputs'
  ``attorneys`` row for the attorney (``DemandFirm.attorney_for``).

WHAT IS MEASURED: each expected line is a paragraph of the filed document
(body or table, whitespace folded), and the lines appear in order. The result
names lines by position, never their words.

A record with no attorney (``resolution.signer`` null) is ``not_applicable``;
nothing to expect (no roster entry, no legacy row) is ``not_applicable`` with
the reason; an installed roster the seat cannot read is ``not_recorded``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from operator_connector_sdk.library import roster as roster_mod

from . import FAIL, NOT_APPLICABLE, NOT_RECORDED, PASS, verdict


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def _pattern(line: str) -> re.Pattern[str]:
    parts = [re.escape(_fold(p)) for p in re.split(r"\{client\}", line)]
    return re.compile(r"\A" + r".+".join(parts) + r"\Z")


def in_order(lines: list[str], paragraphs: list[str]) -> dict[str, Any]:
    """Which expected lines the paragraphs carry, in order: ``found`` and the
    1-based positions of the lines ``missing``."""
    folded = [_fold(p) for p in paragraphs]
    at, missing = 0, []
    for i, line in enumerate(lines, 1):
        pat = _pattern(line)
        hit = next((j for j in range(at, len(folded)) if pat.match(folded[j])), None)
        if hit is None:
            missing.append(i)
        else:
            at = hit + 1
    return {"lines": len(lines), "found": len(lines) - len(missing), "missing": missing}


def _person(roster: roster_mod.Roster, signer: dict[str, Any]) -> roster_mod.Person | None:
    """The roster's entry for the record's attorney: by the key the ledger files
    them under (the staff name, folded), else by the name on the document."""
    return roster.by_name(str(signer.get("key") or "")) or roster.by_name(str(signer.get("name") or ""))


def _drafting_expected(data: Path, signer: dict[str, Any], roster: roster_mod.Roster) -> tuple[list[str], str]:
    if roster.status == roster_mod.OK:
        person = _person(roster, signer)
        if person is None or not person.attorney:
            return [], "the roster lists no attorney by the record's name"
        return list(person.caption_block), "roster caption_block"
    try:
        cap = json.loads((data / "caption.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cap = {}
    lines = [str(x) for x in ((cap.get("attorney_block") or {}).get("lines") or [])]
    return lines, "legacy attorneys entry the job chose"


def _demand_expected(signer: dict[str, Any], roster: roster_mod.Roster, inputs: str | None) -> tuple[list[str], str]:
    name = str(signer.get("name") or signer.get("key") or "")
    if roster.status == roster_mod.OK:
        person = _person(roster, signer)
        sig = roster.signature(person, roster_mod.DEMAND) if person is not None and person.attorney else None
        return ([sig["signer"], sig["title"]] if sig else []), "roster signs.demand"
    if not inputs:
        return [], "no roster is installed and no demand inputs were given"
    from ..demand import firm as demand_firm

    try:
        firm = demand_firm.load(inputs)
        row = firm.attorney_for(str(signer.get("key") or "")) or firm.attorney_for(name)
    except demand_firm.DemandConfigError:
        return [], "no roster is installed and the demand inputs could not be read"
    lines = [str(row[k]) for k in ("signer", "title") if row and row.get(k)]
    return lines, "legacy demand attorneys row"


def score(
    record: dict[str, Any], data: Path, paragraphs: list[str], roster: roster_mod.Roster, inputs: str | None
) -> dict[str, Any]:
    """Test (b) for one filed document."""
    signer = (record.get("resolution") or {}).get("signer")
    if not isinstance(signer, dict):
        return verdict(NOT_APPLICABLE, "the record names no attorney")
    if roster.status == roster_mod.UNUSABLE:
        return verdict(NOT_RECORDED, "the installed roster could not be read")
    demand = record.get("path") == "demand_job"
    lines, source = _demand_expected(signer, roster, inputs) if demand else _drafting_expected(data, signer, roster)
    if not lines:
        return verdict(NOT_APPLICABLE, source)
    got = in_order(lines, paragraphs)
    got["source"] = source
    got["tie"] = "roster" if source.startswith("roster") else "not checkable without a roster"
    if got["missing"]:
        return verdict(FAIL, "the filed document does not carry the attorney's block in order", got)
    return verdict(PASS, "", got)


__all__ = ["in_order", "score"]
