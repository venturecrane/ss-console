""" "Sets of discovery" with no kind named: the standard propounded package.

**The incident (2026-11-23).** An attorney replied in a matter's thread "Please
also prepare sets of discovery to the middle school and school district." No
word of the request named a kind of set, the firm had authored no
``discovery_defaults``, so the plan made one document of no kind; the job read
the whole file for 42 minutes and then the compose refused, asking him which
kind. That question would have gone back to him: a client loop.

**The rule, decided in code.** A propounded-set request that asks for
discovery or for sets ("sets of discovery", "discovery to X", "discovery
sets") and names no kind of set (``sets.names_any_set``) means:

1. The firm's own ``discovery_defaults`` when it authored them (``served_plan``,
   unchanged).
2. Else, when the request says it is more of the same ("also", "as well",
   "same") and the newest earlier propounded-set job on the same matter from
   the same requester planned named kinds, those kinds: the "also" refers to it.
3. Else the standard package: special interrogatories, requests for admission
   and requests for production, one of each per party served.

The attorney notes say which, in one line (``note``). Never a question.

A request that names no kind and says neither "discovery" nor "sets" ("a set
to Delta about the brakes") plans as it always did: the compose drafts the one
set it describes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import sets

#: The standard propounded package, in the order the notes name it.
STANDARD = ("special_interrogatories", "requests_for_admission", "requests_for_production")
#: The request asks for discovery or for sets, by those words.
_PACKAGE = re.compile(r"(?i)\bdiscovery\b|\bsets\b")
#: The request is more of what was asked before.
_MORE = re.compile(r"(?i)\balso\b|\bas\s+well\b|\bsame\b")
#: The state key the plan's basis is kept under, for the notes.
BASIS_KEY = "sets_basis"
_LEAD = "You asked for sets of discovery without naming the kinds; "


def wants_package(request: str) -> bool:
    """The request asks for discovery or sets and names no kind of set."""
    return not sets.names_any_set(request) and bool(_PACKAGE.search(request or ""))


def _labels() -> dict[str, str]:
    return {k: label for k, label, _pat in sets.VARIANTS}


def standard() -> list[sets.Unit]:
    labels = _labels()
    return [sets.Unit(k, labels[k]) for k in STANDARD]


def _read(path: Path) -> dict[str, Any]:
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def prior_kinds(job_dir: Path, job_id: str, matter_id: str, requester: str) -> list[str]:
    """The kinds the newest earlier propounded-set job on this matter from this
    requester planned (its frozen plan in ``data/state.json``), in its order;
    [] when there is none or it planned no named kind. Job ids are ULIDs, so
    "earlier" is "sorts before this one"."""
    root, known = Path(job_dir).parent, set(_labels())
    if not root.is_dir():
        return []
    for d in sorted((p for p in root.iterdir() if p.is_dir() and p.name < job_id), reverse=True):
        env = _read(d / "job.json")
        if env.get("document_class") != "discovery_set" or env.get("matter_id") != matter_id:
            continue
        if str(env.get("requester") or "").strip().lower() != (requester or "").strip().lower():
            continue
        plan = _read(d / "data" / "state.json").get("units") or []
        kinds = [str(p.get("variant")) for p in plan if isinstance(p, dict) and p.get("variant") in known]
        if kinds:
            return list(dict.fromkeys(kinds))
    return []


def plan(request: str, job: Any) -> tuple[list[sets.Unit], str]:
    """The sets an unnamed "discovery" request means, and its basis
    (``prior`` or ``standard``)."""
    if _MORE.search(request or ""):
        kinds = prior_kinds(job.job_dir, job.job_id, job.matter_id, job.requester)
        if kinds:
            labels = _labels()
            return [sets.Unit(k, labels[k]) for k in kinds], "prior"
    return standard(), "standard"


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def note(basis: str | None, kinds: list[str]) -> str | None:
    """The one line the attorney notes carry for the basis; None for none."""
    if basis not in ("prior", "standard") or not kinds:
        return None
    labels = _labels()
    named = _and([labels[k].lower() for k in dict.fromkeys(kinds) if k in labels])
    if basis == "prior":
        return _LEAD + f"these are the kinds of your earlier request on this matter: the {named}."
    return _LEAD + f"these are the {named}."
