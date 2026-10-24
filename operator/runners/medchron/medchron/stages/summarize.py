"""`summarize`: the material pre-incident entries become one cited Prior
Medical History block. Paid (judgment tier), one call.

A first draft ran 41 pages and the firm called it far too long; their own
exemplar is 141 pages at identical density, so the vendor's standard is
disproportionate for a two-provider, six-month case. Post-incident treatment
keeps full detail because that is the claim; pre-incident history becomes
the few sentences a reviewer needs, each keeping its citation. Nothing is
discarded: the full entries stay on disk and the block says the encounters
are itemisable on request.

Containment, not equality: summarising legitimately merges adjacent ranges
from one exhibit, which names no page the source did not. A citation
reaching a page the source never cited, or an exhibit it never used, is a
summary reaching beyond the record. It is decided by rule, never a stop
(2026-10-24 audit, row 9): one retry is told exactly what reached too far;
still beyond, no block is kept and every pre-incident entry is itemized in
the body (under itemize_all they already are), recorded in
prior_history_itemized.json and disclosed in the limitations. The heading is
set here in code (row 10), so build_doc never refuses on its wording.

Under `chronology.pre_incident_history: itemize_all` the block is written and
checked exactly the same way, and the body after it carries EVERY entry,
pre- and post-incident, in date order: the firm asked that no pre-incident
visit be left out.
"""

from __future__ import annotations

import json
import re

from .. import llm, prompts
from .base import StageRun

ENTRY = re.compile(r"(?m)^(?=\d{2}/\d{2}/\d{4}\s*(?:\(|$))")
DATE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})")
CITE = re.compile(r"\(Exhibit (\d+)(?: - p\. ([0-9,\s\-]+))?\)")


def pages_by_exhibit(text: str) -> dict[int, set[int]]:
    out: dict[int, set[int]] = {}
    for m in CITE.finditer(text):
        got = out.setdefault(int(m.group(1)), set())
        if not m.group(2):
            continue
        for tok in re.split(r",\s*", m.group(2).strip()):
            rng = re.match(r"(\d+)\s*-\s*(\d+)$", tok.strip())
            if rng:
                got.update(range(int(rng.group(1)), int(rng.group(2)) + 1))
            elif tok.strip().isdigit():
                got.add(int(tok.strip()))
    return out


def beyond_source(source: str, summary: str) -> list[str]:
    src_p, new_p = pages_by_exhibit(source), pages_by_exhibit(summary)
    bad: list[str] = []
    for ex, pages in new_p.items():
        if ex not in src_p:
            bad.append(f"Exhibit {ex} never cited in source")
            continue
        stray = sorted(pages - src_p[ex])
        if stray:
            bad.append(f"Exhibit {ex} pages {stray[:6]} not in source")
    return bad


def _iso(entry: str) -> str:
    m = DATE.match(entry)
    return f"{m.group(3)}-{m.group(1)}-{m.group(2)}" if m else ""


#: Written when no block could be kept (the pre-incident entries are itemized
#: instead); stages/limitations.py discloses it.
FALLBACK = "prior_history_itemized.json"
HEADING = "Prior Medical History"
# A first line that is a heading of the model's own wording: markdown, bold,
# a trailing colon, or a short line naming a history. The heading is set here
# in code, so build_doc's PRIOR_HEAD check can never refuse on wording.
_HEADISH = re.compile(r"^\s*(#{1,6}\s|\*\*.*\*\*\s*:?\s*$|[^.]{0,80}:\s*$)")
_HISTORY_LINE = re.compile(r"(?i)^[^.]{0,60}\bhistory\b[^.]{0,30}$")


def _is_heading(line: str) -> bool:
    line = line.strip()
    if not line or "(Exhibit" in line or len(line) > 90:
        return False  # a cited line is prose, never a heading
    return bool(_HEADISH.match(line) or _HISTORY_LINE.match(line))


def with_heading(block: str) -> str:
    """The block under the house heading, whatever heading the model wrote
    (or none). Only a heading line is replaced; a first line of prose stays."""
    lines = block.strip().splitlines()
    if lines and _is_heading(lines[0]):
        lines = lines[1:]
    return (HEADING + "\n\n" + "\n".join(lines).strip()).strip()


