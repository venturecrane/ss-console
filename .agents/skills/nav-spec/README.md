# nav-spec (v2)

Companion to `stitch-design` and `stitch-ux-brief`. Authors and enforces a per-venture three-layer navigation specification: **Information Architecture + Patterns + Chrome**. Anchored to Nielsen Norman Group, Material Design 3, Apple HIG, and Dan Brown's 8 IA principles.

## What it does

- Authors `.stitch/NAVIGATION.md` covering three layers:
  1. **IA** — task model, sitemap, reachability matrix, state machine, content taxonomy, URL contract, cross-surface context
  2. **Patterns** — named patterns from established frameworks, per `{surface × archetype}`, with rationale
  3. **Chrome** — header, back, breadcrumbs, footer, skip-link, states, tap targets, mobile↔desktop transforms, per-surface a11y
- Runs two distinct audits: **IA audit** (reachability, patterns, taxonomy) and **drift audit** (chrome consistency)
- Feeds a NAV CONTRACT block (essential + extended) into every Stitch generation via `stitch-design`'s patched pipeline
- Validates generated HTML and shipped surfaces against 24 deterministic rules (R1–R15 chrome, R16–R24 IA + pattern + a11y)

## v2 change

v1 defined **chrome only**. Result: pages shipped with consistent sticky headers and back buttons, but the portal home had no way to reach list views (`/portal/quotes`, `/portal/invoices`, `/portal/documents`, `/portal/engagement`) except by backtracking from a detail — a gaping IA hole the skill could not catch because none of its reviewers or validator rules checked reachability.

v2 adds the two layers above chrome (IA and Patterns) and 9 new validator rules (R16–R24). The principle: never invent patterns — anchor every choice to a published catalog.

## Who this is for

Ventures using Stitch MCP to generate design artifacts that ship to production (Astro + Tailwind). One spec per venture; shared skill across the portfolio.

## When to run

| Moment                                        | Command                                                                             |
| --------------------------------------------- | ----------------------------------------------------------------------------------- |
| First time for a venture (or v1→v2 migration) | `/nav-spec` (author or revise, auto-detected)                                       |
| After route changes / new surface class       | `/nav-spec --ia-audit`                                                              |
| Chrome drift check                            | `/nav-spec --drift-audit`                                                           |
| Updating existing spec                        | `/nav-spec --revise`                                                                |
| After Stitch model version change             | `/nav-spec --phase-0`                                                               |
| Validate a single HTML file                   | `python3 validate.py --file X --surface Y --archetype Z --viewport W [--spec path]` |

## Dependencies

- Stitch MCP connected (curl fallback via `STITCH_API_KEY` when MCP is OAuth-broken)
- Venture has `stitchProjectId` set in `crane-console/config/ventures.json`
- `.stitch/DESIGN.md` recommended but not required
- Python 3.8+ for `validate.py`

## Relationship to other skills

- **stitch-design** reads `.stitch/NAVIGATION.md`, requires 5 classification tags (`surface`, `archetype`, `viewport`, `task`, `pattern`), injects essential + extended NAV CONTRACT block, runs `validate.py` post-generation. Graceful-degradation when spec absent.
- **stitch-ux-brief** reads spec in Phase 1; injects NAV CONTRACT in Phase 7 concept prompts; generates REMOVE/PRESERVE lists from anti-patterns in Phase 11.
- **react-components** (future) reads the chrome contracts to produce aligned Astro components.

## Key files

- `SKILL.md` — entry point; three-layer model overview; 24-rule summary
- `workflows/author.md` — 12-phase authoring workflow (task → IA → pattern → chrome → verification)
- `workflows/ia-audit.md` — reachability, pattern conformance, taxonomy audit
- `workflows/drift-audit.md` — chrome consistency audit
- `workflows/revise.md` — update existing spec; supports v1→v2 migration
- `workflows/phase-0-compliance-test.md` — empirical injection-compliance measurement
- `workflows/validate.md` — rubric for all 24 rules
- `references/pattern-catalog.md` — NN/g + Material + HIG + composite patterns
- `references/ia-principles.md` — Dan Brown's 8 principles
- `references/archetype-catalog.md` — 10 archetypes × default patterns
- `references/reachability-matrix-template.md` — central IA artifact template
- `references/state-machine-template.md` — auth/data/task states
- `references/content-taxonomy-template.md` — labels, verbs, statuses
- `references/task-model-template.md` — task elicitation
- `references/chrome-component-contracts.md` — DOM + Tailwind
- `references/classification-rubric.md` — 5-tag decision rules
- `references/injection-snippet-template.md` — NAV CONTRACT block format
- `references/anti-patterns.md` — 24 anti-patterns (chrome + IA)
- `validate.py` — implementation of R1–R24
- `examples/NAVIGATION.md` — gold-standard v2 spec
- `examples/ia-audit-report.md` — IA audit format
- `examples/drift-audit-report.md` — chrome drift audit format
- `examples/phase-0-compliance-report.md` — compliance measurement format

## Philosophy

1. **Three layers, authored top-down.** Tasks before IA. IA before patterns. Patterns before chrome. Inverting this order produces the v1 failure mode.
2. **Anchor every pattern.** If a pattern doesn't appear in the catalog, the catalog is wrong (add it with citation) or the pattern is invented (replace it). Inventing breaks the rebuild's purpose.
3. **Five surface classes, by auth model.** `public`, `auth-gate`, `token-auth`, `session-auth-client`, `session-auth-admin`. Subdomain is secondary.
4. **Authoring is probabilistic; enforcement is deterministic.** The NAV CONTRACT injection is instructional; `validate.py` is truth.
5. **The reachability matrix is the contract.** R16 enforces it; no surface can orphan a destination without the validator catching it.
6. **Ground specs in shipped code.** The Implementation reviewer flags any contract requiring a refactor; the user chooses to align the spec or flag the refactor.
7. **The spec prevents drift on unseen screens.** Self-consistency (Phase 10) is necessary but not sufficient. Adversarial verification with reachability traversal (Phase 11) is the real gate.

## Installation

The skill is a standalone directory at `~/.agents/skills/nav-spec/`. No install step — Claude Code picks it up via directory presence.

Per-venture command wiring: add `/nav-spec` slash command at `<venture>/.claude/commands/nav-spec.md` pointing to this skill. See `examples/` for a sample.

## Versioning

- `nav-spec-skill-version: 2.0.0` (current)
- Spec-level versioning: each venture's `NAVIGATION.md` tracks its own `spec-version`
- Compatibility: v1 specs still work with the v2 validator (chrome-only rules apply); migrate with `/nav-spec --revise --migrate-to-v2`
