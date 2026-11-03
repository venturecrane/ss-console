"""The facts of a matter-records reply, written by code from the page finder (ADR 0102).

WHY THIS EXISTS (2026-11-02, an A&P records request). An attorney asked the A&P
Operator for records. It found the right files, but both replies it drafted
were wrong, so SMD wrote the one that was sent: the model retyped the finder's
page ranges and counts into its own sentences, dropped pp 48-49 and wrote 59
for 57. Nothing compared those sentences with what the finder had reported.

WHAT THIS DOES. The facts a records reply states are rendered HERE, from the
``find_pages_in_file`` results this turn produced and the files actually staged,
never typed by the model:

* **Attached:** each staged file, its pages and its page count (the count is
  of the staged bytes, ``matter_file_reply.StagedFile.page_count``), with the
  heading and date the finder printed for those pages.
* **Possibly relevant, not attached:** the finder's ``relatedCandidates``,
  labelled as candidates.
* **Not found:** a record the finder looked for over every page of a file.
* **Not established:** a record not found while pages were unread, with the
  counts and the closest sections.

The block goes into the reply BEFORE the relay builds its scan text and html,
so every outbound gate and the SMD review digest cover it. The model writes the
opening line, what the seat could not attach, and the open questions.

WHAT HOLDS. Each of these holds the reply for SMD's review (no redraft loop: the
model cannot fix what code wrote):

* a finder result that is not :data:`SCHEMA_VERSION` (an older connector, or a
  newer one than this overlay knows): no partial block is ever rendered;
* a staged page range that is not a range the finder reported for that file;
* the model's own words stating a page (``pp 48``, ``page 5``) or a count
  (``12 pages``) that the facts do not state. Dates, matter numbers and phone
  numbers do not match these patterns.
"""

from __future__ import annotations

import html as html_lib
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from shared import output_checklist, result_unwrap

FIND_TOOL = "mcp_smokeball_find_pages_in_file"
#: The finder result shape this overlay renders (``page_finder_match.SCHEMA_VERSION``).
SCHEMA_VERSION = 2
#: Where the model asks the seat to put the facts (matter-inbox-router SKILL.md).
#: Capitals on purpose: an overlay that does not render facts holds a reply that
#: still carries it (the checklist's capitals rule), so it never goes out bare.
MARKER = "[[FILE FACTS]]"
_MAX_SESSIONS = 128
_TEXT_CHARS = 120

_PAGE_TOKEN = re.compile(
    r"\b(?:pp\.?|p\.|pages?)\s*(\d{1,5})(?:\s*(?:-|\u2013|to|through)\s*(\d{1,5}))?",
    re.IGNORECASE,
)
_COUNT_TOKEN = re.compile(
    r"\b(\d{1,5})\s+(pages?|files?|records?|documents?|sections?|attachments?)\b",
    re.IGNORECASE,
)
_CAPS = re.compile(r"\b[A-Z]{4,}\b")


@dataclass
class _Search:
    message_id: str
    results: dict[str, dict[str, Any]] = field(default_factory=dict)


_LOCK = threading.Lock()
_SEARCHES: OrderedDict[str, _Search] = OrderedDict()


def _reset_for_tests() -> None:
    with _LOCK:
        _SEARCHES.clear()


def record_from_result(session_id: str, args: Any, result: Any, *, message_id: str) -> None:
    """Note that this turn searched a file's pages, and keep its latest whole result.

    Called from the reply plugin's post-tool hooks, so the result is the
    connector's own (never the model's restatement). A call that errored still
    counts as a search: the reply that follows is held while review is on.

    Nothing is recorded without the email this turn answers: a record that
    could match any later reply would carry one request's file facts into a
    reply about another (second review of PR #3711)."""
    if not session_id or not message_id:
        return
    parsed = result_unwrap.parse_tool_result(result)
    file_id = ""
    if isinstance(parsed, dict) and not parsed.get("error"):
        file_id = str(parsed.get("fileId") or "").strip()
    with _LOCK:
        search = _SEARCHES.get(session_id)
        if search is None or search.message_id != message_id:
            search = _Search(message_id)
        if file_id and isinstance(parsed, dict):
            search.results[file_id] = parsed
        _SEARCHES[session_id] = search
        _SEARCHES.move_to_end(session_id)
        while len(_SEARCHES) > _MAX_SESSIONS:
            _SEARCHES.popitem(last=False)


