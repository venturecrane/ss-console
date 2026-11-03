"""The facts block of a matter-records reply, rendered by code from the page finder (ADR 0102).

Lifted from ``operator/hermes-smd-overlay/shared/matter_file_facts.py`` (the
renderer, the token guard and the body helpers), so the overlay's in-turn reply
and the records lane render the same words. What stays in the overlay is the
per-session store of finder results (``record_from_result``) and ``build``,
which now calls :func:`render`.

The facts a records reply states, never typed by a model:

* **Attached:** each staged file, its pages and its page count, with the
  heading and date the finder printed for those pages.
* **Possibly relevant, not attached:** the finder's ``relatedCandidates``.
* **Not found:** a record looked for over every page of a file.
* **Not established:** a record not found while pages were unread, with the
  counts and the closest sections.

A result-shape problem (:func:`hold_reason`) renders nothing: the reply waits
for SMD, because no one can fix code's facts by redrafting.

Inputs are plain dicts: ``results`` maps a Smokeball file id to its
``find_pages_in_file`` result (``schemaVersion`` 2); ``files`` are staged rows
(``file_id``, ``name``, ``pages`` ("" for the whole file), ``page_count``,
plus whatever else the row carries).
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass, field
from typing import Any

#: The finder result shape this renders (``page_finder_match.SCHEMA_VERSION``).
SCHEMA_VERSION = 2
#: Where the model asks the seat to put the facts (matter-inbox-router SKILL.md).
MARKER = "[[FILE FACTS]]"
_TEXT_CHARS = 120

#: The one list of capitals a heading may keep. The overlay's
#: ``shared.output_checklist.KNOWN_CAPS`` is this value (imported, never copied);
#: tests/test_no_dependencies.py fails if it grows its own list again.
KNOWN_CAPS: frozenset[str] = frozenset(
    {
        "PLAINTIFF",
        "PLAINTIFFS",
        "DEFENDANT",
        "DEFENDANTS",
        "EXHIBIT",
        "DRAFT",
        "FINAL",
        "ESTATE",
        "DHCS",
        "IOLTA",
        "HIPAA",
        "ERISA",
        "FRCP",
        "LASC",
        "USDC",
        "CACI",
        "JAMS",
        "SROG",
        "FROG",
        "PLLC",
        "GEICO",
        "USAA",
        "CSAA",
        "OSHA",
        "CDCR",
        "SNAP",
        "SSDI",
        "LAPD",
        "LASD",
        "USPS",
        "NHTSA",
        "COVID",
    }
)

#: A number as a person writes it: "2,313" is one number, never "313".
_NUM = r"\d{1,3}(?:,\d{3})+(?!\d)|\d{1,6}"
_PAGE_TOKEN = re.compile(
    rf"\b(?:pp\.?|p\.|pages?)\s*({_NUM})(?:\s*(?:-|–|to|through)\s*({_NUM}))?",
    re.IGNORECASE,
)
_COUNT_TOKEN = re.compile(
    rf"(?<![\d,.])({_NUM})\s+(pages?|files?|records?|documents?|sections?|attachments?)\b",
    re.IGNORECASE,
)
#: Before a number that is part of a document's name ("Exhibit 4 file", "Tab 3
#: records", "#2 file"): that number is a label, never a quantity.
_NAME_BEFORE = re.compile(
    r"(?:\b(?:exhibit|ex|tab|part|vol|volume|no|number|item|appendix|attachment|schedule|"
    r"chapter|set|bates|doc|document)\.?|#)\s*$",
    re.IGNORECASE,
)
#: A capitalised word just before the number, not at a sentence start: a name.
_PROPER_BEFORE = re.compile(r"[^.!?\s]\s+[A-Z][\w-]*\s+$")
_CAPS = re.compile(r"\b[A-Z]{4,}\b")

#: Where each finder item landed (``Facts.items[*]["heading"]``).
HEADING_ATTACHED = "attached"
HEADING_NOT_FOUND = "not_found"
HEADING_NOT_ESTABLISHED = "not_established"
#: A found item none of whose ranges is on the reply: the block does not
#: mention it. The lane decides what that means; the renderer only says so.
HEADING_UNPLACED = "unplaced"


@dataclass(frozen=True)
class Facts:
    """The rendered block, what it accounted for, and whether the reply must wait for SMD."""

    text: str
    html: str
    #: One entry per finder item, in result order: ``{file_id, index, asked,
    #: found, heading}`` (``heading`` one of the ``HEADING_*`` values). Empty on a hold.
    items: list[dict[str, Any]] = field(default_factory=list)
    #: Why no block could be written (the reply waits for SMD), else ``None``.
    hold_reason: str | None = None
    #: The one matter the results and files are from, as the finder printed it; ``None`` if none or on a hold.
    matter_id: str | None = None
    #: The page and count tokens the block states (see :func:`tokens`).
    stated: frozenset[str] = frozenset()
    #: The match model's words the block prints (``asked``), guarded like the reply's.
    echoed: tuple[str, ...] = ()
    #: The file names the block prints, exactly as on the matter.
    names: tuple[str, ...] = ()


#: No block: what :func:`insert` writes when the facts cannot be (it still takes the marker out).
EMPTY = Facts("", "")


def _tame(value: Any, known: frozenset[str] = KNOWN_CAPS) -> str:
    """Text from a record, made fit for a staff reply: one line, no emphasis
    marks, no dashes the checklist refuses, and printed capitals in title case."""
    text = " ".join(str(value or "").split())[:_TEXT_CHARS]
    text = text.replace("—", "-").replace("–", "-").replace("!", ".")
    text = re.sub(r"[*_`#|<>]", "", text)
    if text.isupper():
        # A heading printed all in capitals: every word in title case.
        return " ".join(w if w in known else w.title() for w in text.split(" "))
    return _CAPS.sub(lambda m: m.group(0) if m.group(0) in known else m.group(0).title(), text)


def _name(value: Any) -> str:
    """A file's name exactly as the matter holds it; only a line break becomes a space."""
    return str(value or "").replace("\r", " ").replace("\n", " ")


