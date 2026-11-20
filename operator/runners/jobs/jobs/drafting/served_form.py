"""A served form interrogatory set's own keys in ``served.json`` (``served.py``).

A form interrogatory set (Judicial Council DISC-001) is not numbered 1..N: the
propounding party checks boxes beside the form's dotted numbers. So a form set
names, beside the keys every set carries::

    "form": "DISC-001",                       a form the catalogue carries
    "form_revision": "Rev. January 1, 2024",  that form's printed revision
    "checked": ["1.1", "2.1", ...],           the checked numbers, form order
    "definition_boxes": ["4(a)(1)"],          the Sec. 4(a) boxes checked
    "additions": false                        true: extra interrogatories attached

``request_count`` is the checksum: it equals the number of checked numbers.
The numbers, their order and the boxes are checked against the catalogue
(``forms.checked``, ``forms.catalog``), never against arithmetic.

An SMD-drafted form set may leave all five out (SMD drafts it by hand as
before); given one, it gives all five. An Operator-drafted form set gives all
five, and is still refused (``served_invalid``) unless the drafting answers
form sets (``served.FORM_SETS_BY_OPERATOR``, off until the answer path lands)
and it has no additions (attached interrogatories are answered by SMD).
"""

from __future__ import annotations

from typing import Any

from .forms import catalog
from .forms.checked import CheckedError, validate

FORM_TYPE = "form_interrogatories"
FORM_KEYS = ("form", "form_revision", "checked", "definition_boxes", "additions")


def _strings(v: Any) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v)


def fields(i: int, raw: dict[str, Any], problems: list[str], *, by_operator: bool) -> dict[str, Any]:
    """The set's form keys, checked; ``{}`` for a set that gives none. Every
    problem is a sentence appended to ``problems``. ``by_operator``: whether
    the drafting answers form sets yet (``served.FORM_SETS_BY_OPERATOR``)."""
    given = [k for k in FORM_KEYS if k in raw]
    st, drafter = raw.get("set_type"), raw.get("drafted_by")
    if st != FORM_TYPE:
        problems += [f'set {i}: "{k}" is for a form interrogatory set only' for k in given]
        return {}
    operator = drafter == "operator"
    if operator and not by_operator:
        problems.append(f'set {i}: a form interrogatory set is drafted by SMD for now; mark it "drafted_by": "smd"')
        return {}
    if not given and not operator:
        return {}
    missing = [k for k in FORM_KEYS if k not in raw]
    if missing:
        problems.append(f"set {i}: a form interrogatory set names all of {list(FORM_KEYS)}; missing {missing}")
        return {}
    before = len(problems)
    form, rev = raw["form"], raw["form_revision"]
    cat = None
    try:
        cat = catalog.load(str(form), str(rev)) if isinstance(form, str) and isinstance(rev, str) else None
    except catalog.CatalogueError:
        pass
    if cat is None:
        problems.append(f"set {i}: {form!r} [{rev!r}] is not a form and revision the catalogue carries")
    if not isinstance(raw["additions"], bool):
        problems.append(f"set {i}: additions must be true or false")
    if not _strings(raw["checked"]) or not raw["checked"]:
        problems.append(f'set {i}: checked must be a list of the checked numbers, like ["1.1", "2.1"]')
    if not _strings(raw["definition_boxes"]):
        problems.append(f'set {i}: definition_boxes must be a list of the checked Sec. 4(a) boxes, like ["4(a)(1)"]')
    if cat is not None and len(problems) == before:
        boxes = raw["definition_boxes"]
        bad = [b for b in boxes if b not in cat.definition_boxes]
        problems += [f"set {i}: {b!r} is not a definition box on {cat.form}" for b in dict.fromkeys(bad)]
        if len(set(boxes)) != len(boxes):
            problems.append(f"set {i}: a definition box is listed twice")
        count = raw.get("request_count")
        try:
            validate(raw["checked"], count if isinstance(count, int) else -1, cat)
        except CheckedError as exc:
            problems += [f"set {i}: {p}" for p in exc.problems]
    if len(problems) != before:
        return {}
    if operator and raw["additions"]:
        problems.append(
            f'set {i}: a form interrogatory set with additions attached is drafted by SMD; mark it "drafted_by": "smd"'
        )
    return {
        "form": form,
        "form_revision": rev,
        "checked": tuple(raw["checked"]),
        "definition_boxes": tuple(raw["definition_boxes"]),
        "additions": raw["additions"],
    }


def template() -> dict[str, Any]:
    """A form set's keys in the CLI's template, every value to replace."""
    return {
        "set_type": FORM_TYPE,
        "set_number": 1,
        "propounding_party": "<the party that served it, as the set names it>",
        "responding_party": "<our client, as the set names it>",
        "request_count": 2,
        "request_text_path": "<a file beside this one holding the served form's text>",
        "drafted_by": "smd",
        "form": catalog.DISC_001,
        "form_revision": catalog.CURRENT,
        "checked": ["1.1", "2.1"],
        "definition_boxes": ["4(a)(1)"],
        "additions": False,
    }
