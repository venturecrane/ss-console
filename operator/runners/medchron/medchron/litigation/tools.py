"""The tools a read uses on one matter, and the bounded loop that drives them.

A read never receives a matter's documents in one prompt: a litigation file
runs to hundreds of entries, and the 2026-10-07 hand run measured what dumping
them cost. It gets the file LIST (the name-filtered candidates and the newest
emails in full; everything else on request) and four tools:

* ``list_files``: the whole file list, filtered by a regex, a page at a time;
* ``fetch_doc``: one document's text, ``chunk_chars`` at a time. A document
  not yet on disk is fetched and extracted on the spot, so a read that names a
  paper the name filter missed opens it instead of reporting it absent;
* ``search_text``: a regex across every opened document and every file name;
* ``view_page``: one PDF page as an IMAGE, the only source a checkbox fact may
  come from.

The loop is interactive (never batched), one call at a time through the
doorway (every call metered against the job's cap and the month's budget),
and capped at ``tool_iterations`` calls: a read that does not record its result
inside the cap raises ``ReadIncomplete`` rather than returning a partial one.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import extract as extract_mod, fetch as fetch_mod
from .manifest import file_date, full_name

LIST_PAGE = 100
SEARCH_HITS = 40


class ReadIncomplete(RuntimeError):
    """A read hit its iteration cap without recording a result."""


@dataclass
class MatterContext:
    matter_id: str
    files: list[dict[str, Any]]
    data: Path
    chunk_chars: int
    seat: Any = None
    ocr: extract_mod.Ocr | None = None
    log: Callable[[str], None] = print
    integrity: dict[str, str] = field(default_factory=dict)
    refs: dict[int, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        live = [f for f in self.files if not f.get("deleted")]
        live.sort(key=lambda f: (file_date(f), str(f.get("id"))), reverse=True)
        self.refs = {i: f for i, f in enumerate(live, 1)}
        self.by_id = {str(f["id"]): n for n, f in self.refs.items()}

    @property
    def mdir(self) -> Path:
        return fetch_mod.matter_dir(self.data, self.matter_id)

    def line(self, n: int) -> str:
        f = self.refs[n]
        return f"[doc {n}] {full_name(f)} | Smokeball saved {file_date(f) or 'unknown'} | {f.get('size') or 0} bytes"

    def source(self, n: Any, doc_date: Any = None) -> dict[str, Any] | None:
        """The interface's source shape for a doc ref; None for an unknown ref."""
        try:
            f = self.refs[int(n)]
        except (TypeError, ValueError, KeyError):
            return None
        dd = doc_date if isinstance(doc_date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", doc_date) else None
        return {"file_id": str(f["id"]), "name": full_name(f), "doc_date": dd}

    # ---- text --------------------------------------------------------------------
    def _text_path(self, fid: str) -> Path:
        return self.mdir / "txt" / f"{fid}.txt"

    def cached_text(self, fid: str) -> str | None:
        """The extracted text for a file id, or None. Served from the job's
        ``txt/`` first (a content duplicate resolves to its original), so an
        offline replay needs no raw bytes."""
        rows = fetch_mod.pulled(self.data, self.matter_id)
        cur = fid
        for _ in range(3):
            tp = self._text_path(cur)
            if tp.is_file():
                return tp.read_text(encoding="utf-8", errors="replace")
            nxt = (rows.get(cur) or {}).get("duplicate_of")
            if not nxt:
                return None
            cur = str(nxt)
        return None

    def text(self, n: int) -> str:
        f = self.refs[n]
        fid = str(f["id"])
        if fid in self.integrity:
            return "This file entry exists in Smokeball but its content is missing (it cannot be opened)."
        cached = self.cached_text(fid)
        if cached is not None:
            return cached
        failed = next((r for r in extract_mod.failures(self.mdir) if r.get("file_id") == fid), None)
        if failed is not None:
            why = str(failed.get("problem")).split(":")[0]
            return f"This file could not be read ({why}); do not treat it as absent."
        path = fetch_mod.local_path(self.data, self.matter_id, fid)
        if path is None and self.seat is not None:
            row = fetch_mod.fetch_one(self.seat, self.matter_id, f, self.data, self.log)
            missing, _retry = fetch_mod.classify([row])
            if missing:
                self.integrity[fid] = fetch_mod.MISSING
                return "This file entry exists in Smokeball but its content is missing (it cannot be opened)."
            path = fetch_mod.local_path(self.data, self.matter_id, fid)
        if path is None:
            return "This file could not be fetched; do not treat it as absent. Say so in the matter flags."
        orig = path.stem
        tp = self._text_path(orig)
        if not tp.is_file():
            rec = extract_mod.extract_file(path, path.suffix, tp, self.ocr)
            from ..stages.base import append_jsonl

            append_jsonl(self.mdir / "extracted.jsonl", {"file_id": orig, "name": f.get("name"), **rec})
            if not rec.get("ok"):
                return f"This file could not be read ({rec['problem'].split(':')[0]}); do not treat it as absent."
        return tp.read_text(encoding="utf-8", errors="replace")

    def opened(self) -> dict[int, str]:
        out = {}
        for n, f in self.refs.items():
            t = self.cached_text(str(f["id"]))
            if t is not None:
                out[n] = t
        return out

    # ---- the tools -----------------------------------------------------------------
    def list_files(self, args: dict[str, Any]) -> str:
        try:
            rx = re.compile(str(args.get("query") or ""), re.I)
        except re.error as exc:
            return f"invalid regex: {exc}"
        rows = [n for n, f in self.refs.items() if rx.search(full_name(f))]
        off = max(0, int(args.get("offset") or 0))
        page = rows[off : off + LIST_PAGE]
        tail = f"\n(more: call again with offset {off + LIST_PAGE})" if off + LIST_PAGE < len(rows) else ""
        return f"{len(rows)} files match.\n" + "\n".join(self.line(n) for n in page) + tail

    def fetch_doc(self, args: dict[str, Any]) -> str:
        try:
            n = int(args.get("doc") or 0)
            self.refs[n]
        except (TypeError, ValueError, KeyError):
            return "unknown doc number"
        text = self.text(n)
        start = max(0, int(args.get("offset") or 0))
        chunk = text[start : start + self.chunk_chars]
        more = (
            f"\n(more: call fetch_doc with offset {start + self.chunk_chars})"
            if start + self.chunk_chars < len(text)
            else ""
        )
        return f"{self.line(n)}\n{chunk}{more}"

    def search_text(self, args: dict[str, Any]) -> str:
        try:
            rx = re.compile(str(args.get("pattern") or ""), re.I)
        except re.error as exc:
            return f"invalid regex: {exc}"
        hits = [f"{self.line(n)} (file name matches)" for n, f in self.refs.items() if rx.search(full_name(f))]
        for n, text in self.opened().items():
            for m in rx.finditer(text):
                s = " ".join(text[max(0, m.start() - 120) : m.end() + 120].split())
                hits.append(f"[doc {n}] ...{s}...")
                if len(hits) >= SEARCH_HITS * 2:
                    break
        if not hits:
            return "no match in any opened document or file name (unopened documents are not searched)"
        return "\n".join(hits[:SEARCH_HITS]) + ("\n(more hits: narrow the pattern)" if len(hits) > SEARCH_HITS else "")

    def view_page(self, args: dict[str, Any]) -> list[dict[str, Any]] | str:
        try:
            n, page = int(args.get("doc") or 0), int(args.get("page") or 0)
            f = self.refs[n]
        except (TypeError, ValueError, KeyError):
            return "unknown doc or page"
        path = fetch_mod.local_path(self.data, self.matter_id, str(f["id"]))
        if path is None and self.seat is not None:
            fetch_mod.fetch_one(self.seat, self.matter_id, f, self.data, self.log)
            path = fetch_mod.local_path(self.data, self.matter_id, str(f["id"]))
        if path is None:
            return "the page image is not available in this run; rely on the document's text and say so if it matters"
        if path.suffix.lower() != ".pdf":
            return "only a PDF page can be viewed as an image"
        try:
            png = extract_mod.render_png(path.read_bytes(), page)
        except Exception:  # noqa: BLE001 - an unrenderable page is said, never guessed at
            return "that page could not be rendered"
        b64 = base64.standard_b64encode(png).decode()
        return [
            {"type": "text", "text": f"{self.line(n)} page {page}"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
        ]


TOOLS = [
    {
        "name": "list_files",
        "description": "List the matter's files (doc number, name, Smokeball saved date) whose name matches a regex.",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "offset": {"type": "integer"}}},
    },
    {
        "name": "fetch_doc",
        "description": "Read one document's text by doc number. Long documents come in chunks; pass offset for more.",
        "input_schema": {
            "type": "object",
            "properties": {"doc": {"type": "integer"}, "offset": {"type": "integer"}},
            "required": ["doc"],
        },
    },
    {
        "name": "search_text",
        "description": "Search every opened document's text and every file name with a case-insensitive regex.",
        "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]},
    },
    {
        "name": "view_page",
        "description": "See one PDF page as an image. Required for any checkbox or handwritten fact.",
        "input_schema": {
            "type": "object",
            "properties": {"doc": {"type": "integer"}, "page": {"type": "integer"}},
            "required": ["doc", "page"],
        },
    },
]