def without_names(facts: Facts | None, text: str) -> str:
    """``text`` with each file name the facts print replaced by a neutral word,
    for the reply checklist: it judges our prose, not a file's name."""
    for name in sorted(facts.names if facts else (), key=len, reverse=True):
        if name:
            text = text.replace(name, "the file")
    return text


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _f(row: dict[str, Any], key: str) -> str:
    return str(row.get(key) or "")


class _Renderer:
    """The overlay's private helpers, bound to one ``known_caps``."""

    def __init__(self, known: frozenset[str]) -> None:
        self.known = known

    def tame(self, value: Any) -> str:
        return _tame(value, self.known)

    def evidence(self, result: dict[str, Any] | None, pages: str) -> str:
        """The heading and date the finder printed for exactly this range."""
        for item in (result or {}).get("items") or []:
            for rng in item.get("ranges") or []:
                if rng.get("pages") == pages:
                    cells = []
                    for ev in (rng.get("evidence") or [])[:3]:
                        part = ", ".join(p for p in (self.tame(ev.get("heading")), self.tame(ev.get("date"))) if p)
                        if part:
                            cells.append(part)
                    return "; ".join(cells)
        return ""

    def pointer(self, ptr: dict[str, Any]) -> str:
        pages = self.tame(ptr.get("pages"))
        cells = [
            f"pages {pages}" if pages else "",
            self.tame(ptr.get("heading")),
            self.tame(ptr.get("date")),
        ]
        return ", ".join(c for c in cells if c)

    def attached(self, files: list[dict[str, Any]], results: dict[str, dict[str, Any]]) -> list[str]:
        lines = []
        for f in files:
            pages = _f(f, "pages")
            count = _plural(int(f.get("page_count") or 0), "page")
            where = f"pages {pages} ({count})" if pages else f"the whole file ({count})"
            found = self.evidence(results.get(_f(f, "file_id")), pages) if pages else ""
            lines.append(f"- {_name(f.get('name'))}, {where}" + (f": {found}" if found else ""))
        return lines

    def sections(
        self, files: list[dict[str, Any]], results: dict[str, dict[str, Any]]
    ) -> tuple[list[str], list[str], list[tuple[str, list[str]]], list[str], list[dict[str, Any]]]:
        """(related, not found, not established groups, asked texts printed, item accounting)."""
        related: list[str] = []
        not_found: list[str] = []
        groups: list[tuple[str, list[str]]] = []
        asked_texts: list[str] = []
        items: list[dict[str, Any]] = []
        for file_id, result in results.items():
            name = _name(result.get("name") or "the file")
            counts = result.get("counts") or {}
            pending: list[str] = []
            for index, item in enumerate(result.get("items") or []):
                entry = {
                    "file_id": file_id,
                    "index": index,
                    "asked": str(item.get("asked") or ""),
                    "found": bool(item.get("found")),
                }
                items.append(entry)
                for ptr in item.get("relatedCandidates") or []:
                    if _sent(file_id, str(ptr.get("pages") or ""), files):
                        continue
                    line = f"- {name}, {self.pointer(ptr)}"
                    if line not in related:
                        related.append(line)
                if item.get("found"):
                    on_reply = any(
                        _sent(file_id, str(rng.get("pages") or ""), files) for rng in item.get("ranges") or []
                    )
                    entry["heading"] = HEADING_ATTACHED if on_reply else HEADING_UNPLACED
                    continue
                asked = self.tame(item.get("asked")) or "the record asked for"
                asked_texts.append(asked)
                if item.get("established"):
                    total = int(counts.get("pages") or result.get("pageCount") or 0)
                    not_found.append(f"- {asked}: not found in {name} (all {_plural(total, 'page')} were read)")
                    entry["heading"] = HEADING_NOT_FOUND
                    continue
                read = int(counts.get("read") or 0)
                line = f"- {asked}: not found on the {_plural(read, 'page')} of {name} that were read"
                closest = [self.pointer(p) for p in item.get("closest") or []]
                pending.append(line + (f". Closest: {'; '.join(closest)}" if closest else ""))
                entry["heading"] = HEADING_NOT_ESTABLISHED
            if pending:
                groups.append((gap_heading(name, counts), pending))
        return related, not_found, groups, asked_texts, items


