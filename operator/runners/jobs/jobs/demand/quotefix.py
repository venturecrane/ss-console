"""Deterministic repair of the drafting gate's quote findings, before a hold.

Practice job 2 on 2026-10-06 ran every paid stage and then held on ONE gap
audit sentence: a quotation a few words off the record ("[2a] quoted passage
is not contiguous in any source"). A near-quote is a fixable defect, not a
reason to throw away a paid delivery. So, for each such finding, and with no
model call:

* when the record carries a region that matches the quoted words closely
  (word-level similarity at or above ``THRESHOLD``), the quoted span is
  replaced with the record's own words from that region ("quote normalized to
  source"): the quotation is then verbatim, as the discipline requires;
* otherwise the quotation marks come off and the sentence stands as a
  paraphrase, keeping its citation ("quote converted to paraphrase"): nothing
  is presented as the record's words that is not.

A deposition's text layer carries a line number and middle-dot spacers on
every line ("20· ·Q.· · ·So ..."). The record's words are read from the
checker's gutter view, which drops them, so a repair keeps the Q./A. labels
and nothing else of the typesetting; a replacement that would still carry a
spacer or a line number before a label is not written (2026-11-03: a brief
received "· ·So ... 21· ·india; juliet? 22· ·A.· · ·Kilo ..."), and the gate
refuses the quote instead.

The checker's own extraction and normalization are reused (loaded from the
checker file the gate runs), so a span is found here exactly as the gate found
it. Every repair is returned as a line for the attorney notes.
"""

from __future__ import annotations

import difflib
import importlib.util
import re
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

THRESHOLD = 0.85
_FINDING = re.compile(r'^\[2a\] quoted passage is not contiguous in any source: "(.*)"(?: — .*)?$', re.S)
_WORD = re.compile(r"\S+")


