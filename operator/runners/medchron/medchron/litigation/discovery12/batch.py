"""The three passes as single-shot requests, so they can go through the Batch
API at half price (2026-10-29). A tool loop cannot be batched: each request
carries its papers' TEXT (from the job's extracted text and the text cache)
and is answered with one forced tool call.

The unit is one CHUNK of one case: a case's discovery papers, newest first,
cut into chunks of at most ``CHUNK_CHARS`` (one paper is never split; a paper
longer than a chunk is cut at the limit and says so). Each chunk is read,
verified and audited on its own, against the same papers, and the case's
result is its chunks' results together. Every pass writes one file per chunk
(``m/<id>/d12-<pass>-c<k>.json``), so a resume submits only what is missing.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from . import sections

#: Characters of paper text one request carries (about 100k tokens).
CHUNK_CHARS = 400_000
STAGES = {"read": "litigation_d12_read", "verify": "litigation_d12_verify", "audit": "litigation_d12_audit"}
PASSES = ("read", "verify", "audit")
FILE = "d12-{p}-c{k}.json"
MAX_TOKENS = 16000
CUT = "\n[... this paper continues beyond what one request carries; the rest was not read ...]"


def chunk(docs: list[tuple[int, str]], limit: int = CHUNK_CHARS) -> list[list[tuple[int, str]]]:
    """``[(doc number, text)]`` cut into chunks of at most ``limit`` characters,
    in order; a single over-long paper is cut and marked."""
    out: list[list[tuple[int, str]]] = []
    cur: list[tuple[int, str]] = []
    size = 0
    for n, text in docs:
        if len(text) > limit:
            text = text[: limit - len(CUT)] + CUT
        if cur and size + len(text) > limit:
            out.append(cur)
            cur, size = [], 0
        cur.append((n, text))
        size += len(text)
    if cur:
        out.append(cur)
    return out


def papers(lines: Callable[[int], str], docs: list[tuple[int, str]]) -> str:
    return "\n\n".join(f"===== {lines(n)}\n{t}" for n, t in docs)


def user_text(header: str, body: str, k: int, of: int, check: str = "") -> str:
    part = f" (part {k + 1} of {of}: these papers only; the other parts are read separately)" if of > 1 else ""
    return (
        f"MATTER: {header}\nDETERMINE: the twelve discovery sections{part}.\n"
        "Every paper you may cite is below, each headed by its doc number. Cite only these doc numbers.\n\n"
        f"PAPERS:\n{body}\n{check}"
    )


def tool_input(message: Any, name: str) -> dict[str, Any] | None:
    """The forced tool call's input from an API message (object or dict)."""
    for b in getattr(message, "content", None) or (message.get("content") if isinstance(message, dict) else []) or []:
        btype = getattr(b, "type", None) or (b.get("type") if isinstance(b, dict) else None)
        bname = getattr(b, "name", None) or (b.get("name") if isinstance(b, dict) else None)
        if btype == "tool_use" and bname == name:
            got = getattr(b, "input", None) if not isinstance(b, dict) else b.get("input")
            return got if isinstance(got, dict) else None
    return None


def tool_for(role: str) -> dict[str, Any]:
    return sections.RECORD_RESULT if role == "read" else sections.RECORD_VERDICTS


def shape(role: str) -> dict[str, Any]:
    """The request shape for a pass: its one tool, chosen by the model.

    2026-10-29: the proof's every batched read was refused, "tool_choice: type
    'tool' and 'any' are not supported for this model" (claude-sonnet-5-5).
    ``auto`` plus the system prompt's ``ONLY_BY_TOOL`` line asks for the call;
    an answer that does not call it is no result (``tool_input`` is None), so
    the case is marked unread and the gates hold it, never filled in."""
    return {"tools": [tool_for(role)], "tool_choice": {"type": "auto"}}


def only_by_tool(role: str) -> str:
    return (
        f"\n\nAnswer ONLY by calling the {tool_for(role)['name']} tool, exactly once. "
        "Write no other text; an answer outside the tool is discarded."
    )


def merge(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """A case's chunk results together: each section's entries in chunk order,
    an identical entry once; flags once each."""
    out: dict[str, Any] = {"sections": {k: [] for k in sections.KEYS}, "flags": []}
    for c in chunks:
        for k in sections.KEYS:
            for e in (c.get("sections") or {}).get(k) or []:
                if e not in out["sections"][k]:
                    out["sections"][k].append(e)
        out["flags"] += [f for f in c.get("flags") or [] if f not in out["flags"]]
    return out


def project_usd(rate: Any, chars: int, items: int, batch_multiplier: float) -> float:
    """A batch's projected cost before it is submitted: its input at about four
    characters a token, every item's whole output budget, at the batch rate.
    The output side is the ceiling, so the projection errs high."""
    tokens_in = chars / 4
    cents = tokens_in * rate.input_per_million_cents / 1e6 + items * MAX_TOKENS * rate.output_per_million_cents / 1e6
    return cents / 100 * batch_multiplier


def dumps(x: Any) -> str:
    return json.dumps(x, indent=1)
