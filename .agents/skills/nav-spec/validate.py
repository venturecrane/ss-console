#!/usr/bin/env python3
"""validate.py — post-generation navigation validator (v2).

Enforces the 24 rules documented in workflows/validate.md against HTML files.
Chrome rules (R1–R15) are self-contained. IA + pattern + a11y rules (R16–R24)
read `.design/NAVIGATION.md` for context.

Usage:
  python3 validate.py --file generated.html \
      --surface session-auth-client \
      --archetype dashboard \
      --viewport mobile \
      --task see-whats-happening \
      --pattern hub-and-spoke \
      --spec .design/NAVIGATION.md

Output: JSON violation report. Exit 0 = pass, 1 = violations found.

For v1 specs (no reachability matrix, no task/pattern tags), the validator
falls back to chrome-only rules (R1–R15). Pass --spec pointing to a v1 file
and omit --task / --pattern.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Violation:
    rule: str
    selector: str
    severity: str  # "cosmetic" | "semantic" | "structural"
    message: str
    fix: str


SURFACE_AUTHENTICATED = {"session-auth-client", "session-auth-admin", "token-auth"}
SURFACE_ALL = SURFACE_AUTHENTICATED | {"public", "auth-gate"}

TOKEN_EQUIVALENCES = {
    "border": {
        "literal": {"#e2e8f0", "#E2E8F0"},
        "var": "var(--color-border)",
        "tailwind": "slate-200",
    },
    "text-default": {
        "literal": {"#475569"},
        "var": "var(--color-text-secondary)",
        "tailwind": "slate-600",
    },
    "text-bold": {
        "literal": {"#0f172a", "#0F172A"},
        "var": "var(--color-text-primary)",
        "tailwind": "slate-900",
    },
    "primary": {
        "literal": {"#1e40af", "#1E40AF"},
        "var": "var(--color-primary)",
        "tailwind": "blue-800",
    },
    "focus": {
        "literal": {"#3b82f6", "#3B82F6"},
        "var": "var(--color-action)",
        "tailwind": "blue-500",
    },
    "disabled": {
        "literal": {"#94a3b8", "#94A3B8"},
        "var": "var(--color-text-muted)",
        "tailwind": "slate-400",
    },
}


def accepts_border_token(class_str: str) -> bool:
    if any(h in class_str for h in TOKEN_EQUIVALENCES["border"]["literal"]):
        return True
    if TOKEN_EQUIVALENCES["border"]["var"] in class_str:
        return True
    if f"border-{TOKEN_EQUIVALENCES['border']['tailwind']}" in class_str:
        return True
    return False


def find_header_block(html: str) -> tuple[str, str] | None:
    m = re.search(r"<header\b([^>]*)>", html, re.IGNORECASE)
    if not m:
        return None
    attrs = m.group(1)
    cls = re.search(r'class="([^"]*)"', attrs)
    return (m.group(0), cls.group(1) if cls else "")


# ============================================================================
# Spec parsing (v2 — reads NAVIGATION.md to power R16, R20, R21, R23, R24)
# ============================================================================

@dataclass
class SpecContext:
    """Parsed context from NAVIGATION.md needed by the IA + pattern-fitness rules.

    v2 fields (matrix, taxonomy, state, search, context) unchanged.
    v3 adds: evidence_mode, primary_tasks, pattern_decisions, provisional_overrides,
    section_1_to_4_text (for R26 authoring-direction lint).
    """
    spec_version: int = 1
    matrix_rows: list[dict] = None  # [{from, archetype, to, mechanism, required, pattern}]
    taxonomy_objects: dict = None   # {canonical: [forbidden_synonyms]}
    taxonomy_verbs: dict = None     # {action_key: canonical_verb}
    taxonomy_statuses: dict = None  # {entity.dbvalue: label}
    state_declarations: dict = None # {route: [state_name, ...]}
    search_declared: set = None     # {route, ...}
    context_pattern_surfaces: set = None  # {surface_class, ...}
    entry_only_surfaces: set = None
    terminal_surfaces: set = None

    # v3 fields
    evidence_mode: str = "validated"   # "provisional" | "validated"
    provisional_review_date: str = ""  # YYYY-MM-DD (iff evidence_mode=provisional)
    # primary_tasks per surface_class:
    #   {surface_class: [{task, frequency, criticality, evidence_source,
    #                     return_locus, return_locus_evidence}, ...]}
    tasks_by_surface: dict = None
    # pattern_decisions per {surface_class, archetype}:
    #   {(surface_class, archetype): {chosen, runner_up, defense,
    #                                 reviewer_approvals: [...], override_cited_values: [...]}}
    pattern_decisions: dict = None
    # provisional_overrides from .design/provisional-override-*.md artifacts:
    #   [{disqualifier_overridden, declared_pattern, surviving_patterns,
    #     override_rationale, deferred_validation: {event, date, artifact}}]
    provisional_overrides: list = None
    # Section 1–4 combined text for R26 lint
    section_1_to_4_text: str = ""

    def __post_init__(self):
        self.matrix_rows = self.matrix_rows or []
        self.taxonomy_objects = self.taxonomy_objects or {}
        self.taxonomy_verbs = self.taxonomy_verbs or {}
        self.taxonomy_statuses = self.taxonomy_statuses or {}
        self.state_declarations = self.state_declarations or {}
        self.search_declared = self.search_declared or set()
        self.context_pattern_surfaces = self.context_pattern_surfaces or set()
        self.entry_only_surfaces = self.entry_only_surfaces or set()
        self.terminal_surfaces = self.terminal_surfaces or set()
        self.tasks_by_surface = self.tasks_by_surface or {}
        self.pattern_decisions = self.pattern_decisions or {}
        self.provisional_overrides = self.provisional_overrides or []


def parse_spec(spec_path: Path) -> SpecContext:
    """Parse NAVIGATION.md for v2 rule inputs. Defensive — returns empty ctx
    on any parse failure so the validator falls back to chrome-only rules.
    """
    ctx = SpecContext()
    try:
        text = spec_path.read_text(encoding="utf-8")
    except OSError:
        return ctx

    # Spec version from front matter
    fm_match = re.search(r'^---\s*\n(.*?)\n---\s*\n', text, re.DOTALL)
    if fm_match:
        fm = fm_match.group(1)
        sv = re.search(r'^spec-version:\s*(\d+)', fm, re.MULTILINE)
        if sv:
            ctx.spec_version = int(sv.group(1))

    if ctx.spec_version < 2:
        return ctx  # v1 specs lack the sections below

    # Reachability matrix (Section 3.2)
    # Match table rows: | From | To | Mechanism | Required? | Pattern |
    matrix_section = re.search(
        r'###\s+3\.2\s+Matrix\s*\n(.*?)(?=\n##\s|\n###\s+3\.3|\Z)',
        text,
        re.DOTALL,
    )
    if matrix_section:
        for line in matrix_section.group(1).split("\n"):
            m = re.match(
                r'^\|\s*`?([^|`]+)`?\s*(?:\(([^)]+)\))?\s*\|\s*`?([^|`]+)`?\s*'
                r'\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|',
                line.strip()
            )
            if m:
                from_route = m.group(1).strip()
                from_archetype = (m.group(2) or "").strip()
                to_route = m.group(3).strip()
                mechanism = m.group(4).strip()
                required = m.group(5).strip()
                pattern = m.group(6).strip()
                # Skip header rows
                if from_route.lower() in {"from", "---"}:
                    continue
                ctx.matrix_rows.append({
                    "from": from_route,
                    "archetype": from_archetype,
                    "to": to_route,
                    "mechanism": mechanism,
                    "required": required,
                    "pattern": pattern,
                })

    # Entry-only and terminal surfaces (Section 3.3, 3.4)
    eo = re.search(
        r'###\s+3\.3\s+Entry-only[^\n]*\n(.*?)(?=\n##\s|\n###\s+3\.4|\Z)',
        text, re.DOTALL,
    )
    if eo:
        for line in eo.group(1).split("\n"):
            m = re.match(r'^\|\s*`?([^|`]+)`?\s*\|', line.strip())
            if m and m.group(1).strip().lower() not in {"surface", "---"}:
                ctx.entry_only_surfaces.add(m.group(1).strip())

    ts = re.search(
        r'###\s+3\.4\s+Terminal[^\n]*\n(.*?)(?=\n##\s|\Z)',
        text, re.DOTALL,
    )
    if ts:
        for line in ts.group(1).split("\n"):
            m = re.match(r'^\|\s*`?([^|`]+)`?\s*\|', line.strip())
            if m and m.group(1).strip().lower() not in {"surface", "---"}:
                ctx.terminal_surfaces.add(m.group(1).strip())

    # Content taxonomy (Section 12)
    # Object names
    obj_section = re.search(
        r'###\s+(?:10|12)\.1\s+Object[^\n]*\n(.*?)(?=\n###|\n##|\Z)',
        text, re.DOTALL,
    )
    if obj_section:
        for line in obj_section.group(1).split("\n"):
            m = re.match(
                r'^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|',
                line.strip(),
            )
            if m:
                canonical = m.group(2).strip()
                avoid_raw = m.group(3).strip()
                if canonical.lower() in {"label", "canonical label", "---"}:
                    continue
                if avoid_raw and avoid_raw != "—":
                    synonyms = [s.strip() for s in re.split(r"[,/]", avoid_raw) if s.strip()]
                    ctx.taxonomy_objects[canonical] = synonyms

    # Status labels
    status_section = re.search(
        r'###\s+(?:10|12)\.3\s+Status[^\n]*\n(.*?)(?=\n###|\n##|\Z)',
        text, re.DOTALL,
    )
    if status_section:
        for line in status_section.group(1).split("\n"):
            m = re.match(
                r'^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|',
                line.strip(),
            )
            if m:
                entity = m.group(1).strip()
                db_val = m.group(2).strip()
                label = m.group(3).strip()
                if entity.lower() in {"entity", "---"}:
                    continue
                ctx.taxonomy_statuses[f"{entity}.{db_val}"] = label

    # State machine declarations (Section 5) — capture which routes have declared states
    sm_section = re.search(
        r'##\s+5\.\s+Navigation state machine(.*?)(?=\n##\s+\d+|\Z)',
        text, re.DOTALL,
    )
    if sm_section:
        for m in re.finditer(
            r'#####?\s+(`?[/\w\[\]-]+`?)\s*(?:states|[^\n]*)\n',
            sm_section.group(1),
        ):
            route = m.group(1).strip("`")
            # Gather state names mentioned nearby
            start = m.end()
            nxt = re.search(r'#####?\s+', sm_section.group(1)[start:])
            end = start + nxt.start() if nxt else len(sm_section.group(1))
            block = sm_section.group(1)[start:end]
            states = []
            for sm in re.finditer(
                r'\|\s*(Empty|Error|Loading|Populated[^|]*|sent|accepted|declined|expired|paid|overdue)\s*\|',
                block, re.IGNORECASE,
            ):
                states.append(sm.group(1).strip())
            if states:
                ctx.state_declarations[route] = states

    # Persistent-context pattern detection — scan Section 4 (Patterns)
    pattern_section = re.search(
        r'##\s+4\.\s+(?:Pattern|Patterns)[^\n]*\n(.*?)(?=\n##\s|\Z)',
        text, re.DOTALL,
    )
    if pattern_section:
        for m in re.finditer(
            r'surface[\s-]+class:\s*([\w-]+).*?pattern:[^\n]*persistent-context',
            pattern_section.group(1), re.IGNORECASE | re.DOTALL,
        ):
            ctx.context_pattern_surfaces.add(m.group(1).strip())

    # Search declared — scan for "search" references in any section
    if re.search(r'\bsearch\s+(?:strategy|input|affordance)\b', text, re.IGNORECASE):
        # Coarse detection: if the spec says any surface has search, the
        # validator asks for explicit route-level search declaration. Parse
        # "Search present on: <routes>" if present; otherwise treat as a
        # cross-cutting hint not route-specific (no per-surface R23 check).
        search_block = re.search(
            r'###?\s+Search(?:\s+strategy)?\s*\n(.*?)(?=\n##|\n###)',
            text, re.DOTALL | re.IGNORECASE,
        )
        if search_block:
            for m in re.finditer(r'`(/[\w\[\]/-]+)`', search_block.group(1)):
                ctx.search_declared.add(m.group(1))

    # ========================================================================
    # v3 parsing — evidence mode, task columns, pattern decisions
    # ========================================================================
    if ctx.spec_version >= 3:
        _parse_v3_fields(text, ctx, spec_path)

    return ctx


def _parse_v3_fields(text: str, ctx: SpecContext, spec_path: Path) -> None:
    """Populate v3-specific fields on ctx. Defensive; silently tolerates
    missing or malformed sections to keep the validator running."""
    # --- Front-matter: evidence-mode, provisional-review-date ---
    fm_match = re.search(r'^---\s*\n(.*?)\n---\s*\n', text, re.DOTALL)
    if fm_match:
        fm = fm_match.group(1)
        em = re.search(r'^evidence-mode:\s*(\w+)', fm, re.MULTILINE)
        if em:
            ctx.evidence_mode = em.group(1).strip()
        prd = re.search(r'^provisional-review-date:\s*([\d-]+)', fm, re.MULTILINE)
        if prd:
            ctx.provisional_review_date = prd.group(1).strip()

    # --- Section 1–4 combined text for R26 lint ---
    # Capture everything between "## 1." and "## 5." (or EOF).
    s14 = re.search(
        r'^##\s+1\.\s.*?(?=^##\s+5\.\s|\Z)',
        text, re.DOTALL | re.MULTILINE,
    )
    if s14:
        ctx.section_1_to_4_text = s14.group(0)

    # --- Task tables per surface class (Section 1.N) ---
    # Match "### 1.N Surface class: <name>" blocks up to the next "### 1." or "## 2."
    # Tolerate `backtick-wrapped` surface-class names.
    surface_blocks = re.finditer(
        r'^###\s+1\.\d+\s+Surface\s+class:\s*`?([\w-]+)`?[^\n]*\n(.*?)(?=^###\s+1\.\d+|^##\s+\d+\.|\Z)',
        text, re.DOTALL | re.MULTILINE,
    )
    for sb in surface_blocks:
        surface_class = sb.group(1).strip().strip("`")
        block = sb.group(2)
        tasks = _parse_task_table(block)
        if tasks:
            ctx.tasks_by_surface[surface_class] = tasks

    # --- Section 4.N pattern decisions ---
    sec4 = re.search(
        r'##\s+4\.\s+(?:Pattern|Patterns)[^\n]*\n(.*?)(?=\n##\s+\d+\.|\Z)',
        text, re.DOTALL,
    )
    if sec4:
        sec4_body = sec4.group(1)
        # Enumerate each "### 4.N ..." heading along with the block that follows.
        heading_positions = [
            (m.start(), m.end(), m.group(0))
            for m in re.finditer(r'^###\s+4\.\d+[^\n]*$', sec4_body, re.MULTILINE)
        ]
        for idx, (h_start, h_end, heading_line) in enumerate(heading_positions):
            if idx + 1 < len(heading_positions):
                block_end = heading_positions[idx + 1][0]
            else:
                block_end = len(sec4_body)
            block = sec4_body[h_end:block_end]

            # Try to extract surface × archetype from the HEADING line itself
            hdr = re.search(
                r'\b(public|auth-gate|token-auth|session-auth-client|session-auth-admin)\b'
                r'[^\n]*?\b(dashboard|list|detail|form|wizard|empty|error|modal|drawer|transient)\b',
                heading_line, re.IGNORECASE,
            )
            # Fallback: look inside the body
            if not hdr:
                hdr = re.search(
                    r'\b(public|auth-gate|token-auth|session-auth-client|session-auth-admin)\b'
                    r'[^\n]*?\b(dashboard|list|detail|form|wizard|empty|error|modal|drawer|transient)\b',
                    block, re.IGNORECASE,
                )
            if not hdr:
                continue
            surface_class = hdr.group(1).lower()
            archetype = hdr.group(2).lower()

            chosen = _extract_field(block, r'Chosen\s+pattern')
            runner_up = _extract_field(block, r'Runner[-\s]up(?:\s+pattern)?')
            defense = _extract_field(block, r'Defense', multiline=True)

            # Reviewer approvals — lines like "Reviewer approval: IA-architect D1 ..."
            approvals = []
            for m in re.finditer(
                r'(?:Reviewer\s+approval|Approval)\s*(?:\(([^)]+)\))?\s*:\s*([^\n]+)',
                block, re.IGNORECASE,
            ):
                approval_text = (m.group(1) or "") + " " + m.group(2)
                # Parse which disqualifier IDs the approval cites
                cited_ds = re.findall(r'\bD\d+\b', approval_text)
                approvals.append({
                    "reviewer": (m.group(1) or "unknown").strip(),
                    "text": m.group(2).strip(),
                    "cited_disqualifiers": cited_ds,
                })

            # Override-cited values — any "return_locus for X is Y" assertions
            override_values = []
            for m in re.finditer(
                r'return_locus\s+for\s+([\w-]+)\s+is\s+(\w[-\w]*)',
                block, re.IGNORECASE,
            ):
                override_values.append({
                    "task": m.group(1).strip(),
                    "claimed_locus": m.group(2).strip().lower(),
                })

            ctx.pattern_decisions[(surface_class, archetype)] = {
                "chosen": (chosen or "").lower().strip(),
                "runner_up": (runner_up or "").lower().strip(),
                "defense": defense or "",
                "reviewer_approvals": approvals,
                "override_cited_values": override_values,
            }

    # --- Provisional override artifacts ---
    # Look in the spec file's parent directory for provisional-override-*.md
    try:
        parent = spec_path.parent
        for path in parent.glob("provisional-override-*.md"):
            override = _parse_provisional_override(path)
            if override:
                ctx.provisional_overrides.append(override)
    except Exception:
        pass  # silent; validator still runs


def _parse_task_table(block: str) -> list[dict]:
    """Parse a task table from a surface-class block in Section 1.

    Expected columns (in any order): Task, Frequency, Criticality, Trigger,
    Completion, Evidence source, return_locus, return_locus_evidence.

    Returns [{task, frequency, criticality, evidence_source, return_locus,
    return_locus_evidence}, ...].
    """
    tasks = []
    # Find the first table in the block (heuristic: first line starting with | that
    # contains "Task" case-insensitively).
    lines = block.split("\n")
    header_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("|") and re.search(r"\btask\b", line, re.IGNORECASE):
            header_idx = i
            break
    if header_idx is None:
        return tasks

    # Parse header cells — normalize whitespace to underscores for exact lookup
    def _normalize_header(s: str) -> str:
        s = s.strip().strip("`_*").strip().lower()
        # Replace any run of whitespace/underscore with a single underscore
        return re.sub(r'[_\s]+', '_', s)

    header_cells = [_normalize_header(c) for c in lines[header_idx].strip().strip("|").split("|")]

    COL_MAP = {
        "task": "task",
        "frequency": "frequency",
        "criticality": "criticality",
        "trigger": "trigger",
        "completion": "completion",
        "evidence_source": "evidence_source",
        "return_locus": "return_locus",
        "return_locus_evidence": "return_locus_evidence",
    }
    col_keys = [COL_MAP.get(c) for c in header_cells]

    # Skip separator row if present
    start = header_idx + 1
    if start < len(lines) and re.match(r"^\s*\|[\s|:-]+\|\s*$", lines[start]):
        start += 1

    for line in lines[start:]:
        if not line.strip().startswith("|"):
            break  # end of table
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < len(col_keys):
            continue
        entry = {}
        for i, cell in enumerate(cells[:len(col_keys)]):
            if col_keys[i]:
                entry[col_keys[i]] = cell
        if entry.get("task"):
            # Normalize common fields to lowercase
            for k in ("frequency", "criticality", "return_locus"):
                if k in entry:
                    entry[k] = entry[k].lower()
            tasks.append(entry)
    return tasks


def _extract_field(block: str, field_pattern: str, multiline: bool = False) -> str:
    """Extract value of a bold-labeled field like **Chosen pattern:** <value>.
    Handles both `**Chosen pattern:** value` and `**Chosen pattern**: value`.
    If multiline=True, capture until the next **bold field** or blank line.
    """
    # Accept the colon either inside or outside the bold tags.
    if multiline:
        m = re.search(
            rf'\*\*\s*{field_pattern}\s*:?\s*\*\*\s*:?\s*(.+?)(?=\n\s*\*\*|\n\n|\Z)',
            block, re.DOTALL | re.IGNORECASE,
        )
    else:
        m = re.search(
            rf'\*\*\s*{field_pattern}\s*:?\s*\*\*\s*:?\s*([^\n]+)',
            block, re.IGNORECASE,
        )
    return m.group(1).strip() if m else ""


def _parse_provisional_override(path: Path) -> dict | None:
    """Parse a .design/provisional-override-<date>.md artifact's front matter."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    fm = re.search(r'^---\s*\n(.*?)\n---', text, re.DOTALL)
    if not fm:
        return None
    body = fm.group(1)

    def field(name: str) -> str:
        m = re.search(rf'^{name}:\s*(.+?)$', body, re.MULTILINE | re.IGNORECASE)
        return m.group(1).strip() if m else ""

    # deferred_validation is a nested block; parse inline
    dv_block = re.search(
        r'^deferred_validation:\s*\n((?:[ \t]+[^\n]+\n?)+)', body, re.MULTILINE,
    )
    deferred = {}
    if dv_block:
        for m in re.finditer(r'^[ \t]+(\w+):\s*(.+?)$', dv_block.group(1), re.MULTILINE):
            deferred[m.group(1).lower()] = m.group(2).strip()

    return {
        "path": str(path),
        "disqualifier_overridden": field("disqualifier_overridden"),
        "declared_pattern": field("declared_pattern").lower(),
        "surviving_patterns": field("surviving_patterns"),
        "override_rationale": field("override_rationale"),
        "deferred_validation": deferred,
    }