def _search_for(session_id: str, message_id: str) -> _Search | None:
    with _LOCK:
        search = _SEARCHES.get(session_id) if session_id else None
    if search is None or not message_id or search.message_id != message_id:
        return None
    return search


def searched(session_id: str, message_id: str) -> bool:
    """Did the turn answering ``message_id`` call ``find_pages_in_file``?"""
    return _search_for(session_id, message_id) is not None


def _matters(results: dict[str, dict[str, Any]]) -> set[str]:
    return {str(r.get("matterId") or "").strip().lower() for r in results.values()}


def finder_matter(session_id: str, message_id: str) -> str:
    search = _search_for(session_id, message_id)
    for result in search.results.values() if search else []:
        if result.get("matterId"):
            return str(result["matterId"])
    return ""


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Facts:
    """The rendered block and whether the reply must wait for SMD."""

    text: str
    html: str
    hold: str
    #: The page and count tokens the block states (see :func:`tokens`).
    stated: frozenset[str]
    #: The match model's words the block prints (``asked``), guarded like the reply's.
    echoed: tuple[str, ...] = ()


def _tame(value: Any) -> str:
    """Text from a record, made fit for a staff reply: one line, no emphasis
    marks, no dashes the checklist refuses, and printed capitals in title case
    (a heading in capitals reads as shouting, and the checklist holds it)."""
    text = " ".join(str(value or "").split())[:_TEXT_CHARS]
    text = text.replace("\u2014", "-").replace("\u2013", "-").replace("!", ".")
    text = re.sub(r"[*_`#|<>]", "", text)
    known = output_checklist.KNOWN_CAPS
    if text.isupper():
        # A heading printed all in capitals: every word in title case.
        return " ".join(w if w in known else w.title() for w in text.split(" "))
    return _CAPS.sub(lambda m: m.group(0) if m.group(0) in known else m.group(0).title(), text)


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _evidence(result: dict[str, Any] | None, pages: str) -> str:
    """The heading and date the finder printed for exactly this range."""
    for item in (result or {}).get("items") or []:
        for rng in item.get("ranges") or []:
            if rng.get("pages") == pages:
                cells = []
                for ev in (rng.get("evidence") or [])[:3]:
                    part = ", ".join(
                        p for p in (_tame(ev.get("heading")), _tame(ev.get("date"))) if p
                    )
                    if part:
                        cells.append(part)
                return "; ".join(cells)
    return ""


def _pointer(ptr: dict[str, Any]) -> str:
    pages = _tame(ptr.get("pages"))
    cells = [f"pages {pages}" if pages else "", _tame(ptr.get("heading")), _tame(ptr.get("date"))]
    return ", ".join(c for c in cells if c)


def _version_ok(result: dict[str, Any]) -> bool:
    version = result.get("schemaVersion")
    return isinstance(version, int) and not isinstance(version, bool) and version == SCHEMA_VERSION


def _reported(result: dict[str, Any]) -> set[str]:
    return {
        str(rng.get("pages") or "")
        for item in result.get("items") or []
        for rng in item.get("ranges") or []
    }


def _span(pages: str) -> tuple[int, int] | None:
    first, _, last = str(pages or "").partition("-")
    try:
        a, b = int(first), int(last or first)
    except ValueError:
        return None
    return (a, b) if 0 < a <= b else None


def _sent(file_id: str, pages: str, files: list[Any]) -> bool:
    """Are ``pages`` of ``file_id`` already on the reply: a whole staged file,
    or a staged range that covers them?"""
    want = _span(pages)
    for f in files:
        if f.file_id != file_id:
            continue
        if not f.pages:
            return True
        have = _span(f.pages)
        if want and have and have[0] <= want[0] and want[1] <= have[1]:
            return True
    return False


def _attached(files: list[Any], results: dict[str, dict[str, Any]]) -> list[str]:
    lines = []
    for f in files:
        count = _plural(int(f.page_count or 0), "page")
        where = f"pages {f.pages} ({count})" if f.pages else f"the whole file ({count})"
        found = _evidence(results.get(f.file_id), f.pages) if f.pages else ""
        lines.append(f"- {_tame(f.name)}, {where}" + (f": {found}" if found else ""))
    return lines