def _summary(sr: StageRun, pre: list[str]) -> str | None:
    """The cited block, or None when two attempts both reach beyond the
    source. The retry is told exactly which citations reached too far: the
    common case is merged ranges ("pp. 3-7" for "pp. 3, 5" and "p. 7")."""
    source = "\n\n".join(pre)
    messages: list[dict[str, str]] = [{"role": "user", "content": source}]
    for attempt in (1, 2):
        r = sr.doorway.call(
            "summarize",
            model=llm.model_for(sr.cfg, "judgment"),
            system=prompts.load("summarize-system", sr.cfg),
            # Thinking counts toward max_tokens; at effort high a 4000 cap can be
            # spent on reasoning before the summary is written. The cap only
            # bounds, it is not billed unless used.
            max_tokens=16000,
            messages=messages,
            effort="high",
            timeout=240.0,
            custom_id="summarize" if attempt == 1 else "summarize-retry",
        )
        block = with_heading(r.text)
        bad = beyond_source(source, block)
        if not bad:
            return block
        sr.log(f"summary attempt {attempt} reaches beyond the source record:")
        for b in bad[:5]:
            sr.log(f"    - {b}")
        messages = messages + [
            {"role": "assistant", "content": r.text.strip()},
            {
                "role": "user",
                "content": "These citations name pages the entries never cite: "
                + "; ".join(bad)
                + ". Rewrite the section. Cite each page exactly as the entries cite it; never join two cited "
                "pages into a range that covers a page the entries do not cite.",
            },
        ]
    return None


def run(sr: StageRun) -> int:
    d = sr.slug_dir / "runs" / sr.unit.unit
    incident = sr.job.incident_date
    src_p = d / "entries_condensed.md"
    if not src_p.is_file():
        src_p = d / "entries_final.md"
    entries = [e.strip() for e in ENTRY.split(src_p.read_text(encoding="utf-8")) if e.strip() and DATE.match(e.strip())]
    pre, post = [], []
    for e in entries:
        m = DATE.match(e)
        iso = f"{m.group(3)}-{m.group(1)}-{m.group(2)}" if m else ""
        (pre if iso < incident else post).append(e)
    itemize_all = sr.cfg.itemize_all_pre_incident
    if itemize_all:
        sr.log(
            f"{sr.unit.unit}: {len(pre)} pre-incident entries -> summary AND itemized (itemize_all), "
            f"{len(post)} post-incident kept in full"
        )
    else:
        sr.log(f"{sr.unit.unit}: {len(pre)} pre-incident entries -> summary, {len(post)} post-incident kept in full")
    (d / FALLBACK).unlink(missing_ok=True)
    if not pre:
        (d / "entries_scoped_final.md").write_text("\n\n".join(post), encoding="utf-8")
        return 0
    block = _summary(sr, pre)
    if block is None:
        # Still beyond the record after the retry: decided by rule, never a
        # stop. Every pre-incident entry is itemized (under itemize_all they
        # already are, so the block is simply omitted), and the limitations
        # say the history is itemized rather than summarized.
        body = sorted(entries, key=_iso)
        (d / FALLBACK).write_text(
            json.dumps({"itemized": len(pre), "policy": "itemize_all" if itemize_all else "itemize_pre_incident"}),
            encoding="utf-8",
        )
        sr.log(f"no Prior Medical History block: {len(pre)} pre-incident entries itemized in the body instead")
        (d / "entries_scoped_final.md").write_text("\n\n".join(body), encoding="utf-8")
        return 0
    sr.log(f"summary {len(block.split())} words; every cited page is one the source entries cited")
    # itemize_all: the block leads, then EVERY entry in date order, because the
    # firm asked that no pre-incident visit be left out of the itemized body.
    body = sorted(entries, key=_iso) if itemize_all else post
    out = block + "\n\n" + "\n\n".join(body)
    (d / "entries_scoped_final.md").write_text(out, encoding="utf-8")
    before, after = sum(len(e.split()) for e in entries), len(out.split())
    sr.log(f"body words {before} -> {after} ({after / before * 100:.0f}%)")
    return 0
