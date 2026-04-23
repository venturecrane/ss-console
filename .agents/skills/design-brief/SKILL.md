---
name: design-brief
description: Multi-Agent Design Brief Generator
version: 1.0.0
scope: enterprise
owner: agent-team
status: stable
---

# /design-brief - Multi-Agent Design Brief Generator

> **Invocation:** As your first action, call `crane_skill_invoked(skill_name: "design-brief")`. This is non-blocking — if the call fails, log the warning and continue. Usage data drives `/skill-audit`.

This command orchestrates a 4-agent design brief process with configurable rounds. It reads the PRD and existing design artifacts, runs structured design rounds with parallel agents, and synthesizes the output into a production-ready design brief.

The design brief answers "how should this look and feel?" - downstream of the PRD ("what to build and why?"). It requires a PRD to exist before running.

Works in any venture console that has `docs/pm/prd.md`.

## Arguments

```
/design-brief [rounds]
/design-brief --extract-identity <path-to-frontend-design-output>
```

**Default mode (with optional `rounds` argument):**

- `rounds` - number of design rounds (default: **1**). Each additional round adds cross-pollination where agents read and respond to each other's work.
  - **1 round**: Independent analysis + synthesis. Fast. Good for greenfield projects or early design exploration.
  - **2 rounds**: Adds cross-pollination. Agents revise after reading all Round 1 output.
  - **3 rounds**: Full process. Adds final polish round with open design decisions. Best for mature products heading into implementation.

Parse the argument: if `$ARGUMENTS` is empty or not a number, default to 1. If it's a number, use that value. There is no upper bound - if someone wants 5 rounds, run 5 rounds.

Store as `TOTAL_ROUNDS`.

**Identity-extraction mode (`--extract-identity`):**

Ingests output from Anthropic's `frontend-design` plugin (HTML/CSS/component code produced by an identity exploration run) and extracts concrete tokens into the venture's `.design/DESIGN.md`. See [workflows/extract-identity.md](workflows/extract-identity.md).

This mode skips the 4-agent brief process entirely. It parses visual output → token spec → file. Use when you've just run `/frontend-design` and need to codify the chosen aesthetic direction before running `/nav-spec`, `/ux-brief`, and `/product-design` downstream.

## Execution

### Step 1: Locate Source Documents

**PRD (required)**

Check for `docs/pm/prd.md`. If it does not exist, stop and tell the user:

> I couldn't find a PRD at `docs/pm/prd.md`.
>
> The design brief is downstream of product definition - it needs a PRD to work from.
>
> Run `/prd-review` first to generate a PRD, then re-run `/design-brief`.

**Enrichment sources (optional)**

Search for additional design context. These are optional - proceed without them if not found:

1. **Executive summary** - Use `crane_notes` MCP tool to search for notes with tag `executive-summary` scoped to the current venture (determine venture code from repo name, e.g., `ke-console` → `ke`)
2. **Design tokens** - Glob for `**/globals.css` and `**/tailwind.config.*`
3. **Component library** - Glob for `**/components/ui/**/index.{ts,tsx,js,jsx}` (barrel exports only)
4. **Design charter** - Check `docs/ventures/{venture_code}/design-charter.md` or glob `docs/ventures/{venture_code}/design-*.md`
5. **Live site** - Check for a deploy URL in the PRD, project instructions, or package.json (`homepage` field). If found, use WebFetch to capture the current state.

Display a **Design Artifact Inventory** table showing what was found:

| Source            | Status            | Path / Details           |
| ----------------- | ----------------- | ------------------------ |
| PRD               | Found             | `docs/pm/prd.md`         |
| Executive Summary | Found / Not found | VCMS note or -           |
| Design Tokens     | Found / Not found | path or -                |
| Component Library | Found / Not found | path (N components) or - |
| Design Charter    | Found / Not found | path or -                |
| Live Site         | Found / Not found | URL or -                 |

### Step 2: Extract and Confirm Design Context

Read the PRD and all found enrichment sources. Extract the following into a confirmation table:

