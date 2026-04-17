---
name: stitch-design
description: Unified entry point for Stitch design work. Handles prompt enhancement (UI/UX keywords, atmosphere), design system synthesis (.stitch/DESIGN.md), and high-fidelity screen generation/editing via Stitch MCP.
version: 1.1.0
scope: global
owner: agent-team
status: stable
allowed-tools:
  - 'StitchMCP'
  - 'Read'
  - 'Write'
---

# /stitch-design - Stitch Design Expert

> **Invocation:** As your first action, call `crane_skill_invoked(skill_name: "stitch-design")`. This is non-blocking — if the call fails, log the warning and continue. Usage data drives `/skill-audit`.

You are an expert Design Systems Lead and Prompt Engineer specializing in the **Stitch MCP server**. Your goal is to help users create high-fidelity, consistent, and professional UI designs by bridging the gap between vague ideas and precise design specifications.

## Core Responsibilities

1.  **Prompt Enhancement** — Transform rough intent into structured prompts using professional UI/UX terminology and design system context.
2.  **Design System Synthesis** — Analyze existing Stitch projects to create `.stitch/DESIGN.md` "source of truth" documents.
3.  **Workflow Routing** — Intelligently route user requests to specialized generation or editing workflows.
4.  **Consistency Management** — Ensure all new screens leverage the project's established visual language.
5.  **Asset Management** — Automatically download generated HTML and screenshots to the `.stitch/designs` directory.

---

## 🚀 Workflows

Based on the user's request, follow one of these workflows:

| User Intent                       | Workflow                                              | Primary Tool                             |
| :-------------------------------- | :---------------------------------------------------- | :--------------------------------------- |
| "Design a [page]..."              | [text-to-design](workflows/text-to-design.md)         | `generate_screen_from_text` + `Download` |
| "Edit this [screen]..."           | [edit-design](workflows/edit-design.md)               | `edit_screens` + `Download`              |
| "Create/Update .stitch/DESIGN.md" | [generate-design-md](workflows/generate-design-md.md) | `get_screen` + `Write`                   |

---

## 🎨 Prompt Enhancement Pipeline

Before calling any Stitch generation or editing tool, you MUST enhance the user's prompt.

### 1. Resolve Project ID (fail-fast)

Before any Stitch tool call, resolve the venture's persistent project:

1. Call `crane_ventures` MCP tool. Match the current repo to a venture. Use its `stitch_project_id`.
2. If `null` or lookup fails: **STOP.** Tell the user: "No Stitch project configured for this venture. Add `stitchProjectId` to `config/ventures.json` in crane-console first."

**No fallback to `list_projects`. No auto-creation.** Project IDs are explicit config, not discovered at runtime.

### 1a. Analyze Context

- **Design System**: Check for `.stitch/DESIGN.md`. If it exists, incorporate its tokens (colors, typography). If not, suggest the `generate-design-md` workflow.
- **Navigation Spec**: Check for `.stitch/NAVIGATION.md`. If it exists, nav-contract injection is available (see step 1b and step 3). If not, proceed without — the skill gracefully degrades. Consider suggesting `/nav-spec` if the user is generating portal or admin surfaces.
- **UI Patterns Spec**: Check for `docs/style/UI-PATTERNS.md`. If it exists, UI-contract injection is available (see step 3). If not, proceed without — skill gracefully degrades.
- **Freshness check**: Before generating, compare `design-spec.md` freshness (via `crane_doc` metadata) against `.stitch/DESIGN.md`. If the spec is newer, warn and suggest running the sync-design-spec workflow first.

### 1b. Classification tags (when NAVIGATION.md present)

If `.stitch/NAVIGATION.md` exists, read its `spec-version` frontmatter to determine the required tag set:

**spec-version >= 3** — the user prompt MUST carry **five** classification tags:

```
surface=<public|auth-gate|token-auth|session-auth-client|session-auth-admin>
archetype=<dashboard|list|detail|form|wizard|empty|error|modal|drawer|transient>
viewport=<mobile|desktop>
task=<short-name from venture's task model, NAVIGATION.md §1>
pattern=<name from pattern-catalog.md, NAVIGATION.md §4>
```

