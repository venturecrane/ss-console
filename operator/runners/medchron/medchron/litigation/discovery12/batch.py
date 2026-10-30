"""The three passes as single-shot requests, so they can go through the Batch
API at half price (2026-10-29). A tool loop cannot be batched: each request
carries its papers' TEXT (from the job's extracted text and the text cache)
and is answered with one call of its pass's tool.

The unit is one CHUNK of one case: a case's discovery papers, newest first,
cut into chunks of at most ``CHUNK_CHARS`` (one paper is never split; a paper
longer than a chunk is cut at the limit and says so). Each chunk is read,
verified and audited on its own, against the same papers, and the case's
result is its chunks' results together. Every pass writes one file per chunk
(``m/<id>/d12-<pass>-c<k>.json``), so a resume submits only what is missing.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any, Callable

from . import sections

#: Characters of paper text one request carries. A case that fits is read as
#: ONE request (no split). Measured 2026-10-29 on the real API: 382,668
#: characters were 176,349 input tokens (2.17 per token), so 400k came to about
#: 184k input + the 16k output budget + the system prompt, over a 200k window.
#: The repo records no larger window for the read model, so 360k (about 166k
#: input tokens) keeps a measured margin; a split case is reconciled case-wide
#: (``merge``).
CHUNK_CHARS = 360_000
#: Characters per input token for the cost projection: measured 2.17 on the
#: real API (2026-10-29), taken low so the projection errs high. It was 4,
#: which under-projected a batch's input by almost half against the job cap.
CHARS_PER_TOKEN = 2.0
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


ATTACHMENT = "\n\n[ATTACHMENT] "


def paper_key(text: str) -> str:
    """Two copies of one paper (a filed PDF and the same PDF on an email) share this."""
    return " ".join(text.lower().split())[:3000]


def attachments_only(text: str, rx: list[Any], seen: set[str]) -> str:
    """An email's discovery attachments and nothing else (the Captain, 2026-10-29:
    the read covers the discovery PAPERS the quote priced, plus a paper that
    exists only as an attachment; email bodies are out). Keeps an attachment
    whose NAME matches a discovery pattern, that carries text, and whose text
    is not already one of the case's papers (``seen``, updated)."""
    out = []
    for seg in text.split(ATTACHMENT)[1:]:
        name, _, body = seg.partition("\n")
        if not body.strip() or not any(r.search(name) for r in rx):
            continue
        key = paper_key(body)
        if key in seen:
            continue
        seen.add(key)
        out.append(f"[ATTACHMENT] {name}\n{body.strip()}")
    return "\n\n".join(out)


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


#: Which response wins when chunks disagree about one request: a response
#: found in ANY chunk is a response (a split case reads part 2's requests
#: without part 1's responses, 2026-10-29 design review).
_RESPONSE_RANK = {"yes": 3, "not yet due": 2, "unclear": 1, "no": 0}


def _norm(s: Any) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).split())


def _same_request(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """One set or event, read in two chunks: same item, direction and party,
    and served on the same date or one chunk did not see the date."""
    if (_norm(a.get("item")), a.get("direction"), _norm(a.get("party"))) != (
        _norm(b.get("item")),
        b.get("direction"),
        _norm(b.get("party")),
    ):
        return False
    da, db = (a.get("served") or {}).get("date"), (b.get("served") or {}).get("date")
    return not da or not db or da == db


def _first_dated(a: Any, b: Any) -> Any:
    """The value with a date; when both have one, the earlier."""
    da, db = (a or {}).get("date"), (b or {}).get("date")
    if not db:
        return a
    if not da:
        return b
    return a if str(da) <= str(db) else b


def _rank(r: dict[str, Any]) -> tuple[int, bool]:
    return _RESPONSE_RANK.get(str(r.get("value")), 1), bool(r.get("date"))


def _join(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Two chunks' entries for one request as one entry."""
    ra, rb = a.get("response") or {}, b.get("response") or {}
    notes = [n for n in (a.get("notes"), b.get("notes")) if n]
    return {
        **a,
        "served": _first_dated(a.get("served"), b.get("served")),
        "due": _first_dated(a.get("due"), b.get("due")),
        "response": ra if _rank(ra) >= _rank(rb) else rb,
        "extension": _first_dated(a.get("extension"), b.get("extension")),
        "notes": " ".join(dict.fromkeys(notes)),
    }


def merge(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """A case's chunk results together, RECONCILED case-wide: one request read
    in several chunks is one entry, and a response found in any chunk is its
    response (with that chunk's source); each section's entries otherwise in
    chunk order; flags once each."""
    out: dict[str, Any] = {"sections": {k: [] for k in sections.KEYS}, "flags": []}
    for c in chunks:
        for k in sections.KEYS:
            rows = out["sections"][k]
            for e in (c.get("sections") or {}).get(k) or []:
                at = next((i for i, x in enumerate(rows) if _same_request(x, e)), None)
                if at is None:
                    rows.append(copy.deepcopy(e))
                elif rows[at] != e:
                    rows[at] = _join(rows[at], e)
        out["flags"] += [f for f in c.get("flags") or [] if f not in out["flags"]]
    return out


def project_usd(rate: Any, chars: int, items: int, batch_multiplier: float) -> float:
    """A batch's projected cost before it is submitted: its input at
    ``CHARS_PER_TOKEN``, every item's whole output budget, at the batch rate.
    Both sides err high, so the cap check binds before the spend, never after."""
    tokens_in = chars / CHARS_PER_TOKEN
    cents = tokens_in * rate.input_per_million_cents / 1e6 + items * MAX_TOKENS * rate.output_per_million_cents / 1e6
    return cents / 100 * batch_multiplier


def dumps(x: Any) -> str:
    return json.dumps(x, indent=1)
