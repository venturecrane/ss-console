#!/usr/bin/env python3
"""
UI drift audit — counts visual-design anti-patterns across the codebase and
produces a surfaces × rules matrix. Output is a markdown table with per-file
violation counts (default), or a JSON document (`--format json`) with a
stable schema designed for CI threshold gating.

Audits against the six base rules (status display, redundancy ban, button
hierarchy, heading skip, typography scale, spacing rhythm) plus three
token-compliance columns (raw hex/rgb in JSX, raw hex/rgb in inline style,
raw Tailwind palette color classes). The audit informs spec authoring and
CI gates; it does not auto-fix.

Venture-agnostic:
  - Source directories default to `src/pages` + `src/components`. Override
    with --src DIR (repeatable) or `.ui-drift.json` `src_dirs`.
  - Repo root is resolved relative to this script's location, so the same
    file works dropped into any venture's `.agents/skills/ui-drift-audit/`.
  - Status words for the redundancy check are configurable via
    `--status-words` (CLI), `.ui-drift.json` `status_words`, or the
    built-in default. Precedence: CLI > config > default.

Usage:
  python3 .agents/skills/ui-drift-audit/audit.py [options]

  --format {markdown,json}    output format. Default: markdown.
  --out PATH                  output path. Default: <repo>/.design/audits/
                              ui-drift-{YYYY-MM-DD}.{md,json}.
  --src DIR                   source directory. Repeatable. Default:
                              src/pages + src/components.
  --status-words "W1,W2,..."  comma-separated status keywords for the
                              redundancy check. Overrides config and default.
  --config PATH               explicit .ui-drift.json. Default: auto-discover
                              at <repo-root>/.ui-drift.json.
  --repo-root PATH            override repo root (used for relative-path
                              display, tier classification, and resolving
                              relative --src / --out / --config).

================================================================================
JSON output schema (--format json) — version 1.0
================================================================================

{
  "schema_version": "1.0",       # str — bump on breaking shape change
  "generated_at": "ISO-8601",    # str — UTC timestamp
  "repo_root": "/abs/path",      # str — absolute path to the audited repo
  "config": {                    # object — resolved configuration
    "src_dirs": ["src/pages", ...],            # list[str]
    "status_words_source": "cli|config|default",  # str
    "status_words": ["Pending", ...],          # list[str]
    "thresholds": {                            # object — from .ui-drift.json
      "raw_hex_rgb_in_jsx_max": 0,             # int|null
      "raw_hex_rgb_in_inline_style_max": 0,    # int|null
      "raw_tailwind_color_classes_max": 5      # int|null
    }
  },
  "totals": {                    # object — repo-wide aggregates
    "files": 75,                              # int
    "raw_violations": 2225,                   # int — sum of base-rule columns
    "raw_hex_rgb_in_jsx": 0,                  # int
    "raw_hex_rgb_in_inline_style": 0,         # int
    "raw_tailwind_color_classes": 0           # int
  },
  "tier_totals": [                # list — per-tier rollup
    {
      "tier": "client-portal",
      "files": 23,
      "pills": 8,
      "typo_arb": 12, "typo_token": 4,
      "spacing_arb": 0, "spacing_token": 91,
      "heading_skips": 1,
      "primary_cta_violation_files": 0,
      "redundancy_hits": 3,
      "raw_hex_rgb_in_jsx": 0,
      "raw_hex_rgb_in_inline_style": 0,
      "raw_tailwind_color_classes": 0
    }
  ],
  "files": [                      # list — per-file matrix rows
    {
      "path": "src/pages/portal/index.astro",  # str — relative to repo_root
      "tier": "client-portal",
      "pills": 0,
      "typo_arb": 0, "typo_token": 0,
      "spacing_arb": 0, "spacing_token": 4,
      "heading_skips": 0,
      "primary_ctas": 1,
      "redundancy_hits": 0,
      "raw_hex_rgb_in_jsx": 0,
      "raw_hex_rgb_in_inline_style": 0,
      "raw_tailwind_color_classes": 0
    }
  ],
  "redundancy_hits": [            # list — Rule 2 anti-pattern seeds
    {"path": "...", "line": 42, "word": "pending"}
  ],
  "heading_skips": [              # list — Rule 4 seeds
    {"path": "...", "line": 15, "from": 1, "to": 3}
  ],
  "threshold_breaches": [         # list — token-compliance gates exceeded
    {
      "metric": "raw_tailwind_color_classes",
      "actual": 12,
      "max": 5
    }
  ]
}

CI threshold-gate exit code: when --format json is requested, exit code is
non-zero (1) if any threshold from .ui-drift.json `thresholds` is exceeded.
Markdown mode always exits 0 (informational).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SRC_DIRS = ["src/pages", "src/components"]
AUDIT_EXTS = {".astro", ".tsx", ".jsx"}
JSX_TSX_EXTS = {".tsx", ".jsx"}

SCHEMA_VERSION = "1.0"


def set_repo_root(path: Path) -> None:
    """Override the repo root used for relative-path display and tier classification.

    Useful when running the audit from outside the venture's repo (CI runners,
    cross-venture scans). When unset, REPO_ROOT defaults to the script's own
    repo (`.agents/skills/ui-drift-audit/audit.py` -> parents[3]).
    """
    global REPO_ROOT
    REPO_ROOT = path.resolve()


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

# Inline typography: arbitrary sizes or Tailwind size tokens.
TYPO_ARB_RX = re.compile(r"text-\[[0-9.]+(?:px|rem|em)\]")
TYPO_TOKEN_RX = re.compile(r"\btext-(?:xs|sm|base|lg|xl|2xl|3xl|4xl|5xl|6xl|7xl|8xl|9xl)\b")

# Inline spacing: arbitrary values or raw Tailwind spacing tokens.
SPACING_ARB_RX = re.compile(r"(?:p[xytrbl]?|m[xytrbl]?|gap(?:-x|-y)?)-\[[^\]]+\]")
SPACING_TOKEN_RX = re.compile(r"\b(?:p[xytrbl]?|gap(?:-x|-y)?)-[0-9]+(?:\.5)?\b")

# Headings
HEADING_RX = re.compile(r"<h([1-6])\b", re.IGNORECASE)

# Primary-CTA indicators. Rule 3: one primary per view.
PRIMARY_CTA_RX = re.compile(
    r"(?:bg-\[color:var\(--color-primary\)\](?!/)|bg-primary(?![-/\w]))[^\"]*?\bp[xy]?-[0-9]"
)

# --- Token compliance (B3) -------------------------------------------------

# Raw hex (#fff, #ffffff, #ffffffff) and rgb/rgba color literals.
# Word-bounded on hex so we don't match urls or hashes inside class names.
RAW_HEX_RX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
RAW_RGB_RX = re.compile(r"\brgba?\([^)]+\)")

# JSX inline style attribute: matches `style={{ ...anything... }}` (single brace
# pair around the object literal). Greedy across newlines but bounded by the
# outer `}}`. Used to extract the inline-style payload before scanning for raw
# hex/rgb.
INLINE_STYLE_RX = re.compile(r"style=\{\{[^}]*\}\}", re.DOTALL)

# Tailwind palette color classes — `bg-blue-500`, `text-red-300`, etc.
# Anchored on the property prefix list so utility classes like `bg-primary`
# and `text-current` don't match. Matches a fixed list of common color
# families (from Tailwind's default palette through Tailwind 3/4).
TAILWIND_COLOR_RX = re.compile(
    r"\b(?:bg|text|border|ring|fill|stroke|placeholder|outline|divide|from|via|to)"
    r"-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|"
    r"emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)"
    r"-\d{2,3}\b"
)


# --- Redundancy detection --------------------------------------------------

# Built-in default status words (covers common SaaS billing / contracting
# states). Override per-venture via `--status-words` or `.ui-drift.json`
# `status_words`.
DEFAULT_STATUS_WORDS = [
    "Signed", "Paid", "Pending", "Sent", "Viewed", "Expired", "Draft", "Underway",
    "Accepted", "Declined", "Cancelled", "Completed", "Processing", "Active", "Overdue",
    "Ready", "Published", "Countersigned", "Unpaid", "Due", "Approved", "Rejected",
    "Scheduled", "Confirmed", "Open", "Closed", "Paused", "Archived", "Superseded",
    "Deposit", "Final",
]

REDUNDANCY_WINDOW = 10  # lines above+below


def build_status_words_rx(words: list[str]) -> re.Pattern[str]:
    """Compile the status-words regex from the resolved word list.

    Words are escaped and joined with `|`. Returns a case-insensitive,
    word-bounded matcher. An empty list compiles to a never-match pattern.
    """
    if not words:
        # Use a pattern guaranteed never to match (empty alternation isn't
        # valid in Python regex; this one matches an impossible look-around).
        return re.compile(r"(?!)")
    escaped = "|".join(re.escape(w) for w in words)
    return re.compile(rf"\b(?:{escaped})\b", re.IGNORECASE)


# --- Configuration ---------------------------------------------------------

@dataclass
class ResolvedConfig:
    """Resolved venture-agnostic config (CLI > .ui-drift.json > defaults)."""

    src_dirs: list[str]
    status_words: list[str]
    status_words_source: str  # "cli" | "config" | "default"
    thresholds: dict[str, object]  # per-metric maxes; values may be None

    def status_words_rx(self) -> re.Pattern[str]:
        return build_status_words_rx(self.status_words)


def load_config_file(path: Path) -> dict:
    """Load a `.ui-drift.json` config. Missing file returns {}."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"warning: could not read {path}: {e}", file=sys.stderr)
        return {}


