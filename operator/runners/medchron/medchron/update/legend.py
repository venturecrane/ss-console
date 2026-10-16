"""A page's own codes, written as what they mean. $0.

A treatment log may record each visit as letter codes ("C", "R", "LSD")
that a LEGEND or KEY on the same page defines. Compose is told to write the
meaning (``prompts/map-system.md``); this is the deterministic check after it
(2026-10-14 parity run: 19 entry lines quoted bare codes an attorney cannot
read).

* An entry line carrying 2+ code tokens of a legend on a page it cites has
  each code whose meaning the legend states once, legibly, replaced by that
  meaning (``expand``).
* A code the legend defines twice, or illegibly, is never guessed: the line
  keeps it and is logged, and the audit is told to read the legend off the
  page image and to hold the claim unsupported unless it states the code's
  meaning (``audit_note``).

A code token is a legend key standing alone in the line: quoted ("C" or
"C,"), or one of a run of keys ("C, T, L"), except a run of side letters
(R, L, B) before a lowercase word: "R and L shoulders" describes sides.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import body as B
from ..audit import claims as CL

_HEAD = re.compile(r"(?im)^\s*(?:legend|key|abbreviations?|codes?)\s*:?\s*$")
_ROW = re.compile(r"^\s*([A-Z]{1,4})\s*(?:-|=|:|–|—)\s*(\S.*?)\s*$")
_UNREADABLE = re.compile(r"\[illegible\]|\?|not legible", re.I)
_SIDES = {"R", "L", "B"}
_QUOTED = r'"(?P<q>{k})(?P<p>[,.;]?)"'
_KEYS_RUN = r"(?<![A-Za-z0-9]){k}(?![A-Za-z0-9])"
NOTE = (
    "The claim quotes a code ({codes}) that a legend on the cited page defines, and the legend's meaning for it "
    "could not be read unambiguously from the transcription. Read the legend on the page image. The claim is "
    "unsupported unless it states what the code means; a bare code is unreadable to its reader."
)


@dataclass
class Legend:
    meaning: dict[str, str] = field(default_factory=dict)
    unclear: set[str] = field(default_factory=set)

    def keys(self) -> set[str]:
        return set(self.meaning) | self.unclear


def _add(lg: Legend, key: str, text: str | None) -> None:
    """Record `key` as `text`, or as unclear (None, illegible, or a second,
    different meaning: "L" for LIGHT in one column and LUMBAR in another)."""
    if text is None or _UNREADABLE.search(text) or key in lg.unclear:
        lg.unclear.add(key)
        lg.meaning.pop(key, None)
    elif key in lg.meaning and lg.meaning[key] != text:
        lg.unclear.add(key)
        del lg.meaning[key]
    else:
        lg.meaning[key] = text


def parse(page: str | None) -> Legend:
    """The legend on `page`: rows under each LEGEND / KEY heading, up to the
    first line that is not a row (blank lines between rows are kept)."""
    lg = Legend()
    text = page or ""
    for m in _HEAD.finditer(text):
        for ln in text[m.end() :].splitlines():
            if not ln.strip():
                continue
            r = _ROW.match(ln)
            if not r:
                break
            _add(lg, r.group(1), r.group(2))
    return lg


def merged(pages: list[str]) -> Legend:
    out = Legend()
    for p in pages:
        lg = parse(p)
        for k, v in lg.meaning.items():
            _add(out, k, v)
        for k in lg.unclear:
            _add(out, k, None)
    return out


def _spoken(text: str) -> str:
    return text.lower() if text.upper() == text else text


def tokens(line: str, keys: set[str]) -> list[re.Match[str]]:
    """The code tokens of `line`: quoted keys, and keys in a run of two or
    more keys that no lowercase word follows."""
    if not keys:
        return []
    alt = "|".join(sorted((re.escape(k) for k in keys), key=len, reverse=True))
    got = list(re.finditer(_QUOTED.format(k=f"(?:{alt})"), line))
    taken = [(m.start(), m.end()) for m in got]
    bare = [
        m
        for m in re.finditer(_KEYS_RUN.format(k=f"(?P<q>{alt})"), line)
        if not any(a <= m.start() < b for a, b in taken)
    ]
    runs: list[list[re.Match[str]]] = []
    for m in bare:
        if runs and re.fullmatch(r"[\s,/]*(?:and|or)?[\s,/]*", line[runs[-1][-1].end() : m.start()]):
            runs[-1].append(m)
        else:
            runs.append([m])
    for run in runs:
        sides = {m.group("q") for m in run} <= _SIDES and re.match(r"\s+[a-z]", line[run[-1].end() :])
        if len(run) >= 2 and not sides:
            got.extend(run)
    return sorted(got, key=lambda m: m.start())


def _line_pages(line: str, entry: str) -> list[tuple[int, int]]:
    cites = list(B.CITE.finditer(line)) or list(B.CITE.finditer(entry))
    return [(int(c.group(1)), p) for c in cites for p in CL.parse_pages(c.group(2))]


def _rewrite(line: str, lg: Legend) -> tuple[str, list[str], list[str]]:
    """(the line, codes replaced, codes left unclear)."""
    toks = tokens(line, lg.keys())
    if len(toks) < 2:
        return line, [], []
    done, left, out, at = [], [], [], 0
    for m in toks:
        k = m.group("q")
        out.append(line[at : m.start()])
        if k in lg.meaning:
            out.append(_spoken(lg.meaning[k]) + (m.groupdict().get("p") or ""))
            done.append(k)
        else:
            out.append(line[m.start() : m.end()])
            left.append(k)
        at = m.end()
    out.append(line[at:])
    return "".join(out), done, left


def governing(pages: dict[int, dict[int, str]], ex: int, page: int) -> str:
    """The page text whose legend governs `page`: the page itself when it
    carries one, else the nearest earlier page of the same document that
    does (a treatment log's continuation sheets may print none)."""
    doc = pages.get(ex, {})
    for p in range(page, 0, -1):
        if parse(doc.get(p)).keys():
            return doc.get(p) or ""
    return ""


def expand(entries: list[str], pages: dict[int, dict[int, str]]) -> tuple[list[str], list[dict]]:
    """(entries with legend codes written as their meanings, a log row per
    line changed or left holding an unclear code). `pages` is
    {exhibit: {page: text}}."""
    out, log = [], []
    for e in entries:
        lines = []
        for ln in e.split("\n"):
            src = [governing(pages, x, p) for x, p in _line_pages(ln, e)]
            lg = merged(src)
            new, done, left = _rewrite(ln, lg) if lg.keys() else (ln, [], [])
            if done or left:
                cites = sorted({f"{x}:{p}" for x, p in _line_pages(ln, e)})
                log.append({"cites": cites, "expanded": sorted(set(done)), "unclear": sorted(set(left))})
            lines.append(new)
        out.append("\n".join(lines))
    return out, log


def audit_note(claim: str, pages: list[str]) -> str:
    """The audit's instruction for a claim still quoting a code the legend
    on its cited pages does not define unambiguously, else ''."""
    lg = merged(pages)
    left = sorted({m.group("q") for m in tokens(claim, lg.keys()) if m.group("q") not in lg.meaning})
    return NOTE.format(codes=", ".join(f'"{k}"' for k in left)) if left else ""