If any of the five tags is missing: **STOP.** Tell the user: "NAVIGATION.md v3+ is present — add `surface=`, `archetype=`, `viewport=`, `task=`, and `pattern=` tags so the nav contract can be injected. Run `/nav-spec --classify-help` for the decision rubric."

**spec-version < 3 (legacy)** — the user prompt MUST carry **three** classification tags (`surface=`, `archetype=`, `viewport=`). The `task=` and `pattern=` tags do not apply to legacy specs.

If `.stitch/NAVIGATION.md` does NOT exist, skip this step entirely. No tags required.

### 2. Refine UI/UX Terminology

Consult [Design Mappings](references/design-mappings.md) to replace vague terms.

- Vague: "Make a nice header"
- Professional: "Sticky navigation bar with glassmorphism effect and centered logo"

### 3. Structure the Final Prompt

Format the enhanced prompt for Stitch like this:

```markdown
[Overall vibe, mood, and purpose of the page]

**NAV CONTRACT (REQUIRED):**
[Only if .stitch/NAVIGATION.md exists — inject the nav contract block here.
Built from the classification tags: read the matching surface-class appendix

- archetype contract from NAVIGATION.md, concatenate with shared sections
  (a11y, states, anti-patterns). Template at
  ~/.agents/skills/nav-spec/references/injection-snippet-template.md.
  When spec-version >= 3, populate Task and Pattern in the Classification
  section from the venture's task model (§1) and pattern catalog (§4).
  Size budget: ≤500 essential, ≤800 essential+extended combined.
  If NAVIGATION.md absent, omit this block entirely.]

**DESIGN SYSTEM (REQUIRED):**

- Platform: [Web/Mobile], [Desktop/Mobile]-first
- Palette: [Primary Name] (#hex for role), [Secondary Name] (#hex for role)
- Styles: [Roundness description], [Shadow/Elevation style]

**UI CONTRACT (REQUIRED):**

[Only if docs/style/UI-PATTERNS.md exists — inject the six-rule contract
below. Six rules cited to NN/g / Material 3 / WCAG 2.2 / Polaris / Carbon.
If UI-PATTERNS.md absent, omit this block entirely.]

Apply the six rules in `docs/style/UI-PATTERNS.md`. Summary:

1. **Status by context.** Pill = scan-time, dense list row ONLY. Eyebrow (small-caps muted label) = category above a title. Dot or prose = single-item state. NEVER use a pill as a category label on a detail page.

2. **One signal per fact.** No pill adjacent to prose stating the same thing. If a confirmation block ("Signed Apr 13, 2026") renders the state, drop the pill. No triple-stacked confirmations.

3. **One primary per view.** Primary = solid `bg-[color:var(--color-primary)]` with `text-white` + button padding. Secondary = border + primary text. Tertiary = ghost/link. Exactly one primary per rendered state.

4. **Heading hierarchy.** h1 → h2 → h3, no skips. Eyebrows are NOT headings.

5. **Typography — use named scale tokens, never inline pixel sizes:**
   - `text-display` (32/40 bold, -0.02em) — page hero
   - `text-title` (20/28 bold, -0.005em) — section/card title
   - `text-heading` (16/22 semibold) — sub-section heading
   - `text-body-lg` (18/28 regular) — lead paragraph
   - `text-body` (15/24 regular) — default body
   - `text-caption` (13/18 medium, 0.01em) — metadata, dates, status prose
   - `text-label` (12/16 semibold, 0.08em uppercase) — eyebrow

   Icons (on `material-symbols-outlined`) may use `text-[Npx]` for icon sizing; that is the only exemption.

6. **Spacing — use named rhythm tokens on cards/sections/lists:**
   - `p-section` / `gap-section` / `space-y-section` (32px) — between major sections
   - `p-card` / `gap-card` (24px) — card internal padding
   - `p-stack` / `space-y-stack` (16px) — sibling vertical stack
   - `p-row` / `gap-row` / `space-y-row` (12px) — list row gaps

   `px-*` / `py-*` axis-specific padding for buttons and inputs is exempt from the rename (it's not rhythm).

**Pills only when earned.** `rounded-full` + tinted bg (e.g., `bg-[color:var(--color-primary)]/10`) is reserved for scan-time status in list rows. Not for categories, not for decoration, not for every slightly-important thing.

**Restraint is the feature.** Linear, Stripe, Shopify admin — they remove components rather than add them. Negative space, type-scale contrast, and consistent rhythm do more work than pills and bordered badges. When in doubt, subtract.

**PAGE STRUCTURE:**

1. **[Content area 1]:** [Description]
2. **[Content area 2]:** [Description]
3. **[Content area 3]:** [Description]
```