def resolve_config(
    cli_status_words: list[str] | None,
    cli_src_dirs: list[Path] | None,
    config_path: Path | None,
) -> ResolvedConfig:
    """Merge CLI > config-file > built-in defaults."""
    cfg_path = config_path if config_path is not None else REPO_ROOT / ".ui-drift.json"
    file_cfg = load_config_file(cfg_path)

    # status words
    if cli_status_words is not None:
        status_words = cli_status_words
        status_words_source = "cli"
    elif isinstance(file_cfg.get("status_words"), list) and file_cfg["status_words"]:
        status_words = [str(w) for w in file_cfg["status_words"]]
        status_words_source = "config"
    else:
        status_words = list(DEFAULT_STATUS_WORDS)
        status_words_source = "default"

    # src_dirs
    if cli_src_dirs:
        # Already resolved against repo root by caller
        src_dirs = [str(d) for d in cli_src_dirs]
    elif isinstance(file_cfg.get("src_dirs"), list) and file_cfg["src_dirs"]:
        src_dirs = [str(d) for d in file_cfg["src_dirs"]]
    else:
        src_dirs = list(DEFAULT_SRC_DIRS)

    # thresholds
    thresholds_raw = file_cfg.get("thresholds")
    thresholds: dict[str, object] = {
        "raw_hex_rgb_in_jsx_max": None,
        "raw_hex_rgb_in_inline_style_max": None,
        "raw_tailwind_color_classes_max": None,
    }
    if isinstance(thresholds_raw, dict):
        for key in list(thresholds.keys()):
            val = thresholds_raw.get(key)
            if isinstance(val, int):
                thresholds[key] = val

    return ResolvedConfig(
        src_dirs=src_dirs,
        status_words=status_words,
        status_words_source=status_words_source,
        thresholds=thresholds,
    )


