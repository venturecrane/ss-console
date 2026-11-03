"""From the finder's results to the reply's words, in code: shared by the job
(``run.py``) and its rehearsal (``rehearse.py``), so a rehearsal checks
exactly what a live job would send.

* :func:`merge`: one finder result per (record, file) becomes one result per
  file (the renderer's input), each finder item remembering which record it
  answers.
* :func:`ranges_to_cut`: the page ranges a found record is attached as, exact
  strings the finder reported (the renderer refuses any other), the whole file
  when the range is every page.
* :func:`outcomes`: one outcome per record from the renderer's own accounting.
* :func:`compose`: the reply: one fixed opening line and the facts block, and
  nothing a model wrote. A held reply is words only and says SMD is finishing it.
"""

from __future__ import annotations

import html as html_lib
import re
from typing import Any

from records_contract import facts as facts_mod

OPENING = "Here is what we found for the records you asked for on this matter."
HELD_LINE = "SMD is finishing this request."
#: A record's outcome is the first heading any of its items landed under.
_ORDER = (
    facts_mod.HEADING_ATTACHED,
    facts_mod.HEADING_UNPLACED,
    facts_mod.HEADING_NOT_ESTABLISHED,
    facts_mod.HEADING_NOT_FOUND,
)


def found(result: dict[str, Any]) -> bool:
    return any(item.get("found") and item.get("ranges") for item in result.get("items") or [])


def merge(
    located: list[tuple[int, str, dict[str, Any]]],
) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, int], int]]:
    """``[(n, file_id, result)]`` in order -> (results by file id, owner of
    each merged item: ``(file_id, index) -> n``). A file's counts are its last
    result's (the index only grows); its items are every result's, in order."""
    results: dict[str, dict[str, Any]] = {}
    owners: dict[tuple[str, int], int] = {}
    for n, file_id, result in located:
        merged = results.get(file_id)
        if merged is None:
            merged = {**result, "items": []}
            results[file_id] = merged
        else:
            items = merged["items"]
            merged.update({k: v for k, v in result.items() if k != "items"})
            merged["items"] = items
        for item in result.get("items") or []:
            owners[(file_id, len(merged["items"]))] = n
            merged["items"].append(item)
    return results, owners


def ranges_to_cut(result: dict[str, Any], file_id: str, owners: dict[tuple[str, int], int]) -> list[tuple[str, int]]:
    """``(pages, record n)`` for every range of every found item of one file,
    deduplicated (the first record to ask for a range owns its cut), in order.
    ``""`` (the whole file) when a range is the file's every page."""
    total = int(result.get("pageCount") or 0)
    out: list[tuple[str, int]] = []
    seen: set[str] = set()
    for index, item in enumerate(result.get("items") or []):
        if not item.get("found"):
            continue
        for rng in item.get("ranges") or []:
            pages = str(rng.get("pages") or "")
            whole = total > 0 and (pages == f"1-{total}" or (total == 1 and pages == "1"))
            key = "" if whole else pages
            if pages and key not in seen:
                seen.add(key)
                out.append((key, int(owners.get((file_id, index), 0))))
    return out


def outcomes(
    records: list[dict[str, Any]],
    rendered_items: list[dict[str, Any]],
    owners: dict[tuple[str, int], int],
    unresolved: set[int],
    files_by_n: dict[int, int],
    *,
    held: set[int] | None = None,
) -> list[dict[str, Any]]:
    """One ``{n, outcome, files}`` per record asked for. ``held`` when the
    record waits for SMD (``search.py``: a file it needs would pass the cap);
    else ``attached`` when any of its items is on the reply; else ``unplaced``
    (found, not attached: our defect), ``not_established``, ``not_found``;
    ``unresolved`` when no file on the matter could be tied to it."""
    held = held or set()
    heads: dict[int, set[str]] = {}
    for it in rendered_items:
        n = owners.get((str(it.get("file_id")), int(it.get("index") or 0)))
        if n is not None:
            heads.setdefault(n, set()).add(str(it.get("heading")))
    out = []
    for rec in records:
        n = int(rec["n"])
        got = set() if n in unresolved else heads.get(n, set())
        outcome = "held" if n in held else next((h for h in _ORDER if h in got), "unresolved")
        out.append({"n": n, "outcome": outcome, "files": int(files_by_n.get(n, 0))})
    return out