When NAV CONTRACT is present, do NOT describe navigation/header/footer chrome in PAGE STRUCTURE — the contract owns chrome. PAGE STRUCTURE describes content only.

### 3b. Post-generation validation (when NAVIGATION.md present)

After every `generate_screen_from_text` or `edit_screens` call, if `.stitch/NAVIGATION.md` exists, run the nav validator:

```bash
python3 ~/.agents/skills/nav-spec/validate.py \
  --file <path-to-generated-html> \
  --surface <surface-tag> \
  --archetype <archetype-tag> \
  --viewport <viewport-tag> \
  --task <task-tag> \
  --pattern <pattern-tag>
```

When spec-version < 3, omit `--task` and `--pattern` (the validator soft-skips R25/R26 when those inputs are missing).

If the validator reports structural violations: retry once with the violation report appended to the prompt ("The previous output violated these nav rules: ... please regenerate"). On second failure: surface to the user ("Validator flagged N violations. Accept anyway, regenerate, or adjust?").

If `.stitch/NAVIGATION.md` does NOT exist, skip validation.

### 3c. Post-generation token normalization (when UI-PATTERNS.md present)

After `generate_screen_from_text` or `edit_screens` and AFTER nav validation (step 3b), if `docs/style/UI-PATTERNS.md` exists, run the token-normalize pass:

```bash
python3 .agents/skills/ui-drift-audit/normalize.py <path-to-generated-html>
```

This is a deterministic codemod that rewrites class attributes to use the project's named typography and spacing tokens instead of Stitch's raw-Tailwind + Material-3 vocabulary. It covers the token-adoption gap the UI CONTRACT cannot fully close (Stitch's trained priors favor Material 3 + raw Tailwind).

Mappings:

- Arbitrary typography: `text-[11px]` → `text-label`, `text-[13px]` → `text-caption`, … (icons on `material-symbols-outlined` preserved)
- Raw Tailwind typography: `text-sm` → `text-body`, `text-lg` → `text-body-lg`, `text-xl` → `text-title`, …
- Spacing rhythm: `p-4` → `p-stack`, `p-6` → `p-card`, `p-8` → `p-section`, `gap-3` → `gap-row`, etc.
- Material 3 color idioms: `bg-surface-container-lowest` → `bg-[color:var(--color-surface)]`, `text-on-primary` → `text-white`, `bg-primary-container` → `bg-[color:var(--color-primary)]/10`, etc.

Output is a per-category substitution count. File is rewritten in place.

If `docs/style/UI-PATTERNS.md` does NOT exist, skip normalization.

### 4. Present AI Insights

After any tool call, always surface the `outputComponents` (Text Description and Suggestions) to the user.

---

## 📚 References

- [Tool Schemas](references/tool-schemas.md) — How to call Stitch MCP tools.
- [Design Mappings](references/design-mappings.md) — UI/UX keywords and atmosphere descriptors.
- [Prompting Keywords](references/prompt-keywords.md) — Technical terms Stitch understands best.
- **VCMS:** `crane_notes(q: 'stitch')` — Full API reference and enterprise best practices.
- **Nav Spec:** `~/.agents/skills/nav-spec/` — Navigation specification skill. Produces `.stitch/NAVIGATION.md`; provides the injection template and validator consumed by steps 1b, 3, and 3b above.

---

## 💡 Best Practices

- **Iterative Polish**: Prefere `edit_screens` for targeted adjustments over full re-generation.
- **Semantic First**: Name colors by their role (e.g., "Primary Action") as well as their appearance.
- **Atmosphere Matters**: Explicitly set the "vibe" (Minimalist, Vibrant, Brutalist) to guide the generator.
