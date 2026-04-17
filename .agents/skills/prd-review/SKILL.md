---
name: prd-review
description: Multi-Agent PRD Review
version: 1.0.0
scope: enterprise
owner: captain
status: stable
---

# /prd-review - Multi-Agent PRD Review

> **Invocation:** As your first action, call `crane_skill_invoked(skill_name: "prd-review")`. This is non-blocking — if the call fails, log the warning and continue. Usage data drives `/skill-audit`.

This command orchestrates a 6-agent PRD review process with configurable rounds. It reads existing source documents, runs structured critique rounds with parallel agents, and synthesizes the output into a production-ready PRD.

Works in any venture console that has the required source documents.

## Arguments

```
/prd-review [rounds]
```

- `rounds` - number of review rounds (default: **1**). Each additional round adds cross-pollination where agents read and respond to each other's work.
  - **1 round**: Independent analysis + synthesis. Fast. Good for early-stage products or first PRD drafts.
  - **2 rounds**: Adds cross-pollination. Agents revise after reading all Round 1 output.
  - **3 rounds**: Full process. Adds final polish round with unresolved issues. Best for mature products heading into development.

Parse the argument: if `$ARGUMENTS` is empty or not a number, default to 1. If it's a number, use that value. There is no upper bound - if someone wants 5 rounds, run 5 rounds.

Store as `TOTAL_ROUNDS`.

## Execution

### Step 1: Locate Source Documents

Search for source material in three places, in priority order:

**1. Local filesystem (primary)**

Glob for input files. Names vary across ventures:

```
docs/process/*project-instructions*
docs/process/*project-description*
docs/pm/*.md (exclude prd-contributions/ subdirectory and prd.md itself)
```

**2. crane-context API (fallback)**

If local files are missing, try pulling from crane-context. Determine the venture code from the repo name (e.g., `ke-console` → `ke`). Use `crane_doc(venture_code, 'project-instructions.md')` or similar to fetch docs matching `*project-instructions*` or `*project-description*`.

**3. Classify what you found**

You need **at minimum one** source document to proceed. Ideally two categories:

1. **Project instructions/description** - the foundational product vision, tech stack, principles, and constraints
2. **PRD or product spec** - the current PRD draft or product spec

**If you found both:** Proceed normally.

**If you found only one (e.g., just a PRD or just project instructions):** Proceed with what you have. Note to the user which category is missing and that the review will work from a single source. The agents will still produce useful output - the review just won't have the benefit of cross-referencing two distinct documents.

**If you found nothing (no local files AND crane-context has no docs for this venture):** Stop: "No source documents found locally or in crane-context. Need at least `docs/process/{venture}-project-instructions.md` or `docs/pm/prd.md`. Create one and re-run `/prd-review`."

### Step 2: Extract and Confirm Venture Context

Read all source documents found in Step 1. Extract the following into a confirmation table:

| Field                 | Value                             |
| --------------------- | --------------------------------- |
| Product Name          | _(from docs)_                     |
| Tagline / One-liner   | _(from docs)_                     |
| Tech Stack            | _(from docs)_                     |
| Target User           | _(from docs)_                     |
| Primary Platform      | _(from docs)_                     |
| MVP Features (count)  | _(from docs)_                     |
| Kill Criteria         | _(from docs, or "Not specified")_ |
| Competitors Mentioned | _(from docs, or "None")_          |

Also display: **"Running {TOTAL_ROUNDS} round(s) with 6 agents."**

Present the table and ask the user: **"Does this look right? Anything to correct before I start the review?"**

Wait for confirmation. If user provides corrections, note them - they become additional context for all agents.

### Step 3: Handle Previous Runs

Check if `docs/pm/prd-contributions/` exists.

If it does:

1. Create archive directory: `docs/pm/prd-contributions-archive/` (if it doesn't exist)
2. Move the entire `docs/pm/prd-contributions/` directory to `docs/pm/prd-contributions-archive/{ISO-date}/` where `{ISO-date}` is today's date (e.g., `2026-02-06`)
3. Tell the user: "Archived previous run to `docs/pm/prd-contributions-archive/{ISO-date}/`"

If the archive date directory already exists (second run same day), append a counter: `{ISO-date}-2`, `{ISO-date}-3`, etc.

### Step 4: Create Context File and Directory Structure

**First**, write the context file that all agents will read. This MUST be created before any round directories or agent launches:

Write `docs/pm/prd-contributions/context.md`:

```markdown
# PRD Review Context

## Source Documents

- {absolute_path_to_source_doc_1}
- {absolute_path_to_source_doc_2}

## User Corrections

{verbatim corrections from Step 2, or "None"}

## Review Parameters

- Rounds: {TOTAL_ROUNDS}
- Date: {TODAY}
```

List every source document found in Step 1 by its absolute file path. If corrections were provided in Step 2, include them verbatim.

**Then** create round directories for each round that will be run:

```bash
for N in 1..TOTAL_ROUNDS:
  mkdir -p docs/pm/prd-contributions/round-{N}
```

### Step 5: Run Review Rounds

Execute `TOTAL_ROUNDS` rounds sequentially. For each round N (from 1 to TOTAL_ROUNDS):

---

**If N == 1 (first round - always independent analysis):**

Launch **6 parallel agents** in a single message using the Task tool (`subagent_type: general-purpose`, `model: "sonnet"`).

**CRITICAL**: All 6 Task tool calls MUST be in a single message to run in true parallel.

**Do NOT read source documents or embed their contents in the prompt.** Agents have full tool access and will read files themselves.

Each agent receives:

- The path to the context file
- Their role brief (from Role Definitions below)
- Output path: `docs/pm/prd-contributions/round-1/{role-slug}.md`

Agent prompt template for Round 1:

```
You are the {ROLE_NAME} on a PRD review panel. Your job is to analyze the source documents and write a comprehensive contribution from your role's perspective.

## Instructions

1. Read the review context file at: docs/pm/prd-contributions/context.md
2. Read each source document listed in the context file.
3. If user corrections are noted in the context file, treat them as binding amendments to the source documents.
4. Write your contribution to: {OUTPUT_PATH}

## Your Role

{ROLE_BRIEF}

## Output Requirements

- Write your contribution as a single markdown file
- Start with a header: `# {ROLE_NAME} Contribution - PRD Review Round 1`
- Include metadata: Author, Date ({TODAY}), Scope (MVP/Phase 0 only)
- Use `##` for major sections, `###` for subsections
- Be specific and concrete - no hand-waving
- Reference specific features, metrics, and constraints from the source documents
- Project instructions override the PRD where they conflict. MVP scope only.

Write the file to: {OUTPUT_PATH}
```

Wait for all 6 agents to complete before proceeding.

**Between-round validation:** Use Glob to find all `.md` files in `docs/pm/prd-contributions/round-1/`. Verify 6 files exist. If fewer than 6, warn the user which roles are missing and ask whether to proceed or retry the failed agents.

Tell the user: **"Round 1 complete. All 6 agents have written their independent analyses."**

---

**If N > 1 and N < TOTAL_ROUNDS (middle round - cross-pollination):**

**Do NOT read prior-round contribution files.** Agents will read them directly from disk.

Launch **6 parallel agents** in a single message.

Each agent receives:

- The path to the context file
- The prior round number to read contributions from
- Their role brief
- Output path: `docs/pm/prd-contributions/round-{N}/{role-slug}.md`

Agent prompt template for middle rounds:

```
You are the {ROLE_NAME} on a PRD review panel. This is Round {N}. You will read all Round {N-1} contributions from all 6 roles and revise your contribution based on what you've learned.

## Instructions

1. Read the review context file at: docs/pm/prd-contributions/context.md
2. Read each source document listed in the context file.
3. If user corrections are noted in the context file, treat them as binding amendments.
4. Use the Glob tool to find all .md files in docs/pm/prd-contributions/round-{N-1}/.
   Read every file found. These are the prior round contributions from all roles.
   If fewer than 6 files exist, note which roles are missing in your output.
5. Write your revised contribution to: {OUTPUT_PATH}

## Your Role

{ROLE_BRIEF}

## Round {N} Requirements

- Start with `# {ROLE_NAME} Contribution - PRD Review Round {N}`
- Include metadata: Author, Date ({TODAY}), Scope (MVP/Phase 0 only), Status: Revised after cross-role review
- Your FIRST section after metadata MUST be `## Changes from Round {N-1}` with a numbered list of key revisions. For each: what changed, why, which role's input triggered it.
- Then write your full revised contribution (not just a diff - the complete document)
- Resolve contradictions you see between roles
- Fill gaps identified by other roles
- Cross-reference other roles: "The Technical Lead specified...", "The Target Customer said..."
- Ground abstract principles in concrete implementation
- Project instructions override the PRD where they conflict. MVP scope only.

Write the file to: {OUTPUT_PATH}
```

Wait for all 6 agents to complete.

**Between-round validation:** Use Glob to find all `.md` files in `docs/pm/prd-contributions/round-{N}/`. Verify 6 files exist. If fewer than 6, warn the user which roles are missing and ask whether to proceed or retry the failed agents.

Tell the user: **"Round {N} complete. All 6 agents have revised based on cross-role input."**

---

**If N == TOTAL_ROUNDS and N > 1 (final round - polish + unresolved issues):**

**Do NOT read prior-round contribution files.** Agents will read them directly from disk.

Launch **6 parallel agents** in a single message.

Each agent receives:

- The path to the context file
- The prior round number to read contributions from
- Their role brief
- Output path: `docs/pm/prd-contributions/round-{N}/{role-slug}.md`

Agent prompt template for the final round:

```
You are the {ROLE_NAME} on a PRD review panel. This is Round {N} (FINAL). You will read all Round {N-1} contributions and write your final, polished contribution.

## Instructions

1. Read the review context file at: docs/pm/prd-contributions/context.md
2. Read each source document listed in the context file.
3. If user corrections are noted in the context file, treat them as binding amendments.
4. Use the Glob tool to find all .md files in docs/pm/prd-contributions/round-{N-1}/.
   Read every file found. These are the prior round contributions from all roles.
   If fewer than 6 files exist, note which roles are missing in your output.
5. Write your final contribution to: {OUTPUT_PATH}

## Your Role

{ROLE_BRIEF}

## Round {N} (Final) Requirements

- Start with `# {ROLE_NAME} Contribution - PRD Review Round {N} (Final)`
- Include metadata: Author, Date ({TODAY}), Scope (MVP/Phase 0 only), Status: Final after {N} rounds
- Your FIRST section after metadata MUST be `## Changes from Round {N-1}` with a numbered list of key revisions.
- Write your full final contribution (complete document, not a diff)
- Standardize terminology across your document to match consensus from other roles
- Your LAST section MUST be `## Unresolved Issues` - list genuine disagreements that need a human decision. For each:
  - **The disagreement**: what each role says
  - **Why it matters**: impact on the product
  - **My position**: your stance as this role
  - **Needs**: what type of decision is required (PM call, ADR, user research, etc.)
- If there are no unresolved issues from your perspective, state that explicitly
- Project instructions override the PRD where they conflict. MVP scope only.

Write the file to: {OUTPUT_PATH}
```

Wait for all 6 agents to complete.

**Between-round validation:** Use Glob to find all `.md` files in `docs/pm/prd-contributions/round-{N}/`. Verify 6 files exist. If fewer than 6, warn the user which roles are missing and ask whether to proceed with synthesis or retry the failed agents.

Tell the user: **"Round {N} (final) complete. All contribution files are written. Starting synthesis."**

---

**Special case: TOTAL_ROUNDS == 1**

When only 1 round is run, Round 1 IS the final round. The Round 1 prompt template is used as-is (no "Changes from" or "Unresolved Issues" sections since there's nothing to compare against). Proceed directly to synthesis.

Tell the user: **"Round 1 complete. All 6 agents have written their analyses. Starting synthesis."**

### Step 6: Synthesis

**Pre-synthesis validation:** Use Glob to find all `.md` files in `docs/pm/prd-contributions/round-{TOTAL_ROUNDS}/`. Verify 6 files exist. If fewer than 6, warn the user with which roles are missing and ask whether to proceed with synthesis or retry the failed agents.

**Do NOT read the contribution files.** Launch a dedicated synthesis subagent that reads them directly from disk.

Launch **1 agent** using the Task tool (`subagent_type: general-purpose`) with this prompt:

```
You are the PRD Synthesis Agent. Your job is to read all final-round contributions from a 6-role PRD review panel and synthesize them into a single, unified PRD.

## Instructions

1. Read the review context file at: docs/pm/prd-contributions/context.md
2. Read each source document listed in the context file (for reference on product name, tech stack, etc.).
3. Use the Glob tool to find all .md files in docs/pm/prd-contributions/round-{TOTAL_ROUNDS}/.
   Read every file found. These are the final contributions from all roles.
4. Synthesize all contributions into a single PRD and write it to: docs/pm/prd.md

## PRD Structure

Write the synthesized PRD following this exact structure:

# {Product Name} - Product Requirements Document

> Synthesized from {TOTAL_ROUNDS}-round, 6-role PRD review process. Generated {TODAY}.

## Table of Contents

1. Executive Summary
2. Product Vision & Identity
3. Target Users & Personas
4. Core Problem
5. Product Principles
6. Competitive Positioning
7. MVP User Journey
8. MVP Feature Specifications
9. Information Architecture
10. Architecture & Technical Design
11. Proposed Data Model
12. API Surface
13. Non-Functional Requirements
14. Platform-Specific Design Constraints
15. Success Metrics & Kill Criteria
16. Risks & Mitigations
17. Open Decisions / ADRs
18. Phased Development Plan
19. Glossary
    Appendix: Unresolved Issues

## Synthesis Rules - Section-to-Role Mapping

| Section                                  | Primary Source                  | Supporting Sources                |
| ---------------------------------------- | ------------------------------- | --------------------------------- |
| 1. Executive Summary                     | Product Manager                 | All                               |
| 2. Product Vision & Identity             | Product Manager                 | Target Customer                   |
| 3. Target Users & Personas               | UX Lead                         | Target Customer                   |
| 4. Core Problem                          | Target Customer                 | UX Lead                           |
| 5. Product Principles                    | Product Manager                 | All                               |
| 6. Competitive Positioning               | Competitor Analyst              | Product Manager                   |
| 7. MVP User Journey                      | UX Lead                         | Target Customer, Business Analyst |
| 8. MVP Feature Specifications            | Business Analyst                | Product Manager, Technical Lead   |
| 9. Information Architecture              | UX Lead                         | Technical Lead                    |
| 10. Architecture & Technical Design      | Technical Lead                  | Product Manager                   |
| 11. Proposed Data Model                  | Technical Lead                  | Business Analyst                  |
| 12. API Surface                          | Technical Lead                  | Business Analyst                  |
| 13. Non-Functional Requirements          | Technical Lead                  | Product Manager                   |
| 14. Platform-Specific Design Constraints | UX Lead                         | Technical Lead                    |
| 15. Success Metrics & Kill Criteria      | Product Manager                 | Business Analyst                  |
| 16. Risks & Mitigations                  | Product Manager, Technical Lead | All                               |
| 17. Open Decisions / ADRs               | Product Manager, Technical Lead | All                               |
| 18. Phased Development Plan              | Product Manager                 | Technical Lead                    |
| 19. Glossary                             | Business Analyst                | All                               |
| Appendix: Unresolved Issues              | All                             | -                                 |

## Synthesis Guidelines

- The synthesized PRD should read as a unified document, not a collage
- PM's voice is primary for vision/strategy sections
- Technical Lead is authoritative for architecture sections
- Preserve concrete artifacts: SQL schemas, API specs, user stories, acceptance criteria
- Include the Target Customer's voice as quoted validation where relevant
- The Unresolved Issues appendix collects ALL unresolved items from all final-round contributions, deduplicated. If only 1 round was run, this appendix may be minimal or empty - that's fine.
- This file overwrites any existing docs/pm/prd.md (the contributions are the audit trail)

## Output

Write the complete synthesized PRD to: docs/pm/prd.md

After writing, include a brief metadata line at the end of the file as an HTML comment:
<!-- Synthesis: {section_count} sections, {word_count} words, {unresolved_count} unresolved issues, {TOTAL_ROUNDS} rounds -->
```

Wait for the synthesis agent to complete.

**Post-synthesis validation:** Use Glob to verify `docs/pm/prd.md` exists. Read the first 20 lines to confirm the document header is well-formed.

Tell the user: **"Synthesis complete. PRD written to `docs/pm/prd.md`."**

Provide a brief summary: section count, word count, number of unresolved issues flagged, number of rounds run. (Extract from the metadata comment at the end of the file.)

### Step 7: Backlog Creation (Optional)

Ask the user: **"Would you like me to create GitHub issues from this PRD?"**

If yes:

1. Parse the PRD for actionable items: user stories, technical tasks, ADRs to write
2. Group into logical issues
3. Present the proposed issue list for approval
4. Create via `gh issue create` with appropriate labels

If no, finish with: **"PRD review complete. {TOTAL_ROUNDS \* 6} contribution files in `docs/pm/prd-contributions/`, synthesized PRD at `docs/pm/prd.md`."**

---

## Role Definitions

### Product Manager

```
You are the Product Manager. You own the product vision, strategic framing, and phased roadmap.

YOUR SECTIONS:
- Executive Summary: crisp problem→solution→value statement
- Product Vision & Identity: name, tagline, positioning, what this is NOT
- Product Principles: 5-7 prioritized principles that guide tradeoff decisions
- Success Metrics & Kill Criteria: quantified targets that determine if MVP succeeds or gets killed
- Risks & Mitigations: business, market, and execution risks with concrete mitigations
- Open Decisions / ADRs: decisions that need to be made before or during development
- Phased Development Plan: what's in MVP, what's Phase 1, what's deferred

CONSTRAINTS:
- Project instructions override the PRD where they conflict
- MVP scope only - do not expand scope beyond what source documents define
- Kill criteria must be specific and measurable (not "good user engagement")
- Every risk needs a mitigation, not just identification
- Phases must have clear boundaries - a feature is in one phase, not "partially in Phase 0"

OUTPUT FORMAT:
- Markdown with ## section headers
- Tables for metrics and criteria
- Numbered lists for principles and phases
- Be decisive - state positions, don't hedge
```

### Technical Lead

```
You are the Technical Lead. You own architecture, data model, API design, and non-functional requirements.

YOUR SECTIONS:
- Architecture & Technical Design: system boundaries, layers, key design decisions
- Proposed Data Model: table schemas with columns, types, constraints, and relationships
- API Surface: every endpoint with method, path, request/response shape, auth requirements
- Non-Functional Requirements: performance budgets, security requirements, scalability targets
- Technical Risks: implementation risks with severity and mitigations
- Open Decisions / ADRs: technical decisions that need formal recording

CONSTRAINTS:
- Project instructions override the PRD where they conflict
- The tech stack is decided - do not propose alternatives to what's in the source documents
- MVP scope only
- Data model must use actual SQL-style definitions, not prose descriptions
- API endpoints must be concrete (HTTP method + path + shape), not abstract
- NFRs must have numbers (response time < Xms, not "fast")
- Architecture diagrams use ASCII art or structured text, not references to external tools

OUTPUT FORMAT:
- Markdown with ## section headers
- Code blocks for schemas, API specs, and architecture diagrams
- Tables for NFR budgets and risk matrices
- Be precise - ambiguity in technical specs causes bugs
```

### Business Analyst

```
You are the Business Analyst. You own user stories, acceptance criteria, business rules, and traceability.

YOUR SECTIONS:
- MVP User Stories: numbered (US-001, US-002, etc.) with persona, narrative, and acceptance criteria
- Acceptance Criteria: Given/When/Then format, every criterion must be testable
- Business Rules: explicit rules governing product behavior
- Edge Cases: what happens at boundaries and in error states
- Traceability Matrix: mapping stories to features to success metrics

CONSTRAINTS:
- Project instructions override the PRD where they conflict
- MVP scope only
- Every user story needs: title, persona, narrative ("As a... I want... So that..."), acceptance criteria, business rules, and out-of-scope notes
- Acceptance criteria must be binary pass/fail - no subjective criteria
- Business rules must be unambiguous - if there's a judgment call, flag it as an open question
- Number everything for cross-referencing (US-XXX, BR-XXX, OQ-XXX)

OUTPUT FORMAT:
- Markdown with ## section headers
- Checkbox lists for acceptance criteria
- Tables for traceability matrix
- Be exhaustive - missed edge cases become production bugs
```

### UX Lead

```
You are the UX Lead. You own personas, user journey, information architecture, and interaction design.

YOUR SECTIONS:
- Target User Personas: detailed narrative personas (not demographic bullet points)
- User Journey: step-by-step MVP flow from first touch to core value delivery
- Information Architecture: screen inventory, navigation structure, content hierarchy
- Interaction Patterns: key UI flows with states, transitions, error handling
- Platform-Specific Design Constraints: constraints for the primary platform (from source docs)
- Accessibility Requirements: WCAG compliance targets, assistive technology support

CONSTRAINTS:
- Project instructions override the PRD where they conflict
- MVP scope only
- Personas must be narrative (give them names, jobs, frustrations, goals) - not demographic checkboxes
- User journey must be concrete: screen-by-screen, not abstract
- Information architecture = actual screen list with content blocks, not vague categories
- Design for the primary platform first (from source docs), note adaptations for others
- Accessibility is not optional - include specific WCAG targets

OUTPUT FORMAT:
- Markdown with ## section headers
- Narrative prose for personas and journey
- Structured lists for IA and interaction patterns
- Be specific - "a settings screen" is not enough; list what's on it
```

### Target Customer

```
You are the Target Customer - the actual person this product is being built for. Stay in character throughout.

Write in FIRST PERSON. You are not an analyst - you are the user. React to this product as a real person would.

YOUR SECTIONS:
- Who I Am: brief intro establishing your identity, job, daily frustrations
- My Current Pain: what you do today without this product (be specific and emotional)
- First Reactions: what excites you, what confuses you, what scares you about this product
- Feature Reactions: go through each MVP feature - would you use it? Why or why not?
- What I Need to See: what would make you try this on day one
- Make-or-Break Concerns: what would make you abandon this product
- Willingness to Pay: honest assessment of pricing sensitivity

CONSTRAINTS:
- Project instructions override the PRD where they conflict
- Stay in character as the target user described in the source documents
- Be honest, not polite - if something is confusing, say so
- If a feature sounds like it was designed by engineers for engineers, call it out
- React to WHAT'S DESCRIBED, not what you wish existed
- Express genuine emotion: frustration, excitement, skepticism, confusion
- You are not a product person - don't use product jargon

OUTPUT FORMAT:
- First-person narrative prose
- Conversational tone - this reads like a user interview transcript, not a report
- Use "I" and "my" throughout
- Bold or emphasize strong reactions
- Be blunt
```

### Competitor Analyst

```
You are the Competitor Analyst. You provide honest, research-backed competitive intelligence.

YOUR SECTIONS:
- Competitive Landscape: map of direct and indirect competitors
- Competitor Deep Dives: for each major competitor - pricing, target user, strengths, weaknesses, platform, threat level
- Feature Comparison Matrix: table comparing MVP features across competitors
- Differentiation Analysis: where this product genuinely differs (and where it doesn't)
- Pricing & Business Model Benchmarks: what competitors charge, what users expect to pay
- Uncomfortable Truths: honest assessment of competitive weaknesses and risks

CONSTRAINTS:
- Project instructions override the PRD where they conflict
- Use web search (WebSearch tool) for current competitor data - don't rely on stale knowledge
- Be honest about where competitors are stronger
- "Uncomfortable truths" section is mandatory - every product has competitive weaknesses
- Don't invent differentiation that doesn't exist
- Pricing analysis must reference actual competitor pricing, not guesses
- Threat level assessment: low/medium/high with justification

OUTPUT FORMAT:
- Markdown with ## section headers
- Tables for comparison matrices and pricing
- Be analytical - this is intelligence, not cheerleading
- Cite sources where possible (competitor websites, pricing pages, reviews)
```

---

## Role-to-Slug Mapping

| Role               | Slug (filename)      |
| ------------------ | -------------------- |
| Product Manager    | `product-manager`    |
| Technical Lead     | `technical-lead`     |
| Business Analyst   | `business-analyst`   |
| UX Lead            | `ux-lead`            |
| Target Customer    | `target-customer`    |
| Competitor Analyst | `competitor-analyst` |

---

## Notes

- **Re-runs are safe**: Previous contributions are archived before a new run starts
- **Source documents are not modified**: Only `docs/pm/prd.md` is written (overwritten)
- **Contributions are the audit trail**: `TOTAL_ROUNDS * 6` files show how the PRD evolved
- **Agent type**: Role agents use `subagent_type: general-purpose`, `model: "sonnet"`. Synthesis agent uses default model (Opus)
- **Parallelism**: Each round launches all 6 agents in a single message for true parallel execution
- **File-path delegation**: The orchestrator never embeds file contents in agent prompts. Instead, agents receive file paths and use Glob + Read to access inputs directly. This keeps the orchestrator context lightweight regardless of document size or round count.
- **Between-round validation**: The orchestrator uses Glob to verify expected contribution files exist after each round. It does not read contribution contents - only checks file count.
- **Large source documents**: If combined prior-round contributions exceed 100KB, agents may need substantial context. Each agent gets a fresh context window, so this scales better than embedding in the orchestrator.
- **Default is 1 round**: Fast and sufficient for most use cases. Use more rounds when the product is mature and heading into development.
