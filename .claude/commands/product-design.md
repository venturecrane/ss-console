> **Invocation:** As your first action, call `crane_skill_invoked(skill_name: "product-design")`. Non-blocking — if the call fails, log and continue.

> **Design system context.** Before any component generation — including pre-flight, prompt assembly, and the iteration loop — load the cross-venture pattern + component catalog. Generated components should reference the catalog's components map for analogous implementations and respect Patterns 1–8 (status display by context, button hierarchy, shared primitives, actions and menus, etc.):
>
> - `crane_doc('global', 'design-system/patterns/index.md')`
> - `crane_doc('global', 'design-system/components/index.md')`
>
> Then continue with the existing pre-flight: venture identification, NAVIGATION.md / DESIGN.md / UX-brief checks, adapter resolution. The catalog is supplementary context — the venture's own DESIGN.md / @theme remains the source for tokens.

# /product-design — Product UI realization

You produce Astro/React components in a venture's own repo. You consume the harness inputs (nav-spec + DESIGN.md + UX brief + existing component source) and emit components that satisfy them — validated by the venture's build command and `validate.py`, reviewed by the Captain.

**Package manager detection.** At build-check time, detect the venture's package manager by lockfile, in this order: `pnpm-lock.yaml` → `pnpm build`, `yarn.lock` → `yarn build`, `package-lock.json` (or absent) → `npm run build`. Preview commands follow the same mapping (`npm run dev` etc.). Don't hardcode.

You are not a design tool. External design tools are LLM wrappers built for customers without our harness. We have nav-spec v3.1 with citation-anchored disqualifiers, a 26-rule structural validator, a four-agent design-brief methodology, and Claude Code writing production code daily. This skill treats screen realization as a first-class capability of that stack.

## What you produce

**Components, not pages.** You write component files (e.g., `src/components/portal/QuoteDetail.astro`). Pages stay hand-wired with their data fetching — they import your components and pass data. This keeps auth/data layers out of scope and makes components render from props alone.

For greenfield ventures with no pages yet, you may also produce a page wiring example. In mature ventures (the common case), stick to components.

## Arguments

```
/product-design [--surface <name>] [--set <surface-set>] [--revise <path>]
```

- `--surface <name>` — generate a single surface (e.g., `portal-quotes-detail`). Surface names must match the classification in the venture's NAVIGATION.md.
- `--set <surface-set>` — generate or revise a batch (e.g., `portal` covers every client-portal surface declared in NAVIGATION.md §1 task model). **Revise-aware:** for each surface, if a component file already exists at the target path, the skill loads it as prior-version context before generating (same as `--revise` for a single file). This makes `--set` the right tool for both greenfield batch generation AND identity-reset sweeps across shipped surfaces.
- `--revise <path>` — revise an existing component file with a new request. Reads the existing file, treats it as prior context, generates a new version.
- No args — ask the user which surface or set to generate; route accordingly.

## Pre-flight (fail fast)

Before any generation:

1. **Identify venture.** Match the current repo against `crane_ventures`. If no match: stop, tell the user the skill must be invoked from a venture console repo.
2. **NAVIGATION.md exists and is v3+.** Read `.design/NAVIGATION.md` from the venture repo. Check `spec-version` frontmatter. If absent or `< 3`: stop, suggest `/nav-spec` first.
3. **UX brief covers the requested surface.** Look for `.design/<target>-ux-brief.md` where the surface class matches. Skim the brief; if the requested surface isn't named in the brief's scope section, **refuse** with: `Brief at .design/<target>-ux-brief.md does not cover <surface>. Run /ux-brief to extend it, or pick a covered surface.` This is the negative-case behavior — it's the point.
4. **DESIGN.md or @theme tokens discoverable.** Read `.design/DESIGN.md` if present, or extract the `@theme` block from `src/styles/global.css` (Tailwind v4). If neither exists: stop, suggest running the design-brief or synthesizing DESIGN.md first.
5. **Adapter known.** Determine the adapter from the venture's stack:
   - Astro + Tailwind v4 → `adapters/astro-component.md`
   - Next.js + Tailwind v4 → `adapters/nextjs-page.md` (Phase 2; not in v1)
   - Unknown → stop, tell the user the adapter for their stack doesn't exist yet.

If any pre-flight fails, stop before calling the generation workflow. Do not invent context.

## Workflows

