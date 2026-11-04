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


#: The file id prefix a hold reason names (``<code>: <8 hex> ...``).
_HELD_FILE = re.compile(r"^[a-z_]+: ([0-9a-f]{8})\b")
#: Item classes, best first: the ONE heading a record goes under (2026-11-03,
#: dry run 3: record 5 sat under "Not found" and "still to be read" at once).
_ATTACHED, _PARTLY, _NOT_FOUND = range(3)


def _klass(item: dict[str, Any]) -> int:
    if item.get("found") and item.get("ranges"):
        return _ATTACHED
    return _NOT_FOUND if item.get("established") else _PARTLY


def one_heading(
    located: list[tuple[int, str, dict[str, Any]]], holds: dict[int, str], files: dict[str, Any]
) -> tuple[list[tuple[int, str, dict[str, Any]]], dict[int, list[str]], dict[str, Any]]:
    """Each record's results reduced to its ONE heading, in this order:
    attached; held ("still to be read"); not established on a partly read
    file; not found (every page read). A held record's results all leave the
    block and come back as where it was looked for, folded into its one held
    entry (``looked``, by record n). A record's results are grouped by the
    file's sha256: two file ids with one sha are one upload twice (dry run 3:
    a9a240c1 and fc1e2f7b, one 3-page file uploaded twice, rendered as one
    line twice under the first name), kept once, under the first file tried.

    ``folds``: what a record's one entry still owes the reader from the results
    its heading dropped (dry run 4, 2026-11-04: record 3 asked for two visits,
    one was attached, and the reply said nothing of the other, looked for and
    not found). An attached record's item not found anywhere becomes a sentence
    on its attached line (``folds["attached"][n]``); a record not established
    on a partly read file says the files it was looked for in and not found
    (``folds["lines"]``, a line's start and the sentence it ends with)."""
    best: dict[int, int] = {}
    for n, _fid, result in located:
        for item in result.get("items") or []:
            best[n] = min(best.get(n, _NOT_FOUND), _klass(item))
    kept: list[tuple[int, str, dict[str, Any]]] = []
    looked: dict[int, list[str]] = {}
    seen: set[tuple[int, str]] = set()  # (record, sha256, else the file id)
    named: dict[str, str] = {}
    cut = {fid for _n, fid, r in located for i in r.get("items") or [] if _klass(i) == _ATTACHED}
    owed: dict[tuple[int, str], list[str]] = {}
    for n, fid, result in located:
        if n not in holds and best.get(n) != _NOT_FOUND:
            _owe(owed, n, fid, result, best[n])
        key = (n, str((files.get(fid) or {}).get("sha256") or fid))
        if n in holds:
            if key not in seen:
                seen.add(key)
                looked.setdefault(n, []).extend(_looked(fid, result, files, named))
            continue
        # The sha is taken only by a result that carries the record's best
        # class: the first same-sha result may be a not-found with a page not
        # reached while the second copy found it (review of #3725).
        items = [i for i in result.get("items") or [] if _klass(i) == best.get(n)]
        if items and key not in seen:
            seen.add(key)
            # Two files with one name (dry run 3: two 3-page files read as one
            # line twice): the second is said to be one. A cut file keeps its name.
            name = str(result.get("name") or "")
            shown = name if fid in cut or named.setdefault(name, fid) == fid else f"a second file also named {name}"
            kept.append((n, fid, {**result, "items": items, "name": shown}))
    return kept, looked, _folds(owed, kept, best, located)


def _asked(item: dict[str, Any]) -> str:
    return facts_mod._tame(item.get("asked")) or "the record asked for"


def _owe(owed: dict[tuple[int, str], list[str]], n: int, fid: str, result: dict[str, Any], best: int) -> None:
    """Where one dropped item of record ``n`` was not found in this file, in
    the reply's words, keyed by the item's own words."""
    name = facts_mod._name(result.get("name") or "the file")
    counts = result.get("counts") or {}
    total = int(counts.get("pages") or result.get("pageCount") or 0)
    pointer = facts_mod._Renderer(facts_mod.KNOWN_CAPS).pointer
    for item in result.get("items") or []:
        if _klass(item) == best or _klass(item) == _ATTACHED:
            continue
        if item.get("established"):
            where = f"in {name} (all {_pages(total)} read)"
        else:
            closest = "; ".join(pointer(p) for p in item.get("closest") or [])
            where = f"on the {int(counts.get('read') or 0):,} pages read of {name}"
            where += f" (closest: {closest})" if closest else ""
        owed.setdefault((n, _asked(item)), []).append(where)