def _version_ok(result: dict[str, Any]) -> bool:
    version = result.get("schemaVersion")
    return isinstance(version, int) and not isinstance(version, bool) and version == SCHEMA_VERSION


def _reported(result: dict[str, Any]) -> set[str]:
    return {str(rng.get("pages") or "") for item in result.get("items") or [] for rng in item.get("ranges") or []}


def _span(pages: str) -> tuple[int, int] | None:
    first, _, last = str(pages or "").partition("-")
    try:
        a, b = int(first), int(last or first)
    except ValueError:
        return None
    return (a, b) if 0 < a <= b else None


def _sent(file_id: str, pages: str, files: list[dict[str, Any]]) -> bool:
    """Are ``pages`` of ``file_id`` already on the reply: a whole staged file,
    or a staged range that covers them?"""
    want = _span(pages)
    for f in files:
        if _f(f, "file_id") != file_id:
            continue
        if not _f(f, "pages"):
            return True
        have = _span(_f(f, "pages"))
        if want and have and have[0] <= want[0] and want[1] <= have[1]:
            return True
    return False


def gap_heading(name: str, counts: dict[str, Any]) -> str:
    """The "Not established, ..." heading over one file's unsettled items."""
    unreadable = int(counts.get("unreadable") or 0)
    left = int(counts.get("notReached") or 0)
    parts = []
    if unreadable:
        parts.append(f"{_plural(unreadable, 'page')} of {name} could not be read")
    if left:
        parts.append(f"{_plural(left, 'page')} of {name} are not read yet")
    return "Not established, " + (" and ".join(parts) or f"{name} is not fully read") + ":"


def _matters(results: dict[str, dict[str, Any]]) -> set[str]:
    return {str(r.get("matterId") or "").strip().lower() for r in results.values()}