@lru_cache(maxsize=4)
def _checker(path: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location("_demand_gate_checker", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load the drafting gate checker at {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def checker() -> ModuleType:
    from smokeball_connector.record_check import checker_path

    return _checker(str(checker_path()))


def quote_findings(refusals: list[str]) -> list[str]:
    """The normalized quoted passages the gate could not find."""
    out = []
    for r in refusals:
        m = _FINDING.match(r.strip())
        if m:
            out.append(m.group(1))
    return out


def _key(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower())


#: What a transcript's typesetting leaves in its text layer and a quotation never
#: carries: the "·" spacers and a line number standing before a Q./A. label. A
#: replacement that would put either into the brief is refused, never written:
#: the gate would read it as verbatim (it IS in the raw text) and pass it.
_TYPESET = re.compile(r"·|(?:^|\s)\d{1,2} [QA][.:](?:\s|$)")


class _Source:
    """A source as the gate's gutter view reads it: transcript line numbers,
    "·" spacers and page markers removed, every other word as written. A
    region taken from it is contiguous in that same view, so a repaired
    quotation passes the gate as the record's own words."""

    def __init__(self, text: str, ck: ModuleType) -> None:
        self.text = ck.SourceView(Path("source"), "gutter", text.splitlines()).text
        self.spans = [(m.start(), m.end()) for m in _WORD.finditer(self.text)]
        self.keys = [_key(self.text[a:b]) for a, b in self.spans]
        self.index: dict[tuple[str, str, str], list[int]] = {}
        for i in range(len(self.keys) - 2):
            self.index.setdefault((self.keys[i], self.keys[i + 1], self.keys[i + 2]), []).append(i)

    def region(self, qk: list[str], start: int) -> tuple[float, int, int] | None:
        """(similarity, first word, end) of the source words the quotation
        stands for, near ``start``. BOTH ends move: a region whose start is a
        word early carries the line before's tail, a word late loses the
        quotation's first word or label. A region is ranked by its matched
        words over the LONGER of the two, so a changed end word reads as a
        substitution rather than a word to drop; ties go to the region closest
        to the quotation's own length, then to the one the word votes placed. The similarity returned is difflib's
        ratio, the figure ``THRESHOLD`` is set against."""
        best: tuple[tuple[float, int, int], float, int, int] | None = None
        for lo in range(max(0, start - 3), start + 4):
            for hi in range(lo + max(3, len(qk) - 3), min(len(self.keys), lo + len(qk) + 3) + 1):
                sm = difflib.SequenceMatcher(None, qk, self.keys[lo:hi], autojunk=False)
                matched = sum(m.size for m in sm.get_matching_blocks())
                rank = (matched / max(len(qk), hi - lo), -abs(hi - lo - len(qk)), -abs(lo - start))
                if best is None or rank > best[0]:
                    best = (rank, sm.ratio(), lo, hi)
        return None if best is None else best[1:]


def _best(quote: str, sources: list[_Source]) -> tuple[float, str, _Source] | None:
    """(similarity, the source's own words, the source they are from)."""
    qk = [_key(w) for w in quote.split()]
    if len(qk) < 3:
        return None
    best: tuple[float, str, _Source] | None = None
    for src in sources:
        votes: dict[int, int] = {}
        for i in range(len(qk) - 2):
            for pos in src.index.get((qk[i], qk[i + 1], qk[i + 2]), [])[:50]:
                votes[pos - i] = votes.get(pos - i, 0) + 1
        for start, _n in sorted(votes.items(), key=lambda kv: -kv[1])[:5]:
            span = src.region(qk, start)
            if span is not None and (best is None or span[0] > best[0]):
                best = (span[0], src.text[src.spans[span[1]][0] : src.spans[span[2] - 1][1]], src)
    return best


_END_PUNCT = ".,;:?!'"


def exact(words: str, src: _Source) -> str | None:
    """``words`` as the source's exact characters, punctuation included, or
    None. The gate's 2a tolerates a trailing mark the source does not carry
    (a quotation's end is the sentence's), so a repair that changed one (a
    semicolon for the record's comma, 2026-11-04) passed it. A repaired
    quotation is held to the source character for character instead: a
    trailing mark the source does not carry at that place comes off, and words
    that are still not the source's are not written."""
    while words:
        if words in src.text:
            return words
        if words[-1] not in _END_PUNCT:
            return None
        words = words[:-1].rstrip()
    return None


_CLOSE = re.compile(r"'[.,;:?!]*$")


def _fit(words: str, quote: str) -> str:
    """The record's words trimmed to the quotation. A nested quotation's mark
    is kept when its partner is inside the region (the record's own nested
    quotation, verbatim) and comes off when the region boundary cut its
    partner away (a stray mark). Trimming an end keeps the words a substring of
    the source, so they stay verbatim."""
    toks = words.split()
    opens = sum(1 for t in toks if t.startswith("'"))
    closes = sum(1 for t in toks if _CLOSE.search(t))
    if words.startswith("'") and not quote.startswith("'") and opens > closes:
        words, opens = words[1:], opens - 1
    if _CLOSE.search(words) and not _CLOSE.search(quote) and closes > opens:
        words = re.sub(r"'+([.,;:?!]*)$", r"\1", words)
    return words


def repair(
    md: str,
    refusals: list[str],
    source_texts: list[str],
    cited: Callable[[str, Any], str | None] | None = None,
) -> tuple[str, list[str]]:
    """``(the repaired markdown, a line per repair)``. Spans are rewritten from
    the end of the document backwards so earlier offsets stay valid.

    ``cited(md, quote)`` returns the text of the document the quotation's own
    cite names, when the lane can resolve one (drafting's pincheck): a region
    there is preferred to the same words in any other document, which may
    carry other punctuation (a later record restating the earlier one)."""
    wanted = quote_findings(refusals)
    if not wanted:
        return md, []
    ck = checker()
    sources = [_Source(t, ck) for t in source_texts if t.strip()]
    edits: list[tuple[int, int, str, str]] = []
    log_only: list[str] = []
    for q in ck.extract_quotes(md):
        if q.normalized not in wanted:
            continue
        start, end = q.start - 1, q.start + len(q.raw) + 1  # the quotation marks themselves
        near = None
        own = cited(md, q) if cited is not None else None
        if own and own.strip():
            near = _best(q.normalized, [_Source(own, ck)])
            if near is not None and near[0] < THRESHOLD:
                near = None
        if near is None:
            near = _best(q.normalized, sources)
        if near is not None and near[0] >= THRESHOLD:
            words = _fit(near[1], q.normalized)
            if q.raw.rstrip()[-1:] not in ".,;:":  # the sentence's punctuation, not the record's word
                words = words.rstrip(".,;:")
            held = exact(words, near[2])
            if held is None:
                log_only.append(f"quote not repaired, the record's words could not be matched exactly: {words[:120]}")
                continue
            words = held
            if _TYPESET.search(words):
                # Never splice typesetting into the brief: the quote stays as it
                # was and the gate refuses it, failing the job for the attorney.
                log_only.append(
                    f"quote not repaired, the record's region carries transcript typesetting: {words[:120]}"
                )
                continue
            edits.append((start, end, md[start] + words + md[end - 1], f"quote normalized to source ({near[0]:.2f})"))
        else:
            edits.append((start, end, q.raw, "quote converted to paraphrase"))
    log = []
    for start, end, new, what in sorted(edits, key=lambda e: -e[0]):
        log.append(f"{what}: {' '.join(md[start + 1 : end - 1].split())[:120]}")
        md = md[:start] + new + md[end:]
    return md, list(reversed(log)) + log_only


def source_texts(data: Path, firm: Any) -> list[str]:
    from . import gate

    sources, vision, _held = gate.collect(data, firm)
    return [t for _n, t in sources + vision]
