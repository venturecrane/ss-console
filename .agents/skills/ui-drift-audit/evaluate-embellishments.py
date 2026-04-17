#!/usr/bin/env python3
"""
Embellishment evaluator — finds Stitch-invented FEATURES (not styling, not
decoration) that don't exist in source code. Output is a short, human-
readable report with plain-English descriptions and a recommendation
per item.

What this reports:
  - Aggregate stat cards ("Total Outstanding", "Total Value")
  - Auto-action banners (Auto-pay, Auto-renew)
  - Filter / sort toolbars
  - Quick-action grids (Shortcuts, Quick links)
  - Support / help widgets
  - Notification / alert centers

What this does NOT report (handled by normalize/strip):
  - Gradient CTAs (normalize rewrites to solid primary)
  - Blur circles / decorative flourishes (strip removes)
  - Hero imagery, testimonial blocks, marketing CTAs (strip removes)
  - Font / typography / spacing variations (normalize covers)

One item per feature type. If Stitch added "Total Outstanding" to both
mobile and desktop invoice lists, that's ONE decision, not two.

Usage:
  python3 .agents/skills/ui-drift-audit/evaluate-embellishments.py \
    --stitch-dir .stitch/designs/portal-v2-spec-test \
    --source-dir src/pages/portal
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from collections import defaultdict

# --- Feature-suggestion signatures -----------------------------------------
#
# Each entry is a real, reviewable product feature that Stitch may invent.
# Style/decoration is out of scope here (see normalize.py / strip.py).
#
# Fields per signature:
#   id           — short slug for the report
#   title        — human-readable name
#   what_it_is   — plain-English description of the feature
#   why_it_might_ship — what user problem it would solve
#   pattern      — regex that finds evidence in the HTML
#   recommend    — default disposition, reviewer can override

SIGNATURES = [
    {
        "id": "aggregate-outstanding",
        "title": "Aggregate outstanding-balance card",
        "what_it_is": "A prominent card on the invoices list showing the total $ outstanding across all unpaid invoices, typically with a progress-bar showing overdue share.",
        "why_it_might_ship": "Answers 'how much do I owe right now?' without scanning every invoice. Reduces the top-of-mind friction for an owner tracking cash.",
        "pattern": r"Total\s+Outstanding",
        "recommend": "defer — real value but not MVP. Build once we have ≥3 invoices per client in real data.",
    },
    {
        "id": "aggregate-total-value",
        "title": "Aggregate total-value / active-count stat pair",
        "what_it_is": "Two side-by-side cards on the proposals list: total $ value of all proposals + a count of active proposals.",
        "why_it_might_ship": "Gives the owner a pipeline snapshot. Useful if multi-quote is common, noise if single-quote is the norm.",
        "pattern": r"(Total\s+Value|Active\s+Count)",
        "recommend": "reject — most SMD engagements are single-proposal. The stat pair would be 'Total: $5,250 / Active: 1', which is noise. Revisit if we move to multi-engagement accounts.",
    },
    {
        "id": "auto-pay-banner",
        "title": "Auto-pay configuration banner",
        "what_it_is": "A banner (usually on the invoices list) surfacing whether auto-pay is enabled with a 'Configure' CTA.",
        "why_it_might_ship": "Would let owners set up recurring automatic payment for invoices instead of clicking Pay each time. Table-stakes for SaaS billing; unusual for project-based consulting.",
        "pattern": r"Auto[-\s]pay\s+(?:is|off|enabled|disabled|configure)",
        "recommend": "reject — SMD Services engagements are bounded projects paid per SOW, not recurring. Auto-pay doesn't match the business model.",
    },
    {
        "id": "progress-widget",
        "title": "Progress bar / percentage widget",
        "what_it_is": "A horizontal progress bar with a percentage label, typically on the engagement page or overview.",
        "why_it_might_ship": "Gives a glanceable 'how far along are we?' answer. Needs a reliable 'percent complete' signal that currently doesn't exist in the data model (milestones complete / total isn't a clean percent — milestones aren't equal-weighted).",
        "pattern": r"\d+%\s+(?:complete|overdue|on[-\s]track)",
        "recommend": "defer — interesting but requires a data-model decision on how to calculate progress. Not blocking.",
    },
    {
        "id": "filter-sort",
        "title": "Filter / sort toolbar on list pages",
        "what_it_is": "A bar above the list with 'Sort by' / 'Filter by' controls (date, status, amount).",
        "why_it_might_ship": "Useful when lists get long (>10 items). Noise when every client sees 1-3 invoices.",
        "pattern": r"(Sort\s+by|Filter\s+by|Group\s+by)",
        "recommend": "defer — implement when a client's list exceeds 10 items. Premature otherwise.",
    },
    {
        "id": "quick-actions",
        "title": "Quick-actions grid on dashboard",
        "what_it_is": "3-4 tiled shortcut cards at the top of portal home ('Upload document', 'Request update', 'Download SOW').",
        "why_it_might_ship": "Surfaces secondary actions without hiding them in menus. Could be over-engineering when the action-centric ActionCard already handles the primary action.",
        "pattern": r"(Quick\s+(?:actions|links|access)|Shortcuts)",
        "recommend": "reject — conflicts with Rule 3 (one primary per view) and the 'action-centric above the fold' principle. A quick-actions grid means 4 primary-weight tiles.",
    },
    {
        "id": "support-widget",
        "title": "Support / help sidebar widget",
        "what_it_is": "A sidebar card with 'Need help?' or 'Contact support' + a chat or email CTA.",
        "why_it_might_ship": "Makes support reachable without leaving the page. The ConsultantBlock already does this (name + phone); a separate 'Support' widget would be duplicative.",
        "pattern": r"(Need\s+assistance|Need\s+help|Contact\s+support|Chat\s+with\s+us)",
        "recommend": "reject — ConsultantBlock already covers this with the actual consultant's contact info, not generic support.",
    },
    {
        "id": "notifications",
        "title": "Notification / alert center",
        "what_it_is": "A bell-icon header action or dedicated notification panel listing alerts.",
        "why_it_might_ship": "Central inbox for product messages. Currently we use email as the notification channel; duplicating inside the portal creates two notification surfaces.",
        "pattern": r"(<[^>]*role=\"alert\"[^>]*>|Notification\s+center|Alerts\s+panel)",
        "recommend": "reject — email is the authoritative notification channel for SMD. A portal notification center would create two inboxes.",
    },
]


# --- Scan logic ------------------------------------------------------------

def find_in_file(path: Path, patterns: list[dict]) -> dict[str, list[int]]:
    """Return {signature_id: [line_numbers]} for matches in this file."""
    text = path.read_text(encoding="utf-8", errors="replace")
    hits: dict[str, list[int]] = {}
    for sig in patterns:
        rx = re.compile(sig["pattern"], re.IGNORECASE)
        lines = []
        for m in rx.finditer(text):
            line_no = text.count("\n", 0, m.start()) + 1
            lines.append(line_no)
        if lines:
            hits[sig["id"]] = lines
    return hits


def load_source_corpus(source_dir: Path) -> str:
    parts: list[str] = []
    for path in source_dir.rglob("*.astro"):
        try:
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            pass
    return "\n".join(parts).lower()


def signature_in_source(sig: dict, source_corpus: str) -> bool:
    """Check if a signature already exists in source — don't flag as novel."""
    rx = re.compile(sig["pattern"], re.IGNORECASE)
    return bool(rx.search(source_corpus))