def _block_dict(b: Any) -> dict[str, Any]:
    if isinstance(b, dict):
        return b
    if hasattr(b, "model_dump"):
        return b.model_dump(exclude_none=True)
    return {k: v for k, v in vars(b).items() if v is not None}


def _uses(message: Any) -> list[dict[str, Any]]:
    return [d for d in (_block_dict(b) for b in getattr(message, "content", None) or []) if d.get("type") == "tool_use"]


_CACHE = {"type": "ephemeral"}
#: An overloaded API (529) is transient: a read retries for minutes, not
#: seconds, before its matter is marked unread (2026-10-08 replay: one matter
#: lost to three overloaded answers 20 and 40 seconds apart).
CALL_ATTEMPTS, CALL_BACKOFF = 5, 30.0
FINAL_NUDGE = (
    "Stop exploring and answer now with {tool}. Record what the documents establish; for anything you could not "
    "establish, mark it unclear (status Unclear, or a flag saying what is missing). Do not guess."
)


def _roll_cache(messages: list[dict[str, Any]]) -> None:
    """Breakpoints: the system prompt (the doorway), the first user message
    (the matter's documents), and the newest block. Older rolling marks are
    removed so a long loop never carries more than the API allows."""
    for m in messages[1:]:
        if isinstance(m.get("content"), list):
            for b in m["content"]:
                if isinstance(b, dict):
                    b.pop("cache_control", None)
    last = messages[-1]
    if len(messages) > 1 and isinstance(last.get("content"), list) and last["content"]:
        tail = last["content"][-1]
        if isinstance(tail, dict) and tail.get("type") in ("text", "tool_result"):
            tail["cache_control"] = dict(_CACHE)


