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
refuse. Nothing a model writes becomes the responsible person or a file id, or
a defendant's status: the reads report dated events and code derives the status
(``answers``, ``defstatus.derive``); their verdicts are applied in ``verdicts``.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
from typing import Any

from . import vocab
from .answers import merge, to_state  # noqa: F401 - ``read.merge``, ``read.to_state``: callers read them here
from .tools import MatterContext, run_loop
from .verdicts import apply_verdicts  # noqa: F401 - ``read.apply_verdicts``: callers read it here

_REF = {"doc": {"type": ["integer", "null"]}, "doc_date": {"type": ["string", "null"]}}
_DATED = {"type": "object", "properties": {"date": {"type": ["string", "null"]}, **_REF}}
_OTHER_EVENTS = tuple(f for f in vocab.DEFENDANT_EVENTS if f not in ("served", "answered", "sent_for_service"))
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
        "court_dates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"date": {"type": ["string", "null"]}, "event": {"type": "string"}, **_REF},
            },
        },
        "defendants": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "role": {
                        "type": "object",
                        "properties": {"value": {"type": "string", "enum": list(vocab.DEFENDANT_ROLES)}, **_REF},
                    },
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
                    "sent_for_service": {"type": "array", "items": _DATED},
                    **{f: _DATED for f in _OTHER_EVENTS},
                    "flags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name"],
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


def _observe_schema() -> dict[str, Any]:
    """What a read returns while the seat's switch is ``observe``: every field
    the list prints as it always was, a stated status and a next court date
    among them, and the events beside them for the shadow record."""
    schema = copy.deepcopy(RESULT_SCHEMA)
    props = schema["properties"]
    del props["court_dates"]
    props["next_court_date"] = {
        "type": "object",
        "properties": {"date": {"type": ["string", "null"]}, "event": {"type": "string"}, **_REF},
    }
    items = props["defendants"]["items"]
    items["properties"]["status"] = {"type": "string", "enum": list(vocab.DEFENDANT_STATUSES)}
    items["properties"]["sent_for_service"] = _DATED
    items["required"] = ["name", "status"]
    return schema


OBSERVE_SCHEMA = _observe_schema()
RECORD_RESULT_OBSERVE = {**RECORD_RESULT, "input_schema": OBSERVE_SCHEMA}
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
    "case_status | complaint_filed | court_dates (corrected is the whole list of court dates that still stand) | "
    "case_name | court | case_number | firm_role | defendants[<index>].<event>, where <index> is the defendant's "
    "index in the result and <event> is one of " + ", ".join(vocab.DEFENDANT_EVENTS) + " | "
    "defendants[<index>].out_for_service | defendants[<index>].role | defendants[+] (a defendant the result is "
    "missing; corrected is the whole defendant) | discovery_propounded | discovery_served_on_client (corrected is the "
    "whole list). A defendant's status is worked out by code from its events: to change one, correct the event. If the "
    "papers cannot settle it, record defendants[<index>].status with verdict unclear."
)

PATHS_OBSERVE = (
    "case_status | complaint_filed | next_court_date | case_name | court | case_number | firm_role | "
    "defendants[<exact name>].status | defendants[<exact name>].served | defendants[<exact name>].answered | "
    "defendants[<exact name>].out_for_service | defendants[<exact name>].sent_for_service | defendants[+] (a defendant the result is missing; corrected is the "
    "whole defendant) | discovery_propounded | discovery_served_on_client (corrected is the whole list) | "
    "defendants[<exact name>].<event>, for a dated event below, or .role"
)

