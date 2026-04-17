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

### 3c. Token normalization (when UI-PATTERNS.md present)

After nav validation, run the token-normalize pass. Deterministic codemod that rewrites class attributes to use the project's named typography and spacing tokens instead of Stitch's raw-Tailwind + Material-3 vocabulary. Covers the adoption gap the UI CONTRACT cannot fully close.

```bash
python3 .agents/skills/ui-drift-audit/normalize.py <path-to-generated-html>
```

Mappings cover arbitrary typography (`text-[Npx]` → scale tokens), raw Tailwind sizes (`text-sm/lg/xl` → scale), spacing (`p-4/6/8`, `gap-3/4/6/8` → rhythm tokens), and Material 3 color idioms (`bg-surface-container-lowest`, `text-on-primary`, `bg-primary-container`, …) → our semantic color roles. Icons on `material-symbols-outlined` preserved.

Skip if `docs/style/UI-PATTERNS.md` absent.

### 3c-bis. Portal list-row primitive check (UI-PATTERNS R7)

When the target surface is a portal list index (`src/pages/portal/*/index.astro`), the generated output MUST render iterated rows through `src/components/portal/PortalListItem.astro` — not fresh `<a class="block bg-white ...">` markup. Helpers (`formatDate`, `formatCurrency`, `statusColorMap`, `statusLabelMap`, `typeLabels`) MUST come from `src/lib/portal/formatters.ts` and `src/lib/portal/status.ts`. The presence + no-local-redef assertions in `tests/forbidden-strings.test.ts` fail CI on any regeneration that re-rolls inline markup or local formatters. See `docs/style/UI-PATTERNS.md` R7 for the full contract.

### 3d. Hallucination strip pass

After normalize, strip elements Stitch produced despite the UI CONTRACT forbidding them:

```bash
python3 .agents/skills/ui-drift-audit/strip.py <path-to-generated-html>
```

Removes hero imagery, decorative `<figure>` blocks, `<footer>` copyright rows, testimonial blockquotes, marketing CTAs ("Schedule a call", "Book a demo"), announcement banners. Mechanical.

### 3e. Embellishment evaluation (when source exists)

After strip, flag Stitch-generated elements that look like genuine product features Stitch invented (aggregate stat cards, progress widgets, auto-pay banners, filter bars, support widgets). These are NOT hallucinations — they're product suggestions. Humans decide ship / defer / reject.

```bash
python3 .agents/skills/ui-drift-audit/evaluate-embellishments.py \
  --stitch-dir .stitch/designs/<run-dir> \
  --source-dir <source path, e.g. src/pages/portal>
```

Writes `EMBELLISHMENTS.md` next to the generated HTML. Each candidate has its category, matched phrase, and a code snippet. Review before implementing.

### 3f. Viewport default — BOTH viewports

Generate BOTH mobile AND desktop for every surface by default. Mobile (`deviceType: "MOBILE"`, 390×844) establishes primary design. Desktop (`deviceType: "DESKTOP"`, ~1440×900) is an expansion with explicit right-rail or two-column guidance in the prompt. "Desktop later" is not an option — single-viewport designs drift when the other viewport lands.

Fire both generations in parallel.

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
