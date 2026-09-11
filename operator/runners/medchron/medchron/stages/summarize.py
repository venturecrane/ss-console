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
summary reaching beyond the record: exit 1.
"""

from __future__ import annotations

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
    sr.log(f"{sr.unit.unit}: {len(pre)} pre-incident entries -> summary, {len(post)} post-incident kept in full")
    if not pre:
        (d / "entries_scoped_final.md").write_text("\n\n".join(post), encoding="utf-8")
        return 0
    r = sr.doorway.call(
        "summarize",
        model=llm.model_for(sr.cfg, "judgment"),
        system=prompts.load("summarize-system", sr.cfg),
        max_tokens=4000,
        messages=[{"role": "user", "content": "\n\n".join(pre)}],
        effort="",
        timeout=240.0,
        custom_id="summarize",
    )
    block = r.text.strip()
    bad = beyond_source("\n".join(pre), block)
    if bad:
        sr.log("REJECTED: summary reaches beyond the source record:")
        for b in bad[:5]:
            sr.log(f"    - {b}")
        return 1
    sr.log(f"summary {len(block.split())} words; every cited page is one the source entries cited")
    out = block + "\n\n" + "\n\n".join(post)
    (d / "entries_scoped_final.md").write_text(out, encoding="utf-8")
    before, after = sum(len(e.split()) for e in entries), len(out.split())
    sr.log(f"body words {before} -> {after} ({after / before * 100:.0f}%)")
    return 0