def format_report(findings: dict[str, dict]) -> str:
    """findings: {sig_id: {sig: dict, locations: [(file, line), ...]}}"""
    out: list[str] = [
        "# Stitch-invented feature suggestions",
        "",
        "Features Stitch added that don't exist in source. One entry per feature — decide once, applies everywhere Stitch put it.",
        "",
        "Styling/decoration choices (gradient CTAs, blur circles, typography drift) are NOT here — those are handled automatically by `normalize.py` and `strip.py`. What's below is product-level: real UX additions Stitch invented.",
        "",
        "**Your decision per item:** ship (implement in source), defer (real feature, not this pass), reject (drop).",
        "",
        "---",
        "",
    ]

    if not findings:
        out.append("_No novel feature suggestions detected. Clean run._")
        return "\n".join(out)

    for i, (sig_id, data) in enumerate(findings.items(), start=1):
        sig = data["sig"]
        locations = data["locations"]
        # Dedupe by surface (drop line numbers for human digest)
        surfaces = sorted({path.stem for path, _ in locations})
        out.append(f"## {i}. {sig['title']}")
        out.append("")
        out.append(f"**What it is.** {sig['what_it_is']}")
        out.append("")
        out.append(f"**Why it might ship.** {sig['why_it_might_ship']}")
        out.append("")
        out.append(f"**Where Stitch put it.** {', '.join(surfaces)}  ({len(locations)} occurrence(s))")
        out.append("")
        out.append(f"**Recommendation.** {sig['recommend']}")
        out.append("")
        out.append(f"**Your decision:** [ ] ship   [ ] defer   [ ] reject")
        out.append("")
        out.append("---")
        out.append("")

    return "\n".join(out)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stitch-dir", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    if not args.stitch_dir.exists() or not args.source_dir.exists():
        print("error: stitch-dir or source-dir does not exist", file=sys.stderr)
        return 1

    source_corpus = load_source_corpus(args.source_dir)

    # For each signature, aggregate hits across all HTML files — ignoring
    # signatures that already exist in source.
    findings: dict[str, dict] = {}
    for sig in SIGNATURES:
        if signature_in_source(sig, source_corpus):
            continue
        locations: list[tuple[Path, int]] = []
        for path in sorted(args.stitch_dir.glob("*.html")):
            hits = find_in_file(path, [sig])
            for line in hits.get(sig["id"], []):
                locations.append((path, line))
        if locations:
            findings[sig["id"]] = {"sig": sig, "locations": locations}

    out_path = args.out or args.stitch_dir / "EMBELLISHMENTS.md"
    report = format_report(findings)
    out_path.write_text(report, encoding="utf-8")

    print(f"Wrote {out_path}")
    print(f"  {len(findings)} novel feature suggestion(s) across {len(SIGNATURES)} signature types")
    for sig_id, data in findings.items():
        n = len(data["locations"])
        surfaces = sorted({p.stem for p, _ in data["locations"]})
        print(f"  - {data['sig']['title']}: {n} hit(s) in {', '.join(surfaces)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
