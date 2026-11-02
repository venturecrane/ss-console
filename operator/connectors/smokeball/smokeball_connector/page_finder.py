"""Find which pages of a scanned PDF hold a requested record (ADR 0102, amendment 2).

WHY THIS EXISTS (2026-11-02, the ashton-price run). An attorney asked for five
records on a matter. The Operator found the files by name and sent two. The
other three sat inside large image-only sets (a set of nearly 100,000 internal objects, a
second hospital set) and it never tried to cut them, because a scan has no text layer
and it had no way to see which pages hold "the visit on a given date". This
module gives it that way.

HOW. Three steps; the model reads, code decides the boundaries:

1. **An index of the pages, from what is printed on them.** The file is split
   into single pages in the memory-capped child (``page_split.py``), and the
   pages go to the seat's model a batch at a time, each page its own document
   block labelled in code ("Page 37:"). For each page the model reports what is
   printed on it: each section that STARTS there (heading, type, date,
   provider), whether the page's top continues an earlier section, and every
   labelled date on it. The page number is the label CODE wrote (a PDF
   position, never a printed page number), and a batch whose answer does not
   cover exactly the pages sent is refused, so the model cannot move evidence
   between pages. The index is cached per file sha256
   (``SMOKEBALL_PAGE_INDEX_DIR``), so a second request about the same file
   reads no page again.
2. **Sections, by code.** :func:`sections` turns the index into sections and
   their pages: a section runs until the next one starts; a page where a new
   section starts mid-page belongs to both; a "(continued)" page stays in its
   note; a canceled appointment is its own section beside the visit of the
   same date; a date printed only on a late page (a signature page) is listed
   for its section.
3. **The request against the sections.** A text-only call (no page images) is
   given the sections and the colleague's words and names, per record asked
   for, the section ids that hold it, or says it is not there and what the
   sections show instead. Only the named provider's sections answer a provider.
   Code turns the ids into page ranges and composes the evidence; an id it does
   not hold is dropped and counted.

WHAT IT NEVER DOES. It sends nothing and files nothing. Page images go only to
the Messages API the seat already uses (``vision.py``: the seat's own
``ANTHROPIC_API_KEY``, a per-customer workspace key). Each request carries no
tools and is one turn, so a page that reads like an instruction is described,
never obeyed. It never fabricates: a page it could not read is reported as not
read, and a partial index says ``complete: false``.

COST. Opus 5.5 list price ($4 / $20 per million tokens), at effort low: a
scanned page is roughly 1,600 input tokens and its index row roughly 80 output
tokens, so about $0.90 per 100 pages to index, plus a few cents for the match
step (an estimate from the token counts above, not a measured bill; the result
reports the actual tokens). Bounds: :data:`PAGES_PER_CALL` pages per call
inside :data:`CALL_DEADLINE_SECONDS`, :data:`MAX_PAGES_PER_FILE` indexed per
file, :data:`BATCH_PAGES` pages per request.

RUNTIME LAYER. The index holds what page headers say (dates, providers,
headings) for a firm's files: client content on the seat volume at
``/opt/data/smokeball-page-index/``. Purge with
``python -m smokeball_connector.page_finder purge --matter <id>`` (or ``--all``).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from . import vision
from .page_finder_prompts import (
    _SECTION_FIELDS,
    INDEX_INSTRUCTION,
    INDEX_SCHEMA,
    MATCH_INSTRUCTION,
    MATCH_SCHEMA,
)

INDEX_DIR_ENV = "SMOKEBALL_PAGE_INDEX_DIR"
DEFAULT_INDEX_DIR = "/opt/data/smokeball-page-index"
MODEL_ENV = "SMOKEBALL_PAGE_FINDER_MODEL"
#: Bumped when the index prompt or schema changes; an index of another version is not reused.
PROMPT_VERSION = 2
PAGES_PER_CALL = 100
MAX_PAGES_PER_FILE = 1000
BATCH_PAGES = 8
BATCH_BYTES = 12 * 1024 * 1024
PAGE_MAX_BYTES = 8 * 1024 * 1024
INDEX_MAX_TOKENS = 6000
MATCH_MAX_TOKENS = 8000
#: Inside the MCP server's 300 s tool timeout, with room for the match step.
CALL_DEADLINE_SECONDS = 200.0
REQUEST_TIMEOUT_SECONDS = 120.0
RETRIES = 2
_FIELD_CHARS = 160
#: One page-finding run at a time on a seat (each runs ``vision.workers()`` requests).
_LOCK = threading.Semaphore(1)
_RANGE_RE = re.compile(r"^\s*(\d{1,5})\s*(?:-\s*(\d{1,5}))?\s*$")


class FinderRefused(ValueError):
    """The pages cannot be looked at; the message is the plain reason."""


class _CallFailed(RuntimeError):
    def __init__(self, detail: str, *, retryable: bool = False) -> None:
        super().__init__(detail)
        self.retryable = retryable


def model() -> str:
    return (os.environ.get(MODEL_ENV) or "").strip() or vision.model()


# ---------------------------------------------------------------------------
# The index cache
# ---------------------------------------------------------------------------


def index_dir() -> Path:
    return Path(os.environ.get(INDEX_DIR_ENV, "").strip() or DEFAULT_INDEX_DIR)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _index_path(base: Path, sha: str) -> Path:
    return base / f"{sha}.json"


def load_index(sha: str, base: Path | None = None) -> dict[str, Any]:
    """The cached index for a file, or an empty one. Another prompt version or
    model's index is not reused (it would answer a different question)."""
    path = _index_path(base or index_dir(), sha)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict) or data.get("version") != PROMPT_VERSION or data.get("model") != model():
        return {"version": PROMPT_VERSION, "model": model(), "sha256": sha, "pages": {}, "matters": []}
    data.setdefault("pages", {})
    data.setdefault("matters", [])
    return data


