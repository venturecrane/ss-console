""" "Sets of discovery" with no kind named: the standard propounded package.

**The incident (2026-11-23).** An attorney replied in a matter's thread "Please
also prepare sets of discovery to the middle school and school district." No
word of the request named a kind of set, the firm had authored no
``discovery_defaults``, so the plan made one document of no kind; the job read
the whole file for 42 minutes and then the compose refused, asking him which
kind. That question would have gone back to him: a client loop.

**The rule, decided in code.** A propounded-set request whose body (its
signature and quoted reply dropped, ``parties.request_body``) asks for new
discovery in so many words ("sets of discovery", "discovery sets / requests /
package", "draft / prepare / propound / serve [up to two words] discovery to /
on / upon") and names no kind of set (``sets.names_any_set``) means:

1. The firm's own ``discovery_defaults`` when it authored them (``served_plan``,
   unchanged).
2. Else, when the clause asking for it says "also" or "as well", the kinds the
   same requester's newest earlier propounded-set job on the matter planned,
   within 30 days; that job planning no named kind means the standard package.
3. Else the standard package: special interrogatories, requests for admission
   and requests for production, one of each per party served.

The attorney notes say which, in one line (``note``). Never a question.

Never a package: a request that speaks of responses, answers, a supplement,
an amendment, a redo or a revision, of what was served, of "a set", or of a
deposition, a subpoena or an inspection ("Redo the discovery with
Accommodation defined" in the thread of a delivered package). Those, and any
other request naming no kind, plan as they always did; what the compose
cannot settle holds ``request_incomplete`` for SMD, never a question.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import parties, sets

#: The standard propounded package, in the order the notes name it.
STANDARD = ("special_interrogatories", "requests_for_admission", "requests_for_production")
#: The request asks for new discovery to be propounded, in these words only.
_PROPOUND = re.compile(
    r"(?i)\bsets\s+of\s+discovery\b|\bdiscovery\s+(?:sets|requests|package)\b"
    r"|\b(?:draft|prepare|propound|serve)\s+(?:[\w'-]+\s+){0,2}?discovery\s+(?:to|on|upon)\b"
)
#: Words that make it something else: a response, a change to what exists, one set, another device.
_NOT = re.compile(
    r"(?i)\brespon|\banswer|\bsupplement|\bamend|\bredo\b|\brevis|\bserved\b|\ba\s+set\b"
    r"|\bdeposition|\bsubpoena|\binspection"
)
#: The asking clause is more of what was asked before.
_MORE = re.compile(r"(?i)\balso\b|\bas\s+well\b")
_ASKS = re.compile(r"(?i)\bdiscovery\b|\bsets\b")
_CLAUSE = re.compile(r"[.;!?\n]+")
#: How far back an earlier request's kinds are reused.
PRIOR_DAYS = 30
#: The state key the plan's basis is kept under, for the notes.
BASIS_KEY = "sets_basis"
_LEAD = "You asked for sets of discovery without naming the kinds; "


def wants_package(request: str) -> bool:
    """The request asks for new discovery in so many words and names no kind."""
    body = parties.request_body(request or "")
    if sets.names_any_set(body) or _NOT.search(body):
        return False
    return bool(_PROPOUND.search(body))


def more_of_the_same(request: str) -> bool:
    """A clause of the request asks for discovery or sets "also" / "as well"."""
    clauses = _CLAUSE.split(parties.request_body(request or ""))
    return any(_MORE.search(c) and _ASKS.search(c) for c in clauses)


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


_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid_ms(ulid: str) -> int | None:
    """The millisecond time a ULID's first ten characters carry; None for no ULID."""
    head = str(ulid or "").upper()[:10]
    if len(head) != 10 or any(ch not in _CROCKFORD for ch in head):
        return None
    ms = 0
    for ch in head:
        ms = ms * 32 + _CROCKFORD.index(ch)
    return ms


def prior_kinds(job_dir: Path, job_id: str, matter_id: str, requester: str) -> list[str]:
    """The kinds the newest earlier propounded-set job on this matter from this
    requester planned (its frozen plan in ``data/state.json``), in its order,
    when it was queued within ``PRIOR_DAYS`` of this one. The first such job
    decides: one that planned no named kind gives []. Job ids are ULIDs, so
    "earlier" is "sorts before this one"."""
    root, known, here = Path(job_dir).parent, set(_labels()), ulid_ms(job_id)
    if not root.is_dir() or here is None:
        return []
    since = here - PRIOR_DAYS * 86_400_000
    for d in sorted((p for p in root.iterdir() if p.is_dir() and p.name < job_id), reverse=True):
        at = ulid_ms(d.name)
        if at is None:
            continue
        if at < since:
            return []  # older than the window, and every job after it in this order is older still
        env = _read(d / "job.json")
        if env.get("document_class") != "discovery_set" or env.get("matter_id") != matter_id:
            continue
        if str(env.get("requester") or "").strip().lower() != (requester or "").strip().lower():
            continue
        plan = _read(d / "data" / "state.json").get("units") or []
        kinds = [str(p.get("variant")) for p in plan if isinstance(p, dict) and p.get("variant") in known]
        return list(dict.fromkeys(kinds))
    return []


def plan(request: str, job: Any) -> tuple[list[sets.Unit], str]:
    """The sets an unnamed "discovery" request means, and its basis
    (``prior`` or ``standard``)."""
    if more_of_the_same(request):
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
