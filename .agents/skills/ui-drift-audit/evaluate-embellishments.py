#!/usr/bin/env python3
"""
Embellishment evaluator — compares a Stitch-generated HTML against an
existing source file (or a manifest of known components) and flags
elements Stitch added that are NOT in the source.

Output categories:
  - Likely-feature: components that look like real product features
    Stitch invented (aggregate cards, filter/sort bars, summary widgets,
    settings panels).
  - Minor-polish: visual-only additions (decorative dividers, empty
    state illustrations already stripped, etc.).

Writes a markdown report to `.stitch/designs/<dir>/EMBELLISHMENTS.md`
listing each candidate with the exact HTML snippet and a suggested
disposition (ship / defer / reject). Humans decide.

This is NOT the strip pass. Strip removes hallucinations the prompt
explicitly forbade. Evaluator surfaces legitimate-looking suggestions
the prompt didn't mention.

Usage:
  python3 .agents/skills/ui-drift-audit/evaluate-embellishments.py \
    --stitch-dir .stitch/designs/portal-v2-spec-test \
    --source-dir src/pages/portal \
    --out .stitch/designs/portal-v2-spec-test/EMBELLISHMENTS.md
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

# Signatures — regex patterns that catch common "feature suggestion" shapes
# Stitch tends to produce. Each pattern has (category, name, regex).
#
# The regex extracts a representative snippet (the h2/h3 heading + a few
# lines of context) so the report can show the reviewer exactly what was
# added.

FEATURE_SIGNATURES = [
    # Aggregate summary cards: "Total X / Y" stat rows
    ("aggregate-stat",
     "Aggregate stat card (Total / Count / Average / Outstanding)",
     r"(Total\s+(?:Value|Outstanding|Revenue|Count|Invoices|Engagements|Amount)|Active\s+Count|Overdue\s+Amount)"),
    # Progress bar / percentage widgets
    ("progress-widget",
     "Progress / percentage widget",
     r"(\d+%\s+(?:complete|overdue|on[-\s]track)|progress\s+bar)"),
    # Auto-pay / settings strips
    ("auto-settings",
     "Auto-action or settings banner (Auto-pay, Auto-renew, etc.)",
     r"(Auto[-\s]pay|Auto[-\s]renew|Auto[-\s]bill|Automatic\s+payment)"),
    # Filter / sort toolbars
    ("filter-sort",
     "Filter or sort control bar",
     r"(Sort\s+by|Filter\s+by|Group\s+by|<select[^>]+sort)"),
    # Quick-action grids
    ("quick-actions",
     "Quick-actions grid (usually 3-4 tiled shortcuts)",
     r"(Quick\s+(?:actions|links|access)|Shortcuts)"),
    # Support / help sidebars
    ("support-widget",
     "Support or help widget / sidebar",
     r"(Need\s+help\?|Contact\s+support|Chat\s+with\s+us|Get\s+help)"),
    # Recent activity aggregator
    ("activity-widget",
     "Recent activity aggregator (if not in source task model)",
     r"(Last\s+\d+\s+days|This\s+week\s+at\s+a\s+glance|Activity\s+summary)"),
    # Notifications / alerts row
    ("notifications",
     "Notification / alert row",
     r"(<[^>]*role=\"alert\"[^>]*>|Notification\s+center|Alerts)"),
]

# Minor-polish signatures — visual decoration that's harmless but not
# spec'd. Usually stripped by strip.py but we flag here in case strip
# missed them.
POLISH_SIGNATURES = [
    ("decorative-flourish",
     "Decorative flourish (blur, gradient circle, ornament)",
     r"(blur-3xl|blur-2xl|bg-gradient-to|gradient-radial)"),
    ("pictographic",
     "Pictographic avatar / illustration (non-photo)",
     r"(<svg[^>]+class=\"[^\"]*w-(?:16|20|24)[^\"]*\"|illustration)"),
]


def extract_snippet(text: str, match_start: int, lines_before: int = 1,
                    lines_after: int = 3) -> str:
    """Grab a small window around a match position for the report."""
    # Convert position to line number
    line_no = text.count("\n", 0, match_start)
    lines = text.splitlines()
    lo = max(0, line_no - lines_before)
    hi = min(len(lines), line_no + lines_after + 1)
    return "\n".join(lines[lo:hi])[:500]


def scan_for_embellishments(text: str,
                             signatures: list[tuple[str, str, str]]
                             ) -> list[dict]:
    """Return a list of candidates: {category, name, snippet, line}."""
    hits: list[dict] = []
    for category, name, pattern in signatures:
        rx = re.compile(pattern, re.IGNORECASE)
        for m in rx.finditer(text):
            snippet = extract_snippet(text, m.start())
            line_no = text.count("\n", 0, m.start()) + 1
            hits.append({
                "category": category,
                "name": name,
                "snippet": snippet,
                "line": line_no,
                "matched_text": m.group(0)[:80],
            })
    return hits


def load_source_inventory(source_dir: Path) -> str:
    """Concatenate all .astro source files in source_dir + subdirs as a
    single corpus. Used to check whether a Stitch feature already exists
    in source (if the matched phrase appears in source, it's not new)."""
    parts: list[str] = []
    for path in source_dir.rglob("*.astro"):
        try:
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            pass
    return "\n".join(parts)


def filter_novel(hits: list[dict], source_corpus: str) -> list[dict]:
    """Drop hits whose matched phrase already appears in source corpus.
    These aren't novel Stitch additions — they exist in our code."""
    novel: list[dict] = []
    for h in hits:
        # Check a shortened version of the matched text against source.
        needle = h["matched_text"].lower().strip()
        # Trim any HTML tag noise for the match check
        needle = re.sub(r"[<>\"=]", " ", needle)
        # Normalize whitespace for a fairer substring check
        needle = re.sub(r"\s+", " ", needle).strip()
        if needle and needle in source_corpus.lower():
            continue
        novel.append(h)
    return novel


def format_report(stitch_dir: Path,
                   surfaces: dict[str, list[dict]]) -> str:
    out = [
        "# Embellishment evaluation\n",
        f"Generated from `{stitch_dir}`.\n",
        "Stitch-generated elements NOT present in `src/pages/portal/**`.\n",
        "Review each candidate and decide: **ship** (implement in source),",
        "**defer** (real feature but not this pass), or **reject** (drop).\n",
    ]

    if not any(surfaces.values()):
        out.append("\nNo embellishments detected. Clean run.\n")
        return "\n".join(out)

    for surface, hits in sorted(surfaces.items()):
        if not hits:
            continue
        out.append(f"\n## {surface}\n")
        by_cat: dict[str, list[dict]] = {}
        for h in hits:
            by_cat.setdefault(h["category"], []).append(h)
        for cat, cat_hits in sorted(by_cat.items()):
            name = cat_hits[0]["name"]
            out.append(f"\n### {name}  `{cat}`\n")
            for h in cat_hits[:5]:  # cap to avoid spam
                out.append(f"- **Line {h['line']}**: `{h['matched_text']}`")
                out.append("  ```html")
                out.append(f"  {h['snippet']}")
                out.append("  ```")
            if len(cat_hits) > 5:
                out.append(f"- ...and {len(cat_hits) - 5} more hits in this category.")

    out.append("\n## Next step")
    out.append("\nFor each candidate above, add a note to the PR describing")
    out.append("your disposition. Future runs of this evaluator will flag the")
    out.append("same patterns; add matched phrases to the source corpus")
    out.append("(as code or tests) once accepted.")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stitch-dir", type=Path, required=True,
                        help="Directory containing Stitch-generated HTML")
    parser.add_argument("--source-dir", type=Path, required=True,
                        help="Source directory to compare against (e.g., src/pages/portal)")
    parser.add_argument("--out", type=Path, default=None,
                        help="Output report path; default <stitch-dir>/EMBELLISHMENTS.md")
    args = parser.parse_args(argv)

    if not args.stitch_dir.exists():
        print(f"error: {args.stitch_dir} does not exist", file=sys.stderr)
        return 1
    if not args.source_dir.exists():
        print(f"error: {args.source_dir} does not exist", file=sys.stderr)
        return 1

    source_corpus = load_source_inventory(args.source_dir)
    signatures = FEATURE_SIGNATURES + POLISH_SIGNATURES

    surfaces: dict[str, list[dict]] = {}
    for path in sorted(args.stitch_dir.glob("*.html")):
        text = path.read_text(encoding="utf-8", errors="replace")
        hits = scan_for_embellishments(text, signatures)
        novel = filter_novel(hits, source_corpus)
        surfaces[path.name] = novel

    out_path = args.out or args.stitch_dir / "EMBELLISHMENTS.md"
    report = format_report(args.stitch_dir, surfaces)
    out_path.write_text(report, encoding="utf-8")
    total = sum(len(h) for h in surfaces.values())
    print(f"Wrote {out_path}")
    print(f"  {total} embellishment candidates across {len(surfaces)} surfaces")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
