"""`audit`: a second reader tries to REFUTE every closure the first proposed.

The file-review toolset's audit (2026-09-28) found 9 of 82 closures wrong
because a document's NAME matched the task while its content did not. So no
closure the first reader proposes reaches the firm until a second reader,
given the proof documents' full text and the firm's auditor instructions, says
``confirmed``. ``refuted`` and ``unsure`` are reported in the workbook and
never proposed. Decided in code before any call: a task naming money or a
court date, or a statute reminder, is refuted without asking.

One interactive call per matter with closures, all of that matter's closures
in it (never batched: the audit is the gate). The auditor answers through
``record_verdicts``; an answer missing a closure leaves that closure unsure.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from ..litigation.tools import CALL_ATTEMPTS, CALL_BACKOFF, _json_answer, _uses

STAGE = "casefile_audit"
VERDICTS = ("confirmed", "refuted", "unsure")
VERDICT_TOOL: dict[str, Any] = {
    "name": "record_verdicts",
    "description": "Record one verdict per proposed closure, in the order given.",
    "input_schema": {
        "type": "object",
        "properties": {
            "verdicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string"},
                        "verdict": {"type": "string", "enum": list(VERDICTS)},
                        "reason": {"type": "string"},
                        "doc_is": {"type": "string"},
                    },
                    "required": ["task_id", "verdict", "reason"],
                },
            }
        },
        "required": ["verdicts"],
    },
}


def closures(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [t for t in record.get("open_tasks") or [] if isinstance(t, dict) and t.get("action") == "mark_complete"]


def _created(inv: dict[str, Any], task_id: str) -> str:
    t = next((t for t in inv.get("tasks_open") or [] if str(t.get("id")) == task_id), {})
    return str(t.get("createdDate") or "")[:10]


def items(
    record: dict[str, Any], inv: dict[str, Any], meta: dict[str, Any], text_of: Callable[[str], Any], cap: int
) -> list[dict[str, Any]]:
    out = []
    for t in closures(record):
        docs = []
        for d in t.get("proof_doc_ids") or []:
            m = meta.get(str(d).lower()) or meta.get(str(d)) or {}
            text = str(text_of(str(d)) or "")
            docs.append(
                {
                    "doc_id": str(d),
                    "name": m.get("name"),
                    "folder": m.get("folder"),
                    "saved": m.get("date"),
                    "text": text[:cap] + (f"\n[cut at {cap} of {len(text)} chars]" if len(text) > cap else ""),
                }
            )
        out.append(
            {
                "matter": record.get("number"),
                "clients": record.get("clients") or [],
                "task_id": str(t.get("task_id")),
                "subject": t.get("subject"),
                "task_created": _created(inv, str(t.get("task_id"))),
                "first_reader_reason": t.get("why"),
                "proof_documents": docs,
            }
        )
    return out


def decided_in_code(item: dict[str, Any], firm: Any) -> dict[str, Any] | None:
    subj = str(item.get("subject") or "")
    if firm.at_stake_rx.search(subj) or any(p.fullmatch(subj.strip()) for p in firm.statute_rx):
        return {"task_id": item["task_id"], "verdict": "refuted", "reason": "money, a court date or a statute"}
    return None


def audit_matter(doorway: Any, firm: Any, matter_id: str, todo: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """``{task_id: verdict row}`` for every closure, unsure where none came."""
    out: dict[str, dict[str, Any]] = {}
    ask = []
    for it in todo:
        pre = decided_in_code(it, firm)
        if pre is not None:
            out[it["task_id"]] = pre
        else:
            ask.append(it)
    if ask:
        r = doorway.call(
            STAGE,
            model=firm.model("audit"),
            system=firm.prompts["auditor.md"],
            messages=[{"role": "user", "content": [{"type": "text", "text": json.dumps(ask, ensure_ascii=False)}]}],
            max_tokens=16000,
            tools=[VERDICT_TOOL],
            thinking={"type": "adaptive"},
            stream=True,
            custom_id=f"{STAGE}-{matter_id}",
            attempts=CALL_ATTEMPTS,
            backoff=CALL_BACKOFF,
        )
        use = next((u for u in _uses(r.message) if u.get("name") == VERDICT_TOOL["name"]), None)
        got = use.get("input") if use is not None else _json_answer(r.message)
        rows = (got or {}).get("verdicts") if isinstance(got, dict) else None
        wanted = {it["task_id"] for it in ask}
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and row.get("task_id") in wanted and row.get("verdict") in VERDICTS:
                out[str(row["task_id"])] = {
                    k: str(row.get(k) or "")[:400] for k in ("task_id", "verdict", "reason", "doc_is")
                }
    for it in todo:
        out.setdefault(it["task_id"], {"task_id": it["task_id"], "verdict": "unsure", "reason": "no verdict came back"})
    return out


__all__ = ["STAGE", "VERDICTS", "audit_matter", "closures", "decided_in_code", "items"]