# --- File reports ----------------------------------------------------------

@dataclass
class FileReport:
    path: Path
    tier: str
    pills: int = 0
    typo_arb: int = 0
    typo_token: int = 0
    spacing_arb: int = 0
    spacing_token: int = 0
    headings: list[tuple[int, int]] = field(default_factory=list)
    heading_skips: list[tuple[int, int, int]] = field(default_factory=list)
    primary_ctas: int = 0
    redundancy_hits: list[tuple[int, str]] = field(default_factory=list)
    raw_hex_rgb_in_jsx: int = 0
    raw_hex_rgb_in_inline_style: int = 0
    raw_tailwind_color_classes: int = 0

    @property
    def relpath(self) -> str:
        try:
            return str(self.path.relative_to(REPO_ROOT))
        except ValueError:
            return str(self.path)

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

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.relpath,
            "tier": self.tier,
            "pills": self.pills,
            "typo_arb": self.typo_arb,
            "typo_token": self.typo_token,
            "spacing_arb": self.spacing_arb,
            "spacing_token": self.spacing_token,
            "heading_skips": len(self.heading_skips),
            "primary_ctas": self.primary_ctas,
            "redundancy_hits": len(self.redundancy_hits),
            "raw_hex_rgb_in_jsx": self.raw_hex_rgb_in_jsx,
            "raw_hex_rgb_in_inline_style": self.raw_hex_rgb_in_inline_style,
            "raw_tailwind_color_classes": self.raw_tailwind_color_classes,
        }