# ============================================================================
# Chrome rules (R1–R15) — preserved verbatim from v1
# ============================================================================

def check_chrome(html: str, surface: str, archetype: str, viewport: str) -> list[Violation]:
    violations: list[Violation] = []
    header = find_header_block(html)

    # R1 — Header sticky, not fixed
    if header:
        _, hclass = header
        if re.search(r'\bfixed\s+top-0\b|\bfixed\b(?=[^"]*\btop-0\b)', hclass):
            violations.append(Violation(
                rule="R1",
                selector="<header>",
                severity="semantic",
                message="Header uses `fixed top-0` instead of `sticky top-0`.",
                fix="Replace `fixed top-0` with `sticky top-0`. Fixed removes header from document flow.",
            ))

    # R2 — Solid header bg
    if header and surface != "public":
        _, hclass = header
        if re.search(r"backdrop-blur-", hclass):
            violations.append(Violation(
                rule="R2",
                selector="<header>",
                severity="cosmetic",
                message="Header uses `backdrop-blur-*` (glassmorphism).",
                fix="Replace with solid `bg-white`.",
            ))
        if re.search(r"bg-[a-z0-9\-]+/\d+", hclass):
            violations.append(Violation(
                rule="R2b",
                selector="<header>",
                severity="cosmetic",
                message="Header background uses opacity modifier (e.g., `bg-white/85`).",
                fix="Use solid `bg-white` (no opacity suffix).",
            ))

    # R3 — Client name stands alone
    if header and surface in SURFACE_AUTHENTICATED:
        hstart = html.find("<header")
        hend = html.find("</header>", hstart) if hstart >= 0 else -1
        if hstart >= 0 and hend > hstart:
            hinner = html[hstart:hend]
            first_text_match = re.search(r">\s*([A-Za-z][^<]{2,})<", hinner)
            first_text_pos = first_text_match.start() if first_text_match else len(hinner)
            prefix = hinner[:first_text_pos]
            if re.search(r'<span[^>]*class="[^"]*material-symbols-[a-z]+', prefix) \
               or re.search(r"<svg\b", prefix) \
               or re.search(r"<img\b", prefix):
                violations.append(Violation(
                    rule="R3",
                    selector="<header> first child (before client name)",
                    severity="cosmetic",
                    message="Header contains an icon, image, or SVG decoration before the client name.",
                    fix="Remove decorative element. Client name stands alone.",
                ))

    # R4 — Back not wrapped in breadcrumb nav
    breadcrumb_navs = re.findall(
        r'<nav[^>]+aria-label="Breadcrumb"[^>]*>(.*?)</nav>',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    for nav_inner in breadcrumb_navs:
        count = len(re.findall(r"<a\b|<button\b", nav_inner))
        allows_breadcrumbs = (surface == "session-auth-admin" and archetype in {"list", "detail"})
        if not allows_breadcrumbs and count >= 1:
            violations.append(Violation(
                rule="R4",
                selector='<nav aria-label="Breadcrumb">',
                severity="semantic",
                message="Back affordance wrapped in breadcrumb nav on a surface where breadcrumbs are forbidden.",
                fix="Unwrap. Use `<a>` or `<button>` with `aria-label`.",
            ))
        elif allows_breadcrumbs and count == 1:
            violations.append(Violation(
                rule="R4b",
                selector='<nav aria-label="Breadcrumb">',
                severity="semantic",
                message="Breadcrumb nav wraps a single link — this is a back button, not a breadcrumb.",
                fix="Unwrap. Breadcrumbs require multiple levels.",
            ))

    # R5 — Back href is canonical
    if archetype == "detail":
        back_anchors = re.findall(
            r'<a[^>]+href="([^"]*)"[^>]*>.*?(?:chevron_left|arrow_back).*?</a>',
            html,
            re.DOTALL,
        )
        for href in back_anchors:
            if href.strip() in {"#", "javascript:void(0)", "javascript:"}:
                violations.append(Violation(
                    rule="R5",
                    selector=f'<a href="{href}">',
                    severity="semantic",
                    message=f"Back link uses placeholder `href=\"{href}\"`.",
                    fix="Use a hardcoded canonical URL string. Never `#`, `javascript:`, or `history.back()`.",
                ))
        if re.search(r"onclick=\"history\.back\(\)\"", html):
            violations.append(Violation(
                rule="R5b",
                selector="onclick=history.back()",
                severity="semantic",
                message="Back uses `history.back()`.",
                fix="Replace with hardcoded canonical URL. Deep-links have no history to return to.",
            ))

    # R6 — No global nav tabs
    if header and surface != "session-auth-admin":
        hstart = html.find("<header")
        hend = html.find("</header>", hstart) if hstart >= 0 else -1
        if hstart >= 0 and hend > hstart:
            hinner = html[hstart:hend]
            if re.search(r'role="tablist"|role="tab"', hinner):
                violations.append(Violation(
                    rule="R6",
                    selector="<header> (role=tablist/tab)",
                    severity="structural",
                    message="Header contains tablist/tab roles — global nav tabs forbidden.",
                    fix="Remove. Secondary navigation below the header.",
                ))
            anchors = re.findall(r"<a\b[^>]*>([^<]+)</a>", hinner)
            nav_like = [a for a in anchors if a.strip() and len(a.strip().split()) <= 3]
            contact_verbs = {"email", "text", "call", "sms", "phone"}
            contact_like = [a for a in nav_like if any(v in a.strip().lower().split() for v in contact_verbs)]
            effective_tab_count = len(nav_like) - len(contact_like)
            if effective_tab_count >= 3:
                violations.append(Violation(
                    rule="R6b",
                    selector="<header> (3+ short-text non-contact links)",
                    severity="structural",
                    message=f"Header has {effective_tab_count} short-text non-contact links — looks like a nav tab bar.",
                    fix="Remove nav-tab-style links from header.",
                ))

    # R7 — No sticky-bottom outside dialog
    bottom_sticky = re.search(r'class="[^"]*\b(?:fixed|sticky)\s+bottom-0\b', html)
    if bottom_sticky and surface in SURFACE_ALL:
        surrounding = html[max(0, bottom_sticky.start() - 200):bottom_sticky.end()]
        if "<dialog" not in surrounding and 'role="dialog"' not in surrounding:
            violations.append(Violation(
                rule="R7",
                selector="element with `fixed bottom-0` or `sticky bottom-0`",
                severity="structural",
                message="Sticky-bottom outside dialog (bottom-tab nav or duplicated action bar).",
                fix="Remove. Primary action above the fold via document flow.",
            ))

    # R8 — No footer on auth
    if surface in SURFACE_AUTHENTICATED:
        if re.search(r"<footer\b", html):
            violations.append(Violation(
                rule="R8",
                selector="<footer>",
                severity="structural",
                message="Footer on authenticated surface.",
                fix="Remove footer.",
            ))

    # R9 — No real-face photos
    real_face_srcs = re.findall(r'<img[^>]+src="([^"]+)"', html)
    for src in real_face_srcs:
        if (re.search(r"googleusercontent\.com/aida[-/]", src)
                or "unsplash.com" in src
                or "pexels.com" in src):
            violations.append(Violation(
                rule="R9",
                selector=f'<img src="{src[:60]}...">',
                severity="structural",
                message="Image uses real-face photo placeholder source.",
                fix="Replace with solid-color initials circle.",
            ))
            break

    # R10 — No marketing CTAs on auth
    if surface in SURFACE_AUTHENTICATED:
        marketing_patterns = [
            r"\b(?:schedule|book)\s+(?:a\s+)?(?:call|demo|meeting|consultation)\b",
            r"\bget\s+started\b",
            r"\blearn\s+more\b",
            r"\bsign\s+up\s+(?:now|today|free)\b",
        ]
        for pat in marketing_patterns:
            if re.search(pat, html, re.IGNORECASE):
                violations.append(Violation(
                    rule="R10",
                    selector=f"text match: {pat}",
                    severity="structural",
                    message="Marketing CTA on authenticated surface.",
                    fix="Remove. User is already a customer.",
                ))
                break

    # R11 — Header height matches viewport
    if header:
        _, hclass = header
        expected_mobile = {"h-14", "h-[56px]"}
        expected_desktop_any = {"h-16", "h-[64px]", "md:h-16", "md:h-[64px]"}
        has_h_class = re.search(r"\bh-\[?\d+(?:px)?\]?\b", hclass) or re.search(r"\bmd:h-\[?\d+(?:px)?\]?\b", hclass)
        if has_h_class:
            if viewport == "mobile" and not any(cls in hclass for cls in expected_mobile):
                if not re.search(r"\b(?:h-14|h-\[56px\])\b", hclass):
                    violations.append(Violation(
                        rule="R11",
                        selector="<header>",
                        severity="cosmetic",
                        message="Header height does not match 56px (mobile).",
                        fix="Use `h-14` on mobile.",
                    ))
            if viewport == "desktop" and not any(cls in hclass for cls in expected_desktop_any):
                if not re.search(r"\b(?:h-16|h-\[64px\]|md:h-16|md:h-\[64px\])\b", hclass):
                    violations.append(Violation(
                        rule="R11",
                        selector="<header>",
                        severity="cosmetic",
                        message="Header height does not match 64px (desktop).",
                        fix="Use `h-16` or `md:h-16` on desktop.",
                    ))

    # R14 — Landmarks
    if not re.search(r"<header\b[^>]*role=\"banner\"|<header\b", html):
        violations.append(Violation(
            rule="R14a",
            selector="<header>",
            severity="semantic",
            message="No <header> landmark found.",
            fix="Wrap top band in `<header role=\"banner\">`.",
        ))
    if not re.search(r"<main\b", html):
        violations.append(Violation(
            rule="R14b",
            selector="<main>",
            severity="semantic",
            message="No <main> landmark found.",
            fix="Wrap primary content in `<main role=\"main\">`.",
        ))

    # R15 — Skip-to-main
    skip_link_match = re.search(
        r'<a\b(?=[^>]*\bclass="[^"]*sr-only)[^>]*\bhref="#([a-zA-Z][\w-]*)"',
        html,
    )
    if not skip_link_match:
        violations.append(Violation(
            rule="R15",
            selector="skip-to-main link",
            severity="semantic",
            message="No skip-to-main link detected.",
            fix='Prepend `<a href="#main" class="sr-only focus:not-sr-only ...">Skip to main content</a>` before <header>. <main> must carry matching `id`.',
        ))
    else:
        target_id = skip_link_match.group(1)
        if not re.search(rf'<main\b[^>]*id="{re.escape(target_id)}"', html):
            violations.append(Violation(
                rule="R15b",
                selector=f'<main id="{target_id}">',
                severity="semantic",
                message=f'Skip-link targets #{target_id} but no <main id="{target_id}"> exists.',
                fix=f'Add `id="{target_id}"` to <main>.',
            ))

    return violations


# ============================================================================
# IA, Pattern, A11y rules (R16–R24) — NEW in v2
# ============================================================================

# Pattern required-elements map. Expandable. Keys are pattern names from
# pattern-catalog.md; values are predicates that return a list of violations
# given the HTML and classification context.

PATTERN_REQUIRED_ELEMENTS: dict[str, list[tuple[str, str]]] = {
    # pattern name → list of (description, regex-that-should-match)
    "sequential": [
        ("Progress indicator (Step N of M)", r"[Ss]tep\s+\d+\s+of\s+\d+"),
        ("Previous button", r"<button[^>]*>\s*(?:Previous|Back|Prev)\s*<|>\s*(?:Previous|Back|Prev)\s*</"),
        ("Next button", r"<button[^>]*>\s*(?:Next|Continue|Submit)\s*<|>\s*(?:Next|Continue|Submit)\s*</"),
    ],
    "modal": [
        ("aria-modal=\"true\"", r'aria-modal="true"'),
        ("Close button", r'<button[^>]*aria-label="(?:Close|Dismiss)"|×|&times;'),
    ],
    "drawer": [
        ("aria-label or aria-labelledby", r'aria-label(?:ledby)?="[^"]+"'),
    ],
    # Hub-and-spoke and master-detail are primarily checked via R16
    # (reachability matrix); no extra per-element checks here.
}


def check_ia(html: str, surface: str, archetype: str, viewport: str,
             task: str | None, pattern: str | None,
             ctx: SpecContext, current_route: str | None = None
             ) -> list[Violation]:
    violations: list[Violation] = []

    if ctx.spec_version < 2:
        # v1 spec: skip all IA rules (no matrix, no taxonomy)
        return violations

    # -------- R16 — Reachability matrix enforcement --------
    # Filter matrix rows by surface class to avoid cross-surface false positives.
    # Map surface class to the canonical dashboard root path. If --route is
    # provided, prefer literal match.
    SURFACE_DASHBOARD_ROOT = {
        "public": "/",
        "session-auth-client": "/portal",
        "session-auth-admin": "/admin",
    }
    if archetype == "dashboard":
        if current_route:
            allowed_froms = {current_route}
        else:
            root = SURFACE_DASHBOARD_ROOT.get(surface)
            allowed_froms = {root} if root else set()

        required_tos = set()
        for row in ctx.matrix_rows:
            if row["archetype"].lower() != "dashboard":
                continue
            if row["required"].lower() not in {"yes", "y"}:
                continue
            # Filter by surface — only check rows where From matches our
            # current surface's dashboard root.
            if allowed_froms and row["from"] not in allowed_froms:
                continue
            required_tos.add(row["to"])

        for to in required_tos:
            # Skip external protocols/tokens, contact mechanisms, and API
            # endpoints (actions like logout, not destinations).
            if to.startswith("<") or to.startswith("mailto:") or to.startswith("sms:") \
               or to.startswith("tel:") or to.startswith("/api/"):
                continue
            pattern_to = re.escape(to).replace(r"\[id\]", r"[^\"]+")
            if not re.search(rf'href="{pattern_to}"', html):
                violations.append(Violation(
                    rule="R16",
                    selector=f'missing <a href="{to}">',
                    severity="structural",
                    message=f'Dashboard must link to sibling list `{to}` per reachability matrix, but no matching <a href> found.',
                    fix=f'Add `<a href="{to}">` as a section card on the dashboard. Matrix row declares Required=Yes.',
                ))

    # -------- R17 — Pattern conformance --------
    if pattern:
        reqs = PATTERN_REQUIRED_ELEMENTS.get(pattern, [])
        for description, pattern_regex in reqs:
            if not re.search(pattern_regex, html, re.IGNORECASE):
                violations.append(Violation(
                    rule="R17",
                    selector=f"pattern={pattern}",
                    severity="structural",
                    message=f'Pattern "{pattern}" requires: {description}, but not found in output.',
                    fix=f'Implement {description} per pattern-catalog.md, or change the pattern declaration.',
                ))

    # -------- R18 — No dead ends --------
    # A surface has ≥1 exit: <a href>, <button> with handler, form, or back.
    if archetype not in {"error"}:  # error surfaces handled separately
        has_link = bool(re.search(r'<a\b[^>]+href="[^"#]+', html))
        has_button = bool(re.search(r"<button\b", html))
        has_form = bool(re.search(r"<form\b", html))
        if not (has_link or has_button or has_form):
            # Check if surface is declared Terminal
            is_terminal = any(current_route and current_route in ts
                              for ts in ctx.terminal_surfaces)
            if not is_terminal:
                violations.append(Violation(
                    rule="R18",
                    selector="<main>",
                    severity="structural",
                    message="Surface has no navigation exit (no links, buttons, or forms).",
                    fix="Add a primary action, back affordance, or sibling link.",
                ))
    elif archetype == "error":
        # Error must have recovery
        recovery_patterns = [
            r"\b(?:[Rr]etry|[Tt]ry again|[Gg]o (?:to )?home|[Bb]ack (?:to )?(?:home|portal))\b",
            r"<a[^>]+href=\"/\"",
            r"<button[^>]*>(?:[^<]*(?:retry|reload|home))",
        ]
        if not any(re.search(p, html) for p in recovery_patterns):
            violations.append(Violation(
                rule="R18",
                selector="error surface",
                severity="structural",
                message="Error archetype has no recovery affordance (Retry, Go home, or Contact).",
                fix="Add a recovery CTA linking to a known-good surface.",
            ))

    # -------- R19 — Token-auth cold arrival --------
    if surface == "token-auth":
        cold_violations = [
            (r"[Ww]elcome back", "prior-session-implying copy ('welcome back')"),
            (r"[Cc]ontinue where you left off", "prior-session-implying copy ('continue where you left off')"),
            (r"<button[^>]*>\s*Sign\s*out", "sign-out button on token-auth surface"),
        ]
        for pat, desc in cold_violations:
            if re.search(pat, html):
                violations.append(Violation(
                    rule="R19",
                    selector=f"text/element: {desc}",
                    severity="structural",
                    message=f"Token-auth surface assumes prior session: {desc}.",
                    fix="Render self-contained context. Token-auth is cold-arrival by definition.",
                ))

    # -------- R20 — Content taxonomy adherence --------
    if ctx.taxonomy_objects:
        # Extract visible text from key positions (crude — h1/h2/h3/button/a/span)
        visible_text_matches = re.findall(
            r'<(?:h[1-6]|button|a|span|div|p|td)[^>]*>([^<]+)</(?:h[1-6]|button|a|span|div|p|td)>',
            html,
        )
        full_visible = " ".join(t.strip() for t in visible_text_matches)
        for canonical, synonyms in ctx.taxonomy_objects.items():
            for syn in synonyms:
                # Case-insensitive whole-word match
                if re.search(rf"\b{re.escape(syn)}\b", full_visible, re.IGNORECASE):
                    # Don't report if the canonical term also appears (may be intentional contrast)
                    if not re.search(rf"\b{re.escape(canonical)}\b", full_visible, re.IGNORECASE):
                        violations.append(Violation(
                            rule="R20",
                            selector=f'text match "{syn}"',
                            severity="semantic",
                            message=f'Forbidden synonym "{syn}" used; canonical term is "{canonical}".',
                            fix=f'Replace "{syn}" with "{canonical}" per taxonomy.',
                        ))
                        break  # one violation per canonical, avoid noise

    # -------- R21 — State machine completeness --------
    # Heuristic: if the HTML renders no visible content (<main> essentially empty),
    # an empty or error state was probably intended but not rendered.
    main_match = re.search(r"<main\b[^>]*>(.*?)</main>", html, re.DOTALL)
    if main_match:
        main_inner = main_match.group(1)
        # Strip tags to check if there's any text content
        stripped = re.sub(r"<[^>]+>", "", main_inner).strip()
        if len(stripped) < 10:
            violations.append(Violation(
                rule="R21",
                selector="<main>",
                severity="structural",
                message="<main> has no visible text content — state rendering likely missing.",
                fix="Render explicit empty/error state with copy per state-machine-template.md.",
            ))

    # -------- R22 — Heading hierarchy --------
    h1_count = len(re.findall(r"<h1\b", html, re.IGNORECASE))
    if h1_count == 0:
        violations.append(Violation(
            rule="R22a",
            selector="<h1>",
            severity="semantic",
            message="No <h1> element found.",
            fix="Add a single <h1> as the surface's primary heading.",
        ))
    elif h1_count > 1:
        violations.append(Violation(
            rule="R22a",
            selector="<h1>",
            severity="semantic",
            message=f"{h1_count} <h1> elements; each surface should have exactly one.",
            fix="Demote secondary <h1>s to <h2>.",
        ))
    # Check for level skipping (h1 → h3 without h2)
    levels_present = set()
    for m in re.finditer(r"<h([1-6])\b", html, re.IGNORECASE):
        levels_present.add(int(m.group(1)))
    if 1 in levels_present and 3 in levels_present and 2 not in levels_present:
        violations.append(Violation(
            rule="R22b",
            selector="heading hierarchy",
            severity="semantic",
            message="Heading hierarchy skips level: <h1> and <h3> present but no <h2>.",
            fix="Use <h2> for major sections between <h1> and <h3>.",
        ))

    # -------- R23 — Search affordance --------
    if current_route and current_route in ctx.search_declared:
        if not re.search(r'<input[^>]+type="search"|<input[^>]+role="searchbox"', html):
            violations.append(Violation(
                rule="R23",
                selector="search input",
                severity="semantic",
                message=f'Spec declares search on `{current_route}` but no search input found.',
                fix='Add `<input type="search">` or update spec to remove search declaration.',
            ))

    # -------- R24 — Cross-surface context --------
    if surface in ctx.context_pattern_surfaces:
        # Require a context indicator in header (e.g., client chip, breadcrumb prefix)
        header = find_header_block(html)
        if header:
            hstart = html.find("<header")
            hend = html.find("</header>", hstart) if hstart >= 0 else -1
            hinner = html[hstart:hend] if hend > hstart else ""
            # Look for a context-indicator pattern: span/chip with data-context attribute,
            # or a persistent workspace name display
            has_context = bool(
                re.search(r'data-context="[^"]+"', hinner) or
                re.search(r'<(?:span|div|nav)[^>]*class="[^"]*(?:context|workspace|tenant|client-chip)[^"]*"', hinner)
            )
            if not has_context:
                violations.append(Violation(
                    rule="R24",
                    selector="<header> context indicator",
                    severity="structural",
                    message=f"Surface class `{surface}` uses persistent-context pattern but no context indicator in header.",
                    fix='Add a context indicator (chip, label, or breadcrumb prefix) showing the current workspace entity.',
                ))

    return violations


# ============================================================================
# v3 rules: R25 (pattern fitness) and R26 (authoring-direction lint)
# ============================================================================

# Pattern disqualifier conditions mirrored from references/pattern-disqualifiers.md.
# Each entry: pattern name → list of (disqualifier_id, predicate_fn, message_fn,
# citation, heuristic_untested).
#
# predicate_fn takes (profile, archetype, viewport) and returns True iff the
# disqualifier fires. profile is a dict of task-model inputs derived from the
# spec + surface class.

def _disq_hub_and_spoke_d1(profile, archetype, viewport):
    """D1: ≥2 of the top-3-by-frequency tasks have return_locus ≠ hub."""
    tasks = profile.get("tasks", [])
    if not tasks:
        return False, None
    # Rank by frequency (high > medium > low/variable); ties broken by criticality
    # (blocking > high > medium > low).
    FREQ = {"high": 3, "medium": 2, "variable": 2, "low": 1}
    CRIT = {"blocking": 4, "high": 3, "medium": 2, "low": 1}
    ranked = sorted(
        tasks,
        key=lambda t: (
            -FREQ.get((t.get("frequency") or "").lower(), 0),
            -CRIT.get((t.get("criticality") or "").lower(), 0),
            t.get("task", ""),
        ),
    )
    top3 = ranked[:3]
    non_hub = [t for t in top3 if (t.get("return_locus") or "").lower() != "hub"]
    if len(non_hub) >= 2:
        names = ", ".join(t.get("task", "?") for t in non_hub)
        return True, f"top-3-by-frequency includes {len(non_hub)} tasks with non-hub return_locus: {names}"
    return False, None


def _disq_hub_and_spoke_d2(profile, archetype, viewport):
    dc = profile.get("destination_count", 0)
    if dc > 7:
        return True, f"destination_count={dc} exceeds 7 (NN/g hub cap)"
    return False, None


def _disq_persistent_tabs_d3(profile, archetype, viewport):
    dc = profile.get("destination_count", 0)
    if dc > 5 and viewport == "mobile":
        return True, f"destination_count={dc} exceeds 5 (Material 3 bottom-nav cap on mobile)"
    return False, None


def _disq_persistent_tabs_d4(profile, archetype, viewport):
    dc = profile.get("destination_count", 0)
    if dc > 7 and viewport == "desktop":
        return True, f"destination_count={dc} exceeds 7 (HIG tab-bar practical cap on desktop)"
    return False, None


def _disq_persistent_tabs_d5(profile, archetype, viewport):
    if (profile.get("task_ordering") or "").lower() == "mandatory_sequence":
        return True, "task_ordering=mandatory_sequence contradicts tabs' peer-destination premise"
    return False, None


def _disq_sequential_d6(profile, archetype, viewport):
    if (profile.get("task_ordering") or "").lower() != "mandatory_sequence":
        return True, f"task_ordering={profile.get('task_ordering', 'unspecified')} is not mandatory_sequence"
    return False, None


def _disq_nested_doll_d7(profile, archetype, viewport):
    mtd = profile.get("max_tree_depth", 0)
    if mtd < 3:
        return True, f"max_tree_depth={mtd} is <3 (nested-doll requires deep hierarchy)"
    return False, None


def _disq_faceted_d8(profile, archetype, viewport):
    idx = profile.get("index_size", 0)
    if idx < 30:
        return True, f"index_size={idx} is <30 (HEURISTIC)"
    return False, None


PATTERN_DISQUALIFIERS = {
    "hub-and-spoke": [
        ("D1", _disq_hub_and_spoke_d1,
         "NN/g Mobile Navigation Patterns — requires hub return",
         False),
        ("D2", _disq_hub_and_spoke_d2,
         "NN/g Hub-and-Spoke guidance — 3–7 destinations",
         False),
    ],
    "persistent-tabs": [
        ("D3", _disq_persistent_tabs_d3,
         "Material Design 3 Bottom navigation — 3–5 destinations on mobile",
         False),
        ("D4", _disq_persistent_tabs_d4,
         "Apple HIG Tab bars — practical cap on desktop",
         False),
        ("D5", _disq_persistent_tabs_d5,
         "Material 3 Tabs — peer destinations, not sequential steps",
         False),
    ],
    "sequential": [
        ("D6", _disq_sequential_d6,
         "NN/g Wizard Design — requires mandatory sequence",
         False),
    ],
    "nested-doll": [
        ("D7", _disq_nested_doll_d7,
         "NN/g Mobile Navigation Patterns — nested-doll for deep hierarchies",
         False),
    ],
    "faceted": [
        ("D8", _disq_faceted_d8,
         "NN/g Filters vs. Facets (heuristic threshold)",
         True),  # HEURISTIC: UNTESTED
    ],
}


def _build_task_profile(ctx: SpecContext, surface: str, archetype: str,
                        current_route: str | None) -> dict:
    """Derive a task profile (the inputs to disqualifier predicates) from the
    parsed spec context for the given surface + archetype."""
    tasks = ctx.tasks_by_surface.get(surface, [])

    # destination_count — count of distinct "To" routes in matrix from the
    # surface's dashboard archetype that are Required=Yes and section-card-like.
    SURFACE_DASHBOARD_ROOT = {
        "public": "/",
        "session-auth-client": "/portal",
        "session-auth-admin": "/admin",
    }
    root = SURFACE_DASHBOARD_ROOT.get(surface)
    destinations = set()
    for row in ctx.matrix_rows:
        if row.get("from") != root:
            continue
        if row.get("archetype", "").lower() != "dashboard":
            continue
        if row.get("required", "").lower() not in {"yes", "y"}:
            continue
        to = row.get("to", "")
        if to.startswith("<") or to.startswith("mailto:") or to.startswith("sms:") \
           or to.startswith("tel:") or to.startswith("/api/"):
            continue
        destinations.add(to)

    # max_tree_depth — longest ancestor chain expressible from matrix
    depth = 1
    for row in ctx.matrix_rows:
        slashes = row.get("to", "").count("/")
        if slashes > depth:
            depth = slashes

    # task_ordering — if the spec declares any wizard archetype in matrix, treat
    # the evaluated surface/archetype as independent unless it IS a wizard.
    ordering = "independent"
    if archetype == "wizard":
        ordering = "mandatory_sequence"

    # index_size — not currently declared in spec; default 0.
    # (A future spec field could capture this.)
    index_size = 0

    return {
        "tasks": tasks,
        "destination_count": len(destinations),
        "max_tree_depth": depth,
        "task_ordering": ordering,
        "index_size": index_size,
    }


def _evaluate_disqualifiers_for(pattern: str, profile: dict, archetype: str,
                                 viewport: str) -> list[dict]:
    """Return list of fired disqualifier dicts for the given pattern."""
    fired = []
    rules = PATTERN_DISQUALIFIERS.get(pattern, [])
    for d_id, pred, citation, heuristic in rules:
        is_fired, detail = pred(profile, archetype, viewport)
        if is_fired:
            fired.append({
                "id": d_id,
                "citation": citation,
                "heuristic_untested": heuristic,
                "detail": detail or "",
            })
    return fired


def _surviving_patterns(profile: dict, archetype: str, viewport: str) -> list[str]:
    """Return the list of catalog patterns whose disqualifiers do NOT fire
    (ignoring HEURISTIC disqualifiers)."""
    survivors = []
    for pat in PATTERN_DISQUALIFIERS:
        fired = [
            d for d in _evaluate_disqualifiers_for(pat, profile, archetype, viewport)
            if not d["heuristic_untested"]
        ]
        if not fired:
            survivors.append(pat)
    return survivors


def _override_satisfies_disqualifier(decision: dict, fired_d: dict,
                                      profile: dict) -> tuple[bool, str]:
    """Check whether the author's defense plus reviewer approvals form a
    sufficient override for a specific fired disqualifier.

    Returns (accepted, reason_or_failure_message).
    """
    d_id = fired_d["id"]

    # Gate 1: defense must cite specific input values that would make the
    # disqualifier not fire. For D1, the override must claim at least one
    # top-3 task's return_locus is actually hub (with structural evidence).
    cited_values = decision.get("override_cited_values", [])
    if d_id == "D1":
        hub_claims = [v for v in cited_values if v.get("claimed_locus") == "hub"]
        if not hub_claims:
            return False, "defense does not cite any return_locus=hub reclassification for D1"
    elif d_id in {"D2", "D3", "D4"}:
        # Overrides for destination-count thresholds must cite a specific recount
        if "destination_count" not in (decision.get("defense", "") or ""):
            return False, f"defense does not cite a destination_count recount for {d_id}"
    else:
        # For others, require ANY value citation
        if not cited_values and "return_locus" not in (decision.get("defense", "") or ""):
            return False, f"defense does not cite specific input values for {d_id}"

    # Gate 2: ≥2 of 3 reviewers must explicitly approve the override and cite
    # the disqualifier ID.
    approvals = decision.get("reviewer_approvals", [])
    matching = [a for a in approvals if d_id in a.get("cited_disqualifiers", [])]
    if len(matching) < 2:
        return False, (
            f"only {len(matching)} of 3 reviewers approved override of {d_id}; "
            f"need ≥2 with cited disqualifier ID"
        )

    return True, f"override accepted: defense cites values + {len(matching)} reviewer approvals for {d_id}"


def _override_satisfied_by_provisional(ctx: SpecContext, fired_d: dict,
                                        pattern: str) -> tuple[bool, str]:
    """Check if any filed provisional-override artifact covers this fire."""
    import datetime
    today = datetime.date.today()
    for override in ctx.provisional_overrides:
        if override.get("disqualifier_overridden") != fired_d["id"]:
            continue
        if override.get("declared_pattern") != pattern.lower():
            continue
        dv = override.get("deferred_validation", {})
        date_str = dv.get("date", "")
        try:
            date = datetime.date.fromisoformat(date_str)
        except ValueError:
            return False, f"provisional-override at {override['path']} has invalid date"
        if date < today:
            return False, f"provisional-override at {override['path']} expired ({date})"
        if not dv.get("event"):
            return False, f"provisional-override at {override['path']} lacks deferred_validation.event"
        return True, f"covered by {override['path']} (valid until {date})"
    return False, "no provisional-override artifact covers this disqualifier"


def check_authoring_direction(ctx: SpecContext) -> list[Violation]:
    """R26: Sections 1–4 must not cite src/components/** or *.astro paths.

    This is the trivial-case catch for authoring-direction inversion. The
    sophisticated-case catch is the IA Architect reviewer's rubric item.
    """
    violations: list[Violation] = []
    text = ctx.section_1_to_4_text
    if not text:
        return violations  # nothing parsed; quietly skip

    # Find any citation-shaped reference to a component file in §1–4.
    # Patterns are ORed; de-dupe by (citation, section) to avoid redundant fires
    # when a single citation matches multiple patterns.
    forbidden_patterns = [
        (r'`(src/components/[^`\s]+)`', "src/components/ path"),
        (r'`([^`\s]+\.astro)`', "*.astro file path"),
        (r'\bsrc/components/[\w/.-]+\b', "src/components/ path"),
    ]
    seen = set()
    for pattern, kind in forbidden_patterns:
        for m in re.finditer(pattern, text):
            citation = m.group(1) if m.lastindex else m.group(0)
            citation = citation.strip("` ")
            # Find which section the hit is in
            hit_pos = m.start()
            section_match = None
            for sec in re.finditer(r'^##\s+([1-4])\.\s', text, re.MULTILINE):
                if sec.start() < hit_pos:
                    section_match = sec.group(1)
            sec_label = f"§{section_match}" if section_match else "§1–4"
            key = (citation, sec_label)
            if key in seen:
                continue
            seen.add(key)
            violations.append(Violation(
                rule="R26",
                selector=f"{sec_label} cites `{citation}`",
                severity="structural",
                message=(
                    f"Authoring-direction violation: Sections 1–4 cite shipped "
                    f"code ({kind}: `{citation}`). Task model and pattern "
                    f"selection must be authorable before shipped code existed."
                ),
                fix=(
                    f"Remove the `{citation}` reference from Sections 1–4. If "
                    f"the claim relies on shipped code, source it from a "
                    f"contract, ticket, interview, or analytics event instead. "
                    f"Chrome contracts (§6) may cite shipped components freely."
                ),
            ))
    return violations


def check_pattern_disqualifiers(ctx: SpecContext, surface: str | None = None,
                                 archetype: str | None = None,
                                 viewport: str | None = None) -> list[Violation]:
    """R25: Pattern fitness check. Runs citation-anchored disqualifiers against
    the declared pattern per {surface × archetype}. Returns violations for
    each fired disqualifier without a valid override.

    If surface/archetype are provided, check only that combo. Otherwise iterate
    all decisions in the spec.
    """
    violations: list[Violation] = []

    # v2 specs have no pattern_decisions; emit a soft skip warning to stderr
    if ctx.spec_version < 3:
        if ctx.spec_version >= 2:
            print(
                "R25 skipped — spec is v2. Run `/nav-spec --revise --migrate-to-v3` "
                "to enable Pattern Fitness checks.",
                file=sys.stderr,
            )
        return violations

    if not ctx.pattern_decisions:
        print(
            "R25 skipped — no Section 4 decision entries parsed from spec.",
            file=sys.stderr,
        )
        return violations

    # Select which decisions to evaluate
    targets = []
    for (sc, at), decision in ctx.pattern_decisions.items():
        if surface and sc != surface:
            continue
        if archetype and at != archetype:
            continue
        targets.append((sc, at, decision))

    for sc, at, decision in targets:
        pat = decision.get("chosen", "")
        if not pat:
            continue
        if pat not in PATTERN_DISQUALIFIERS:
            # Unknown pattern — not our concern (catalog may have composite
            # patterns we don't model); skip silently.
            continue

        profile = _build_task_profile(ctx, sc, at, None)
        viewport_local = viewport or "mobile"  # default when unspecified
        fired = _evaluate_disqualifiers_for(pat, profile, at, viewport_local)
        # Structural disqualifiers (not HEURISTIC) drive structural R25
        structural_fires = [f for f in fired if not f["heuristic_untested"]]

        if not structural_fires:
            continue

        survivors = _surviving_patterns(profile, at, viewport_local)

        for fired_d in structural_fires:
            # Try override paths
            accepted_by_defense, defense_msg = _override_satisfies_disqualifier(
                decision, fired_d, profile,
            )
            if accepted_by_defense:
                continue

            if ctx.evidence_mode == "provisional":
                accepted_by_override, ov_msg = _override_satisfied_by_provisional(
                    ctx, fired_d, pat,
                )
                if accepted_by_override:
                    continue
            else:
                ov_msg = ""

            # No override accepted → fire R25
            survivors_str = ", ".join(survivors) if survivors else "(none)"
            msg = (
                f"Pattern `{pat}` disqualified on {sc}/{at}: {fired_d['id']} "
                f"fired — {fired_d['detail']} (anchor: {fired_d['citation']}). "
                f"Surviving patterns: {survivors_str}. "
                f"Defense gate: {defense_msg}."
            )
            if ctx.evidence_mode == "provisional" and ov_msg:
                msg += f" Provisional override: {ov_msg}."

            # Emit remediation checklist once per fire to stderr
            print(
                f"\n--- R25 remediation checklist for {sc}/{at} ({fired_d['id']}) ---\n"
                f"1. Surfaces to change: enumerate routes whose chrome would change if "
                f"pattern switches from `{pat}` to {survivors[0] if survivors else '(surviving pattern)'}.\n"
                f"2. Chrome diff scope: what DOM + Tailwind changes per surface.\n"
                f"3. URL migration: what routes stay, what redirects are needed.\n"
                f"4. Validation metric: analytics event or measurement to confirm the switch works.\n"
                f"---",
                file=sys.stderr,
            )

            violations.append(Violation(
                rule="R25",
                selector=f"pattern={pat} on {sc}/{at}",
                severity="structural",
                message=msg,
                fix=(
                    f"Choose one: (a) revise §4 defense to cite specific input "
                    f"values overriding {fired_d['id']} AND obtain ≥2/3 reviewer "
                    f"approvals citing {fired_d['id']}, (b) switch declared "
                    f"pattern to a surviving pattern ({survivors_str}), or "
                    f"(c) in evidence-mode: provisional, file "
                    f".design/provisional-override-<date>.md per the schema in "
                    f"references/task-model-template.md."
                ),
            ))
    return violations


# ============================================================================
# Entry point
# ============================================================================

def check(html: str, surface: str, archetype: str, viewport: str,
          task: str | None = None, pattern: str | None = None,
          spec_path: Path | None = None, current_route: str | None = None
          ) -> list[Violation]:
    violations = check_chrome(html, surface, archetype, viewport)

    if spec_path and spec_path.exists():
        ctx = parse_spec(spec_path)
        if ctx.spec_version >= 2:
            violations.extend(check_ia(html, surface, archetype, viewport,
                                       task, pattern, ctx, current_route))
        if ctx.spec_version >= 3:
            # R25 runs per {surface, archetype}; limit to the invocation's
            # surface/archetype to avoid cross-surface false positives.
            violations.extend(check_pattern_disqualifiers(
                ctx, surface=surface, archetype=archetype, viewport=viewport,
            ))
            # R26 runs once per spec, not per file
            violations.extend(check_authoring_direction(ctx))

    return violations


def main() -> int:
    ap = argparse.ArgumentParser(
        description="nav-spec v3 validator (R1–R26)"
    )
    # --file is required for chrome/IA validation; NOT required when
    # --check-pattern-fitness is used (spec-only mode).
    ap.add_argument("--file", default=None,
                    help="HTML file to validate (required unless --check-pattern-fitness)")
    ap.add_argument("--surface",
                    choices=["public", "auth-gate", "token-auth",
                             "session-auth-client", "session-auth-admin"],
                    help="Surface class (required unless --check-pattern-fitness)")
    ap.add_argument("--archetype",
                    choices=["dashboard", "list", "detail", "form", "wizard",
                             "empty", "error", "modal", "drawer", "transient"],
                    help="Archetype (required unless --check-pattern-fitness)")
    ap.add_argument("--viewport", choices=["mobile", "desktop"],
                    help="Viewport (required unless --check-pattern-fitness)")
    ap.add_argument("--task", default=None,
                    help="Task name from venture task model (optional; required by R17 for some patterns)")
    ap.add_argument("--pattern", default=None,
                    help="Pattern name from pattern-catalog.md (optional; required by R17)")
    ap.add_argument("--spec", default=None,
                    help="Path to NAVIGATION.md (required by R16, R20, R21, R23, R24, R25, R26)")
    ap.add_argument("--route", default=None,
                    help="Current route (optional; used by R18 terminal check and R23)")
    ap.add_argument("--check-pattern-fitness", action="store_true",
                    dest="check_pattern_fitness",
                    help="Run R25 + R26 against the spec only (no HTML required). "
                         "Iterates all Section 4 pattern decisions in the spec.")
    args = ap.parse_args()

    # Spec-only mode: R25 (per decision) + R26 (authoring direction lint)
    if args.check_pattern_fitness:
        if not args.spec:
            print(json.dumps({"error": "--check-pattern-fitness requires --spec"}), file=sys.stderr)
            return 1
        spec_path = Path(args.spec)
        if not spec_path.exists():
            print(json.dumps({"error": f"Spec not found: {args.spec}"}), file=sys.stderr)
            return 1
        ctx = parse_spec(spec_path)
        violations: list[Violation] = []
        violations.extend(check_pattern_disqualifiers(
            ctx, surface=args.surface, archetype=args.archetype, viewport=args.viewport,
        ))
        violations.extend(check_authoring_direction(ctx))
        report = {
            "mode": "pattern-fitness",
            "spec": args.spec,
            "spec_version": ctx.spec_version,
            "evidence_mode": ctx.evidence_mode,
            "nav_spec_skill_version": "3.0.0",
            "pass": len(violations) == 0,
            "violation_count": len(violations),
            "structural_count": sum(1 for v in violations if v.severity == "structural"),
            "semantic_count": sum(1 for v in violations if v.severity == "semantic"),
            "violations": [
                {"rule": v.rule, "selector": v.selector, "severity": v.severity,
                 "message": v.message, "fix": v.fix}
                for v in violations
            ],
        }
        print(json.dumps(report, indent=2))
        return 0 if report["pass"] else 1

    # Standard per-file validation mode
    missing = [n for n in ("file", "surface", "archetype", "viewport")
               if getattr(args, n) in (None, "")]
    if missing:
        print(json.dumps({"error": f"Missing required args: {', '.join('--' + m for m in missing)}"}),
              file=sys.stderr)
        return 1

    try:
        with open(args.file, "r", encoding="utf-8") as f:
            html = f.read()
    except FileNotFoundError:
        print(json.dumps({"error": f"File not found: {args.file}"}), file=sys.stderr)
        return 1
    except OSError as e:
        print(json.dumps({"error": f"Cannot read {args.file}: {e}"}), file=sys.stderr)
        return 1

    spec_path = Path(args.spec) if args.spec else None
    violations = check(html, args.surface, args.archetype, args.viewport,
                       args.task, args.pattern, spec_path, args.route)

    report = {
        "file": args.file,
        "surface": args.surface,
        "archetype": args.archetype,
        "viewport": args.viewport,
        "task": args.task,
        "pattern": args.pattern,
        "spec": args.spec,
        "nav_spec_skill_version": "3.0.0",
        "pass": len(violations) == 0,
        "violation_count": len(violations),
        "structural_count": sum(1 for v in violations if v.severity == "structural"),
        "semantic_count": sum(1 for v in violations if v.severity == "semantic"),
        "cosmetic_count": sum(1 for v in violations if v.severity == "cosmetic"),
        "violations": [
            {"rule": v.rule, "selector": v.selector, "severity": v.severity,
             "message": v.message, "fix": v.fix}
            for v in violations
        ],
    }
    print(json.dumps(report, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
