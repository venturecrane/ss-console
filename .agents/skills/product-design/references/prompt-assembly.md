# Prompt assembly

How to build the prompt that gets passed to Claude Code for a single component generation. Order matters — the model weights later instructions more heavily when they conflict with earlier ones.

## Block order (top to bottom)

### 1. Intent + target

Single line:

```
Generate the <ComponentName> component for the <surface> surface in <venture-name>.
Target file: src/components/<area>/<ComponentName>.astro
```

### 2. Classification

```
Classification:
  surface:   <surface-tag>
  archetype: <archetype-tag>
  viewport:  <viewport-tag>
  task:      <task-tag>
  pattern:   <pattern-tag>
```

These are read from NAVIGATION.md §1 (task model) and §4 (pattern catalog).

### 3. Adapter template

Inline the full content of `adapters/<adapter-name>.md`. This includes file conventions, frontmatter rules, markup rules, styling rules, and venture-specific notes.

Header: `## Adapter rules`

### 4. Nav contract block

Build using the nav-spec injection template: `~/.agents/skills/nav-spec/references/injection-snippet-template.md`.

Read NAVIGATION.md's surface-class appendix for the current surface class. Extract the archetype contract. Concatenate with shared sections (a11y, states, anti-patterns). For spec-version ≥ 3, include the Classification section populated from the venture's task model and pattern catalog.

Size budget: ≤ 500 chars essential, ≤ 800 chars with extended sections.

Header: `## Nav contract (REQUIRED)`

### 5. Design system tokens

Priority order for source:

1. `.design/DESIGN.md` if it exists
2. The `@theme` block extracted from `src/styles/global.css` (Tailwind v4 ventures)
3. `tailwind.config.*` (Tailwind v3 ventures; not currently used by any active venture)

Extract colors, typography scale, spacing scale, roundness scale. Present as a table or structured list. Include semantic roles when declared (e.g., "primary = #1E40AF (action, links)" vs bare "#1E40AF").

Header: `## Design system (REQUIRED)`

### 6. UX brief excerpt

Read `.design/<target>-ux-brief.md` and extract the section covering this surface. Typical ss-console brief covers persona, visit modes, structural concepts, success metrics, and design tokens. Include what's relevant to this surface — don't paste the entire brief.

Header: `## UX brief (for this surface)`

### 7. Existing component source

**Pass raw file contents of every `.astro` and `.tsx` under `src/components/**` in the venture repo.\*\* No registry, no summarization, no prop-signature extraction — Claude reads TypeScript fine and the component source IS the contract.

Walk `src/components/` with Glob. For each file:

```
--- <relative-path> ---
<file contents>
```

This is the biggest block. For ss-console with ~24 components it's a few thousand tokens. Accept the cost; it's what makes reuse work without a registry.

Header: `## Existing components (reuse these, do not reinvent)`

### 8. Prior version (only for `--revise`)

If revising an existing file, include its current contents:

```
## Current version of the file
<file contents>

## Revision request
<Captain's request>
```

### 9. Instructions

Close with explicit do/don't:

```
## Your task

1. Produce the component file at src/components/<area>/<ComponentName>.astro
2. Also produce the preview route src/pages/design-preview/<surface-name>.astro and
   fixture src/pages/design-preview/<surface-name>.fixture.json (if they don't exist)
3. Import existing components from src/components/** where they fit. Do not reinvent
   chrome that already exists (PortalHeader, PortalTabs, etc.)
4. Tailwind classes only — no inline styles, no arbitrary hex values
5. Props are the only data input. This component does not fetch.
6. Match the nav contract above exactly — no footer on authenticated surfaces, no
   stock photos, etc.

Use the Write tool to produce the files. Report path(s) written. No explanation needed
unless something is ambiguous — in that case, ask one question and wait.
```

## Optional: Visual exemplars from `.design/designs/`

If a venture has `.design/designs/` with historical HTML output (e.g., ss-console still carries 60+ baselines from its pre-retirement design tool), you MAY include 1-3 relevant files as visual exemplars. Relevance is chosen by matching the filename to the surface tags (`portal-quotes-v2-spec-test.html` is relevant for `portal-quotes-*` surfaces).

**Gate 0 test:** for the first batch run, generate one surface WITHOUT exemplars (pass `--no-exemplars`). Compare quality with the others. If comparable → leave them out. If visibly worse → keep them with a sunset date (remove when DESIGN.md + NAVIGATION.md + brief are rich enough to stand alone).

Default behavior: include relevant exemplars if they exist; log that they were included and cite the filename.

## What NOT to include

- Don't include `CLAUDE.md` — noise
- Don't include `package.json` — the adapter knows the stack
- Don't include `.astro`/`.tsx` files from `src/pages/` AS REUSE TARGETS — pages are hand-wired and your component is a body, not a page. **Exception:** for multi-state detail components, DO include the shipped page that renders the same surface as **behavioral reference** in an explicit block after the component source, framed as: "This is the EXISTING page rendering the same surface. Reproduce its state-variation rendering logic, but take all data as props — no Astro.locals, no fetch, no D1." Single-state archetypes (list, empty, error) don't need this.
- Don't include `tests/` — not useful for this generation
- Don't include the whole NAVIGATION.md — just the surface-class appendix plus the nav contract block
- Don't include the whole UX brief — just the section covering this surface
- Don't summarize anything Claude can read directly. If it's a file, pass the file.
