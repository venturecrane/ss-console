#!/usr/bin/env python3
"""
UI drift audit — counts visual-design anti-patterns across the codebase and
produces a surfaces × rules matrix. Output is a markdown table with per-file
violation counts, ready to seed `docs/style/UI-PATTERNS.md` anti-pattern
citations.

Audits against the six rules in the plan (status display, redundancy ban,
button hierarchy, heading skip, typography scale, spacing rhythm) without
enforcing them — this audit informs Phase 2, it doesn't gate.

Usage:
  python3 .agents/skills/ui-drift-audit/audit.py [--out PATH]

Default output: .design/audits/ui-drift-{YYYY-MM-DD}.md
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_DIRS = [REPO_ROOT / "src/pages", REPO_ROOT / "src/components"]
AUDIT_EXTS = {".astro", ".tsx", ".jsx"}

# --- Pattern definitions ---------------------------------------------------

# Pill: rounded-full element that is tinted/filled as a status label, not an
# avatar or decorative circle. Heuristic: rounded-full on the same line as a
# tinted background (opacity suffix or *-100 tone). Excludes avatars (which
# pair rounded-full with fixed square dimensions and a base/surface bg).
PILL_RX = re.compile(
    r"rounded-full[^\"]*bg-\[color:var\(--color-[a-z-]+\)\]/\d+"
    r"|rounded-full[^\"]*bg-[a-z]+-(?:50|100|200)"
    r"|rounded-full[^\"]*\bbg-indigo-50\b"
)
# Note: ConsultantBlock-style avatar (`w-14 h-14 rounded-full bg-[color:var(--color-background)]`)
# falls through because its bg is a base color, not a tint.

# Inline typography: arbitrary sizes or Tailwind size tokens.
# Rule 5 target: all text resolves to a named scale token (text-display/title/heading/body/caption/label).
TYPO_ARB_RX = re.compile(r"text-\[[0-9.]+(?:px|rem|em)\]")
TYPO_TOKEN_RX = re.compile(r"\btext-(?:xs|sm|base|lg|xl|2xl|3xl|4xl|5xl|6xl|7xl|8xl|9xl)\b")

# Inline spacing: arbitrary values or raw Tailwind spacing tokens.
# Rule 6 target: gaps and padding resolve to rhythm tokens.
SPACING_ARB_RX = re.compile(r"(?:p[xytrbl]?|m[xytrbl]?|gap(?:-x|-y)?)-\[[^\]]+\]")
SPACING_TOKEN_RX = re.compile(r"\b(?:p[xytrbl]?|gap(?:-x|-y)?)-[0-9]+(?:\.5)?\b")

# Headings
HEADING_RX = re.compile(r"<h([1-6])\b", re.IGNORECASE)

# Primary-CTA indicators. Rule 3: one primary per view.
#
# Heuristic: bg-primary (or the var-reference equivalent) as a standalone
# class — NOT as a tinted background (bg-primary/5, bg-primary/90) and NOT
# as a compound modifier (bg-primary-hover). The element must also look
# button-shaped — paired with button-like padding (px-N or py-N) on the
# same line. This excludes:
#   - tinted backgrounds (bg-primary/5, bg-primary/10)
#   - hover/alternate colors (bg-primary-hover)
#   - progress bars (bg-primary on h-N or w-N elements without px/py)
#   - decorative icon circles (bg-primary text-white without px/py)
#
# Still a heuristic — state-branch conditional CTAs in a single file can
# inflate the count. Manual review required when count > 1.
PRIMARY_CTA_RX = re.compile(
    r"(?:bg-\[color:var\(--color-primary\)\](?!/)|bg-primary(?![-/\w]))[^\"]*?\bp[xy]?-[0-9]"
)

# --- Redundancy detection --------------------------------------------------

# Known STATUS states that appear in pills AND often in adjacent prose.
# Keep curated; entity-kind nouns (Proposal, Invoice, Quote, Engagement) are
# intentionally excluded — a "Quote" pill on a quotes page is a Rule 1 eyebrow
# misuse, not a Rule 2 redundancy, and matching them produces false positives
# on every entity-detail page.
STATUS_WORDS_RX = re.compile(
    r"\b(Signed|Paid|Pending|Sent|Viewed|Expired|Draft|Underway|"
    r"Accepted|Declined|Cancelled|Completed|Processing|Active|Overdue|"
    r"Ready|Published|Countersigned|Unpaid|Due|Approved|Rejected|"
    r"Scheduled|Confirmed|Open|Closed|Paused|Archived|Superseded|"
    r"Deposit|Final)\b",
    re.IGNORECASE,
)

REDUNDANCY_WINDOW = 10  # lines above+below


@dataclass
class FileReport:
    path: Path
    tier: str
    pills: int = 0
    typo_arb: int = 0
    typo_token: int = 0
    spacing_arb: int = 0
    spacing_token: int = 0
    headings: list[tuple[int, int]] = field(default_factory=list)  # (lineno, level)
    heading_skips: list[tuple[int, int, int]] = field(default_factory=list)  # (lineno, prev, cur)
    primary_ctas: int = 0
    redundancy_hits: list[tuple[int, str]] = field(default_factory=list)  # (lineno, word)

    @property
    def relpath(self) -> str:
        return str(self.path.relative_to(REPO_ROOT))

    def total_violations(self) -> int:
        return (
            self.pills
            + self.typo_arb
            + self.typo_token
            + self.spacing_arb
            + self.spacing_token
            + len(self.heading_skips)
            + max(self.primary_ctas - 1, 0)
            + len(self.redundancy_hits)
        )


# --- Tier classification ---------------------------------------------------

def classify_tier(path: Path) -> str:
    rel = str(path.relative_to(REPO_ROOT))
    if rel.startswith("src/pages/portal/") or rel.startswith("src/components/portal/"):
        return "client-portal"
    if rel.startswith("src/pages/admin/"):
        return "admin"
    if rel.startswith("src/pages/auth/"):
        return "auth"
    if rel.startswith("src/pages/book") or rel.startswith("src/components/booking/"):
        return "booking"
    if rel.startswith("src/pages/dev/"):
        return "dev-preview"
    return "public-marketing"


TIER_ORDER = [
    "client-portal",
    "admin",
    "booking",
    "public-marketing",
    "auth",
    "dev-preview",
]


# --- Audit logic -----------------------------------------------------------

def iter_source_files() -> Iterable[Path]:
    for d in SRC_DIRS:
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if p.suffix in AUDIT_EXTS and p.is_file():
                yield p


def count_headings(lines: list[str]) -> tuple[list[tuple[int, int]], list[tuple[int, int, int]]]:
    """Return (headings, skips). Skip = jump of >1 level in document order."""
    headings: list[tuple[int, int]] = []
    skips: list[tuple[int, int, int]] = []
    prev_level = 0
    for i, line in enumerate(lines, start=1):
        for m in HEADING_RX.finditer(line):
            level = int(m.group(1))
            headings.append((i, level))
            if prev_level and level > prev_level + 1:
                skips.append((i, prev_level, level))
            prev_level = level
    return headings, skips


def detect_redundancy(lines: list[str]) -> list[tuple[int, str]]:
    """Find pills whose status label is echoed in nearby prose.

    Heuristic:
    1. Find a line with a tinted pill (rounded-full + tint bg).
    2. Scan forward ≤5 lines for status keywords (pill content often lands in a
       ternary expression on a separate line from the opening element).
    3. For each status keyword found, scan ±WINDOW lines (excluding lines that
       themselves contain rounded-full) for word-bounded matches in prose.
    4. A match = redundancy hit.
    """
    hits: list[tuple[int, str]] = []
    for i, line in enumerate(lines, start=1):
        if not PILL_RX.search(line):
            continue

        # Forward window for pill content (Astro ternaries land below the open tag)
        fwd_lo = i - 1
        fwd_hi = min(len(lines), i + 5)
        pill_content = "\n".join(lines[fwd_lo:fwd_hi])
        candidates = {m.group(0).lower() for m in STATUS_WORDS_RX.finditer(pill_content)}
        if not candidates:
            continue

        # Surrounding prose window, excluding pill-line occurrences
        lo = max(0, i - 1 - REDUNDANCY_WINDOW)
        hi = min(len(lines), i + REDUNDANCY_WINDOW)
        prose_lines = [ln for idx, ln in enumerate(lines[lo:hi], start=lo + 1)
                       if not PILL_RX.search(ln)]
        prose_text = "\n".join(prose_lines)

        for word in sorted(candidates):
            pat = re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE)
            if pat.search(prose_text):
                hits.append((i, word))
                break  # one hit per pill
    return hits


def audit_file(path: Path) -> FileReport:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    rep = FileReport(path=path, tier=classify_tier(path))
    rep.pills = len(PILL_RX.findall(text))
    rep.typo_arb = len(TYPO_ARB_RX.findall(text))
    rep.typo_token = len(TYPO_TOKEN_RX.findall(text))
    rep.spacing_arb = len(SPACING_ARB_RX.findall(text))
    rep.spacing_token = len(SPACING_TOKEN_RX.findall(text))
    rep.primary_ctas = len(PRIMARY_CTA_RX.findall(text))
    rep.headings, rep.heading_skips = count_headings(lines)
    rep.redundancy_hits = detect_redundancy(lines)

    return rep


# --- Reporting -------------------------------------------------------------

def format_matrix(reports: list[FileReport]) -> str:
    # Group by tier
    by_tier: dict[str, list[FileReport]] = {}
    for r in reports:
        by_tier.setdefault(r.tier, []).append(r)

    out: list[str] = []
    out.append("| File | Tier | Pills | Typo (arb / token) | Spacing (arb / token) | H-skips | Primary CTAs | Redundancy |")
    out.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |")

    for tier in TIER_ORDER:
        if tier not in by_tier:
            continue
        files = sorted(by_tier[tier], key=lambda r: -r.total_violations())
        for r in files:
            out.append(
                f"| `{r.relpath}` | {r.tier} | {r.pills} | {r.typo_arb} / {r.typo_token} | "
                f"{r.spacing_arb} / {r.spacing_token} | {len(r.heading_skips)} | {r.primary_ctas} | {len(r.redundancy_hits)} |"
            )

    return "\n".join(out)


def format_tier_totals(reports: list[FileReport]) -> str:
    by_tier: dict[str, list[FileReport]] = {}
    for r in reports:
        by_tier.setdefault(r.tier, []).append(r)

    out: list[str] = []
    out.append("| Tier | Files | Pills | Typo (arb/token) | Spacing (arb/token) | H-skips | Primary>1 files | Redundancy hits |")
    out.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for tier in TIER_ORDER:
        if tier not in by_tier:
            continue
        files = by_tier[tier]
        pills = sum(f.pills for f in files)
        ta = sum(f.typo_arb for f in files)
        tt = sum(f.typo_token for f in files)
        sa = sum(f.spacing_arb for f in files)
        st = sum(f.spacing_token for f in files)
        hs = sum(len(f.heading_skips) for f in files)
        multi_primary = sum(1 for f in files if f.primary_ctas > 1)
        redund = sum(len(f.redundancy_hits) for f in files)
        out.append(
            f"| {tier} | {len(files)} | {pills} | {ta} / {tt} | {sa} / {st} | {hs} | {multi_primary} | {redund} |"
        )
    return "\n".join(out)


def format_redundancy_detail(reports: list[FileReport]) -> str:
    out: list[str] = []
    for r in sorted(reports, key=lambda r: -len(r.redundancy_hits)):
        if not r.redundancy_hits:
            continue
        out.append(f"\n### `{r.relpath}` — {len(r.redundancy_hits)} hit(s)")
        for lineno, word in r.redundancy_hits[:12]:
            out.append(f"- line {lineno}: pill text `{word}` echoed in surrounding context")
        if len(r.redundancy_hits) > 12:
            out.append(f"- …and {len(r.redundancy_hits) - 12} more")
    return "\n".join(out)


def format_heading_skip_detail(reports: list[FileReport]) -> str:
    out: list[str] = []
    for r in sorted(reports, key=lambda r: -len(r.heading_skips)):
        if not r.heading_skips:
            continue
        out.append(f"\n### `{r.relpath}`")
        for lineno, prev, cur in r.heading_skips:
            out.append(f"- line {lineno}: h{prev} → h{cur}")
    return "\n".join(out)


def build_report(reports: list[FileReport]) -> str:
    date = dt.date.today().isoformat()
    total_files = len(reports)
    total_violations = sum(r.total_violations() for r in reports)

    redundancy_block = format_redundancy_detail(reports) or "\n(none detected)"
    heading_block = format_heading_skip_detail(reports) or "\n(none detected)"

    header = f"""# UI drift audit — {date}

