"""The text checkers wave 2 folds into one set of text rules, and how their inputs are stored.

``TARGETS`` names every checker the text-rule goldens pin: date reading and
writing, money, citations, "the system's own words", markers, garbled-text and
text-layer tests, RTF/HTML stripping. Each is called by its module and name,
so the goldens run the real code. Their inputs are what the existing tests
feed them (``text_rule_harvest.py`` records it) plus one fictional sample
deliverable (``test_text_rule_goldens.py``).

Inputs are stored in a small tagged JSON form so they can be replayed exactly:
a tuple, a dict, a date, a set, bytes and a citation ``Policy`` each keep their
type. A call whose arguments cannot be stored that way (a path, an open file,
a live object) is not recorded.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
from typing import Any, Callable

#: (module, function). Only checkers whose arguments are plain values.
TARGETS: tuple[tuple[str, str], ...] = (
    ("jobs.proof.checks", "system_vocabulary"),
    ("jobs.proof.checks", "extraction_vocabulary"),
    ("jobs.proof.checks", "sentinels"),
    ("jobs.proof.checks", "money"),
    ("jobs.proof.checks", "money_in_sources"),
    ("jobs.proof.checks", "money_not_in"),
    ("jobs.proof.checks", "dates"),
    ("jobs.proof.checks", "dates_not_in"),
    ("jobs.proof.checks", "future"),
    ("jobs.proof.checks", "fmt_cents"),
    ("jobs.proof.review", "parse"),
    ("jobs.proof.review", "complete"),
    ("jobs.drafting.citations", "kind_of"),
    ("jobs.drafting.citations", "validate"),
    ("jobs.drafting.citations", "policy_for"),
    ("jobs.drafting.citations", "apply"),
    ("jobs.drafting.citations", "check"),
    ("jobs.demand.gapaudit", "firm_words"),
    ("jobs.demand.review", "firm_banned"),
    ("jobs.demand.review", "malformed_markers"),
    ("jobs.demand.review", "wording"),
    ("jobs.stages.review", "_system_findings"),
    ("jobs.stages.review", "_as_date"),
    ("jobs.stages.review", "_date_forms"),
    ("jobs.stages.review", "heading_findings"),
    ("jobs.stages.extract", "not_english"),
    ("jobs.stages.extract", "glyph_junk"),
    ("jobs.stages.extract", "thin"),
    ("jobs.stages.extract", "garbled"),
    ("jobs.stages.extract", "_ocr_tells"),
    ("jobs.litigation.extract", "garbled"),
    ("jobs.litigation.extract", "strip_rtf"),
    ("jobs.litigation.extract", "rtf_text"),
    ("jobs.litigation.extract", "ocr_pages"),
    ("jobs.litigation.extract", "html_text"),
    ("jobs.negotiation.text", "ocr_pages"),
    ("jobs.negotiation.text", "html_text"),
    ("jobs.wording", "fill"),
    ("jobs.wording", "subjects"),
    ("jobs.wording", "names_records_differ"),
)
#: A stored call's arguments may not exceed this many characters of JSON.
MAX_ARG_CHARS = 10_000
#: At most this many distinct calls per checker are kept (the first by sha).
MAX_CALLS = 200


def modules() -> dict[str, Any]:
    """The target modules, each imported by name here (never a computed import)."""
    from jobs import wording
    from jobs.demand import gapaudit, review as demand_review
    from jobs.drafting import citations
    from jobs.litigation import extract as litigation_extract
    from jobs.negotiation import text as negotiation_text
    from jobs.proof import checks, review as proof_review
    from jobs.stages import extract as stages_extract, review as stages_review

    got = {
        "jobs.proof.checks": checks,
        "jobs.proof.review": proof_review,
        "jobs.drafting.citations": citations,
        "jobs.demand.gapaudit": gapaudit,
        "jobs.demand.review": demand_review,
        "jobs.stages.review": stages_review,
        "jobs.stages.extract": stages_extract,
        "jobs.litigation.extract": litigation_extract,
        "jobs.negotiation.text": negotiation_text,
        "jobs.wording": wording,
    }
    assert set(got) == {m for m, _ in TARGETS}
    return got


def module(name: str) -> Any:
    return modules()[name]


def target(mod: str, fn: str) -> Callable[..., Any]:
    return getattr(module(mod), fn)


class Unstorable(TypeError):
    pass


def encode(x: Any) -> Any:
    from jobs.drafting.citations import Policy

    if x is None or isinstance(x, (bool, int, float, str)):
        return x
    if isinstance(x, list):
        return [encode(v) for v in x]
    if isinstance(x, tuple):
        return {"__tuple": [encode(v) for v in x]}
    if isinstance(x, dict):
        if not all(isinstance(k, str) for k in x):
            raise Unstorable("a map with non-string keys")
        return {"__dict": {k: encode(v) for k, v in x.items()}}
    if isinstance(x, dt.datetime):
        raise Unstorable("a datetime")
    if isinstance(x, dt.date):
        return {"__date": x.isoformat()}
    if isinstance(x, (set, frozenset)):
        items = [encode(v) for v in x]
        return {"__frozenset" if isinstance(x, frozenset) else "__set": sorted(items, key=json.dumps)}
    if isinstance(x, (bytes, bytearray)):
        return {"__b64": base64.b64encode(bytes(x)).decode("ascii")}
    if isinstance(x, Policy):
        return {"__policy": sorted(x.allow)}
    raise Unstorable(type(x).__name__)


def decode(x: Any) -> Any:
    from jobs.drafting.citations import Policy

    if isinstance(x, list):
        return [decode(v) for v in x]
    if isinstance(x, dict):
        if len(x) != 1:
            raise ValueError(f"untagged map in a stored input: {sorted(x)}")
        tag, body = next(iter(x.items()))
        if tag == "__tuple":
            return tuple(decode(v) for v in body)
        if tag == "__dict":
            return {k: decode(v) for k, v in body.items()}
        if tag == "__date":
            return dt.date.fromisoformat(body)
        if tag == "__frozenset":
            return frozenset(decode(v) for v in body)
        if tag == "__set":
            return {decode(v) for v in body}
        if tag == "__b64":
            return base64.b64decode(body)
        if tag == "__policy":
            return Policy(frozenset(body))
        raise ValueError(f"untagged map in a stored input: {sorted(x)}")
    return x


def call(mod: str, fn: str, stored: dict[str, Any]) -> Any:
    """The checker's answer for one stored call, as plain JSON (or what it raised)."""
    import goldens_kit as gk

    args = decode(stored.get("args") or [])
    kwargs = decode(stored.get("kwargs") or {"__dict": {}})
    try:
        return {"out": gk.plain(target(mod, fn)(*args, **kwargs))}
    except Exception as exc:  # noqa: BLE001 - a checker that raises on an input is a fact to pin
        return {"raises": f"{type(exc).__name__}: {exc}"}