# --- Tier classification ---------------------------------------------------

def classify_tier(path: Path) -> str:
    try:
        rel = str(path.relative_to(REPO_ROOT))
    except ValueError:
        rel = str(path)
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

def iter_source_files(src_dirs: list[Path]) -> Iterable[Path]:
    for d in src_dirs:
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


def detect_redundancy(
    lines: list[str], status_words_rx: re.Pattern[str]
) -> list[tuple[int, str]]:
    """Find pills whose status label is echoed in nearby prose."""
    hits: list[tuple[int, str]] = []
    for i, line in enumerate(lines, start=1):
        if not PILL_RX.search(line):
            continue

        fwd_lo = i - 1
        fwd_hi = min(len(lines), i + 5)
        pill_content = "\n".join(lines[fwd_lo:fwd_hi])
        candidates = {m.group(0).lower() for m in status_words_rx.finditer(pill_content)}
        if not candidates:
            continue

        lo = max(0, i - 1 - REDUNDANCY_WINDOW)
        hi = min(len(lines), i + REDUNDANCY_WINDOW)
        prose_lines = [ln for idx, ln in enumerate(lines[lo:hi], start=lo + 1)
                       if not PILL_RX.search(ln)]
        prose_text = "\n".join(prose_lines)

        for word in sorted(candidates):
            pat = re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE)
            if pat.search(prose_text):
                hits.append((i, word))
                break
    return hits


def count_raw_color_literals(text: str) -> int:
    """Count occurrences of raw hex AND rgb/rgba literals."""
    return len(RAW_HEX_RX.findall(text)) + len(RAW_RGB_RX.findall(text))


def count_inline_style_color_literals(text: str) -> int:
    """Count raw hex/rgb literals appearing inside JSX `style={{...}}` blocks."""
    total = 0
    for m in INLINE_STYLE_RX.finditer(text):
        total += count_raw_color_literals(m.group(0))
    return total


def audit_file(path: Path, status_words_rx: re.Pattern[str]) -> FileReport:
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
    rep.redundancy_hits = detect_redundancy(lines, status_words_rx)

    # Token-compliance counts apply only to JSX/TSX files (where inline
    # style={{...}} and raw color literals show up). .astro files use a
    # different attribute syntax and are excluded from these three columns.
    if path.suffix in JSX_TSX_EXTS:
        rep.raw_hex_rgb_in_jsx = count_raw_color_literals(text)
        rep.raw_hex_rgb_in_inline_style = count_inline_style_color_literals(text)
    rep.raw_tailwind_color_classes = len(TAILWIND_COLOR_RX.findall(text))

    return rep


# --- Markdown reporting ----------------------------------------------------

def format_matrix(reports: list[FileReport]) -> str:
    by_tier: dict[str, list[FileReport]] = {}
    for r in reports:
        by_tier.setdefault(r.tier, []).append(r)

    out: list[str] = []
    out.append(
        "| File | Tier | Pills | Typo (arb / token) | Spacing (arb / token) | "
        "H-skips | Primary CTAs | Redundancy | Raw hex/rgb (JSX) | "
        "Raw hex/rgb (inline) | Raw TW color |"
    )
    out.append(
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"
    )

    for tier in TIER_ORDER:
        if tier not in by_tier:
            continue
        files = sorted(by_tier[tier], key=lambda r: -r.total_violations())
        for r in files:
            out.append(
                f"| `{r.relpath}` | {r.tier} | {r.pills} | "
                f"{r.typo_arb} / {r.typo_token} | "
                f"{r.spacing_arb} / {r.spacing_token} | "
                f"{len(r.heading_skips)} | {r.primary_ctas} | {len(r.redundancy_hits)} | "
                f"{r.raw_hex_rgb_in_jsx} | {r.raw_hex_rgb_in_inline_style} | "
                f"{r.raw_tailwind_color_classes} |"
            )

    return "\n".join(out)