| Field                | Value                                                               |
| -------------------- | ------------------------------------------------------------------- |
| Product Name         | _(from PRD)_                                                        |
| Tagline              | _(from PRD)_                                                        |
| Tech Stack           | _(from PRD)_                                                        |
| Primary Platform     | _(from PRD)_                                                        |
| Target User          | _(from PRD)_                                                        |
| Emotional Context    | _(from PRD - what emotional state is the user in when using this?)_ |
| **Design Maturity**  | _(from filesystem scan - see classification below)_                 |
| Existing Palette     | _(from globals.css custom properties, or "None")_                   |
| Component Count      | _(from ui/ barrel exports, or "0")_                                 |
| Dark Mode            | _(from globals.css: "Implemented" / "Partial" / "None")_            |
| Accessibility Target | _(from PRD/charter, or default "WCAG 2.1 AA")_                      |

**Design Maturity classification** (critical - this changes agent behavior):

- **Greenfield**: No design tokens, no components. Agents propose everything from scratch - concrete hex values, type scales, spacing systems.
- **Tokens defined**: globals.css has CSS custom properties, minimal components (0-2). Agents respect existing tokens and extend the system.
- **Full system**: Design tokens + 3 or more components in ui/. Agents refine, document, and fill gaps - they do not replace what exists.

Also display: **"Running {TOTAL_ROUNDS} round(s) with 4 design agents. Design Maturity: {MATURITY}."**

Present the table and ask the user: **"Does this look right? Anything to correct before I start the design brief?"**

Wait for confirmation. If user provides corrections, note them - they become additional context for all agents.

### Step 3: Handle Previous Runs

Check if `docs/design/contributions/` exists.

If it does:

1. Create archive directory: `docs/design/contributions-archive/` (if it doesn't exist)
2. Move the entire `docs/design/contributions/` directory to `docs/design/contributions-archive/{ISO-date}/` where `{ISO-date}` is today's date (e.g., `2026-02-13`)
3. Tell the user: "Archived previous run to `docs/design/contributions-archive/{ISO-date}/`"

If the archive date directory already exists (second run same day), append a counter: `{ISO-date}-2`, `{ISO-date}-3`, etc.

### Step 4: Create Directory Structure

Create round directories for each round that will be run:

```bash
for N in 1..TOTAL_ROUNDS:
  mkdir -p docs/design/contributions/round-{N}
```

### Step 5: Run Design Rounds

Execute `TOTAL_ROUNDS` rounds sequentially. For each round N (from 1 to TOTAL_ROUNDS):

---

**If N == 1 (first round - always independent analysis):**

Launch **4 parallel agents** in a single message using the Task tool (`subagent_type: general-purpose`, `model: "sonnet"`).

**CRITICAL**: All 4 Task tool calls MUST be in a single message to run in true parallel.

Before launching agents, write a `docs/design/context.md` file listing all source document paths discovered in Step 1-2:

```markdown
# Design Brief Context

## Source Documents

- PRD: docs/pm/prd.md
- Executive Summary: {VCMS note ID or "Not found"}
- Design Tokens: {path to globals.css or "Not found"}
- Component Library: {path to barrel export or "Not found"}
- Design Charter: {path to charter or "Not found"}
- Live Site: {URL or "Not found"}

## Design Maturity: {MATURITY_LEVEL}

## User Corrections: {corrections or "None"}
```

Each agent receives:

- The path to `docs/design/context.md` (agents read source documents via tools)
- The Design Maturity classification and what it means for their work
- Their role brief (from Role Definitions below)
- Output path: `docs/design/contributions/round-1/{role-slug}.md`

Agent prompt template for Round 1:

```
You are the {ROLE_NAME} on a design brief panel. Your job is to analyze the PRD and existing design artifacts, then write a comprehensive contribution from your role's perspective.

## Context File

Read `docs/design/context.md` for the list of source documents. Then read each source document listed there using the Read tool. The PRD at `docs/pm/prd.md` is required - read it first.

## Design Maturity: {MATURITY_LEVEL}

{MATURITY_DESCRIPTION - explain what this means for the agent's work:
- Greenfield: "No existing design system. Propose concrete values - specific hex colors, font stacks, spacing scales. Do not say 'choose a primary color' - choose it."
- Tokens defined: "Existing CSS custom properties found. Respect these values. Extend the system - don't replace it. Propose additions that are consistent with what exists."
- Full system: "Mature design system with tokens and components. Your job is to refine, document gaps, and ensure consistency - not to redesign."}

## Your Role

{ROLE_BRIEF}

## Output Requirements

- Write your contribution as a single markdown file
- Start with a header: `# {ROLE_NAME} Contribution - Design Brief Round 1`
- Include metadata: Author, Date ({TODAY}), Design Maturity: {MATURITY_LEVEL}
- Use `##` for major sections, `###` for subsections
- Be specific and concrete - no hand-waving. Provide actual values, not placeholders.
- Reference specific features and screens from the PRD
- The PRD is the source of truth for what to build. You define how it should look and feel.

Write the file to: {OUTPUT_PATH}
```

Wait for all 4 agents to complete before proceeding.

Tell the user: **"Round 1 complete. All 4 design agents have written their independent analyses."**

---

**If N > 1 and N < TOTAL_ROUNDS (middle round - cross-pollination):**

Then launch **4 parallel agents** in a single message.

Each agent receives:

- The path to `docs/design/context.md` (same as Round 1)
- The path to Round N-1 contributions directory: `docs/design/contributions/round-{N-1}/`
- Their role brief
- Output path: `docs/design/contributions/round-{N}/{role-slug}.md`

Agent prompt template for middle rounds:

```
You are the {ROLE_NAME} on a design brief panel. This is Round {N}. Read all Round {N-1} contributions, then revise your contribution based on what you've learned.

## Context File

Read `docs/design/context.md` for the list of source documents. Then read each source document listed there using the Read tool. The PRD at `docs/pm/prd.md` is required.

## Round {N-1} Contributions

Read all 4 files in `docs/design/contributions/round-{N-1}/`:
- `brand-strategist.md`
- `interaction-designer.md`
- `design-technologist.md`
- `target-user.md`

## Design Maturity: {MATURITY_LEVEL}

{MATURITY_DESCRIPTION}

## Your Role

{ROLE_BRIEF}

## Round {N} Requirements

- Start with `# {ROLE_NAME} Contribution - Design Brief Round {N}`
- Include metadata: Author, Date ({TODAY}), Design Maturity: {MATURITY_LEVEL}, Status: Revised after cross-role review
- Your FIRST section after metadata MUST be `## Changes from Round {N-1}` with a numbered list of key revisions. For each: what changed, why, which role's input triggered it.
- Then write your full revised contribution (not just a diff - the complete document)
- Resolve contradictions you see between roles
- Fill gaps identified by other roles
- Cross-reference other roles: "The Brand Strategist proposed...", "The Target User reacted..."
- The PRD is the source of truth for what to build. You define how it should look and feel.

Write the file to: {OUTPUT_PATH}
```

Wait for all 4 agents to complete.

Tell the user: **"Round {N} complete. All 4 design agents have revised based on cross-role input."**

---

**If N == TOTAL_ROUNDS and N > 1 (final round - polish + open design decisions):**

Then launch **4 parallel agents** in a single message.

Each agent receives:

- The path to `docs/design/context.md` (same as previous rounds)
- The path to Round N-1 contributions directory: `docs/design/contributions/round-{N-1}/`
- Their role brief
- Output path: `docs/design/contributions/round-{N}/{role-slug}.md`

Agent prompt template for the final round:

```
You are the {ROLE_NAME} on a design brief panel. This is Round {N} (FINAL). Read all Round {N-1} contributions, then write your final, polished contribution.

## Context File

Read `docs/design/context.md` for the list of source documents. Then read each source document listed there using the Read tool. The PRD at `docs/pm/prd.md` is required.

## Round {N-1} Contributions

Read all 4 files in `docs/design/contributions/round-{N-1}/`:
- `brand-strategist.md`
- `interaction-designer.md`
- `design-technologist.md`
- `target-user.md`

## Design Maturity: {MATURITY_LEVEL}

{MATURITY_DESCRIPTION}

## Your Role

{ROLE_BRIEF}

## Round {N} (Final) Requirements

- Start with `# {ROLE_NAME} Contribution - Design Brief Round {N} (Final)`
- Include metadata: Author, Date ({TODAY}), Design Maturity: {MATURITY_LEVEL}, Status: Final after {N} rounds
- Your FIRST section after metadata MUST be `## Changes from Round {N-1}` with a numbered list of key revisions.
- Write your full final contribution (complete document, not a diff)
- Standardize terminology across your document to match consensus from other roles
- Your LAST section MUST be `## Open Design Decisions` - list genuine design disagreements or unresolved questions that need a human decision. For each:
  - **The question**: what needs to be decided
  - **Options considered**: what each role suggested
  - **Why it matters**: impact on user experience
  - **My recommendation**: your stance as this role
  - **Needs**: what type of decision is required (founder call, user testing, A/B test, design spike, etc.)
- If there are no open design decisions from your perspective, state that explicitly
- The PRD is the source of truth for what to build. You define how it should look and feel.

Write the file to: {OUTPUT_PATH}
```

Wait for all 4 agents to complete.

Tell the user: **"Round {N} (final) complete. All contribution files are written. Starting synthesis."**

---

**Special case: TOTAL_ROUNDS == 1**

When only 1 round is run, Round 1 IS the final round. The Round 1 prompt template is used as-is (no "Changes from" or "Open Design Decisions" sections since there's nothing to compare against). Proceed directly to synthesis.

Tell the user: **"Round 1 complete. All 4 design agents have written their analyses. Starting synthesis."**

### Step 6: Synthesis

Read ALL 4 contributions from the final round (round TOTAL_ROUNDS). Synthesize into a single `docs/design/brief.md` following this structure:

```markdown
# {Product Name} - Design Brief

> Synthesized from {TOTAL_ROUNDS}-round, 4-role design brief process. Generated {TODAY}.
> Design Maturity: {MATURITY_LEVEL}

## Table of Contents

1. Product Identity
2. Brand Personality & Design Principles
3. Target User Context
4. Visual Language
5. Screen Inventory & Key Screens
6. Interaction Patterns
7. Component System Direction
8. Technical Constraints
9. Inspiration & Anti-Inspiration
10. Design Asks
11. Open Design Decisions
```

**Synthesis rules:**

| Section                                  | Primary Source       | Supporting Sources                     |
| ---------------------------------------- | -------------------- | -------------------------------------- |
| 1. Product Identity                      | Brand Strategist     | Target User                            |
| 2. Brand Personality & Design Principles | Brand Strategist     | Target User, Interaction Designer      |
| 3. Target User Context                   | Target User          | Brand Strategist, Interaction Designer |
| 4. Visual Language                       | Brand Strategist     | Design Technologist                    |
| 5. Screen Inventory & Key Screens        | Interaction Designer | Design Technologist                    |
| 6. Interaction Patterns                  | Interaction Designer | Target User, Design Technologist       |
| 7. Component System Direction            | Design Technologist  | Interaction Designer                   |
| 8. Technical Constraints                 | Design Technologist  | Interaction Designer                   |
| 9. Inspiration & Anti-Inspiration        | Brand Strategist     | Target User                            |
| 10. Design Asks                          | Interaction Designer | All                                    |
| 11. Open Design Decisions                | All                  | -                                      |

**Synthesis guidelines:**

- The synthesized brief should read as a unified document, not a collage
- Brand Strategist's voice is primary for identity and visual language sections
- Design Technologist is authoritative for technical constraint and component sections
- Preserve concrete artifacts: hex values, contrast ratios, component specs, ARIA patterns
- Include the Target User's voice as quoted reactions where relevant (first person, emotional)
- The Open Design Decisions section collects ALL unresolved items from all final-round contributions, deduplicated. If TOTAL_ROUNDS == 1, this section may be minimal or empty - that's fine.
- **Design Asks** should be a numbered list of specific, actionable design tasks extracted from all contributions. Each ask should have: title, description, priority (P0/P1/P2), and which role originated it.
- This file overwrites any existing `docs/design/brief.md` (the contributions are the audit trail)

Tell the user: **"Synthesis complete. Design brief written to `docs/design/brief.md`."**

Provide a brief summary: section count, word count, number of open design decisions flagged, number of design asks, number of rounds run.

### Step 6b: Generate Design Spec

After synthesis, extract the core design reference from the brief into a standardized design spec:

1. Read the synthesized `docs/design/brief.md`
2. Extract: color tokens (with hex values), typography (font stacks, scale), spacing system, surface hierarchy, component inventory, and accessibility notes
3. Write to `docs/design/design-spec.md` in the **current repo** (wherever the skill runs)
4. If the current repo is `crane-console`, also write to `docs/ventures/{venture_code}/design-spec.md`
5. The design spec format follows the template at `templates/venture/docs/design/design-spec.md` - structured for agent consumption with token tables, not prose

Tell the user: **"Design spec generated at `docs/design/design-spec.md`. Design specs sync to D1 automatically via GitHub Action when merged to main."**

Do not attempt the API upload from within the skill (no credentials available in skill context).

### Step 7: Follow-up (Optional)

Ask the user: **"What would you like to do next?"** and present three options:

1. **Store in VCMS** - Save the design brief to the enterprise knowledge store using `crane_note` with tag `design` and the current venture scope.

2. **Create design issues** - Parse the Design Asks section. For each ask, create a GitHub issue via `gh issue create` with the `area:design` label. Present the proposed issue list for approval before creating.

3. **Generate design charter** - If `docs/design/charter.md` does not already exist, offer to generate one. The charter is a governance document that captures: design principles, decision-making process, token naming conventions, component contribution guidelines, and accessibility standards. Write it to `docs/design/charter.md`.

If the user declines all options, finish with: **"Design brief complete. {TOTAL_ROUNDS \* 4} contribution files in `docs/design/contributions/`, synthesized brief at `docs/design/brief.md`."**

---

## Role Definitions

### Brand Strategist

```
You are the Brand Strategist. You own brand personality, visual identity, and emotional design direction.

YOUR SECTIONS:
- Brand Personality: 3-5 personality traits with descriptions and "this, not that" examples (e.g., "Warm, not cutesy")
- Design Principles: 5-7 prioritized principles that guide visual and interaction tradeoffs
- Color System: primary, secondary, accent, semantic (success/warning/error/info), and neutral palette. Every color as hex value with WCAG AA contrast ratio against its most common background. Light and dark mode variants if applicable.
- Typography: font stack (Google Fonts or system fonts only - no paid fonts). Scale with specific sizes for h1-h6, body, small, caption. Line heights and letter spacing.
- Spacing & Rhythm: base unit and scale (e.g., 4px base: 4, 8, 12, 16, 24, 32, 48, 64)
- Imagery & Iconography: icon style (outline/solid/duo-tone), icon library recommendation, illustration style if applicable, photography direction if applicable
- Inspiration Board: 3-5 real products/brands that capture the right feel, with specific URLs and what to take from each
- Anti-Inspiration: 2-3 products/brands that represent the wrong direction, with what to avoid

CONSTRAINTS:
- If existing design tokens were found (Design Maturity: "Tokens defined" or "Full system"), START from those values. Respect what exists. Propose changes only where the current system has gaps or inconsistencies.
- If greenfield (Design Maturity: "Greenfield"), propose CONCRETE hex values - never say "choose a primary color." Choose it. Be decisive.
- All text/background color pairings MUST pass WCAG AA contrast (4.5:1 for normal text, 3:1 for large text). Include the contrast ratio for each pairing.
- Typography must use only Google Fonts or system font stacks. No paid/licensed fonts.
- Inspiration references must be real, current products with URLs.

OUTPUT FORMAT:
- Markdown with ## section headers
- Color tables with hex values and contrast ratios
- Type scale as a table with size, weight, line-height, letter-spacing
- Be decisive and specific - designers need concrete values to implement
```

### Interaction Designer

```
You are the Interaction Designer. You own screen inventory, user flows, navigation, and interaction patterns.

YOUR SECTIONS:
- Screen Inventory: complete list of every MVP screen/page with URL pattern, purpose, and primary action. This must map 1:1 to PRD features - every feature has at least one screen, every screen traces back to a feature.
- Key Screen Breakdowns: the top 5 most important screens. For each:
  - Layout description (mobile-first)
  - Content elements and hierarchy
  - Primary action and its visual weight
  - Empty state (what shows when there's no data)
  - Loading state (skeleton, spinner, or progressive)
  - Error state (inline, toast, or full-page)
- Navigation Model: primary nav structure, secondary nav, breadcrumbs if applicable. Max depth. Mobile nav pattern (bottom tabs, hamburger, etc.)
- User Flows: top 3 critical task flows. Step-by-step: screen → action → screen → result. Include happy path and primary error path.
- Form Patterns: input styles, validation timing (on-blur, on-submit), error message placement, required field indicators
- Feedback Patterns: toast/notification style, success confirmations, destructive action confirmations, progress indicators
- Responsive Strategy: breakpoints, what changes at each breakpoint, mobile-first vs desktop-first

CONSTRAINTS:
- Screen inventory MUST map 1:1 to PRD features. If the PRD lists a feature, there must be a screen for it. If you list a screen, it must trace to a PRD feature.
- Mobile-first descriptions. Describe the mobile layout, then note desktop adaptations.
- Every data-displaying screen MUST specify empty state, loading state, and error state.
- Max 2 taps/clicks to reach any primary feature from the home screen.
- User flows must be concrete: "User taps X → sees Y screen → enters Z → taps Submit → sees confirmation toast" - not abstract.

OUTPUT FORMAT:
- Markdown with ## section headers
- Tables for screen inventory
- Step-by-step numbered lists for user flows
- Be exhaustive on screens - a missing screen becomes a missed feature
```

### Design Technologist

```
You are the Design Technologist. You own the component system, token architecture, accessibility, and technical implementation constraints.

YOUR SECTIONS:
- Component Inventory: every UI component needed for MVP. For each:
  - Name (PascalCase)
  - Purpose (one sentence)
  - Variants (e.g., primary/secondary/ghost for Button)
  - Key props interface (TypeScript-style)
  - Status: "Exists" / "Exists (needs update)" / "New" (based on component library scan)
  - ARIA role/pattern (e.g., role="dialog", combobox pattern)
- Design Token Architecture:
  - Naming convention using venture prefix (e.g., --ke-color-primary, --sc-spacing-4)
  - Token categories: color, spacing, typography, radius, shadow, motion
  - CSS custom property definitions
  - Tailwind config mapping (if Tailwind is in the tech stack)
- CSS Strategy: methodology (utility-first, BEM, CSS modules, etc.), based on tech stack
- Dark Mode Implementation: strategy (CSS custom properties + class toggle, media query, or both), token structure for light/dark
- Responsive Implementation: container queries vs media queries, fluid typography, responsive spacing
- Accessibility:
  - Focus management strategy (focus trap for modals, skip links, focus restoration)
  - Keyboard navigation patterns (arrow keys for lists, tab for form fields, escape to close)
  - ARIA patterns for complex widgets (combobox, tabs, accordion, dialog)
  - Reduced-motion: what animations to disable, what to replace with crossfade
  - Screen reader announcements: live regions for dynamic content
- Performance Budget:
  - First Contentful Paint: target in ms
  - Largest Contentful Paint: target in ms
  - Cumulative Layout Shift: target
  - Total CSS bundle size: target in KB
  - Font loading strategy: swap/optional/block
- Animation & Motion: easing curves, duration scale, what animates and what doesn't

CONSTRAINTS:
- If existing components were found (Design Maturity: "Full system"), mark each as "Exists" / "Exists (needs update)" / "New." Do not propose replacing existing components.
- Token naming MUST use venture prefix pattern (e.g., --ke-*, --sc-*, --dfg-*)
- Every component MUST specify its ARIA role or pattern
- Performance budget must have specific numbers, not "fast"
- Animation durations: 100-150ms for micro-interactions, 200-300ms for transitions, 300-500ms for page transitions
- Accessibility target is WCAG 2.1 AA unless the PRD or charter specifies otherwise

OUTPUT FORMAT:
- Markdown with ## section headers
- Tables for component inventory
- Code blocks for token definitions and CSS examples
- TypeScript interfaces for component props
- Be precise - ambiguity in design specs causes inconsistent implementation
```

### Target User

```
You are the Target User - the actual person this product is being designed for. Stay in character throughout.

Write in FIRST PERSON. You are not an analyst or a designer - you are the user. React to the design directions as a real person would.

YOUR SECTIONS:
- Who I Am: brief intro establishing your identity, daily life, and emotional state when you'd use this product
- My Environment: where and how I use apps like this - device, time of day, attention level, physical context (commuting? at desk? in bed?)
- First Impressions: if I saw this product for the first time, what would I think? Does it look trustworthy? Professional? Fun? Confusing?
- Emotional Reactions: go through each key screen described by the Interaction Designer (or inferred from the PRD). How does each one make me feel? What do I notice first? What confuses me?
- What Feels Right: design patterns from apps I already use that feel natural and good. Name real apps. "I love how Notion does X" or "The way Linear handles Y is perfect"
- What Would Turn Me Off: specific design anti-patterns that would make me distrust or abandon this product. Be blunt and emotional.
- Navigation Expectations: how I expect to move through this product. What should be one tap away? What's OK to bury?
- Make-or-Break Moments: the 2-3 moments in the user experience where design quality will determine if I stay or leave

CONSTRAINTS:
- First person ONLY. Never break character. Never use design jargon (no "affordance," "heuristic," "information architecture").
- Be honest, not polite - if something sounds ugly or confusing, say so
- Reference REAL apps as comparisons (Venmo, Notion, Linear, Instagram, etc.)
- Express genuine emotion: frustration, delight, anxiety, confusion, trust, skepticism
- React to WHAT'S DESCRIBED in the PRD, not what you wish existed
- "If it looks like a government website I'm leaving" - that level of honesty
- Your reactions should reflect the emotional context from the PRD (stressed parent? busy professional? casual browser?)

OUTPUT FORMAT:
- First-person narrative prose
- Conversational tone - this reads like a user interview, not a report
- Use "I" and "my" throughout
- Bold or emphasize strong reactions
- Be blunt and emotional - sugar-coating helps no one
```

---

## Role-to-Slug Mapping

| Role                 | Slug (filename)        |
| -------------------- | ---------------------- |
| Brand Strategist     | `brand-strategist`     |
| Interaction Designer | `interaction-designer` |
| Design Technologist  | `design-technologist`  |
| Target User          | `target-user`          |

---

## Notes

- **PRD is required**: Unlike `/prd-review` which works from project instructions alone, `/design-brief` requires `docs/pm/prd.md` - the design brief is downstream of product definition
- **Design Maturity drives behavior**: Not a flow control flag, but context that fundamentally changes what agents produce (greenfield = propose everything, full system = refine and extend)
- **Codebase scanning**: Agents receive CSS token values and component barrel exports, not raw component source code
- **4 roles not 6**: Design is more focused than product definition; 6 roles would produce redundancy
- **Output in `docs/design/`**: Design artifacts are a separate concern from PM artifacts in `docs/pm/`
- **Re-runs are safe**: Previous contributions are archived before a new run starts
- **Source documents are not modified**: Only `docs/design/brief.md` is written (overwritten)
- **Contributions are the audit trail**: `TOTAL_ROUNDS * 4` files show how the design brief evolved
- **Agent type**: All role agents use `subagent_type: general-purpose`, `model: "sonnet"` via the Task tool
- **Parallelism**: Each round launches all 4 agents in a single message for true parallel execution
- **Context size**: Round 2+ agents receive large prompts (all previous round outputs). This is expected and necessary for cross-pollination.
- **Default is 1 round**: Fast and sufficient for most use cases. Use more rounds when the design system is mature and heading into implementation.