COMMON_OBSERVE = """You read one litigation matter's documents in a law firm's case-management system and report facts.

RULES. Every value comes from a document you opened, cited by its doc number. "Nothing in the file" is a valid
finding. Never guess a date, a name or a party. A date is the court's or the event's date printed in the
document (the file stamp, the e-filing receipt, the date of service, the hearing date), NEVER the "Smokeball
saved" date in the file list. Facts that rest on a checkbox come only from view_page, never from text. Before you
record any negative (not served, no answer, not dismissed, not filed, active), search for what would contradict
it: answers (including inside emails), dismissals, proofs of service and process-server emails, stipulations and
extensions, defaults, case management statements newer than your finding, settlement emails ("settled",
"release", "W-9", "settlement check", "notice of settlement"). Settlement language in an email changes the case
status. Take defendants from the complaint caption and any amendments, and add any party a proof of service, a
Doe amendment or a case management statement names as a defendant. Record each defendant's served and answered
dates whatever its status now (a dismissed or settled defendant was usually served first); a defendant dropped by an amended
complaint is "{dropped}". Never list the firm's own client as a defendant unless the firm is defense counsel, in
which case firm_role is "defense" and the client is "{firm_client}". An uninsured/underinsured motorist arbitration
respondent is "{uim}", not a court defendant. A defendant whose service was rejected (a registered agent's
notice that it is not the agent, a returned service) and who was not then served is "{rejected}". Use only the listed statuses,
spelled exactly; a corrected status is one of them too.
Case statuses: {case_statuses}.
Defendant statuses: {defendant_statuses}.
Write plain sentences for flags and notes: facts only, no legal advice, no doc numbers, no file ids.

Sent out for service (sent_for_service, on each defendant): the date the firm sent the papers out to be served,
which is its request to, or dispatch of, a process server or service company (an email or order to InfoTrack,
One Legal, a registered process server and the like), cited to that email or document. It is NEVER the date the
defendant was served. Record it whatever the defendant's status; if the papers went out more than once, record
the most recent request. If you searched and found no such request, record sent_for_service with a null date.

Today is {today}. A next court date must be the next one AFTER today; if none is set in the file, leave its date
null. Discovery: list each set the firm propounded (what, on whom, the date served) and each set served on the
firm's client (what, by whom, the date served, and whether responses were served, with their date).

DATED EVENTS. Beside each defendant's status, report any of these you find, each with its date and document, and
leave out one you do not find: appeared (a general or special appearance, a motion or a stipulation filed for it,
with no answer); service_rejected; dismissed; settled; dropped (an amended complaint no longer names it);
default_entered; default_set_aside. A role goes in role, with its document: "{role_client}" for the firm's own client,
"{role_uim}" for an uninsured/underinsured motorist arbitration respondent.

The court's forms in this firm's files:
{hints}"""

COMMON = """You read one litigation matter's documents in a law firm's case-management system and report facts.

RULES. Every value comes from a document you opened, cited by its doc number. "Nothing in the file" is a valid
finding. Never guess a date, a name or a party. A date is the court's or the event's date printed in the
document (the file stamp, the e-filing receipt, the date of service, the hearing date), NEVER the "Smokeball
saved" date in the file list. Facts that rest on a checkbox come only from view_page, never from text. Before you
record any negative (not served, no answer, not dismissed, not filed, active), search for what would contradict
it: answers (including inside emails), dismissals, proofs of service and process-server emails, stipulations and
extensions, defaults, case management statements newer than your finding, settlement emails ("settled",
"release", "W-9", "settlement check", "notice of settlement"). Settlement language in an email changes the case
status. Take defendants from the complaint caption and any amendments, and add any party a proof of service, a
Doe amendment or a case management statement names as a defendant. Never list the firm's own client as a defendant
unless the firm is defense counsel, in which case firm_role is "defense" and the client has role "{firm_client}". An
uninsured/underinsured motorist arbitration respondent has role "{uim}". Cite the document for any role; every other
defendant has no role.
A case status is one of: {case_statuses}.
Write plain sentences for flags and notes: facts only, no legal advice, no doc numbers, no file ids.

EVENTS. Report every dated event you find, each with its document, whatever has happened to the defendant since (a
dismissed or settled defendant was usually served first). Code works out each defendant's status from these; do not
report a status. A defendant's events: served; answered; appeared (a general or special appearance, a motion or a
stipulation filed for it, with no answer); service_rejected (a registered agent's notice that it is not the agent, a
returned service); dismissed; settled; dropped (an amended complaint no longer names it); default_entered;
default_set_aside. A paper that states an event without a date: leave the date null and say so in that defendant's
flags. Flag an answer to the original complaint that does not reach the amended one in those words.

Sent out for service (sent_for_service, on each defendant): every date the firm sent the papers out to be served,
which is its request to, or dispatch of, a process server or service company (an email or order to InfoTrack,
One Legal, a registered process server and the like), each cited to that email or document. It is NEVER the date the
defendant was served. Record it whatever else has happened. If you searched and found no such request, record an
empty list.

Today is {today}. court_dates: every court date the file sets, each with its event and document; leave out one that
was continued, vacated or taken off calendar. Discovery: list each set the firm propounded (what, on whom, the date
served) and each set served on the firm's client (what, by whom, the date served, and whether responses were served,
with their date).

The court's forms in this firm's files:
{hints}"""