Generated by `.agents/skills/ui-drift-audit/audit.py`. Surfaces × rules matrix
informing Phase 2 (`docs/style/UI-PATTERNS.md`). Counts are source-level grep
matches; they approximate drift scale. Playwright/rendered-DOM checks are
earned-not-planned.

**Rule mapping.**

- **Pills** → Rule 1 (status display by context) + Rule 2 (redundancy ban)
- **Typo (arb/token)** → Rule 5 (typography scale). Arbitrary `text-[Npx]` is always a violation; `text-xs/sm/lg/xl` is a violation in covered contexts (post-Rule 5 tokens).
- **Spacing (arb/token)** → Rule 6 (spacing rhythm). Same shape as typography.
- **H-skips** → Rule 4 (heading skip ban).
- **Primary CTAs** → Rule 3 (one primary per view). Column shows count; violation is any file >1.
- **Redundancy** → Rule 2 (pill adjacent to matching text).

**Totals.** {total_files} files audited, {total_violations} raw violation candidates.

## Tier totals

{format_tier_totals(reports)}

## Per-file matrix

{format_matrix(reports)}

## Redundancy detail (Rule 2 seeds)
{redundancy_block}

## Heading-skip detail (Rule 4 seeds)
{heading_block}

---

## Notes

- Dev-preview surfaces (`src/pages/dev/*`) are documented but excluded from remediation priority; they exist to exercise portal primitives.
- Redundancy detection is a heuristic (pill text ±{REDUNDANCY_WINDOW} lines). Known false-positive patterns: (a) pill labels that are generic nouns; (b) sibling conditional branches (`isPaid ? ... : isExpired ? ...`) where the same state word appears in a branch that can never render simultaneously with the flagged pill. Review each hit before including as a Rule 2 anti-pattern citation.
- Token counts in Typo/Spacing are informational — Rule 5 and Rule 6 formally bind these violations only once tokens land in `src/styles/global.css @theme`. Until then, token-count columns size the Phase 3 Rule 5/6 remediation PRs.
- Primary CTA count >1 doesn't directly mean Rule 3 violation — the same component may render different states. Use the detail section to confirm before citing.
"""
    return header


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    default_out = REPO_ROOT / ".stitch" / "audits" / f"ui-drift-{dt.date.today().isoformat()}.md"
    parser.add_argument("--out", type=Path, default=default_out, help="output markdown path")
    args = parser.parse_args(argv)

    files = list(iter_source_files())
    reports = [audit_file(f) for f in files]
    report_md = build_report(reports)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report_md, encoding="utf-8")
    print(f"Wrote {args.out.relative_to(REPO_ROOT)}")
    print(f"  {len(reports)} files audited")
    print(f"  {sum(r.total_violations() for r in reports)} raw violation candidates")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
