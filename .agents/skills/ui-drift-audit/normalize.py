#!/usr/bin/env python3
"""
Token-normalize pass — post-Stitch cleanup that rewrites generated HTML
to use the project's named typography and spacing tokens instead of
Stitch's raw-Tailwind + Material-3 vocabulary.

Runs after `generate_screen_from_text` or `edit_screens`, before the
HTML is written to `.design/designs/`. Deterministic codemod: same input
always yields same output; no LLM involvement.

Covers the token-adoption gap that the UI CONTRACT in the Stitch prompt
cannot fully close (Stitch has strong trained priors favoring Material 3
and raw Tailwind). The UI CONTRACT still owns semantic rules (pill vs
eyebrow, one primary, heading hierarchy) — those are judgment calls.
This pass owns vocabulary substitution — deterministic mappings.

Usage:
  python3 .agents/skills/ui-drift-audit/normalize.py <path-to-html>
  # rewrites in place; prints a delta summary.

  python3 .agents/skills/ui-drift-audit/normalize.py <in> --out <out>
  # writes to a separate file.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# --- Class mappings --------------------------------------------------------
#
# Ordered by specificity: longer / more-specific patterns first so that
# `text-[11px]` is matched before any generic `text-` fallback.

# Typography — arbitrary pixel sizes. Must match inside class attributes.
TYPO_ARBITRARY = [
    (r"text-\[11px\]", "text-label"),
    (r"text-\[12px\]", "text-label"),
    (r"text-\[13px\]", "text-caption"),
    (r"text-\[14px\]", "text-caption"),
    (r"text-\[15px\]", "text-body"),
    (r"text-\[16px\]", "text-body"),
    (r"text-\[18px\]", "text-body-lg"),
    (r"text-\[20px\]", "text-title"),
    (r"text-\[22px\]", "text-title"),
    (r"text-\[24px\]", "text-title"),
    (r"text-\[28px\]", "text-display"),
    (r"text-\[32px\]", "text-display"),
    (r"text-\[36px\]", "text-display"),
    (r"text-\[42px\]", "text-display"),
]

# Typography — raw Tailwind tokens. Mapped to our scale.
TYPO_TAILWIND = [
    (r"\btext-xs\b", "text-caption"),
    (r"\btext-sm\b", "text-body"),
    (r"\btext-base\b", "text-body"),
    (r"\btext-lg\b", "text-body-lg"),
    (r"\btext-xl\b", "text-title"),
    (r"\btext-2xl\b", "text-title"),
    (r"\btext-3xl\b", "text-display"),
    (r"\btext-4xl\b", "text-display"),
    (r"\btext-5xl\b", "text-display"),
    (r"\btext-6xl\b", "text-display"),
]

# Spacing rhythm. Applies to p-*, gap-*, gap-x-*, gap-y-*, space-y-*, space-x-*.
# Keys are raw-numeric Tailwind values; values are named tokens.
SPACING_NUMS = {
    "3": "row",    # 12px — list-row gaps
    "4": "stack",  # 16px — sibling vertical stack
    "6": "card",   # 24px — card internal padding
    "8": "section",# 32px — section gap / hero padding
}
SPACING_PROPS = ["p", "gap", "gap-x", "gap-y", "space-y", "space-x"]

# Gradient CTA stylings — Stitch prefers gradient-to-* buttons.
# UI-PATTERNS Rule 3: primary CTA is SOLID bg-primary with text-white, not gradient.
# These patterns collapse gradient stacks to solid primary.
GRADIENT_CTAS = [
    # bg-gradient-to-{r,l,t,b,br,bl,tr,tl} from-primary to-primary-container
    (r"bg-gradient-to-[a-z]{1,2}\s+from-primary\s+to-primary-container",
     "bg-[color:var(--color-primary)]"),
    (r"bg-gradient-to-[a-z]{1,2}\s+from-[\w\-\[\]/:.]+\s+to-[\w\-\[\]/:.]+",
     "bg-[color:var(--color-primary)]"),
]

# Material 3 color tokens Stitch tends to reach for. Map to our semantic roles.
MATERIAL_COLORS = [
    (r"\bbg-surface-container-lowest\b", "bg-[color:var(--color-surface)]"),
    (r"\bbg-surface-container-low\b", "bg-[color:var(--color-surface)]"),
    (r"\bbg-surface-container\b", "bg-[color:var(--color-surface)]"),
    (r"\bbg-surface\b", "bg-[color:var(--color-surface)]"),
    (r"\bbg-background\b", "bg-[color:var(--color-background)]"),
    (r"\btext-on-surface-variant\b", "text-[color:var(--color-text-secondary)]"),
    (r"\btext-on-surface\b", "text-[color:var(--color-text-primary)]"),
    (r"\btext-on-primary-container\b", "text-[color:var(--color-primary)]"),
    (r"\btext-on-primary\b", "text-white"),
    (r"\bbg-primary-container\b", "bg-[color:var(--color-primary)]/10"),
    (r"\btext-on-error\b", "text-white"),
    (r"\bbg-error-container\b", "bg-[color:var(--color-error)]/10"),
    (r"\btext-on-error-container\b", "text-[color:var(--color-error)]"),
    (r"\bbg-outline\b", "bg-[color:var(--color-border)]"),
    (r"\bborder-outline\b", "border-[color:var(--color-border)]"),
    (r"\btext-outline\b", "text-[color:var(--color-text-muted)]"),
]


# --- Normalize logic -------------------------------------------------------

def _build_spacing_subs() -> list[tuple[str, str]]:
    """Generate (pattern, replacement) pairs for every prop × number combo."""
    subs: list[tuple[str, str]] = []
    for prop in SPACING_PROPS:
        for num, name in SPACING_NUMS.items():
            # Match prop-N as a whole token (not prop-40, not prop-foo).
            subs.append((
                rf"(?<![-\w]){re.escape(prop)}-{num}(?![0-9.])",
                f"{prop}-{name}",
            ))
    return subs


def _iter_class_attrs(text: str):
    """Yield (start, end, inner) for every class="..." or className="...".

    The codemod only touches the inside of class attributes — never inline
    styles, data- attrs, or free text. This keeps `text-sm` in prose
    untouched while still rewriting it inside a real class attribute.
    """
    rx = re.compile(r"""(class|className)\s*=\s*"([^"]*)\"""", re.DOTALL)
    for m in rx.finditer(text):
        yield m.start(2), m.end(2), m.group(2)


def normalize(text: str) -> tuple[str, dict[str, int]]:
    """Rewrite class attributes in `text` using the deterministic mappings.

    Returns (new_text, counts) where counts is a per-pattern substitution
    tally for the summary output.
    """
    spacing_subs = _build_spacing_subs()
    all_subs = (
        [(p, r, "gradient-cta") for p, r in GRADIENT_CTAS]
        + [(p, r, "typo-arbitrary") for p, r in TYPO_ARBITRARY]
        + [(p, r, "typo-tailwind") for p, r in TYPO_TAILWIND]
        + [(p, r, "spacing-rhythm") for p, r in spacing_subs]
        + [(p, r, "material-color") for p, r in MATERIAL_COLORS]
    )

    # Compile once
    compiled = [(re.compile(p), r, cat) for p, r, cat in all_subs]
    counts: dict[str, int] = {}

    # Iterate class attrs in reverse order so offsets don't drift as we
    # rewrite.
    spans = list(_iter_class_attrs(text))
    for start, end, inner in reversed(spans):
        new_inner = inner
        # Skip material-symbols icon sizing — Stitch uses text-[Npx] on
        # icons and that's the correct idiom (icons aren't body text).
        is_icon = "material-symbols" in new_inner
        for rx, rep, cat in compiled:
            if is_icon and cat == "typo-arbitrary":
                continue  # preserve icon sizing
            new_inner, n = rx.subn(rep, new_inner)
            if n:
                counts[cat] = counts.get(cat, 0) + n
        if new_inner != inner:
            text = text[:start] + new_inner + text[end:]

    return text, counts


# --- CLI -------------------------------------------------------------------

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="HTML file to normalize")
    parser.add_argument("--out", type=Path, default=None,
                        help="Optional output path; default is in-place")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show counts without writing")
    args = parser.parse_args(argv)

    if not args.path.exists():
        print(f"error: {args.path} does not exist", file=sys.stderr)
        return 1

    text = args.path.read_text(encoding="utf-8")
    new_text, counts = normalize(text)

    total = sum(counts.values())
    if total == 0:
        print(f"{args.path}: no changes needed")
        return 0

    print(f"{args.path}: {total} substitutions")
    for cat, n in sorted(counts.items()):
        print(f"  {cat}: {n}")

    if args.dry_run:
        print("  (dry-run; no file written)")
        return 0

    out = args.out or args.path
    out.write_text(new_text, encoding="utf-8")
    if out != args.path:
        print(f"  wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