def hold_reason(
    results: dict[str, dict[str, Any]],
    files: list[dict[str, Any]],
    *,
    staged_matter: str = "",
    read_matters: set[str] | None = None,
    known_caps: frozenset[str] = KNOWN_CAPS,
) -> str | None:
    """Why no facts block can be written, or ``None``. Each one sends the reply
    to SMD, never back to a model: it cannot fix code's facts.

    ``staged_matter`` is the matter the ``files`` were staged from (staged files
    with no matter hold). ``read_matters``, when given, is every matter whose
    content the caller read; any matter outside the facts' own one holds."""
    stale = [r for r in results.values() if not _version_ok(r)]
    if stale:
        return (
            "the page finder's result is not the version this seat renders (schemaVersion "
            f"{stale[0].get('schemaVersion')!r}, expected {SCHEMA_VERSION}); no facts were written"
        )
    matters = _matters(results)
    if files:
        matters.add(str(staged_matter or "").strip().lower())
    if len(matters) > 1 or (bool(results) and "" in matters):
        return "the page finder's results and the staged files are not all from one matter"
    if matters and {m.strip().lower() for m in read_matters or set()} - matters:
        return "this session read another matter's content, so no file facts go on this reply"
    for f in files:
        pages, file_id = _f(f, "pages"), _f(f, "file_id")
        if pages and file_id in results and pages not in _reported(results[file_id]):
            return f"the staged pages {pages} of {f.get('name')} are not a range the page finder reported"
    for result in results.values():
        for item in result.get("items") or []:
            if not item.get("found") and int(item.get("droppedSections") or 0) > 0:
                # The match named sections the index does not hold: what it
                # meant is unknown, so "not found" would be a guess.
                return (
                    "the page finder named sections it does not hold for "
                    f"{_tame(item.get('asked'), known_caps) or 'a record'}; whether it was found is unknown"
                )
    return None


def _one_matter(results: dict[str, dict[str, Any]], staged_matter: str) -> str | None:
    for result in results.values():
        if result.get("matterId"):
            return str(result["matterId"])
    return staged_matter or None


def render(
    results: dict[str, dict[str, Any]],
    files: list[dict[str, Any]],
    *,
    staged_matter: str = "",
    read_matters: set[str] | None = None,
    known_caps: frozenset[str] = KNOWN_CAPS,
) -> Facts:
    """The facts block for these finder ``results`` and staged ``files``.

    A hold returns ``Facts("", "", hold_reason=...)``. Nothing to say returns
    empty text and html (with ``items`` and ``echoed`` still filled). The
    overlay's ``build`` returns ``None`` before calling this when its turn
    neither searched nor staged; this function never returns ``None``."""
    why = hold_reason(results, files, staged_matter=staged_matter, read_matters=read_matters, known_caps=known_caps)
    if why:
        return Facts("", "", hold_reason=why)
    r = _Renderer(known_caps)
    blocks: list[tuple[str, list[str]]] = []
    if files:
        blocks.append(("Attached:", r.attached(files, results)))
    related, not_found, groups, asked, items = r.sections(files, results)
    if related:
        blocks.append(("Possibly relevant, not attached (candidates the finder named):", related))
    if not_found:
        blocks.append(("Not found:", not_found))
    blocks.extend(groups)
    matter = _one_matter(results, staged_matter)
    if not blocks:
        return Facts("", "", items=items, matter_id=matter, echoed=tuple(asked))
    text = "\n\n".join(head + "\n" + "\n".join(lines) for head, lines in blocks)
    # What the block states is what CODE wrote: the match model's ``asked``
    # words are taken out first, so a page they mention is guarded, not vouched for.
    facts_only = text
    for words in asked:
        facts_only = facts_only.replace(words, " ")
    names = tuple(
        dict.fromkeys([_name(f.get("name")) for f in files] + [_name(x.get("name")) for x in results.values()])
    )
    return Facts(
        text,
        html_blocks(blocks),
        items=items,
        matter_id=matter,
        stated=tokens(facts_only),
        echoed=tuple(asked),
        names=names,
    )