def _sections(
    files: list[Any], results: dict[str, dict[str, Any]]
) -> tuple[list[str], list[str], list[tuple[str, list[str]]], list[str]]:
    """(related, not found, not established groups, asked texts printed)."""
    related: list[str] = []
    not_found: list[str] = []
    groups: list[tuple[str, list[str]]] = []
    asked_texts: list[str] = []
    for file_id, result in results.items():
        name = _tame(result.get("name") or "the file")
        counts = result.get("counts") or {}
        pending: list[str] = []
        for item in result.get("items") or []:
            for ptr in item.get("relatedCandidates") or []:
                if _sent(file_id, str(ptr.get("pages") or ""), files):
                    continue
                line = f"- {name}, {_pointer(ptr)}"
                if line not in related:
                    related.append(line)
            if item.get("found"):
                continue
            asked = _tame(item.get("asked")) or "the record asked for"
            asked_texts.append(asked)
            if item.get("established"):
                total = int(counts.get("pages") or result.get("pageCount") or 0)
                not_found.append(
                    f"- {asked}: not found in {name} (all {_plural(total, 'page')} were read)"
                )
                continue
            read = int(counts.get("read") or 0)
            line = f"- {asked}: not found on the {_plural(read, 'page')} of {name} that were read"
            closest = [_pointer(p) for p in item.get("closest") or []]
            pending.append(line + (f". Closest: {'; '.join(closest)}" if closest else ""))
        if pending:
            groups.append((_gap_heading(name, counts), pending))
    return related, not_found, groups, asked_texts


def _gap_heading(name: str, counts: dict[str, Any]) -> str:
    unreadable = int(counts.get("unreadable") or 0)
    left = int(counts.get("notReached") or 0)
    parts = []
    if unreadable:
        parts.append(f"{_plural(unreadable, 'page')} of {name} could not be read")
    if left:
        parts.append(f"{_plural(left, 'page')} of {name} are not read yet")
    return "Not established, " + (" and ".join(parts) or f"{name} is not fully read") + ":"


def _hold_reason(
    results: dict[str, dict[str, Any]], files: list[Any], staging: Any, read_matters: set[str]
) -> str:
    """Why no facts block can be written for this reply, or ``""``. Each one
    sends the reply to SMD, never back to the model: it cannot fix code's facts."""
    stale = [r for r in results.values() if not _version_ok(r)]
    if stale:
        return (
            "the page finder's result is not the version this seat renders (schemaVersion "
            f"{stale[0].get('schemaVersion')!r}, expected {SCHEMA_VERSION}); no facts were written"
        )
    matters = _matters(results)
    if staging is not None and files:
        matters.add(str(staging.matter_id or "").strip().lower())
    if len(matters) > 1 or (bool(results) and "" in matters):
        return "the page finder's results and the staged files are not all from one matter"
    if matters and {m.strip().lower() for m in read_matters} - matters:
        return "this session read another matter's content, so no file facts go on this reply"
    for f in files:
        if f.pages and f.file_id in results and f.pages not in _reported(results[f.file_id]):
            return (
                f"the staged pages {f.pages} of {f.name} are not a range the page finder reported"
            )
    for result in results.values():
        for item in result.get("items") or []:
            if not item.get("found") and int(item.get("droppedSections") or 0) > 0:
                # The match named sections the index does not hold: what it
                # meant is unknown, so "not found" would be a guess.
                return (
                    "the page finder named sections it does not hold for "
                    f"{_tame(item.get('asked')) or 'a record'}; whether it was found is unknown"
                )
    return ""


def build(
    session_id: str, message_id: str, staging: Any, read_matters: set[str] | None = None
) -> Facts | None:
    """The facts block for the reply to ``message_id``, or ``None`` when the
    turn neither searched a file's pages nor staged a file. ``read_matters``
    (the session's content reads) must name only the facts' own matter."""
    search = _search_for(session_id, message_id)
    files = list(staging.files) if staging is not None and not staging.refusal else []
    if search is None and not files:
        return None
    results = dict(search.results) if search else {}
    why = _hold_reason(results, files, staging, read_matters or set())
    if why:
        return Facts("", "", why, frozenset())
    blocks: list[tuple[str, list[str]]] = []
    if files:
        blocks.append(("Attached:", _attached(files, results)))
    related, not_found, groups, asked = _sections(files, results)
    if related:
        blocks.append(("Possibly relevant, not attached (candidates the finder named):", related))
    if not_found:
        blocks.append(("Not found:", not_found))
    blocks.extend(groups)
    if not blocks:
        return Facts("", "", "", frozenset(), tuple(asked))
    text = "\n\n".join(head + "\n" + "\n".join(lines) for head, lines in blocks)
    # What the block states is what CODE wrote: the match model's ``asked``
    # words are taken out first, so a page they mention is guarded, not vouched for.
    facts_only = text
    for words in asked:
        facts_only = facts_only.replace(words, " ")
    return Facts(text, _html(blocks), "", tokens(facts_only), tuple(asked))


