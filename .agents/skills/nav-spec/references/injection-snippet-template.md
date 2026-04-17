# Injection snippet template

Canonical NAV CONTRACT block injected between DESIGN SYSTEM and PAGE STRUCTURE in every `stitch-design` prompt. v2 splits the block into **essential** (always injected) and **extended** (loaded only when archetype/pattern requires).

Total token budget: ≤800 tokens combined when both essential and extended are loaded; ≤500 when essential only.

---

## Essential block (always injected)

This block is required for every Stitch generation. Replaces the v1 single-block format.

```
NAV CONTRACT — ESSENTIAL (REQUIRED — do not invent beyond this block):

# Classification
Surface class: <surface-class> — <one-line description>.
Archetype: <archetype>.
Viewport: <viewport, e.g., "mobile 390x844">.
Task: <task name from venture task model>.
Pattern: <pattern name from pattern-catalog.md, e.g., "hub-and-spoke">.

# IA contract (REQUIRED links)
This surface MUST link to the following destinations (per reachability matrix):
<list of {To, Mechanism} rows from matrix where From = this surface and Required ∈ {Yes, Conditional}>

# Chrome allowed (inclusive)
<Chrome-allowed list for {surface, archetype, viewport}, from spec appendix>

# Chrome FORBIDDEN
<Chrome-forbidden list from anti-patterns, filtered to surface class>

# State colors (exact hex)
- Active: <hex> | Default: <hex> | Hover: <hex>
- Focus ring: 2px <hex> at 2px offset, keyboard-focus only
- Disabled: <hex> | Tap target minimum: 44x44px

# Transition
<Back target URL (canonical, hardcoded, not # or history.back) — for detail/list/form/wizard/empty/error>
<Modal close rules — for modal/drawer>

# A11y floor
- <header role="banner">, <main role="main" id="main">
- Skip-to-main <a> sr-only-until-focused, first body child, href="#main"
- Single <h1>; <h2> for major sections; no level skipping
- aria-label on every icon-only button
- Focus rings keyboard-only

# Semantic precision (Phase 0 drift targets)
- Header: `sticky top-0`, NOT `fixed top-0`.
- Header bg: solid `bg-white`. No `backdrop-blur-*`, no opacity modifiers.
- Client name stands alone in header — no preceding icon/emoji/SVG.
- Back: single <a> or <button>, never wrapped in <nav>, never aria-label="Breadcrumb" for a single link.
- Back href: hardcoded canonical URL.

If any element below in PAGE STRUCTURE conflicts with this block, THIS BLOCK WINS.
```

---

## Extended block (loaded conditionally)

The extended block is added when:

- The archetype requires it (dashboard always loads "IA — sibling list completeness")
- The pattern requires it (sequential always loads "Sequential — step ordering")
- The state machine has non-default behaviors (any surface with declared task states)
- The taxonomy has terms relevant to the prompt (any surface that renders status badges)

Format:

```
NAV CONTRACT — EXTENDED (REQUIRED for this archetype/pattern):

# Pattern requirements: <pattern name>
<Required elements list, copied from pattern-catalog.md>

# State machine
<Auth state behavior>
<Data state behavior for this surface>
<Task state behavior if relevant>

# Content taxonomy (relevant labels)
<Object labels: from taxonomy>
<Action verbs: from taxonomy>
<Status labels with required casing/colors>
<Empty-state copy if applicable>

# Cross-surface context
<If this surface participates in persistent-context pattern: declare context indicator and where it renders>
```

---

## Substitution at prompt-enhancement time

`stitch-design`'s pipeline does these lookups:

1. Read classification tags from prompt: `surface=`, `archetype=`, `viewport=`, `task=`, `pattern=`.
2. Open `.stitch/NAVIGATION.md`.
3. Build essential block:
   - Surface description: from Section 3 (surface-class taxonomy).
   - IA contract: filter Section 3 (reachability matrix) by `From = current surface`; render rows where `Required = Yes` or `Conditional`.
   - Chrome allowed: Section 6 chrome contracts, filtered by archetype, overridden by surface appendix.
   - Chrome forbidden: Section 9 anti-patterns, filtered by surface class.
   - State colors: Section 7 state conventions.
   - Transition: Section 8, filtered by archetype.
   - A11y floor: Section 10.
4. Build extended block IF:
   - Archetype is `dashboard` → always load IA — sibling list completeness from matrix.
   - Pattern is in `{sequential, master-detail, faceted, persistent-context}` → load pattern requirements from pattern-catalog.md.
   - Surface has declared task states → load state machine entry from Section 5.
   - Surface renders status badges → load taxonomy from Section 11.
