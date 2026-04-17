#!/usr/bin/env python3
"""
Strip pass — post-Stitch cleanup that removes hallucinated chrome and
decoration the UI CONTRACT explicitly forbade but Stitch produced anyway.

This is NOT the token-normalize pass (that one rewrites class names). This
pass removes entire DOM subtrees whose content violates the prompt
prohibitions. It is mechanical and deterministic: same input yields
same output, no LLM involvement.

Targets (derived from UI CONTRACT + NAV CONTRACT FORBIDDEN lists):
- Hero imagery and decorative illustrations (photo placeholders)
- Marketing CTAs ("Schedule a call", "Book a demo", "Contact us" on
  authenticated surfaces)
- Testimonial blocks (italicized client-voice paragraphs)
- Copyright footers and legal link rows
- Announcement / promotional banners
- Duplicated "View all" links, social-share bars

Run AFTER normalize.py, BEFORE the embellishment evaluator. Strip first
so the evaluator isn't reporting chrome as a "feature suggestion."

Usage:
  python3 .agents/skills/ui-drift-audit/strip.py <path-to-html>
  # rewrites in place; prints a per-category removal count.

  --dry-run to preview without writing.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# --- Strip patterns --------------------------------------------------------
#
# Each pattern matches a full element (span-level or block-level) to delete.
# We use regex on source HTML rather than a DOM parser so this script has no
# external dependencies.

# Hero imagery / decorative illustrations
HERO_IMG_PATTERNS = [
    # <img> with lh3.googleusercontent.com (Stitch's AI image host)
    r'<img[^>]*src="[^"]*lh3\.googleusercontent\.com[^"]*"[^>]*/?>',
    # <div> with role="img" and a background-image style
    r'<div[^>]*role="img"[^>]*style="[^"]*background-image[^"]*"[^>]*></div>',
    # explicit <figure>
    r'<figure[^>]*>.*?</figure>',
]

# Marketing CTAs on authenticated surfaces (span-level anchor/button patterns)
MARKETING_CTA_PATTERNS = [
    r'<a[^>]*>\s*Schedule a call\s*</a>',
    r'<a[^>]*>\s*Book a demo\s*</a>',
    r'<a[^>]*>\s*Get started\s*</a>',
    r'<a[^>]*>\s*Sign up\s*</a>',
    r'<button[^>]*>\s*Schedule a call\s*</button>',
    r'<button[^>]*>\s*Book a demo\s*</button>',
]

# Testimonials — italicized client-voice paragraphs. Pattern: <blockquote>,
# <q>, or <p class="italic">
TESTIMONIAL_PATTERNS = [
    r'<blockquote[^>]*>.*?</blockquote>',
    r'<q[^>]*>.*?</q>',
]

# Copyright footers and legal link rows
FOOTER_PATTERNS = [
    # Any <footer> block — authenticated surfaces don't need them
    r'<footer[^>]*>.*?</footer>',
    # Inline copyright text (© YYYY or "Copyright YYYY")
    r'<p[^>]*>[^<]*(?:&copy;|©|Copyright)\s*20\d{2}[^<]*</p>',
]

# Announcement / promotional banners (class pattern)
ANNOUNCEMENT_PATTERNS = [
    r'<div[^>]*class="[^"]*(?:announce|promo|banner-promo)[^"]*"[^>]*>.*?</div>',
]

# Decorative ornaments — blur circles, gradient-radial ornaments, empty
# illustration placeholders. These are pure visual polish Stitch adds to
# fill space; they carry no information.
DECORATIVE_PATTERNS = [
    # Absolute-positioned blur decorations (the classic "bg-primary/5 rounded-full blur-3xl" corner ornament)
    r'<div[^>]*class="[^"]*\babsolute\b[^"]*\bblur-(?:2xl|3xl)\b[^"]*"[^>]*></div>',
    r'<div[^>]*class="[^"]*\bblur-(?:2xl|3xl)\b[^"]*\babsolute\b[^"]*"[^>]*></div>',
    # "Optional Illustration/Graphic Area" — Stitch's empty-slot pattern.
    r'<!--\s*Optional Illustration/Graphic Area\s*-->\s*<div[^>]*>.*?</div>\s*(?=<!--|<section|</section|</div)',
    # Isolated illustration wrappers with gradient-radial backgrounds
    r'<div[^>]*class="[^"]*\bgradient-radial\b[^"]*"[^>]*>.*?</div>',
]

CATEGORIES: list[tuple[str, list[str]]] = [
    ("hero-img", HERO_IMG_PATTERNS),
    ("marketing-cta", MARKETING_CTA_PATTERNS),
    ("testimonial", TESTIMONIAL_PATTERNS),
    ("footer", FOOTER_PATTERNS),
    ("announcement", ANNOUNCEMENT_PATTERNS),
    ("decorative", DECORATIVE_PATTERNS),
]


def strip(text: str) -> tuple[str, dict[str, int]]:
    """Remove forbidden elements from HTML source."""
    counts: dict[str, int] = {}
    for category, patterns in CATEGORIES:
        for pat in patterns:
            rx = re.compile(pat, re.DOTALL | re.IGNORECASE)
            new_text, n = rx.subn("", text)
            if n:
                counts[category] = counts.get(category, 0) + n
                text = new_text
    return text, counts


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="HTML file to strip")
    parser.add_argument("--out", type=Path, default=None,
                        help="Optional output path; default is in-place")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show counts without writing")
    args = parser.parse_args(argv)

    if not args.path.exists():
        print(f"error: {args.path} does not exist", file=sys.stderr)
        return 1

    text = args.path.read_text(encoding="utf-8")
    new_text, counts = strip(text)
    total = sum(counts.values())
    if total == 0:
        print(f"{args.path}: no strips needed")
        return 0

    print(f"{args.path}: {total} removals")
    for cat, n in sorted(counts.items()):
        print(f"  {cat}: {n}")

    if args.dry_run:
        print("  (dry-run; no file written)")
        return 0

    out = args.out or args.path
    out.write_text(new_text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