def _folds(
    owed: dict[tuple[int, str], list[str]],
    kept: list[tuple[int, str, dict[str, Any]]],
    best: dict[int, int],
    located: list[tuple[int, str, dict[str, Any]]],
) -> dict[str, Any]:
    found = {(n, _asked(i)) for n, _f, r in located for i in r.get("items") or [] if _klass(i) == _ATTACHED}
    partly = {n: _asked(i) for n, _f, r in kept for i in r.get("items") or [] if _klass(i) == _PARTLY}
    attached: dict[int, list[str]] = {}
    lines: list[tuple[str, str]] = []
    for (n, asked), wheres in owed.items():
        wheres = list(dict.fromkeys(wheres))
        if best.get(n) == _ATTACHED and (n, asked) not in found:
            attached.setdefault(n, []).append(f"{asked[:1].upper()}{asked[1:]} was not found {' or '.join(wheres)}.")
        elif best.get(n) == _PARTLY and n in partly:
            lines.append((f"- {partly[n]}: not found on the", f"Also looked for in {_and([w[3:] for w in wheres])}."))
    return {"attached": attached, "lines": lines}


def with_folds(
    block_text: str, block_html: str, folds: dict[str, Any] | None, files: list[dict[str, Any]]
) -> tuple[str, str]:
    """Each fold's sentence at the end of its record's one line, text and html
    alike: an attached record's on its last attached file's line."""
    if not folds:
        return block_text, block_html
    ends = list(folds.get("lines") or [])
    for n, sentences in (folds.get("attached") or {}).items():
        mine = [f for f in files if int(f.get("n") or 0) == n]
        if mine:
            ends.append((f"- {facts_mod._name(mine[-1].get('name'))}, ", " ".join(sentences)))
    lines = block_text.split("\n")
    for start, sentence in ends:
        at = next((k for k, ln in enumerate(lines) if ln.startswith(start)), None)
        if at is None:
            continue
        old = lines[at]
        lines[at] = f"{old}{' ' if old.endswith('.') else '. '}{sentence}"
        li = f"<li>{html_lib.escape(old[2:])}</li>"
        block_html = block_html.replace(li, f"<li>{html_lib.escape(lines[at][2:])}</li>", 1)
    return "\n".join(lines), block_html


def _looked(fid: str, result: dict[str, Any], files: dict[str, Any], named: dict[str, str]) -> list[str]:
    """Where a held record was looked for in one file, in the reply's words.
    A second file with the same name is said to be one, never shown as a repeat."""
    name = facts_mod._name(result.get("name") or (files.get(fid) or {}).get("name") or "the file")
    shown = name if named.setdefault(name, fid) == fid else f"a second file also named {name}"
    counts = result.get("counts") or {}
    total = int(counts.get("pages") or result.get("pageCount") or 0)
    read = int(counts.get("read") or 0)
    pointer = facts_mod._Renderer(facts_mod.KNOWN_CAPS).pointer
    out = []
    for item in result.get("items") or []:
        if item.get("established"):
            out.append(f"{shown} (all {_pages(total)} read)")
            continue
        closest = "; ".join(pointer(p) for p in item.get("closest") or [])
        out.append(f"{shown} ({read:,} of {_pages(total)} read" + (f"; closest: {closest})" if closest else ")"))
    return list(dict.fromkeys(out))


def _pages(n: int) -> str:
    return f"{n:,} {'page' if n == 1 else 'pages'}"