#: The pages a cap hold names (``per_job_cap_usd: <id prefix> <unread> pages``).
_HELD_PAGES = re.compile(r"\b(\d{1,7}) pages\b")


def held_line(asked: str, reason: str) -> str:
    """One held record, in the reply's words: its own words and why it is not
    established yet, never a dollar figure, a setting name or a file id."""
    m = _HELD_PAGES.search(reason or "")
    if str(reason or "").startswith("scope_unresolved"):
        why = "no file on the matter is named for it, and SMD is finishing it"
    elif m:
        n = int(m.group(1))
        are = "is" if n == 1 else "are"
        why = f"{n:,} {'page' if n == 1 else 'pages'} of the file it is in {are} not read yet, and SMD is finishing it"
    else:
        why = "SMD is finishing it"
    return f"- {facts_mod._tame(asked) or 'the record asked for'}: not established yet; {why}"


def with_gaps(
    block_text: str, block_html: str, results: dict[str, dict[str, Any]], files: list[dict[str, Any]]
) -> tuple[str, str]:
    """The facts block, plus each ATTACHED file with pages the model could not
    read: a record found on the readable pages goes out (2026-11-03: 47 pages,
    23 unreadable, the visit found on 1-2 and 6-39), and the reply says, in the
    contract's gap words, how many pages of that file were not read. A file the
    renderer already named in a "Not established" heading is not named twice."""
    lines = []
    for fid in dict.fromkeys(str(f.get("file_id")) for f in files):
        result = results.get(fid) or {}
        counts = result.get("counts") or {}
        name = facts_mod._name(result.get("name") or "the file")
        for count, words in ((counts.get("unreadable"), "could not be read"), (counts.get("notReached"), "not read")):
            n = int(count or 0)
            verb = "" if words.startswith("could") else ("was " if n == 1 else "were ")
            said = f"{facts_mod._plural(n, 'page')} of {name} {verb}{words}"
            if int(count or 0) and said not in block_text:
                lines.append(f"- {said}")
    if not lines:
        return block_text, block_html
    head = "Could not be read:"
    text = "\n\n".join(p for p in (block_text, head + "\n" + "\n".join(lines)) if p)
    return text, block_html + facts_mod.html_blocks([(head, lines)])


def with_held(
    block_text: str, block_html: str, records: list[dict[str, Any]], holds: dict[int, str]
) -> tuple[str, str]:
    """The facts block with every held record under "Not established", so no
    record the sender asked for is ever left out of the reply."""
    if not holds:
        return block_text, block_html
    lines = [held_line(str(r.get("asked") or ""), holds[int(r["n"])]) for r in records if int(r["n"]) in holds]
    head = "Not established, still to be read:"
    text = "\n\n".join(p for p in (block_text, head + "\n" + "\n".join(lines)) if p)
    return text, block_html + facts_mod.html_blocks([(head, lines)])


def compose(block_text: str, block_html: str, *, held: bool) -> tuple[str, str]:
    """The reply's text and html. Delivered: the opening line and the block.
    Held: the block (words only, nothing attached) and the held line."""
    parts_t: list[str] = []
    parts_h: list[str] = []
    if not held:
        parts_t.append(OPENING)
        parts_h.append(f"<p>{html_lib.escape(OPENING)}</p>")
    if block_text:
        parts_t.append(block_text)
        parts_h.append(block_html)
    if held:
        parts_t.append(HELD_LINE)
        parts_h.append(f"<p>{html_lib.escape(HELD_LINE)}</p>")
    return "\n\n".join(parts_t), "".join(parts_h)


def render(results: dict[str, dict[str, Any]], files: list[dict[str, Any]], matter_id: str) -> facts_mod.Facts:
    """The contract's renderer, bound to the one matter this job read."""
    return facts_mod.render(results, files, staged_matter=matter_id, read_matters={matter_id})


__all__ = ["HELD_LINE", "OPENING", "compose", "found", "merge", "outcomes", "ranges_to_cut", "render"]