| User intent                                         | Workflow                                                                                                                               |
| --------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| "Design/build one surface"                          | [workflows/generate-single-surface.md](workflows/generate-single-surface.md)                                                           |
| "Design/build the whole client portal (or any set)" | [workflows/generate-surface-set.md](workflows/generate-surface-set.md)                                                                 |
| "Revise this component"                             | [workflows/generate-single-surface.md](workflows/generate-single-surface.md) with `--revise` — same flow, prior file loaded as context |

## The iteration loop (per component)

Five steps. Every generation follows this shape. Do not add steps without Captain approval.

1. **Assemble prompt.** See [references/prompt-assembly.md](references/prompt-assembly.md) for exactly what goes into the prompt and in what order. Inputs: nav-spec surface-class appendix, DESIGN.md or @theme tokens, UX brief section for this surface, five-tag classification (`surface=`, `archetype=`, `viewport=`, `task=`, `pattern=`), adapter template, **raw source of every file under the venture's `src/components/**`\*\*. No registry. No AST. Let Claude read the component source directly.
2. **Generate code.** Use the Write tool to produce the component file at its target path in the venture repo.
3. **Build check.** Run the venture's build command (detected from lockfile — see package-manager note above). If it fails: read the compile errors, append to the prompt, regenerate. Max one retry.
4. **Structural validate.** If a preview route is wired for this surface, extract the rendered HTML and run `~/.agents/skills/nav-spec/validate.py --file <html> --surface <tag> --archetype <tag> --viewport <tag> --task <tag> --pattern <tag> --spec <path-to-NAVIGATION.md>`. If structural violations: append to prompt, regenerate. Max one retry. If no preview route exists for this surface yet, skip — validator runs after the Captain promotes.
5. **Land.** The component file is in place. Report to the Captain: file path, how to preview (the venture's dev command → `/design-preview/<surface>`), and anything you iterated on. Done.

**Iteration budget: 2 total** (initial + 1 retry). If the retry fails build or validator, stop. Do not polish on iteration 3. Surface the diagnostic (which check failed, why) and ask the Captain how to proceed.

**No vision-critique loop in v1.** The Captain is the visual critic at gate 0. Chrome MCP screenshots, vision critique, reuse-check scripts — all deferred to Phase 2, added only if gate 0 surfaces quality problems the simple loop missed.

## Preview route convention

Each generated component must be previewable at `/design-preview/<surface>` in the venture's dev server. See [references/preview-route-pattern.md](references/preview-route-pattern.md) for the exact convention. Preview routes live in `src/pages/design-preview/` and are gated to `import.meta.env.DEV`, so they never serve in production. Fixture data co-located: `<surface>.fixture.json`.

If a preview route for the requested surface doesn't exist yet, the skill creates it alongside the component. ~15 lines per preview route.

## What you refuse

- Generating a surface not covered by the current UX brief (refuse with a clear pointer to extending the brief)
- Generating against a v<3 NAVIGATION.md (refuse with a pointer to `/nav-spec` v3)
- Generating into a venture without DESIGN.md or discoverable @theme tokens
- Generating a stack you don't have an adapter for
- Modifying pages under `src/pages/` other than the preview routes (pages are hand-wired; you produce components)
- Adding third-party runtime dependencies — you work with what's already in `package.json`

## References

- [prompt-assembly.md](references/prompt-assembly.md) — exact prompt structure and input ordering
- [preview-route-pattern.md](references/preview-route-pattern.md) — how to wire a surface for preview
- [adapters/astro-component.md](adapters/astro-component.md) — Astro + Tailwind v4 adapter template
- **Reused, read-only:**
  - `~/.agents/skills/nav-spec/validate.py` — structural validator
  - `~/.agents/skills/nav-spec/references/injection-snippet-template.md` — nav-contract block template

## Related skills

- `design-brief` — PRD → design charter (upstream)
- `nav-spec` — IA + patterns + chrome authority (sibling; structural authority)
- `ux-brief` — three-reviewer surface-level UX brief (upstream)

## Known limits (v1)

- Astro adapter only. Next.js adapter lands in Phase 2 (KE/DC).
- No vision-critique. Captain visually reviews via the venture's dev command.
- No batch parallelism. Surfaces in a `--set` generate serially; whole batch must fit within one Claude Code session (Max plan billing).
- No feedback-log. Git history is the log.
- No cross-venture consistency analysis. Each product is itself.
