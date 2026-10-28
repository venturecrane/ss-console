"""`read`: one matter's review, an interactive tool loop (never batched).

The reader gets the matter's pack (``pack.py``) as its first message and three
tools from the shared litigation reader (``litigation/tools.py``):
``list_files``, ``fetch_doc`` (a document not yet on disk is fetched and
extracted on the spot) and ``search_text``. There is no page-image tool in this
job: a scan is not transcribed here, so the price stays predictable. It answers
by calling ``record_review`` with the record the firm's contract describes.

The record is checked in CODE before it is accepted (``validate.py``): a record
the validator refuses goes back to the reader as the tool's result, with every
violation, to fix by correcting the record (never by weakening a claim into a
closure); after ``validate_retries`` refusals the matter is held alone.

THE CACHED PREFIX. The tool definitions, then the system prompt (the firm's
contract and reader instructions, fixed for the whole job), then the pack:
every matter after the first reads the tools and the system prompt from the
cache, and every turn of one matter's loop reads its pack from the cache.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..litigation.tools import (
    CALL_ATTEMPTS,
    CALL_BACKOFF,
    TOOLS as LIT_TOOLS,
    MatterContext,
    ReadIncomplete,
    _block_dict,
    _roll_cache,
    _tool_results,
    _uses,
)
from . import recheck

STAGE = "casefile_read"
MAX_TOKENS = 32000
_STR = {"type": "string"}
_ARR = {"type": "array", "items": {"type": "object"}}

#: The record shape (the firm's contract names what goes in each field; the
#: validator holds the rules). Every key the record carries is listed here, so
#: the tool definition is one fixed block in the cached prefix.
RECORD_KEYS = (
    "number",
    "matter_id",
    "clients",
    "matter_type",
    "date_of_loss",
    "statute_date",
    "opened",
    "status",
    "responsible",
    "assisting",
    "headline",
    "posture",
    "posture_why",
    "facilities",
    "open_tasks",
    "done_tasks_checked",
    "records_to_order",
    "medicals",
    "insurance",
    "injury_flags",
    "time_sensitive",
    "letters",
    "questions",
    "unreadable_docs",
    "specialty_appointments",
)
REVIEW_TOOL: dict[str, Any] = {
    "name": "record_review",
    "description": (
        "Record this matter's review, exactly in the shape the contract gives. Cite every document "
        "as [doc N], its number in the listing and in fetch_doc (in a document-id field such as "
        'proof_doc_ids or source_doc_id, write "[doc N]"); the record is stored with each '
        "document's id. Call it once the whole pack is read; a record the validator refuses comes "
        "back with the violations to fix. When the pack carries a PRIOR REVIEW RECORD, prefer "
        "record_review_patch."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            **{k: _STR for k in ("number", "matter_id", "matter_type", "date_of_loss", "statute_date", "opened")},
            **{k: _STR for k in ("status", "responsible", "assisting", "headline", "posture", "posture_why")},
            "clients": {"type": "array", "items": _STR},
            "questions": {"type": "array", "items": _STR},
            "insurance": {"type": "object"},
            **{
                k: _ARR
                for k in (
                    "facilities",
                    "open_tasks",
                    "done_tasks_checked",
                    "records_to_order",
                    "medicals",
                    "injury_flags",
                    "time_sensitive",
                    "letters",
                    "unreadable_docs",
                    "specialty_appointments",
                )
            },
        },
        "required": list(RECORD_KEYS),
    },
}
_KEYS_TEXT = "; ".join(f"{k} by {' + '.join(v) if v else 'the string itself'}" for k, v in recheck.LIST_KEYS.items())
PATCH_TOOL: dict[str, Any] = {
    "name": "record_review_patch",
    "description": (
        "Only when the pack carries a PRIOR REVIEW RECORD: record this matter's review as the "
        "changes to that record, not the whole record. Code applies the patch to the prior record "
        "and checks the result exactly as a whole record is checked. `set` replaces a top-level "
        "field whole (any record field; a list given here replaces the whole list). `upsert` maps "
        "a list field to its new or changed items, each written in full; an item replaces the prior "
        "item with the same key, or is added. `remove` maps a list field to the items (or their "
        f"keys) to drop. Items are matched by: {_KEYS_TEXT}. Leave out everything that did not "
        "change; an empty patch says nothing changed. Every open task the pack lists that the prior "
        "record has no row for needs an upserted open_tasks row. Cite documents as [doc N]. A patch "
        "that cannot be applied unambiguously comes back asking for record_review with the whole record."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "set": {"type": "object"},
            "upsert": {"type": "object", "additionalProperties": {"type": "array"}},
            "remove": {"type": "object", "additionalProperties": {"type": "array"}},
        },
    },
}
#: The fixed tool list (no page images in this job). Never changes in a job,
#: whole reads and re-checks alike, so every matter shares one cached prefix.
TOOLS = [*[t for t in LIT_TOOLS if t["name"] != "view_page"], PATCH_TOOL, REVIEW_TOOL]
ANSWERS = (REVIEW_TOOL["name"], PATCH_TOOL["name"])


class CasefileContext(MatterContext):
    """The litigation reader's context, with no page images."""

    def view_page(self, args: dict[str, Any]) -> str:
        return "page images are not read in this job; rely on the document's text and say so if it matters"


def system_prompt(firm: Any) -> str:
    return firm.prompts["contract.md"].strip() + "\n\n" + firm.prompts["reader.md"].strip()