def _html(blocks: list[tuple[str, list[str]]]) -> str:
    out = []
    for head, lines in blocks:
        items = "".join(f"<li>{html_lib.escape(line[2:])}</li>" for line in lines)
        out.append(f"<p>{html_lib.escape(head)}</p><ul>{items}</ul>")
    return "".join(out)


# ---------------------------------------------------------------------------
# Putting the block in the reply, and the guard on the model's own words
# ---------------------------------------------------------------------------


def tokens(text: str) -> frozenset[str]:
    """Every page reference and page or file count in ``text``, normalised."""
    found = set()
    for m in _PAGE_TOKEN.finditer(text or ""):
        found.add(f"p{m.group(1)}" + (f"-{m.group(2)}" if m.group(2) else ""))
    for m in _COUNT_TOKEN.finditer(text or ""):
        found.add(f"{m.group(1)} {m.group(2).lower().rstrip('s')}")
    return frozenset(found)


def unstated(facts: Facts, model_text: str) -> str:
    """A page or count the model's words state that the facts do not, else ``""``."""
    extra = sorted((tokens(model_text) | tokens(" ".join(facts.echoed))) - facts.stated)
    return ", ".join(extra[:5])


def _text_key(args: dict[str, Any]) -> str:
    return (
        "body_text" if isinstance(args.get("body_text"), str) and not args.get("text") else "text"
    )


def _put(body: str, block: str, sep: str, after: str) -> str:
    if MARKER in body:
        return body.replace(MARKER, block, 1).replace(MARKER, "")
    head, cut, rest = body.partition(after)
    if not cut:
        return f"{body}{sep}{block}" if body.strip() else block
    return f"{head}{cut}{block}{sep}{rest.lstrip()}" if rest.strip() else f"{head}{cut}{block}"


def insert(args: dict[str, Any], facts: Facts) -> dict[str, Any]:
    """``args`` with the facts in the body (at the model's marker, else after
    its opening paragraph). A block with nothing to say removes the marker."""
    out = dict(args)
    key = _text_key(out)
    text = out.get(key) if isinstance(out.get(key), str) else ""
    html = out.get("html") if isinstance(out.get("html"), str) else ""
    if text:
        out[key] = (
            _put(text, facts.text, "\n\n", "\n\n") if facts.text else text.replace(MARKER, "")
        )
    if html:
        out["html"] = _put(html, facts.html, "", "</p>") if facts.html else html.replace(MARKER, "")
    return out


#: No block: what :func:`insert` writes when the facts cannot be (it still takes the marker out).
EMPTY = Facts("", "", "", frozenset())


def missing(facts: Facts, send_text: str, send_html: str) -> str:
    """Why the body about to go breaks the rule, or ``""``: it still carries the
    marker, or it lacks the facts block a turn that used the finder must carry."""
    if MARKER in (send_text or "") or MARKER in (send_html or ""):
        return "the facts marker is still in the reply"
    if facts.text and send_text and facts.text not in send_text:
        return "the facts block is missing from the reply's text"
    if facts.html and send_html and facts.html not in send_html:
        return "the facts block is missing from the reply's html"
    if facts.text and not (send_text or send_html):
        return "the reply has no body to carry its facts"
    return ""


def model_words(args: dict[str, Any]) -> str:
    """What the model itself wrote in the body, the marker taken out."""
    key = _text_key(args)
    text = args.get(key) if isinstance(args.get(key), str) else ""
    html = args.get("html") if isinstance(args.get("html"), str) else ""
    body = text or html_lib.unescape(re.sub(r"<[^>]+>", " ", html))
    return body.replace(MARKER, " ")


__all__ = [
    "FIND_TOOL",
    "EMPTY",
    "MARKER",
    "SCHEMA_VERSION",
    "Facts",
    "build",
    "finder_matter",
    "insert",
    "missing",
    "model_words",
    "record_from_result",
    "searched",
    "tokens",
    "unstated",
]