def html_blocks(blocks: list[tuple[str, list[str]]]) -> str:
    """``(heading, ["- line", ...])`` blocks as ``<p>heading</p><ul><li>...</li></ul>``."""
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
    text = text or ""
    found = set()
    for m in _PAGE_TOKEN.finditer(text):
        first, last = _n(m.group(1)), _n(m.group(2)) if m.group(2) else ""
        found.add(f"p{first}" + (f"-{last}" if last else ""))
    for m in _COUNT_TOKEN.finditer(text):
        before = text[max(0, m.start() - 40) : m.start()]
        if _NAME_BEFORE.search(before) or _PROPER_BEFORE.search(before):
            continue
        found.add(f"{_n(m.group(1))} {m.group(2).lower().rstrip('s')}")
    return frozenset(found)


def _n(number: str) -> str:
    return number.replace(",", "")


def unstated(facts: Facts, model_text: str) -> str:
    """A page or count the model's words state that the facts do not, else ``""``."""
    extra = sorted((tokens(model_text) | tokens(" ".join(facts.echoed))) - facts.stated)
    return ", ".join(extra[:5])


def _text_key(args: dict[str, Any]) -> str:
    return "body_text" if isinstance(args.get("body_text"), str) and not args.get("text") else "text"


def _put(body: str, block: str, sep: str, after: str, marker: str) -> str:
    if marker in body:
        return body.replace(marker, block, 1).replace(marker, "")
    head, cut, rest = body.partition(after)
    if not cut:
        return f"{body}{sep}{block}" if body.strip() else block
    return f"{head}{cut}{block}{sep}{rest.lstrip()}" if rest.strip() else f"{head}{cut}{block}"


def insert(args: dict[str, Any], facts: Facts, *, marker: str = MARKER) -> dict[str, Any]:
    """``args`` with the facts in the body (at the marker, else after the
    opening paragraph). A block with nothing to say removes the marker."""
    out = dict(args)
    key = _text_key(out)
    text = out.get(key) if isinstance(out.get(key), str) else ""
    html = out.get("html") if isinstance(out.get("html"), str) else ""
    if text:
        out[key] = _put(text, facts.text, "\n\n", "\n\n", marker) if facts.text else text.replace(marker, "")
    if html:
        out["html"] = _put(html, facts.html, "", "</p>", marker) if facts.html else html.replace(marker, "")
    return out


def missing(facts: Facts, send_text: str, send_html: str, *, marker: str = MARKER) -> str:
    """Why the body about to go breaks the rule, or ``""``: it still carries the
    marker, or it lacks the facts block it must carry."""
    if marker in (send_text or "") or marker in (send_html or ""):
        return "the facts marker is still in the reply"
    if facts.text and send_text and facts.text not in send_text:
        return "the facts block is missing from the reply's text"
    if facts.html and send_html and facts.html not in send_html:
        return "the facts block is missing from the reply's html"
    if facts.text and not (send_text or send_html):
        return "the reply has no body to carry its facts"
    return ""


def model_words(args: dict[str, Any], *, marker: str = MARKER) -> str:
    """What the model itself wrote in the body, the marker taken out."""
    key = _text_key(args)
    text = args.get(key) if isinstance(args.get(key), str) else ""
    html = args.get("html") if isinstance(args.get("html"), str) else ""
    body = text or html_lib.unescape(re.sub(r"<[^>]+>", " ", html))
    return body.replace(marker, " ")


__all__ = [
    "EMPTY",
    "HEADING_ATTACHED",
    "HEADING_NOT_ESTABLISHED",
    "HEADING_NOT_FOUND",
    "HEADING_UNPLACED",
    "KNOWN_CAPS",
    "MARKER",
    "SCHEMA_VERSION",
    "Facts",
    "gap_heading",
    "hold_reason",
    "html_blocks",
    "insert",
    "missing",
    "model_words",
    "render",
    "tokens",
    "unstated",
    "without_names",
]