def _and(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def held_line(asked: str, reason: str, looked: list[str] | None = None, files: dict[str, Any] | None = None) -> str:
    """One held record, in the reply's words: its own words, where it was
    looked for, and why it is not established yet, never a dollar figure, a
    setting name or a file id."""
    reason = str(reason or "")
    parts = [f"looked for in {_and(looked)}"] if looked else []
    m, f = _HELD_PAGES.search(reason), _HELD_FILE.match(reason)
    if reason.startswith("scope_unresolved"):
        parts.append("no file on the matter is named for it")
    elif m:
        n = int(m.group(1))
        which = next((v.get("name") for k, v in (files or {}).items() if f and k.startswith(f.group(1))), None)
        where = facts_mod._name(which) if which else "the file it is in"
        parts.append(f"{_pages(n)} of {where} {'is' if n == 1 else 'are'} not read yet")
    why = "; ".join(parts) + ", and SMD is finishing it" if parts else "SMD is finishing it"
    return f"- {facts_mod._tame(asked) or 'the record asked for'}: not established yet; {why}"


def with_gaps(
    block_text: str,
    block_html: str,
    results: dict[str, dict[str, Any]],
    files: list[dict[str, Any]],
    folds: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """The facts block, plus each ATTACHED file with pages the model could not
    read: a record found on the readable pages goes out (2026-11-03: 47 pages,
    23 unreadable, the visit found on 1-2 and 6-39), and the reply says, in the
    contract's gap words, how many pages of that file were not read. A file the
    renderer already named in a "Not established" heading, or a record's own
    entry, with the same count is not named twice. Each record's ``folds``
    (:func:`one_heading`) end its line first."""
    block_text, block_html = with_folds(block_text, block_html, folds, files)
    lines = []
    before = block_text.splitlines()
    for fid in dict.fromkeys(str(f.get("file_id")) for f in files):
        result = results.get(fid) or {}
        counts = result.get("counts") or {}
        name = facts_mod._name(result.get("name") or "the file")
        for count, words in ((counts.get("unreadable"), "could not be read"), (counts.get("notReached"), "not read")):
            n = int(count or 0)
            verb = "" if words.startswith("could") else ("was " if n == 1 else "were ")
            said = f"{facts_mod._plural(n, 'page')} of {name} {verb}{words}"
            if n and not any(name in ln and f"{n:,} " in ln for ln in before):
                lines.append(f"- {said}")
    if not lines:
        return block_text, block_html
    head = "Could not be read:"
    text = "\n\n".join(p for p in (block_text, head + "\n" + "\n".join(lines)) if p)
    return text, block_html + facts_mod.html_blocks([(head, lines)])


def with_held(
    block_text: str,
    block_html: str,
    records: list[dict[str, Any]],
    holds: dict[int, str],
    looked: dict[int, list[str]] | None = None,
    files: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """The facts block with every held record under "Not established", so no
    record the sender asked for is ever left out of the reply; each with
    where it was looked for (:func:`one_heading`)."""
    if not holds:
        return block_text, block_html
    lines = [
        held_line(str(r.get("asked") or ""), holds[int(r["n"])], (looked or {}).get(int(r["n"])), files)
        for r in records
        if int(r["n"]) in holds
    ]
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
    return _once("\n\n".join(parts_t)), _LI_TWICE.sub(r"\1", "".join(parts_h))


#: An html list line repeated straight after itself.
_LI_TWICE = re.compile(r"(<li>[^<]*</li>)(?:\1)+")


def _once(text: str) -> str:
    """No bullet line twice in one block (two same-named files, one count)."""
    out: list[str] = []
    seen: set[str] = set()
    for line in text.split("\n"):
        if not line.strip():
            seen = set()
        elif line.startswith("- ") and line in seen:
            continue
        seen.add(line)
        out.append(line)
    return "\n".join(out)


def render(results: dict[str, dict[str, Any]], files: list[dict[str, Any]], matter_id: str) -> facts_mod.Facts:
    """The contract's renderer, bound to the one matter this job read."""
    return facts_mod.render(results, files, staged_matter=matter_id, read_matters={matter_id})


__all__ = [
    "HELD_LINE",
    "OPENING",
    "compose",
    "found",
    "merge",
    "one_heading",
    "with_folds",
    "outcomes",
    "ranges_to_cut",
    "render",
]
