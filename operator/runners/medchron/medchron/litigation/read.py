"""The three reads of one matter, and how their answers become state.

* **Pass 1, extract** (``models.read``): fill the result for the field groups
  this run must determine, with a built-in negative check, every value cited
  to a doc number.
* **Pass 2, verify** (``models.verify``): an independent read that re-checks
  every negative and every positive date against its source, reads the
  newest emails itself (settlement language changes the case status), and
  opens any paper it names. Its overturns are applied by code.
* **Pass 3, audit** (``models.audit``): before delivery, open the cited source
  of every value that CHANGED this run (every value, for a new matter or a
  seed that had only two passes); checkbox facts from the page image only;
  every quote and its sender checked; every settlement-scan hit resolved.

The model cites by ``[doc N]``; code maps N to ``{file_id, name, doc_date}``.
A value whose doc number does not resolve keeps no source, which the gates
refuse. Nothing a model writes becomes the responsible person or a file id.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
from typing import Any

from . import vocab
from .tools import MatterContext, run_loop

_REF = {"doc": {"type": ["integer", "null"]}, "doc_date": {"type": ["string", "null"]}}
_DATED = {"type": "object", "properties": {"date": {"type": ["string", "null"]}, **_REF}}
RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "case_name": {"type": ["string", "null"]},
        "court": {"type": ["string", "null"]},
        "case_number": {"type": ["string", "null"]},
        "firm_role": {"type": "string", "enum": list(vocab.FIRM_ROLES)},
        "case_status": {
            "type": "object",
            "properties": {
                "value": {"type": "string", "enum": list(vocab.CASE_STATUSES)},
                "detail": {"type": "string"},
                **_REF,
            },
        },
        "complaint_filed": _DATED,
        "next_court_date": {
            "type": "object",
            "properties": {"date": {"type": ["string", "null"]}, "event": {"type": "string"}, **_REF},
        },
        "defendants": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "status": {"type": "string", "enum": list(vocab.DEFENDANT_STATUSES)},
                    "out_for_service": {
                        "type": "object",
                        "properties": {"value": {"type": "string", "enum": ["yes", "no", "unknown"]}, **_REF},
                    },
                    "served": {
                        "type": "object",
                        "properties": {
                            "date": {"type": ["string", "null"]},
                            "method": {"type": ["string", "null"]},
                            **_REF,
                        },
                    },
                    "answered": _DATED,
                    "flags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "status"],
            },
        },
        "discovery_propounded": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "set": {"type": "string"},
                    "served_on": {"type": "string"},
                    "date": {"type": ["string", "null"]},
                    **_REF,
                },
            },
        },
        "discovery_served_on_client": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "set": {"type": "string"},
                    "served_by": {"type": "string"},
                    "date": {"type": ["string", "null"]},
                    **_REF,
                    "responses_served": {
                        "type": "object",
                        "properties": {
                            "value": {"type": "string", "enum": ["yes", "no", "unclear"]},
                            "date": {"type": ["string", "null"]},
                            **_REF,
                        },
                    },
                },
            },
        },
        "matter_flags": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
}
RECORD_RESULT = {
    "name": "record_result",
    "description": "Record the matter's result. Call once, at the end.",
    "input_schema": RESULT_SCHEMA,
}
_VERDICT = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "verdict": {"type": "string", "enum": ["confirmed", "overturned", "unclear"]},
        "corrected": {},
        "why": {"type": "string"},
        **_REF,
    },
    "required": ["path", "verdict"],
}
RECORD_VERDICTS = {
    "name": "record_verdicts",
    "description": "Record a verdict for every value you checked. Call once, at the end.",
    "input_schema": {
        "type": "object",
        "properties": {
            "verdicts": {"type": "array", "items": _VERDICT},
            "settlement_reviewed": {
                "type": "array",
                "items": {"type": "object", "properties": {"doc": {"type": "integer"}, "finding": {"type": "string"}}},
            },
        },
        "required": ["verdicts"],
    },
}

PATHS = (
    "case_status | complaint_filed | next_court_date | case_name | court | case_number | firm_role | "
    "defendants[<exact name>].status | defendants[<exact name>].served | defendants[<exact name>].answered | "
    "defendants[<exact name>].out_for_service | defendants[+] (a defendant the result is missing; corrected is the "
    "whole defendant) | discovery_propounded | discovery_served_on_client (corrected is the whole list)"
)

COMMON = """You read one litigation matter's documents in a law firm's case-management system and report facts.