@dataclass
class ReadResult:
    record: dict[str, Any]
    problems: list[str]
    turns: int
    #: How the accepted (or last) record came: "full" (record_review) or
    #: "patch" (record_review_patch merged onto the prior record).
    answer: str = "full"
    #: Patches that could not be merged and were answered with a request for the
    #: whole record (fail closed, never a best-effort merge).
    patch_refused: int = 0


def _call(doorway: Any, firm: Any, messages: list[dict[str, Any]], ctx: Any, suffix: str = "") -> Any:
    _roll_cache(messages)
    return doorway.call(
        STAGE,
        model=firm.model("read"),
        system=system_prompt(firm),
        messages=messages,
        max_tokens=MAX_TOKENS,
        tools=TOOLS,
        thinking={"type": "adaptive"},
        stream=True,
        custom_id=f"{STAGE}-{ctx.matter_id}{suffix}",
        attempts=CALL_ATTEMPTS,
        backoff=CALL_BACKOFF,
    )


def _refusal(problems: list[str], patched: bool) -> str:
    again = (
        "call record_review_patch again (it now applies to the record as last merged) or record_review "
        "with the whole record"
        if patched
        else "call record_review again"
    )
    return (
        "The validator refused this record. Fix every violation by correcting the record (never by "
        f"weakening a claim into a closure), then {again}:\n" + "\n".join(f"- {p}" for p in problems)
    )


def _patch_back(why: str) -> str:
    return (
        f"This patch cannot be applied to the prior record unambiguously ({why}). Nothing was recorded. "
        "Call record_review with the whole record."
    )


def _answer(
    use: dict[str, Any], base: dict[str, Any] | None, refs: dict[int, Any]
) -> tuple[dict[str, Any] | None, list[str], str]:
    """(record, resolution problems, refused-why) for one answer: a whole
    record, or a patch merged onto ``base``. Documents cited ``[doc N]`` are
    turned into their ids first; a number that is not a document of this
    matter is a violation the validator's refusal carries back."""
    raw = use.get("input")
    got, problems = recheck.resolve(raw if isinstance(raw, dict) else {}, refs)
    if use.get("name") == REVIEW_TOOL["name"]:
        return got, problems, ""
    if base is None:
        return None, [], "the pack carries no prior record"
    try:
        return recheck.merge(base, got), problems, ""
    except recheck.PatchRefused as exc:
        return None, [], str(exc)


def read_matter(
    doorway: Any,
    firm: Any,
    ctx: Any,
    pack_text: str,
    check: Callable[[dict[str, Any]], list[str]],
    prior: dict[str, Any] | None = None,
) -> ReadResult:
    """Drive one review to an accepted record, or return the last refused one
    with its problems. Raises ``ReadIncomplete`` when no record came at all.
    ``prior`` is the prior delivered record (the review itself) a patch
    applies to; a whole read passes None and a patch there is refused."""
    handlers = {"list_files": ctx.list_files, "fetch_doc": ctx.fetch_doc, "search_text": ctx.search_text}
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": pack_text, "cache_control": {"type": "ephemeral"}}]}
    ]
    refusals, last = int(firm.get("validate_retries")), ReadResult({}, ["no record"], 0)
    cap = int(firm.get("tool_iterations"))
    base, patch_refused = prior, 0
    for turn in range(1, cap + 1):
        final_turn = turn == cap
        r = _call(doorway, firm, messages, ctx, "-final" if final_turn else "")
        uses = _uses(r.message)
        messages.append({"role": "assistant", "content": [_block_dict(b) for b in r.message.content or []]})
        rec = next((u for u in uses if u.get("name") in ANSWERS), None)
        if rec is not None:
            record, problems, why = _answer(rec, base, ctx.refs)
            patched = rec.get("name") == PATCH_TOOL["name"]
            if record is None:
                patch_refused += 1
                others = [u for u in uses if u is not rec]
                results = _tool_results(others, handlers) if others else []
                results.append({"type": "tool_result", "tool_use_id": rec.get("id"), "content": _patch_back(why)})
                messages.append({"role": "user", "content": results})
                continue
            problems = problems + check(record)
            last = ReadResult(record, problems, turn, "patch" if patched else "full", patch_refused)
            if patched:
                base = record
            if not problems or refusals <= 0 or final_turn:
                return last
            refusals -= 1
            others = [u for u in uses if u is not rec]
            results = _tool_results(others, handlers) if others else []
            results.append(
                {"type": "tool_result", "tool_use_id": rec.get("id"), "content": _refusal(problems, patched)}
            )
            messages.append({"role": "user", "content": results})
            continue
        if not uses:
            messages.append({"role": "user", "content": [{"type": "text", "text": "Call record_review now."}]})
            continue
        results = _tool_results(uses, handlers)
        if turn == cap - 1:
            results.append({"type": "text", "text": "Stop exploring and call record_review now with what you found."})
        messages.append({"role": "user", "content": results})
    if last.record:
        last.patch_refused = patch_refused
        return last
    raise ReadIncomplete(f"{STAGE}: no record within {cap} calls")


__all__ = [
    "ANSWERS",
    "PATCH_TOOL",
    "REVIEW_TOOL",
    "STAGE",
    "TOOLS",
    "CasefileContext",
    "ReadResult",
    "read_matter",
    "system_prompt",
]