def _hints(firm: Any) -> str:
    return "\n".join(f"- {k}: {v}" for k, v in sorted(firm.data["form_hints"].items())) or "- (none authored)"


def _quoted(values: tuple[str, ...]) -> str:
    """The closed list as the prompt names it. The verify and audit passes
    return a free-form ``corrected`` value, so this text is the only place
    they learn the list (2026-10-14: an audit could not choose between two
    statuses it had never been shown and recorded "Unclear")."""
    return "; ".join(f'"{v}"' for v in values)


def _base_prompt(firm: Any, today: dt.date, enforcing: bool) -> str:
    if enforcing:
        return COMMON.format(
            today=today.isoformat(),
            hints=_hints(firm),
            firm_client=vocab.ROLE_FIRM_CLIENT,
            uim=vocab.ROLE_UIM,
            case_statuses=_quoted(vocab.CASE_STATUSES),
        )
    return COMMON_OBSERVE.format(
        today=today.isoformat(),
        hints=_hints(firm),
        dropped=vocab.DROPPED,
        firm_client=vocab.FIRM_CLIENT,
        uim=vocab.UIM,
        rejected=vocab.SERVICE_REJECTED,
        role_client=vocab.ROLE_FIRM_CLIENT,
        role_uim=vocab.ROLE_UIM,
        case_statuses=_quoted(vocab.CASE_STATUSES),
        defendant_statuses=_quoted(vocab.DEFENDANT_STATUSES),
    )


def system_for(role: str, firm: Any, today: dt.date, enforcing: bool = False) -> str:
    base = _base_prompt(firm, today, enforcing)
    paths = PATHS if enforcing else PATHS_OBSERVE
    tail = {
        "read": "\n\nYOUR PASS: determine the field groups you are asked for, then call record_result.",
        "verify": (
            "\n\nYOUR PASS: an independent check of another reader's result. Check EVERY negative and EVERY positive date "
            "against its cited source, and read the newest emails listed yourself. Open any paper the result should have "
            "considered. A document you have not opened is not absent: open it. Paths you may correct: "
            + paths
            + ". Then call record_verdicts with one verdict per value checked."
        ),
        "audit": (
            "\n\nYOUR PASS: the last check before the list goes to the firm, after an independent verify pass already "
            "confirmed the positive dates against their sources. Audit ONLY: every negative (not served, no answer, not "
            "filed, not dismissed, active, no date sent out for service), every fact that rests on a checkbox (view_page; if you cannot see it the verdict "
            "is unclear), any contradiction between documents, and every quote in a flag or note (verbatim, right sender). "
            "Do not re-open a positive date's source unless a document contradicts it. Resolve every settlement-scan hit you "
            "are given in settlement_reviewed (doc + one line). "
            "Paths you may correct: " + paths + ". Then call record_verdicts."
        ),
    }[role]
    return base + tail


def file_list(
    ctx: MatterContext, candidates: list[str], newest: list[str], context_chars: int = 0, doc_chars: int = 0
) -> str:
    """The files a read is handed, in priority order (court papers newest
    first, the newest emails, service records, discovery, named emails), and
    the TEXT of as many as fit ``context_chars`` (at most ``doc_chars`` of
    each), so a read starts from the papers instead of spending calls on
    them. The tools fetch the rest."""
    order = [ctx.by_id[c] for c in candidates if c in ctx.by_id]
    lines = [ctx.line(n) + ("  (one of the newest emails)" if str(ctx.refs[n]["id"]) in newest else "") for n in order]
    rest = len(ctx.refs) - len(order)
    out = "\n".join(lines) + f"\n\n{rest} other files are on the matter; list_files shows them by name."
    used, shown, texts = 0, 0, []
    for n in order:
        if used >= context_chars:
            break
        body = ctx.text(n)
        take = min(doc_chars, context_chars - used, len(body))
        more = f"\n(more: fetch_doc {n} offset {take})" if take < len(body) else ""
        texts.append(f"===== {ctx.line(n)}\n{body[:take]}{more}")
        used += take
        shown += 1
    if texts:
        out += (
            f"\n\nDOCUMENT TEXTS ({shown} of {len(order)} files above, in that order, each cut at {doc_chars} "
            "characters; fetch_doc reads the rest and the others):\n\n" + "\n\n".join(texts)
        )
    return out


