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
) -> list[dict[str, Any]]:
    """One ``{n, outcome, files}`` per record asked for. ``attached`` when any
    of its items is on the reply; else ``unplaced`` (found, not attached: our
    defect), ``not_established``, ``not_found``; ``unresolved`` when no file on
    the matter could be tied to it."""
    heads: dict[int, set[str]] = {}
    for it in rendered_items:
        n = owners.get((str(it.get("file_id")), int(it.get("index") or 0)))
        if n is not None:
            heads.setdefault(n, set()).add(str(it.get("heading")))
    out = []
    for rec in records:
        n = int(rec["n"])
        got = set() if n in unresolved else heads.get(n, set())
        outcome = next((h for h in _ORDER if h in got), "unresolved")
        out.append({"n": n, "outcome": outcome, "files": int(files_by_n.get(n, 0))})
    return out


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
