"""The twelve sections: the closed vocabulary, the model's result schema, its
answer in the report's shape, the checks' verdicts, and the code gates.

The model cites by doc number; code maps it to ``{file_id, name, doc_date}``
(``MatterContext.source``). A date with no resolvable source, or one the cited
document's text does not carry, is a gate finding: the report holds.
"""

from __future__ import annotations

import copy
import datetime as dt
from typing import Any, Callable

from .. import gates

SECTIONS = (
    ("frogs", "Form interrogatories (FROGs)"),
    ("srogs", "Special interrogatories (SROGs)"),
    ("rfps", "Requests for production (RFPs)"),
    ("rfas", "Requests for admission (RFAs)"),
    ("depositions", "Depositions"),
    ("nonparty_subpoenas", "Nonparty deposition subpoenas"),
    ("exams", "Physical and mental examinations"),
    ("expert", "Expert discovery"),
    ("esi", "Electronically stored information (ESI)"),
    ("inspection", "Inspection and testing"),
    ("initial_disclosures", "Initial disclosures"),
    ("supplemental", "Supplemental responses"),
)
KEYS = tuple(k for k, _l in SECTIONS)
LABEL = dict(SECTIONS)
DIRECTIONS = ("propounded by the firm", "served on the firm's client", "other")
RESPONSES = ("yes", "no", "unclear", "not yet due")
NONE_IN_FILE = "None in file"
#: The dated fields of one entry, in print order.
DATED = ("served", "due", "response", "extension")

_REF = {"doc": {"type": ["integer", "null"]}, "doc_date": {"type": ["string", "null"]}}
_DATE = {"type": "object", "properties": {"date": {"type": ["string", "null"]}, **_REF}}
ENTRY = {
    "type": "object",
    "properties": {
        "item": {"type": "string"},
        "direction": {"type": "string", "enum": list(DIRECTIONS)},
        "party": {"type": "string"},
        "served": _DATE,
        "due": _DATE,
        "response": {
            "type": "object",
            "properties": {
                "value": {"type": "string", "enum": list(RESPONSES)},
                "date": {"type": ["string", "null"]},
                **_REF,
            },
        },
        "extension": {
            "type": "object",
            "properties": {"date": {"type": ["string", "null"]}, "detail": {"type": "string"}, **_REF},
        },
        "notes": {"type": "string"},
    },
    "required": ["item", "direction"],
}
RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "sections": {"type": "object", "properties": {k: {"type": "array", "items": ENTRY} for k in KEYS}},
        "flags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["sections"],
}
RECORD_RESULT = {
    "name": "record_discovery",
    "description": "Record the case's discovery in the twelve sections. Call once, at the end.",
    "input_schema": RESULT_SCHEMA,
}
RECORD_VERDICTS = {
    "name": "record_verdicts",
    "description": "Record one verdict per section you checked. Call once, at the end.",
    "input_schema": {
        "type": "object",
        "properties": {
            "verdicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "section": {"type": "string", "enum": list(KEYS)},
                        "verdict": {"type": "string", "enum": ["confirmed", "overturned", "unclear"]},
                        "corrected": {"type": "array", "items": ENTRY},
                        "why": {"type": "string"},
                    },
                    "required": ["section", "verdict"],
                },
            }
        },
        "required": ["verdicts"],
    },
}

RULES = """You read one litigation case's discovery papers in a law firm's case-management system and report
facts, section by section, in these twelve sections: {sections}.

RULES. Every value comes from a document you opened, cited by its doc number. "Nothing in the file" is a valid
finding: an empty section is an empty list. Never guess a date, a party or a response. A date is the date printed
in the document (the proof of service date, the date served, the response's own date, the stipulation's date),
NEVER the "Smokeball saved" date in the file list. One entry per set or event (Form Interrogatories, Set One
served on one party is one entry). For each entry record: the item, its direction (one of: {directions}), the
other party (who served it, or on whom it was served), served (date), due (the response due date as the papers
or a stipulation state it; null when no document states it), response (whether a response was served: one of
{responses}, and its date), extension (a stipulated or granted extension, its date and what it extends to), and
plain notes. Supplemental and amended responses go under supplemental, naming the set they supplement. A
nonparty deposition subpoena (records or testimony) goes under nonparty_subpoenas, not depositions. Before you
record "no" response or an empty section, search the file for what would contradict it, including emails.
Write plain sentences: facts only, no legal advice, no doc numbers, no file ids. Today is {today}."""

TAILS = {
    "read": "\n\nYOUR PASS: determine all twelve sections, then call record_discovery.",
    "verify": (
        "\n\nYOUR PASS: an independent check of another reader's result. Check EVERY date against its cited source, "
        "every empty section and every 'no' response against the file (open what the result should have considered). "
        "A section you correct is replaced whole: corrected is the section's full list. Then call record_verdicts."
    ),
    "audit": (
        "\n\nYOUR PASS: the last check before the report goes to the firm, after a verify pass confirmed the dates. "
        "Audit ONLY every empty section, every 'no' or 'unclear' response, and any contradiction between documents. "
        "A section you correct is replaced whole. Then call record_verdicts."
    ),
}