def ask(header: str, groups: list[str], listing: str, extra: str = "", enforcing: bool = False) -> str:
    want = ", ".join(groups)
    each = "its events" if enforcing else "a status"
    must = (
        f"\nEvery defendant named in the complaint caption or any amendment goes in defendants, each with {each}."
        if vocab.GROUP_DEFENDANTS in groups
        else ""
    )
    return (
        f"MATTER: {header}\nFIELD GROUPS TO DETERMINE: {want}{must}\n\n"
        "Read the document texts below first; use the tools only for what they do not settle.\n\n"
        f"FILES (court papers, recent emails, service records, discovery):\n{listing}\n{extra}"
    )


# ---- the passes ------------------------------------------------------------------------
def read_raw(
    doorway: Any,
    firm: Any,
    ctx: MatterContext,
    header: str,
    groups: list[str],
    listing: str,
    today: dt.date,
    extra: str = "",
    tail: str = "",
    enforcing: bool = False,
) -> dict[str, Any]:
    """The read pass, its answer as the model recorded it."""
    return run_loop(
        doorway,
        "litigation_read",
        model=firm.model("read"),
        system=system_for("read", firm, today, enforcing) + tail,
        user=ask(header, groups, listing, extra, enforcing),
        ctx=ctx,
        final_tool=RECORD_RESULT if enforcing else RECORD_RESULT_OBSERVE,
        max_iterations=int(firm.get("tool_iterations")),
    )


def pass1(
    doorway: Any,
    firm: Any,
    ctx: MatterContext,
    header: str,
    groups: list[str],
    listing: str,
    today: dt.date,
    enforcing: bool = False,
) -> dict[str, Any]:
    raw = read_raw(doorway, firm, ctx, header, groups, listing, today, enforcing=enforcing)
    return to_state(ctx, raw, groups, today, enforcing)


#: Not what a reader checks: bookkeeping, and how code arrived at a status.
_NOT_SHOWN = ("source", "provenance", "overturns", "integrity", "status_from", "stored_status", "status_unclear")


def for_model(result: dict[str, Any], ctx: MatterContext) -> str:
    """The result with sources shown as doc numbers (what the reader can open)
    and each defendant with the index its verdicts name it by."""

    def conv(x: Any) -> Any:
        if isinstance(x, dict):
            out = {k: conv(v) for k, v in x.items() if k not in _NOT_SHOWN}
            src = x.get("source")
            if isinstance(src, dict):
                out["doc"] = ctx.by_id.get(str(src.get("file_id")))
            return out
        return [conv(v) for v in x] if isinstance(x, list) else x

    shown = conv(result)
    if "court_dates_read" in shown:
        shown["court_dates"] = shown.pop("court_dates_read")
    for i, d in enumerate(shown.get("defendants") or []):
        if isinstance(d, dict):
            d["index"] = i
            d.update(d.pop("shadow", {}))  # the reader checks an event wherever code keeps it
            d.pop("overturned", None)
    return json.dumps(shown, indent=1)


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
    groups: list[str] | None = None,
    enforcing: bool = False,
) -> dict[str, Any]:
    stage, model = (
        ("litigation_verify", firm.model("verify")) if role == "verify" else ("litigation_audit", firm.model("audit"))
    )
    want = list(groups or vocab.GROUPS)
    scope = (
        f"\nCHECK ONLY the values in these field groups: {', '.join(want)}." if set(want) != set(vocab.GROUPS) else ""
    )
    user = ask(header, want, listing, f"\nTHE RESULT TO CHECK:\n{for_model(result, ctx)}\n{scope}{extra}", enforcing)
    got = run_loop(
        doorway,
        stage,
        model=model,
        system=system_for(role, firm, today, enforcing),
        user=user,
        ctx=ctx,
        final_tool=RECORD_VERDICTS,
        max_iterations=max(4, int(firm.get("tool_iterations")) // 2),
    )
    return {"verdicts": got.get("verdicts") or [], "settlement_reviewed": got.get("settlement_reviewed") or []}