def save_index(index: dict[str, Any], base: Path | None = None) -> None:
    base = base or index_dir()
    base.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions - owner-only on purpose; client content
        os.chmod(base, 0o700)
    path = _index_path(base, index["sha256"])
    tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({**index, "saved_at": time.time()}, fh, sort_keys=True)
    os.replace(tmp, path)


def purge(*, matter_id: str | None = None, everything: bool = False, base: Path | None = None) -> int:
    """Delete cached indexes for one matter (or all). Returns how many."""
    base = base or index_dir()
    removed = 0
    for path in sorted(base.glob("*.json")) if base.is_dir() else []:
        try:
            matters = json.loads(path.read_text(encoding="utf-8")).get("matters") or []
        except (OSError, ValueError, AttributeError):
            matters = []
        if everything or (matter_id and matter_id.lower() in [str(m).lower() for m in matters]):
            path.unlink(missing_ok=True)
            removed += 1
    return removed


# ---------------------------------------------------------------------------
# One request to the model
# ---------------------------------------------------------------------------


def _post(body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    """POST one Messages request; (the parsed JSON answer, token usage)."""
    import httpx

    headers = {
        "content-type": "application/json",
        "anthropic-version": vision.API_VERSION,
        "x-api-key": os.environ["ANTHROPIC_API_KEY"].strip(),
    }
    payload = json.dumps(body).encode("utf-8")
    for attempt in range(RETRIES + 1):
        client = vision._http_client(REQUEST_TIMEOUT_SECONDS)
        try:
            response = client.post(vision.API_URL, content=payload, headers=headers)
            status = response.status_code
            if status != 200:
                raise _CallFailed(f"HTTP {status}", retryable=vision._retryable_status(status))
            return _answer(response.json())
        except httpx.TransportError as exc:
            failure = _CallFailed(exc.__class__.__name__, retryable=True)
        except _CallFailed as exc:
            failure = exc
        finally:
            with contextlib.suppress(Exception):
                client.close()
        if not failure.retryable or attempt == RETRIES:
            raise failure
        time.sleep(vision.BACKOFF_BASE_SECONDS * (2**attempt))
    raise _CallFailed("unreachable")


def _answer(message: Any) -> tuple[dict[str, Any], dict[str, int]]:
    if not isinstance(message, dict):
        raise _CallFailed("not a message")
    usage = message.get("usage") or {}
    tokens = {k: int(usage.get(k) or 0) for k in ("input_tokens", "output_tokens")}
    if message.get("stop_reason") != "end_turn":
        raise _CallFailed(f"stopped: {message.get('stop_reason')}")
    texts = [b.get("text") for b in message.get("content") or [] if isinstance(b, dict) and b.get("type") == "text"]
    try:
        data = json.loads("".join(t for t in texts if isinstance(t, str)))
    except ValueError as exc:
        raise _CallFailed("answer was not JSON") from exc
    if not isinstance(data, dict):
        raise _CallFailed("answer was not an object")
    return data, tokens


def _body(content: list[dict[str, Any]], schema: dict[str, Any], max_tokens: int) -> dict[str, Any]:
    """One turn, no tools, effort low, structured output. Thinking is left at
    the API default (vision.py: never disabled on a read)."""
    return {
        "model": model(),
        "max_tokens": max_tokens,
        "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        "messages": [{"role": "user", "content": content}],
    }


def index_batch(pages: list[tuple[int, bytes]]) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """Index rows for a batch of single-page PDFs, keyed by page number as text.
    Refused unless the answer covers exactly the pages sent."""
    import base64

    content: list[dict[str, Any]] = []
    for number, blob in pages:
        content.append({"type": "text", "text": f"Page {number}:"})
        content.append(
            {
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": base64.b64encode(blob).decode()},
            }
        )
    content.append({"type": "text", "text": INDEX_INSTRUCTION})
    data, tokens = _post(_body(content, INDEX_SCHEMA, INDEX_MAX_TOKENS))
    rows = data.get("pages")
    sent = {n for n, _ in pages}
    if not isinstance(rows, list):
        raise _CallFailed("no pages in the answer")
    got: dict[str, dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("page"), int) and row["page"] in sent:
            got[str(row["page"])] = _page_row(row)
    if set(got) != {str(n) for n in sent} or len(rows) != len(sent):
        raise _CallFailed("the answer did not cover exactly the pages sent")
    return got, tokens


def _clip(value: Any) -> str:
    return " ".join(str(value or "").split())[:_FIELD_CHARS]


def _page_row(row: dict[str, Any]) -> dict[str, Any]:
    """One page's index row, normalised: text clipped, lists bounded."""
    sections = []
    for sec in row.get("sections") or []:
        if isinstance(sec, dict):
            sections.append({k: _clip(sec.get(k)) for k in _SECTION_FIELDS})
    return {
        "continues_previous": bool(row.get("continues_previous")),
        "sections": sections[:8],
        "dates": [_clip(d) for d in (row.get("dates") or []) if str(d or "").strip()][:8],
        "running_header": _clip(row.get("running_header")),
        "page_of": _clip(row.get("page_of")),
        "legible": bool(row.get("legible")),
    }


# ---------------------------------------------------------------------------
# Building the index
# ---------------------------------------------------------------------------


def parse_window(value: Any) -> tuple[int, int] | None:
    if value in (None, ""):
        return None
    m = _RANGE_RE.match(str(value))
    if not m or int(m.group(1)) < 1 or int(m.group(2) or m.group(1)) < int(m.group(1)):
        raise FinderRefused(f"pages {value!r} is not a range like 1-120")
    return int(m.group(1)), int(m.group(2) or m.group(1))


def _batches(pages: list[tuple[int, bytes]]) -> list[list[tuple[int, bytes]]]:
    out: list[list[tuple[int, bytes]]] = []
    for item in pages:
        if not out or len(out[-1]) >= BATCH_PAGES or sum(len(b) for _, b in out[-1]) + len(item[1]) > BATCH_BYTES:
            out.append([])
        out[-1].append(item)
    return out


def _wanted(index: dict[str, Any], total: int, window: tuple[int, int] | None) -> list[int]:
    first, last = window or (1, total)
    todo = [n for n in range(first, min(last, total) + 1) if str(n) not in index["pages"]]
    room = max(0, MAX_PAGES_PER_FILE - len(index["pages"]))
    return todo[: min(PAGES_PER_CALL, room)]


def build_index(
    source: Path,
    workdir: Path,
    *,
    window: tuple[int, int] | None,
    base: Path | None = None,
    matter_id: str | None = None,
) -> dict[str, Any]:
    """Index the pages of ``source`` not yet in its cached index (at most
    :data:`PAGES_PER_CALL` this call). Returns the index plus this call's facts.
    One run at a time per seat; a second waits briefly, then is refused."""
    if not _LOCK.acquire(timeout=vision.lock_timeout()):
        raise FinderRefused("another file's pages are being read right now; try again in a minute")
    try:
        return _build(source, workdir, window=window, base=base, matter_id=matter_id)
    finally:
        _LOCK.release()


def _build(
    source: Path, workdir: Path, *, window: tuple[int, int] | None, base: Path | None, matter_id: str | None
) -> dict[str, Any]:
    from .page_cut import CutRefused
    from .page_split import split

    if vision.disabled() or not (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
        raise FinderRefused("this seat cannot read page images (no model credential, or reading is turned off)")
    sha = file_sha256(source)
    index = load_index(sha, base)
    try:
        total, _ = split(source, 1, 0, workdir)  # counts pages, writes none
        todo = _wanted(index, total, window)
        written = split(source, todo[0], todo[-1], workdir)[1] if todo else []
    except CutRefused as exc:
        raise FinderRefused(str(exc)) from exc
    index["page_count"] = total
    if matter_id and matter_id not in index["matters"]:
        index["matters"].append(matter_id)
    facts: dict[str, Any] = {
        "read_now": [],
        "unreadable": [],
        "failed": [],
        "tokens": {"input_tokens": 0, "output_tokens": 0},
    }
    pages: list[tuple[int, bytes]] = []
    for n in (n for n in todo if n in written):
        blob = (workdir / f"p{n}.pdf").read_bytes()
        (workdir / f"p{n}.pdf").unlink(missing_ok=True)
        if len(blob) > PAGE_MAX_BYTES:
            facts["unreadable"].append(n)
        else:
            pages.append((n, blob))
    _run_batches(_batches(pages), index, facts, time.monotonic() + CALL_DEADLINE_SECONDS)
    save_index(index, base)
    return {"index": index, **facts}


def _run_batches(
    batches: list[list[tuple[int, bytes]]], index: dict[str, Any], facts: dict[str, Any], deadline: float
) -> None:
    lock = threading.Lock()

    def job(batch: list[tuple[int, bytes]]) -> None:
        if time.monotonic() > deadline:
            return
        try:
            rows, tokens = index_batch(batch)
        except _CallFailed:
            with lock:
                facts["failed"].extend(n for n, _ in batch)
            return
        with lock:
            index["pages"].update(rows)
            facts["read_now"].extend(int(k) for k in rows)
            for key, value in tokens.items():
                facts["tokens"][key] += value

    if not batches:
        return
    with ThreadPoolExecutor(max_workers=max(1, min(vision.workers(), len(batches)))) as pool:
        list(pool.map(job, batches))


# ---------------------------------------------------------------------------
# The request against the index
# ---------------------------------------------------------------------------


def sections(pages: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The file's sections and the PDF pages each spans, decided by CODE from the index.

    A section runs from the page it starts on through every following page until
    the next section starts. The page where the next one starts still belongs to
    the earlier section when its top continues it (``continues_previous``): a new
    visit beginning mid-page shares that page with the note it follows. Several
    sections starting on one page all include it. A continuation page with no
    known start (an unindexed gap before it) opens an untitled section, so no
    page is ever silently attached to a section it may not belong to."""
    out: list[dict[str, Any]] = []
    last_page = 0
    for key in sorted(pages, key=int):
        n, row = int(key), pages[key]
        starts = row.get("sections") or []
        adjacent = bool(out) and last_page == n - 1
        if adjacent and (not starts or row.get("continues_previous")):
            for sec in out:
                if sec["last"] == n - 1 and not sec.get("closed"):
                    sec["last"] = n
        elif not starts:
            out.append(
                {
                    "first": n,
                    "last": n,
                    "heading": "(untitled: starts before the indexed pages)",
                    "document_type": "",
                    "date": "",
                    "provider": "",
                }
            )
        for start in starts:
            # A section that started earlier on this same page ends here.
            out.append({"first": n, "last": n, **{k: start.get(k, "") for k in _SECTION_FIELDS}})
        if starts:
            # Only the LAST section starting on a page can run on past it: an
            # earlier one that started or continued onto this page ends here.
            for sec in out[:-1]:
                sec["closed"] = True
        last_page = n
    for index, sec in enumerate(out, start=1):
        sec["id"] = f"S{index}"
        seen: list[str] = []
        for page in range(sec["first"], sec["last"] + 1):
            for d in pages.get(str(page), {}).get("dates") or []:
                if d not in seen:
                    seen.append(d)
        sec["dates"] = seen[:8]
        sec.pop("closed", None)
    return out


def section_lines(secs: list[dict[str, Any]]) -> str:
    out = []
    for sec in secs:
        cells = [sec.get(k) or "-" for k in _SECTION_FIELDS]
        pages_txt = f"pp. {sec['first']}-{sec['last']}"
        dates = "; ".join(sec.get("dates") or []) or "-"
        out.append(" | ".join([sec["id"], pages_txt, *cells, f"dates printed: {dates}"]))
    return "\n".join(out)


def _ranges(chosen: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """The pages of the chosen sections, merged into contiguous PDF ranges."""
    pages = sorted({n for sec in chosen for n in range(sec["first"], sec["last"] + 1)})
    spans: list[list[int]] = []
    for n in pages:
        if spans and spans[-1][1] >= n - 1:
            spans[-1][1] = n
        else:
            spans.append([n, n])
    return [(a, b) for a, b in spans]


def _view(sec: dict[str, Any]) -> dict[str, Any]:
    return {
        "section": sec["id"],
        "pages": f"{sec['first']}-{sec['last']}",
        **{k: sec.get(k, "") for k in _SECTION_FIELDS},
        "dates": sec.get("dates") or [],
    }


def match(pages: dict[str, dict[str, Any]], what: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Per record asked for: found, PDF page ranges, reason, and the sections
    (code-composed) the ranges rest on. The model names section ids only; the
    page boundaries are code's, from the index."""
    secs = sections(pages)
    if not secs:
        return [], {"input_tokens": 0, "output_tokens": 0}
    by_id = {sec["id"]: sec for sec in secs}
    content = [
        {"type": "text", "text": "SECTIONS:\n" + section_lines(secs)},
        {"type": "text", "text": "THE COLLEAGUE'S REQUEST:\n" + what.strip()[:4000]},
        {"type": "text", "text": MATCH_INSTRUCTION},
    ]
    data, tokens = _post(_body(content, MATCH_SCHEMA, MATCH_MAX_TOKENS))
    items = []
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        ids = [str(i).strip() for i in item.get("sections") or []]
        chosen = [by_id[i] for i in dict.fromkeys(ids) if i in by_id]
        dropped = len([i for i in ids if i not in by_id])
        found = bool(chosen) and bool(item.get("found"))
        items.append(
            {
                "asked": str(item.get("asked") or "")[:300],
                "found": found,
                "ranges": [
                    {
                        "pages": f"{a}-{b}",
                        "evidence": [_view(s) for s in chosen if s["first"] <= b and s["last"] >= a],
                    }
                    for a, b in (_ranges(chosen) if found else [])
                ],
                "reason": str(item.get("reason") or "")[:500],
                **({"droppedSections": dropped} if dropped else {}),
            }
        )
    return items, tokens


def documents(pages: dict[str, dict[str, Any]], limit: int = 120) -> list[dict[str, Any]]:
    """The file's sections, as the outline a reply can cite."""
    return [_view(sec) for sec in sections(pages)[:limit]]


def locate(
    source: Path,
    what: str,
    workdir: Path,
    *,
    window: tuple[int, int] | None = None,
    base: Path | None = None,
    matter_id: str | None = None,
) -> dict[str, Any]:
    """The whole finding behind the tool and the CLI."""
    if not str(what or "").strip():
        raise FinderRefused("say what to look for: the request's own words")
    built = build_index(source, workdir, window=window, base=base, matter_id=matter_id)
    index = built["index"]
    total = int(index.get("page_count") or 0)
    indexed = len(index["pages"])
    try:
        items, match_tokens = match(index["pages"], what)
    except _CallFailed as exc:
        items, match_tokens = [], {"input_tokens": 0, "output_tokens": 0}
        built["match_error"] = str(exc)
    tokens = {k: built["tokens"][k] + match_tokens[k] for k in built["tokens"]}
    out: dict[str, Any] = {
        "pageCount": total,
        "indexedPages": indexed,
        "complete": indexed >= total,
        "readNow": len(built["read_now"]),
        "fromCache": indexed - len(built["read_now"]),
        "items": items,
        "documents": documents(index["pages"]),
        "tokens": tokens,
        "sha256": index["sha256"],
    }
    for key, label in (("failed", "failedPages"), ("unreadable", "unreadablePages")):
        if built[key]:
            out[label] = _compress(built[key])
    if built.get("match_error"):
        out["matchError"] = built["match_error"]
    if not out["complete"]:
        out["note"] = (
            f"Only {indexed} of {total} pages are indexed so far. Call again (cached pages are free) "
            "before saying a record is not in this file."
        )
    return out


def _compress(numbers: list[int]) -> str:
    numbers = sorted(set(numbers))
    spans: list[list[int]] = []
    for n in numbers:
        if spans and spans[-1][1] == n - 1:
            spans[-1][1] = n
        else:
            spans.append([n, n])
    return ", ".join(f"{a}-{b}" if a != b else str(a) for a, b in spans)


__all__ = [
    "FinderRefused",
    "build_index",
    "documents",
    "file_sha256",
    "index_batch",
    "section_lines",
    "sections",
    "load_index",
    "locate",
    "match",
    "parse_window",
    "purge",
    "save_index",
]


if __name__ == "__main__":
    import sys

    from .page_finder_tools import main

    sys.exit(main(sys.argv[1:]))