RULES. Every value comes from a document you opened, cited by its doc number. "Nothing in the file" is a valid
finding. Never guess a date, a name or a party. A date is the court's or the event's date printed in the
document (the file stamp, the e-filing receipt, the date of service, the hearing date), NEVER the "Smokeball
saved" date in the file list. Facts that rest on a checkbox come only from view_page, never from text. Before you
record any negative (not served, no answer, not dismissed, not filed, active), search for what would contradict
it: answers (including inside emails), dismissals, proofs of service and process-server emails, stipulations and
extensions, defaults, case management statements newer than your finding, settlement emails ("settled",
"release", "W-9", "settlement check", "notice of settlement"). Settlement language in an email changes the case
status. Take defendants from the complaint caption and any amendments; a defendant dropped by an amended
complaint is "{dropped}". Never list the firm's own client as a defendant unless the firm is defense counsel, in
which case firm_role is "defense" and the client is "{firm_client}". An uninsured/underinsured motorist arbitration
respondent is "{uim}", not a court defendant. Use only the listed statuses. Write plain sentences for flags and
notes: facts only, no legal advice, no doc numbers, no file ids.

Today is {today}. A next court date must be the next one AFTER today; if none is set in the file, leave its date
null. Discovery: list each set the firm propounded (what, on whom, the date served) and each set served on the
firm's client (what, by whom, the date served, and whether responses were served, with their date).

The court's forms in this firm's files:
{hints}"""


def _hints(firm: Any) -> str:
    return "\n".join(f"- {k}: {v}" for k, v in sorted(firm.data["form_hints"].items())) or "- (none authored)"


def system_for(role: str, firm: Any, today: dt.date) -> str:
    base = COMMON.format(
        today=today.isoformat(), hints=_hints(firm), dropped=vocab.DROPPED, firm_client=vocab.FIRM_CLIENT, uim=vocab.UIM
    )
    tail = {
        "read": "\n\nYOUR PASS: determine the field groups you are asked for, then call record_result.",
        "verify": (
            "\n\nYOUR PASS: an independent check of another reader's result. Check EVERY negative and EVERY positive date "
            "against its cited source, and read the newest emails listed yourself. Open any paper the result should have "
            "considered. A document you have not opened is not absent: open it. Paths you may correct: "
            + PATHS
            + ". Then call record_verdicts with one verdict per value checked."
        ),
        "audit": (
            "\n\nYOUR PASS: the last check before the list goes to the firm. For every value you are asked to audit, open the "
            "cited source yourself; the date must be the court or event date in the document. Checkbox facts: view_page, and "
            "if you cannot see it the verdict is unclear. Every quote in a flag or note must appear verbatim in its source, "
            "with the right sender. Resolve every settlement-scan hit you are given in settlement_reviewed (doc + one line). "
            "Paths you may correct: " + PATHS + ". Then call record_verdicts."
        ),
    }[role]
    return base + tail


def file_list(ctx: MatterContext, candidates: list[str], newest: list[str]) -> str:
    shown = [ctx.by_id[c] for c in candidates if c in ctx.by_id]
    lines = [
        ctx.line(n) + ("  (one of the newest emails)" if str(ctx.refs[n]["id"]) in newest else "")
        for n in sorted(shown)
    ]
    rest = len(ctx.refs) - len(shown)
    return "\n".join(lines) + f"\n\n{rest} other files are on the matter; list_files shows them by name."


def ask(header: str, groups: list[str], listing: str, extra: str = "") -> str:
    want = ", ".join(groups)
    return f"MATTER: {header}\nFIELD GROUPS TO DETERMINE: {want}\n\nFILES (court papers, service records, recent emails):\n{listing}\n{extra}"


# ---- model answer -> state ---------------------------------------------------------
def _val(ctx: MatterContext, v: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    v = v if isinstance(v, dict) else {}
    out = {k: v.get(k) for k in keys}
    out["source"] = ctx.source(v.get("doc"), v.get("doc_date"))
    return out


def _defendant(ctx: MatterContext, d: Any) -> dict[str, Any]:
    d = d if isinstance(d, dict) else {}
    status = d.get("status") if d.get("status") in vocab.DEFENDANT_STATUSES else vocab.D_UNCLEAR
    return {
        "name": str(d.get("name") or "").strip(),
        "status": status,
        "out_for_service": _val(ctx, d.get("out_for_service"), ("value",)),
        "served": _val(ctx, d.get("served"), ("date", "method")),
        "answered": _val(ctx, d.get("answered"), ("date",)),
        "flags": [str(x) for x in d.get("flags") or []],
    }


def _discovery(ctx: MatterContext, rows: Any, theirs: bool) -> list[dict[str, Any]]:
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        row = _val(ctx, r, ("set", "served_by", "date") if theirs else ("set", "served_on", "date"))
        if theirs:
            row["responses_served"] = _val(ctx, r.get("responses_served"), ("value", "date"))
        out.append(row)
    return out


def to_state(ctx: MatterContext, raw: dict[str, Any], groups: list[str]) -> dict[str, Any]:
    """The model's answer for ``groups`` in the state schema."""
    out: dict[str, Any] = {}
    if vocab.GROUP_CASE in groups:
        cs_raw = raw.get("case_status")
        cs: dict[str, Any] = cs_raw if isinstance(cs_raw, dict) else {}
        out.update(
            case_name=raw.get("case_name"),
            court=raw.get("court"),
            case_number=raw.get("case_number"),
            firm_role=raw.get("firm_role") if raw.get("firm_role") in vocab.FIRM_ROLES else "plaintiff",
            case_status={
                **_val(ctx, cs, ("value", "detail")),
                "value": cs.get("value") if cs.get("value") in vocab.CASE_STATUSES else vocab.UNCLEAR,
            },
            complaint_filed=_val(ctx, raw.get("complaint_filed"), ("date",)),
            next_court_date=_val(ctx, raw.get("next_court_date"), ("date", "event")),
        )
    if vocab.GROUP_DEFENDANTS in groups:
        out["defendants"] = [_defendant(ctx, d) for d in raw.get("defendants") or [] if isinstance(d, dict)]
    if vocab.GROUP_DISCOVERY in groups:
        out["discovery_propounded"] = _discovery(ctx, raw.get("discovery_propounded"), False)
        out["discovery_served_on_client"] = _discovery(ctx, raw.get("discovery_served_on_client"), True)
    out["matter_flags"] = [str(x) for x in raw.get("matter_flags") or []]
    if raw.get("notes"):
        out["notes"] = str(raw["notes"])
    return out


