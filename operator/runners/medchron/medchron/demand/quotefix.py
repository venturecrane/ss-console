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
from typing import Any

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


class _Source:
    def __init__(self, text: str, ck: ModuleType) -> None:
        self.text = ck.normalize(ck.strip_markdown(text))
        self.spans = [(m.start(), m.end()) for m in _WORD.finditer(self.text)]
        self.keys = [_key(self.text[a:b]) for a, b in self.spans]
        self.index: dict[tuple[str, str, str], list[int]] = {}
        for i in range(len(self.keys) - 2):
            self.index.setdefault((self.keys[i], self.keys[i + 1], self.keys[i + 2]), []).append(i)


def best_region(quote: str, sources: list[_Source]) -> tuple[float, str] | None:
    """(similarity, the source's own words) for the closest region, or None."""
    qk = [_key(w) for w in quote.split()]
    if len(qk) < 3:
        return None
    best: tuple[float, str] | None = None
    for src in sources:
        votes: dict[int, int] = {}
        for i in range(len(qk) - 2):
            for pos in src.index.get((qk[i], qk[i + 1], qk[i + 2]), [])[:50]:
                votes[pos - i] = votes.get(pos - i, 0) + 1
        for start, _n in sorted(votes.items(), key=lambda kv: -kv[1])[:5]:
            for delta in (0, -1, 1, -2, 2, -3, 3):
                a, b = max(0, start), min(len(src.keys), start + len(qk) + delta)
                if b - a < 3:
                    continue
                ratio = difflib.SequenceMatcher(None, qk, src.keys[a:b], autojunk=False).ratio()
                if best is None or ratio > best[0]:
                    best = (ratio, src.text[src.spans[a][0] : src.spans[b - 1][1]])
    return best


def repair(md: str, refusals: list[str], source_texts: list[str]) -> tuple[str, list[str]]:
    """``(the repaired markdown, a line per repair)``. Spans are rewritten from
    the end of the document backwards so earlier offsets stay valid."""
    wanted = quote_findings(refusals)
    if not wanted:
        return md, []
    ck = checker()
    sources = [_Source(t, ck) for t in source_texts if t.strip()]
    edits: list[tuple[int, int, str, str]] = []
    for q in ck.extract_quotes(md):
        if q.normalized not in wanted:
            continue
        start, end = q.start - 1, q.start + len(q.raw) + 1  # the quotation marks themselves
        near = best_region(q.normalized, sources)
        if near is not None and near[0] >= THRESHOLD:
            words = near[1]
            if q.raw.rstrip()[-1:] not in ".,;:":  # the sentence's punctuation, not the record's word
                words = words.rstrip(".,;:")
            edits.append((start, end, md[start] + words + md[end - 1], f"quote normalized to source ({near[0]:.2f})"))
        else:
            edits.append((start, end, q.raw, "quote converted to paraphrase"))
    log = []
    for start, end, new, what in sorted(edits, key=lambda e: -e[0]):
        log.append(f"{what}: {' '.join(md[start + 1 : end - 1].split())[:120]}")
        md = md[:start] + new + md[end:]
    return md, list(reversed(log))


def source_texts(data: Path, firm: Any) -> list[str]:
    from . import gate

    sources, vision, _held = gate.collect(data, firm)
    return [t for _n, t in sources + vision]