def format_tier_totals(reports: list[FileReport]) -> str:
    by_tier: dict[str, list[FileReport]] = {}
    for r in reports:
        by_tier.setdefault(r.tier, []).append(r)

    out: list[str] = []
    out.append(
        "| Tier | Files | Pills | Typo (arb/token) | Spacing (arb/token) | "
        "H-skips | Primary>1 files | Redundancy | Raw hex/rgb (JSX) | "
        "Raw hex/rgb (inline) | Raw TW color |"
    )
    out.append(
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"
    )
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
        rh_jsx = sum(f.raw_hex_rgb_in_jsx for f in files)
        rh_inline = sum(f.raw_hex_rgb_in_inline_style for f in files)
        rh_tw = sum(f.raw_tailwind_color_classes for f in files)
        out.append(
            f"| {tier} | {len(files)} | {pills} | {ta} / {tt} | {sa} / {st} | "
            f"{hs} | {multi_primary} | {redund} | {rh_jsx} | {rh_inline} | {rh_tw} |"
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
            out.append(f"- ...and {len(r.redundancy_hits) - 12} more")
    return "\n".join(out)


def format_heading_skip_detail(reports: list[FileReport]) -> str:
    out: list[str] = []
    for r in sorted(reports, key=lambda r: -len(r.heading_skips)):
        if not r.heading_skips:
            continue
        out.append(f"\n### `{r.relpath}`")
        for lineno, prev, cur in r.heading_skips:
            out.append(f"- line {lineno}: h{prev} -> h{cur}")
    return "\n".join(out)


def build_markdown_report(reports: list[FileReport], cfg: ResolvedConfig) -> str:
    date = dt.date.today().isoformat()
    total_files = len(reports)
    total_violations = sum(r.total_violations() for r in reports)
    total_rh_jsx = sum(r.raw_hex_rgb_in_jsx for r in reports)
    total_rh_inline = sum(r.raw_hex_rgb_in_inline_style for r in reports)
    total_rh_tw = sum(r.raw_tailwind_color_classes for r in reports)

    redundancy_block = format_redundancy_detail(reports) or "\n(none detected)"
    heading_block = format_heading_skip_detail(reports) or "\n(none detected)"

    return f"""# UI drift audit -- {date}

Generated by `.agents/skills/ui-drift-audit/audit.py`. Surfaces x rules matrix
informing pattern-spec authoring and CI token-compliance gates. Counts are
source-level grep matches; they approximate drift scale.

**Configuration.**
- Source dirs: {", ".join(f"`{d}`" for d in cfg.src_dirs)}
- Status words ({cfg.status_words_source}): {len(cfg.status_words)} keyword(s)
- Thresholds: {json.dumps(cfg.thresholds)}

**Rule mapping.**

- **Pills** -> Rule 1 (status display by context) + Rule 2 (redundancy ban)
- **Typo (arb/token)** -> Rule 5 (typography scale).
- **Spacing (arb/token)** -> Rule 6 (spacing rhythm).
- **H-skips** -> Rule 4 (heading skip ban).
- **Primary CTAs** -> Rule 3 (one primary per view). Violation = file count > 1.
- **Redundancy** -> Rule 2 (pill adjacent to matching text).
- **Raw hex/rgb (JSX)** -> Token compliance: hard-coded `#abc` / `rgba(...)` in `.tsx`/`.jsx`.
- **Raw hex/rgb (inline)** -> Token compliance: same regex inside `style={{...}}`.
- **Raw TW color** -> Token compliance: Tailwind palette classes (`bg-blue-500`, `text-red-300`, ...).

**Totals.** {total_files} files audited, {total_violations} raw violation candidates, {total_rh_jsx} raw-hex-in-JSX, {total_rh_inline} raw-hex-in-inline-style, {total_rh_tw} raw Tailwind palette uses.

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

- Dev-preview surfaces (`src/pages/dev/*`) are documented but excluded from remediation priority.
- Redundancy detection is a heuristic (pill text +/- {REDUNDANCY_WINDOW} lines).
- Token counts in Typo/Spacing are informational until tokens land in `@theme`.
- Primary CTA count > 1 doesn't directly mean Rule 3 violation (see detail).
- Token-compliance columns are hard violations once tokens are adopted; in CI they gate via the configured thresholds.
"""


# --- JSON reporting --------------------------------------------------------

def build_json_report(
    reports: list[FileReport], cfg: ResolvedConfig
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Build the JSON document and the list of threshold breaches.

    Returns (document, breaches). Caller decides exit code based on breaches.
    """
    by_tier: dict[str, list[FileReport]] = {}
    for r in reports:
        by_tier.setdefault(r.tier, []).append(r)

    tier_totals: list[dict[str, object]] = []
    for tier in TIER_ORDER:
        files = by_tier.get(tier, [])
        if not files:
            continue
        tier_totals.append({
            "tier": tier,
            "files": len(files),
            "pills": sum(f.pills for f in files),
            "typo_arb": sum(f.typo_arb for f in files),
            "typo_token": sum(f.typo_token for f in files),
            "spacing_arb": sum(f.spacing_arb for f in files),
            "spacing_token": sum(f.spacing_token for f in files),
            "heading_skips": sum(len(f.heading_skips) for f in files),
            "primary_cta_violation_files": sum(1 for f in files if f.primary_ctas > 1),
            "redundancy_hits": sum(len(f.redundancy_hits) for f in files),
            "raw_hex_rgb_in_jsx": sum(f.raw_hex_rgb_in_jsx for f in files),
            "raw_hex_rgb_in_inline_style": sum(f.raw_hex_rgb_in_inline_style for f in files),
            "raw_tailwind_color_classes": sum(f.raw_tailwind_color_classes for f in files),
        })

    totals = {
        "files": len(reports),
        "raw_violations": sum(r.total_violations() for r in reports),
        "raw_hex_rgb_in_jsx": sum(r.raw_hex_rgb_in_jsx for r in reports),
        "raw_hex_rgb_in_inline_style": sum(r.raw_hex_rgb_in_inline_style for r in reports),
        "raw_tailwind_color_classes": sum(r.raw_tailwind_color_classes for r in reports),
    }

    # Compute threshold breaches against repo-wide totals.
    breaches: list[dict[str, object]] = []
    threshold_map = {
        "raw_hex_rgb_in_jsx": "raw_hex_rgb_in_jsx_max",
        "raw_hex_rgb_in_inline_style": "raw_hex_rgb_in_inline_style_max",
        "raw_tailwind_color_classes": "raw_tailwind_color_classes_max",
    }
    for metric, threshold_key in threshold_map.items():
        max_val = cfg.thresholds.get(threshold_key)
        if isinstance(max_val, int):
            actual = int(totals[metric])
            if actual > max_val:
                breaches.append({"metric": metric, "actual": actual, "max": max_val})

    redundancy_hits_flat = [
        {"path": r.relpath, "line": lineno, "word": word}
        for r in reports
        for lineno, word in r.redundancy_hits
    ]
    heading_skips_flat = [
        {"path": r.relpath, "line": lineno, "from": prev, "to": cur}
        for r in reports
        for lineno, prev, cur in r.heading_skips
    ]

    document = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "repo_root": str(REPO_ROOT),
        "config": {
            "src_dirs": cfg.src_dirs,
            "status_words_source": cfg.status_words_source,
            "status_words": cfg.status_words,
            "thresholds": cfg.thresholds,
        },
        "totals": totals,
        "tier_totals": tier_totals,
        "files": [r.to_dict() for r in reports],
        "redundancy_hits": redundancy_hits_flat,
        "heading_skips": heading_skips_flat,
        "threshold_breaches": breaches,
    }
    return document, breaches


# --- CLI -------------------------------------------------------------------

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="UI drift audit -- source-level visual-design drift counter.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help=(
            "venture repo root used for relative-path display, tier classification, "
            "and resolving relative --src / --out / --config. Defaults to the "
            "script's own repo (parents[3])."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "output path. Default: <repo-root>/.design/audits/"
            "ui-drift-{YYYY-MM-DD}.{md,json}."
        ),
    )
    parser.add_argument(
        "--src",
        type=Path,
        action="append",
        default=None,
        help=(
            "source directory to scan, relative to repo root or absolute. "
            f"Repeatable. Default: {' + '.join(DEFAULT_SRC_DIRS)}."
        ),
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "json"],
        default="markdown",
        help="output format. Default: markdown.",
    )
    parser.add_argument(
        "--status-words",
        type=str,
        default=None,
        help=(
            "comma-separated status keywords for the redundancy check. "
            "Overrides .ui-drift.json and the built-in default."
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=(
            "explicit .ui-drift.json config path. Default: auto-discover at "
            "<repo-root>/.ui-drift.json."
        ),
    )
    args = parser.parse_args(argv)

    if args.repo_root is not None:
        set_repo_root(args.repo_root)

    # Resolve --src against repo root (so relative paths work venture-wide).
    cli_src_dirs: list[Path] | None = None
    if args.src:
        cli_src_dirs = [d if d.is_absolute() else REPO_ROOT / d for d in args.src]

    # Parse --status-words into a list (or None if not provided).
    cli_status_words: list[str] | None = None
    if args.status_words is not None:
        cli_status_words = [w.strip() for w in args.status_words.split(",") if w.strip()]

    # Resolve config path.
    config_path = args.config
    if config_path is not None and not config_path.is_absolute():
        config_path = REPO_ROOT / config_path

    cfg = resolve_config(cli_status_words, cli_src_dirs, config_path)

    # Build absolute scan-dir list from resolved config.
    if cli_src_dirs is not None:
        src_dirs_abs = cli_src_dirs
    else:
        src_dirs_abs = [
            (Path(d) if Path(d).is_absolute() else REPO_ROOT / d) for d in cfg.src_dirs
        ]

    status_words_rx = cfg.status_words_rx()
    files = list(iter_source_files(src_dirs_abs))
    reports = [audit_file(f, status_words_rx) for f in files]

    # Determine output path.
    today = dt.date.today().isoformat()
    if args.out is not None:
        out_path = args.out if args.out.is_absolute() else REPO_ROOT / args.out
    else:
        ext = "json" if args.format == "json" else "md"
        out_path = REPO_ROOT / ".design" / "audits" / f"ui-drift-{today}.{ext}"

    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.format == "json":
        document, breaches = build_json_report(reports, cfg)
        out_path.write_text(json.dumps(document, indent=2), encoding="utf-8")
        try:
            out_display = out_path.relative_to(REPO_ROOT)
        except ValueError:
            out_display = out_path
        print(f"Wrote {out_display}")
        print(f"  {len(reports)} files audited")
        print(f"  {document['totals']['raw_violations']} raw violation candidates")
        print(f"  {document['totals']['raw_hex_rgb_in_jsx']} raw hex/rgb in JSX")
        print(f"  {document['totals']['raw_hex_rgb_in_inline_style']} raw hex/rgb in inline style")
        print(f"  {document['totals']['raw_tailwind_color_classes']} raw Tailwind color classes")
        if breaches:
            print("Threshold breaches:")
            for b in breaches:
                print(f"  {b['metric']}: {b['actual']} > {b['max']}")
            return 1
        return 0

    # Markdown
    report_md = build_markdown_report(reports, cfg)
    out_path.write_text(report_md, encoding="utf-8")
    try:
        out_display = out_path.relative_to(REPO_ROOT)
    except ValueError:
        out_display = out_path
    print(f"Wrote {out_display}")
    print(f"  {len(reports)} files audited")
    print(f"  {sum(r.total_violations() for r in reports)} raw violation candidates")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