def merge(prior: dict[str, Any] | None, fresh: dict[str, Any], groups: list[str]) -> dict[str, Any]:
    """The fresh groups over the prior result; flags and notes from this read
    replace the prior's only when the whole matter was read."""
    base = copy.deepcopy(prior) if prior else {}
    for g in groups:
        for k in vocab.GROUP_FIELDS[g]:
            if k in fresh:
                base[k] = fresh[k]
    full = set(groups) == set(vocab.GROUPS)
    if full or not base.get("matter_flags"):
        base["matter_flags"] = fresh.get("matter_flags", [])
    else:
        base["matter_flags"] = list(dict.fromkeys([*base.get("matter_flags", []), *fresh.get("matter_flags", [])]))
    if fresh.get("notes") and (full or not base.get("notes")):
        base["notes"] = fresh["notes"]
    base["fields_read"] = sorted(set(base.get("fields_read") or []) | set(groups))
    return base


# ---- verdicts ------------------------------------------------------------------------
def _find_defendant(result: dict[str, Any], name: str) -> dict[str, Any] | None:
    key = " ".join(name.lower().split())
    return next(
        (d for d in result.get("defendants") or [] if " ".join(str(d.get("name")).lower().split()) == key), None
    )


def _corrected_value(ctx: MatterContext, field: str, v: dict[str, Any]) -> Any:
    c = v.get("corrected")
    ref = {"doc": v.get("doc"), "doc_date": v.get("doc_date")}
    if field in ("case_name", "court", "case_number"):
        return c if isinstance(c, str) else None
    if field == "firm_role":
        return c if c in vocab.FIRM_ROLES else None
    if field == "status":
        return c if c in vocab.DEFENDANT_STATUSES else None
    if field == "case_status":
        val = c.get("value") if isinstance(c, dict) else c
        return (
            {
                "value": val,
                "detail": (c or {}).get("detail") if isinstance(c, dict) else v.get("why"),
                "source": ctx.source(ref["doc"], ref["doc_date"]),
            }
            if val in vocab.CASE_STATUSES
            else None
        )
    if field in ("discovery_propounded", "discovery_served_on_client"):
        return _discovery(ctx, c, field == "discovery_served_on_client") if isinstance(c, list) else None
    keys = {
        "served": ("date", "method"),
        "answered": ("date",),
        "out_for_service": ("value",),
        "complaint_filed": ("date",),
        "next_court_date": ("date", "event"),
    }.get(field)
    if keys is None:
        return None
    c = c if isinstance(c, dict) else {"date": c} if isinstance(c, str) or c is None else {}
    return _val(ctx, {**ref, **c}, keys)


