"""The letter's RE block against the matter record, mechanically, before render.

The auditor reads sections against the digest; nothing compared the front
matter (who the client is, when the loss was) with the matter's own fields
(review of #3074). A demand naming the wrong client or the wrong date of loss
is the most damaging error the letter can carry and the cheapest to catch, so
code compares them and a mismatch holds the job. A marker in the slot (the
draft left it for the attorney) is not a mismatch; a field the matter does not
carry is reported as unchecked, never as agreeing.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from .house import RenderError, parse

_MARKER = re.compile(r"\{\{|\[(TO BE SUPPLIED|ATTORNEY|CONFIRM|INSERT)")


def _tokens(name: str) -> set[str]:
    return {t for t in re.findall(r"[a-z]+", name.lower()) if len(t) > 1}


def _date(raw: str) -> date | None:
    raw = raw.strip()
    for fmt in ("%m/%d/%Y", "%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def matched_client(draft_md: str, facts: dict[str, Any]) -> str:
    """The matter record's own spelling of the client the letter names (the
    file label; on a multi-plaintiff matter, the right one of several). Falls
    back to the matter's first client when the letter leaves a marker."""
    names = [str(n) for n in (facts.get("client_names") or []) if n] or [str(facts.get("client_name") or "")]
    try:
        client = parse(draft_md)[0].get("client", "")
    except RenderError:
        return names[0]
    hits = [w for w in names if _tokens(w) <= _tokens(client) or _tokens(client) <= _tokens(w)]
    return hits[0] if len(hits) == 1 else names[0]


def check(draft_md: str, facts: dict[str, Any]) -> dict[str, list[str]]:
    """``{"mismatches": [...], "unchecked": [...]}``."""
    try:
        front, _body, _notes = parse(draft_md)
    except RenderError as exc:
        return {"mismatches": [f"the letter's front matter does not parse: {exc}"], "unchecked": []}
    out: dict[str, list[str]] = {"mismatches": [], "unchecked": []}
    client = front.get("client", "")
    wants = [str(n) for n in (facts.get("client_names") or [facts.get("client_name")]) if n]
    if _MARKER.search(client):
        out["unchecked"].append("client: left as a marker in the letter")
    elif not wants:
        out["unchecked"].append("client: the matter carries no client name")
    elif not any(_tokens(w) <= _tokens(client) or _tokens(client) <= _tokens(w) for w in wants):
        out["mismatches"].append(f"client: the letter says {client!r}, the matter's clients are {wants!r}")
    dol, want_dol = front.get("dol", ""), str(facts.get("date_of_loss") or "")
    if _MARKER.search(dol):
        out["unchecked"].append("date of loss: left as a marker in the letter")
    elif not want_dol:
        out["unchecked"].append("date of loss: the matter carries none")
    elif _date(dol) is None or _date(dol) != _date(want_dol):
        out["mismatches"].append(f"date of loss: the letter says {dol!r}, the matter record says {want_dol!r}")
    return out