5. Concatenate essential + extended (with separator) and inject between DESIGN SYSTEM and PAGE STRUCTURE.

Keep substitution simple: string concatenation with placeholders, no templating engine.

---

## Size budget tracking

For each `{surface, archetype, viewport, pattern}` combo, measure the assembled block size and record it in NAVIGATION.md front matter:

```yaml
injection-budgets:
  session-auth-client/dashboard/mobile/hub-and-spoke: { essential: 482, extended: 287, total: 769 }
  session-auth-client/list/mobile/master-detail: { essential: 421, extended: 156, total: 577 }
  ...
```

If any combo exceeds 800 tokens, shorten the surface-class appendix rather than the semantic-precision section. The latter is load-bearing per Phase 0 measurements.

If essential alone exceeds 500 tokens, that's a sign the surface class has too many forbidden chrome items — refactor the spec.

---

## Versioning and compatibility

- Semantic-precision section is the most volatile (drifts as Stitch evolves). Bump spec-version when it materially changes.
- IA contract section is stable per spec-version (changes only when reachability matrix changes).
- Chrome allowed/forbidden are stable per spec-version.

When the spec moves from v1 to v2:

- v1 specs lacked task and pattern tags. The pipeline still accepts v1 specs and falls back to legacy injection (essential block without IA contract section).
- v2-aware ventures benefit from full injection.

---

## Example — assembled essential block (portal home, mobile)

```
NAV CONTRACT — ESSENTIAL (REQUIRED — do not invent beyond this block):

# Classification
Surface class: session-auth-client — Authenticated client portal user. Cookie session. Hosted on portal.smd.services.
Archetype: dashboard.
Viewport: mobile 390x844.
Task: see-whats-happening.
Pattern: hub-and-spoke (with dominant-action and recent-activity variants).

# IA contract (REQUIRED links)
This surface MUST link to:
- /portal/quotes (Section card, Required)
- /portal/invoices (Section card, Required)
- /portal/documents (Section card, Required)
- /portal/engagement (Section card, Required)
- /portal/invoices/[id] (ActionCard, Conditional: when has pending invoice)
- mailto:<consultant_email> (Contact icon, Conditional: when consultant assigned)
- sms:<consultant_phone> (Contact icon, Conditional)
- tel:<consultant_phone> (Contact icon, Conditional)

# Chrome allowed
- Top band: sticky, bg-white, h-14 (mobile), border-b border-[color:var(--color-border)]
  - Left: client name only (Inter 500, 13/18, #475569)
  - Right: optional contact icons (mail/sms/tel) and Sign out icon
- Section card grid: 1 column on mobile, 2 columns on desktop, between hero and Recent Activity
- Recent Activity timeline: ≤5 entries, time-ordered, with artifact links
- Right rail (desktop only): ActionCard or status card + ConsultantBlock
- ConsultantBlock: solid initials circle (no real face), name, role, contact icons

# Chrome FORBIDDEN
- Logo or brand mark in header
- Nav tabs or hamburger menu in header
- Breadcrumbs anywhere
- Bottom-tab nav
- Footer with copyright/legal
- Marketing CTAs ("Schedule a call", "Get started", "Learn more")
- Real-face photos (Unsplash, googleusercontent/aida, etc.)
- bg-white/85, backdrop-blur-*, fixed top-0

# State colors
- Active: #1E40AF | Default: #475569 | Hover: #0F172A
- Focus ring: 2px #3B82F6 at 2px offset, keyboard-focus only
- Disabled: #94A3B8 | Tap target: 44x44px minimum

# Transition
- Direct arrival; no back affordance on this dashboard.
- Section card click → navigate to list URL.
- ActionCard click → navigate to /portal/invoices/[id].

# A11y floor
- <header role="banner">, <main role="main" id="main">
- Skip-to-main <a> sr-only-until-focused, first body child
- Single <h1>: "Your engagement is in flight." (or state-appropriate)
- <h2>: "Recent activity"
- aria-label on icon-only buttons
- Focus rings keyboard-only

# Semantic precision
- Header sticky top-0, not fixed.
- Header bg solid white.
- Client name stands alone (no leading icon).
- No back button on dashboard.

If PAGE STRUCTURE conflicts with this block, THIS BLOCK WINS.
```

---

## Reference implementation

The 2026-04-15 ss-console run used a ~450-token v1 template for Phase 0. v2 essential is ~480 tokens; extended adds 200–300 tokens depending on surface complexity. Total stays within 800-token budget for all measured combos.