def apply_verdicts(
    ctx: MatterContext, result: dict[str, Any], verdicts: list[dict[str, Any]], pass_name: str
) -> list[dict[str, Any]]:
    """Apply every overturn (and every unclear status) in place. Returns the
    overturn log: path, old, new, the source the overturn cites, why."""
    log: list[dict[str, Any]] = []
    for v in verdicts if isinstance(verdicts, list) else []:
        if not isinstance(v, dict) or v.get("verdict") not in ("overturned", "unclear"):
            continue
        path = str(v.get("path") or "")
        if path == "defendants[+]":
            d = _defendant(ctx, v.get("corrected"))
            if d["name"] and _find_defendant(result, d["name"]) is None:
                result.setdefault("defendants", []).append(d)
                log.append(
                    {
                        "path": f"defendants[{d['name']}]",
                        "old": None,
                        "new": d,
                        "source": ctx.source(v.get("doc"), v.get("doc_date")),
                        "why": v.get("why"),
                        "pass": pass_name,
                    }
                )
            continue
        target, field = result, path
        if path.startswith("defendants[") and "]." in path:
            name, field = path[len("defendants[") :].split("].", 1)
            target = _find_defendant(result, name)  # type: ignore[assignment]
            if target is None:
                continue
        if v["verdict"] == "unclear":
            new: Any = (
                vocab.D_UNCLEAR
                if field == "status"
                else (
                    {"value": vocab.UNCLEAR, "detail": v.get("why"), "source": None} if field == "case_status" else None
                )
            )
            if new is None:
                target.setdefault("flags" if target is not result else "matter_flags", []).append(
                    f"Unclear: {v.get('why') or path}"
                )
                continue
        else:
            new = _corrected_value(ctx, field, v)
            if new is None:
                continue
        old = copy.deepcopy(target.get(field))
        if old == new:
            continue
        target[field] = new
        log.append(
            {
                "path": path,
                "old": old,
                "new": new,
                "source": ctx.source(v.get("doc"), v.get("doc_date")),
                "why": v.get("why"),
                "pass": pass_name,
            }
        )
    return log


# ---- the passes ------------------------------------------------------------------------
def pass1(
    doorway: Any, firm: Any, ctx: MatterContext, header: str, groups: list[str], listing: str, today: dt.date
) -> dict[str, Any]:
    raw = run_loop(
        doorway,
        "litigation_read",
        model=firm.model("read"),
        system=system_for("read", firm, today),
        user=ask(header, groups, listing),
        ctx=ctx,
        final_tool=RECORD_RESULT,
        max_iterations=int(firm.get("tool_iterations")),
    )
    return to_state(ctx, raw, groups)


def _for_model(result: dict[str, Any], ctx: MatterContext) -> str:
    """The result with sources shown as doc numbers (what the reader can open)."""

    def conv(x: Any) -> Any:
        if isinstance(x, dict):
            out = {k: conv(v) for k, v in x.items() if k not in ("source", "provenance", "overturns", "integrity")}
            src = x.get("source")
            if isinstance(src, dict):
                out["doc"] = ctx.by_id.get(str(src.get("file_id")))
            return out
        return [conv(v) for v in x] if isinstance(x, list) else x

    return json.dumps(conv(result), indent=1)


def check_pass(
    doorway: Any,
    firm: Any,
    ctx: MatterContext,
    role: str,
    header: str,
    result: dict[str, Any],
    listing: str,
    today: dt.date,
    extra: str,
) -> dict[str, Any]:
    stage, model = (
        ("litigation_verify", firm.model("verify")) if role == "verify" else ("litigation_audit", firm.model("audit"))
    )
    user = ask(header, list(vocab.GROUPS), listing, f"\nTHE RESULT TO CHECK:\n{_for_model(result, ctx)}\n{extra}")
    got = run_loop(
        doorway,
        stage,
        model=model,
        system=system_for(role, firm, today),
        user=user,
        ctx=ctx,
        final_tool=RECORD_VERDICTS,
        max_iterations=int(firm.get("tool_iterations")),
    )
    return {"verdicts": got.get("verdicts") or [], "settlement_reviewed": got.get("settlement_reviewed") or []}