def _without_thinking(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for m in messages:
        c = m.get("content")
        if m.get("role") == "assistant" and isinstance(c, list):
            c = [b for b in c if not (isinstance(b, dict) and b.get("type") in ("thinking", "redacted_thinking"))]
        out.append({**m, "content": c})
    return out


def _tool_results(uses: list[dict[str, Any]], handlers: dict[str, Callable[[dict[str, Any]], Any]]) -> list[dict]:
    out = []
    for u in uses:
        fn = handlers.get(str(u.get("name")))
        got = fn(u.get("input") or {}) if fn else f"unknown tool {u.get('name')}"
        out.append({"type": "tool_result", "tool_use_id": u.get("id"), "content": got})
    return out


def run_loop(
    doorway: Any,
    stage: str,
    *,
    model: str,
    system: str,
    user: str,
    ctx: MatterContext,
    final_tool: dict[str, Any],
    max_iterations: int,
    max_tokens: int = 16000,
) -> dict[str, Any]:
    """Drive one read to its ``final_tool`` call; its input is the result.
    The last of ``max_iterations`` calls is forced: it offers only the final
    tool (no thinking, so the choice can be forced) and answers with what was
    found, unclear where nothing was established."""
    handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
        "list_files": ctx.list_files,
        "fetch_doc": ctx.fetch_doc,
        "search_text": ctx.search_text,
        "view_page": ctx.view_page,
    }
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": user, "cache_control": dict(_CACHE)}]}
    ]
    for _ in range(max(1, max_iterations - 1)):
        _roll_cache(messages)
        r = doorway.call(
            stage,
            model=model,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            tools=[*TOOLS, final_tool],
            thinking={"type": "adaptive"},
            stream=True,
            custom_id=f"{stage}-{ctx.matter_id}",
            attempts=CALL_ATTEMPTS,
            backoff=CALL_BACKOFF,
        )
        uses = _uses(r.message)
        final = next((u for u in uses if u.get("name") == final_tool["name"]), None)
        if final is not None:
            got = final.get("input")
            return got if isinstance(got, dict) else {}
        messages.append({"role": "assistant", "content": [_block_dict(b) for b in r.message.content or []]})
        if not uses:
            nudge = f"Call {final_tool['name']} now with what you found."
            messages.append({"role": "user", "content": [{"type": "text", "text": nudge}]})
            continue
        messages.append({"role": "user", "content": _tool_results(uses, handlers)})
    return _forced_final(doorway, stage, model, system, messages, final_tool, max_tokens, ctx)


def _forced_final(
    doorway: Any,
    stage: str,
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    final_tool: dict[str, Any],
    max_tokens: int,
    ctx: MatterContext,
) -> dict[str, Any]:
    nudge = {"type": "text", "text": FINAL_NUDGE.format(tool=final_tool["name"])}
    last = messages[-1]
    if last.get("role") == "user" and isinstance(last.get("content"), list):
        last["content"] = [*last["content"], nudge]
    else:
        messages.append({"role": "user", "content": [nudge]})
    _roll_cache(messages)
    r = doorway.call(
        stage,
        model=model,
        system=system,
        messages=_without_thinking(messages),
        max_tokens=max_tokens,
        tools=[final_tool],
        tool_choice={"type": "tool", "name": final_tool["name"]},
        stream=True,
        custom_id=f"{stage}-{ctx.matter_id}-final",
        attempts=CALL_ATTEMPTS,
        backoff=CALL_BACKOFF,
    )
    final = next((u for u in _uses(r.message) if u.get("name") == final_tool["name"]), None)
    if final is None or not isinstance(final.get("input"), dict):
        raise ReadIncomplete(f"{stage}: no result, the forced final answer included")
    return final["input"]


def dump(path: Path, obj: Any) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
    tmp.replace(path)