def system_for(role: str, today: dt.date) -> str:
    return (
        RULES.format(
            sections="; ".join(f"{k} ({v})" for k, v in SECTIONS),
            directions="; ".join(f'"{d}"' for d in DIRECTIONS),
            responses="; ".join(f'"{r}"' for r in RESPONSES),
            today=today.isoformat(),
        )
        + TAILS[role]
    )


# ---- the model's answer -> the report's shape ------------------------------------------------
def _dated(src: Callable[[Any, Any], Any], v: Any, extra: tuple[str, ...] = ()) -> dict[str, Any]:
    v = v if isinstance(v, dict) else {}
    out = {k: v.get(k) for k in ("date", *extra)}
    out["source"] = src(v.get("doc"), v.get("doc_date"))
    return out


def entry(src: Callable[[Any, Any], Any], e: Any) -> dict[str, Any] | None:
    if not isinstance(e, dict) or not str(e.get("item") or "").strip():
        return None
    resp = _dated(src, e.get("response"), ("value",))
    if resp.get("value") not in RESPONSES:
        resp["value"] = "unclear"
    return {
        "item": str(e["item"]).strip(),
        "direction": e.get("direction") if e.get("direction") in DIRECTIONS else "other",
        "party": str(e.get("party") or "").strip(),
        "served": _dated(src, e.get("served")),
        "due": _dated(src, e.get("due")),
        "response": resp,
        "extension": _dated(src, e.get("extension"), ("detail",)),
        "notes": str(e.get("notes") or "").strip(),
    }


def section_list(src: Callable[[Any, Any], Any], rows: Any) -> list[dict[str, Any]]:
    return [x for x in (entry(src, e) for e in (rows if isinstance(rows, list) else [])) if x is not None]


def to_result(src: Callable[[Any, Any], Any], raw: dict[str, Any]) -> dict[str, Any]:
    got = raw.get("sections")
    secs: dict[str, Any] = got if isinstance(got, dict) else {}
    return {
        "sections": {k: section_list(src, secs.get(k)) for k in KEYS},
        "flags": [str(x) for x in raw.get("flags") or []],
    }


def apply_verdicts(
    src: Callable[[Any, Any], Any], result: dict[str, Any], verdicts: Any, pass_name: str
) -> list[dict[str, Any]]:
    """An overturn replaces its section whole; an unclear one becomes a flag.
    Returns the overturn log."""
    log: list[dict[str, Any]] = []
    for v in verdicts if isinstance(verdicts, list) else []:
        if not isinstance(v, dict) or v.get("section") not in KEYS:
            continue
        key = v["section"]
        if v.get("verdict") == "unclear":
            result.setdefault("flags", []).append(f"Unclear ({LABEL[key]}): {v.get('why') or 'see the file'}")
        elif v.get("verdict") == "overturned" and isinstance(v.get("corrected"), list):
            new = section_list(src, v["corrected"])
            old = copy.deepcopy(result["sections"].get(key) or [])
            if new != old:
                result["sections"][key] = new
                log.append({"section": key, "old": old, "new": new, "why": v.get("why"), "pass": pass_name})
    return log


# ---- the code gates ------------------------------------------------------------------------------
def dated_values(result: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """(readable path, value) for every date the result shows."""
    out = []
    for key in KEYS:
        for i, e in enumerate(result.get("sections", {}).get(key) or []):
            for f in DATED:
                v = e.get(f) or {}
                if v.get("date"):
                    out.append((f"{key}[{i}].{f}", v))
    return out


#: What a date the gates could not confirm prints instead of the date
#: (2026-10-29): an unconfirmed date is never shown as fact, and one such value
#: no longer holds the whole report (the 10-23 list held on 34 of them, 11 of
#: which the paper did carry in a form the matcher missed).
UNCONFIRMED = "date not confirmed in the cited paper"
#: The share of a report's dates that may be unconfirmed before it holds.
MAX_UNCONFIRMED_SHARE = 0.10


def unconfirm(result: dict[str, Any], paths: set[str]) -> int:
    """Move each named value's date out of ``date`` (kept as
    ``unconfirmed_date``, marked ``unconfirmed``); returns how many moved."""
    n = 0
    for key in KEYS:
        for i, e in enumerate(result.get("sections", {}).get(key) or []):
            for f in DATED:
                v = e.get(f)
                if isinstance(v, dict) and v.get("date") and f"{key}[{i}].{f}" in paths:
                    v["unconfirmed_date"], v["date"], v["unconfirmed"] = v["date"], None, True
                    n += 1
    return n


def findings(
    number: str, result: dict[str, Any], text_of: Callable[[str], str | None], on_matter: set[str]
) -> list[tuple[str, str, str]]:
    """(matter #, path, problem) for every date that is not an ISO date, has
    no source on the matter, or is not in its cited document's text."""
    out = []
    for path, v in dated_values(result):
        d = str(v.get("date"))
        try:
            dt.date.fromisoformat(d)
        except ValueError:
            out.append((number, path, "not a date"))
            continue
        fid = str((v.get("source") or {}).get("file_id") or "")
        if not fid or fid not in on_matter:
            out.append((number, path, "no source on the matter"))
            continue
        text = text_of(fid)
        if text is None or not gates.text_carries(text, d):
            out.append((number, path, "date not in the cited document"))
    return out
