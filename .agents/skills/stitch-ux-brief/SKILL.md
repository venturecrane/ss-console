---
name: stitch-ux-brief
description: Stitch UX Brief & Generation
---

# /stitch-ux-brief - Stitch UX Brief & Generation

Produces a production-grade UX brief through a three-reviewer iteration (product, design, customer persona), then commissions Stitch to generate three structurally distinct concepts at a single surface + viewport, iterates a winner with the user, expands to the full matrix of surfaces and viewports, and strips default-web gold-plating. Final artifacts land in `.stitch/` in the current venture repo.

Works in any venture console that has a Stitch project configured in `crane-console/config/ventures.json` (field: `stitchProjectId`). If one does not exist, this skill creates it and offers to write it back.

## Arguments

```
/stitch-ux-brief [target] [--rounds=3] [--generate=true]
```

- `target` — short name of the surface being designed (e.g., `client-portal`, `signup-flow`, `admin-inbox`). Required. Used for artifact file names.
- `rounds` — brief review rounds (default **3**). Each round spawns three parallel reviewers; output is synthesized between rounds. Fewer than 3 is not recommended.
- `generate` — whether to invoke Stitch after brief approval (default **true**). Set `false` to stop after the approved brief and generate later or by hand.

Parse the arguments. If `target` is missing, stop and ask the user what surface they want to brief.

## The methodology, in one paragraph

This skill exists because a UX brief handed to a generative design tool fails in predictable ways: the objectives are pretty but unmeasurable, the tokens are described in vibes instead of hex, the concepts all collapse into card-grid variations, and the generated output comes back padded with marketing chrome. This skill fixes each of those by running the brief through a critical three-reviewer pass (one of them a named customer who talks back), forcing structural divergence in the concept brief ("timeline vs archive vs action-centric, not three visual variations of the same layout"), harnessing Stitch's drift as intentional rather than policing it, and running an explicit strip pass on generated output. Every step has a learning embedded; the skill is the accumulated scar tissue of running this end-to-end.

## Phases

1. Intake — establish target, scope, existing context
2. Draft brief v1
3. Three-reviewer passes with synthesis
4. Decision checkpoints — resolve open questions surfaced by reviewers
5. Final brief, user-approved, saved to `.stitch/<target>-ux-brief.md`
6. Stitch project resolution (create or reuse)
7. Three structurally distinct concepts, one surface × one viewport
8. Convergence check — user picks winner or hybrid direction
9. Winner iteration (one targeted edit pass)
10. Matrix expansion (all remaining surfaces × viewports)
11. Gold-plating strip pass (batch edit, whitelist preserve)
12. Artifact consolidation in `.stitch/designs/<target>-vN/`

Each phase ends with a checkpoint. The user can stop at any checkpoint. Do not skip phases.

---

## Phase 1 — Intake

Ask the user these questions if they aren't already answered by the conversation:

1. **Target surface.** What surface are we designing? (e.g., "client portal home dashboard + invoice and proposal deep-link landings").
2. **Authentication context.** Is the user logged in? Prospect? Public-web visitor? This shapes the "no marketing chrome" framing.
3. **Customer persona seed.** Give me a realistic archetype — named if possible. Role, revenue range, tech comfort, what they care about. If the user hasn't thought about this, offer to draft one and confirm.
4. **Any existing brief, PRD, or prior design** to build on? Check for `docs/pm/prd.md`, `docs/design/*`, `.stitch/*-ux-brief.md`, or a prior version of the target.

Scan the repo for context:

- `CLAUDE.md` for venture positioning / tone rules
- `src/styles/globals.css` or `**/globals.css` for design tokens (hex values, type families)
- `tailwind.config.*` for palette and type scale
- Existing page at the target path (e.g., `src/pages/portal/*.astro`) for current data model and surface structure
- `.stitch/DESIGN.md` if present (established design system for the venture)
- `.stitch/NAVIGATION.md` if present (navigation specification — governs chrome in concept prompts and strip passes). If absent, warn: "Consider running `/nav-spec` first — briefs produce more consistent results when navigation is spec'd." Proceed without; briefs still have value.

Display an **Intake Summary** table:

| Field          | Value                                         |
| -------------- | --------------------------------------------- |
| Target         | _e.g., `client-portal`_                       |
| Authentication | _logged-in / prospect / public_               |
| Persona        | _e.g., Mike Delgado, 52, plumbing HVAC owner_ |
| Prior brief    | _path or "none"_                              |
| Tokens source  | _path to globals.css or config_               |
| Nav spec       | _spec-version N or "absent"_                  |
| Venture code   | _resolved from `crane_ventures`_              |
| Stitch project | _ID or "will create"_                         |

Present the table and ask: **"Does this capture the intake? Anything to correct before I draft?"**

Wait for confirmation.

---

## Phase 2 — Draft v1

Write a v1 brief to `.stitch/<target>-ux-brief.md` (create the directory if needed). Use this structure:

```markdown
# <Title> — UX Redesign Brief (v1)

## Context

<2-4 paragraphs: venture positioning, what this surface is for, why we're redesigning>

## Scope of this Stitch pass

<surfaces + viewports to be generated>

## Visit modes

<5-ish modes: action responder, status checker, etc. Be specific to this surface>

## Objectives, ranked

1. ... 2. ... 3. ... 4. ... 5. ...

## Entry points (with email context)

<each entry point, with the subject line and CTA from the email that triggers it>

## Design principles

<3-5 principles that constrain Stitch without dictating layout>

## Three concepts requested (structurally distinct, not visual variations)

A — <axis>
B — <axis>
C — <axis>

## Worked example (fidelity reference)

<one concept × one surface × one viewport, with inline type specs>

## Above-fold specs for B and C

<matching A's format>

## What must be preserved

<typography, palette, shape, voice>

## What is open

<layout, hierarchy, flow — full creative freedom>

## Anti-patterns (do not produce)

<bullet list of explicit no-gos>

## Mobile spec

<390×844, thumb zone, tap target, no hover, no horizontal scroll>

## Desktop spec

<1280px, right-rail placement, eye-level action>

## Contact affordance spec

<primary channel, fallback, SLA — operational commitment, not copy>

## Copy samples (tone calibration)

<5-6 lines showing the voice in concrete context>

## Error states (must design)

<per-surface error list — each includes named human + next step>

## Activity timeline schema

<if the surface has time-series data, define the event shape>

## Money rule

<dollar figures only, never bars or percentages, if applicable>

## Photo placeholder rule

<harder than "neutral placeholder" — use initials-in-circle or solid shape; never real faces>

## Accessibility floor

<WCAG 2.2 AA, focus rings with hex, landmarks, tap targets>

## Success criteria

**Primary acceptance test:** <one measurable design constraint — e.g., "on 390×844, [Pay invoice] button top edge at y ≤ 700px, no scroll">

Secondary: <2-4 testable criteria>

## Follow-ups (scheduled, not gaps)

<real priorities scheduled after this pass, each with a target window>

## Data available

<data fields the design can use; ask for flag if design needs data we don't capture>

## Constraints

<stack, viewports, scope limits>

## Approver

<name — Stitch output reviewed before any iteration>

## Appendix: Hard design tokens

### Color

<hex block — use exact values from globals.css or tailwind config>

### Typography

<family, weights, sizes, line-heights, tracking>

### Spacing and shape

<rounded, padding, rhythm, tap target, breakpoints>
```

Write the brief. Then present the draft to the user and proceed to Phase 3.

---

## Phase 3 — Three-reviewer passes

Run `TOTAL_ROUNDS` iterations. Each iteration spawns three parallel agents via the Task tool using `subagent_type: general-purpose`. The three reviewers are:

### Product manager reviewer

**Role prompt preamble:**

> You are a senior product manager at a [venture type] firm. You review UX briefs for client-facing surfaces. You are direct, critical, and do not validate work you find weak.

**Focus:**

- Are user objectives correctly framed and ranked?
- Are lifecycle states complete? What failure states are missing (declined, paused, expired, disputed, cancelled, overdue)?
- Are success criteria measurable? Name the primary metric.
- Is scope bounded for a realistic first pass?
- Does the data inventory match what the design is being asked to do?
- Business logic edge cases: multi-contact reality, partial payments, SOW revisions, out-of-portal artifacts
- Is "reach a human" spec'd as an affordance or left as a phrase?
- Are empty/error states required? Accessibility floor? Approver named?
- Is the deliverable list realistic for a first Stitch pass, or unbounded?

### UX designer reviewer

**Role prompt preamble:**

> You are a senior UX designer with deep experience using AI design tools — Stitch, v0, Magic Patterns, Figma Make. You know what makes them produce generic output vs considered work.

**Focus:**

- Are design tokens described precisely enough? Vague tokens ("muted violet") produce inconsistent runs across concepts. Require hex.
- Is mobile-first a slogan or a constraint? Specific viewport, thumb zone, tap targets, no-hover rules.
- Will three runs produce structurally distinct concepts, or three visual variations of the same layout? Commission structural divergence with explicit axis names.
- Is information hierarchy clear? What dominates, what recedes?
- Are anti-patterns named? Are placeholder instructions strong enough to prevent real-face drift?
- Predict what Stitch will produce as-is, including what's likely to go wrong.

### Customer persona reviewer

**Role prompt preamble (customize per surface):**

> You are <NAME>, <AGE>. You own <BUSINESS>. <BACKGROUND: years, team, revenue>. You are not <disqualifying tech trait> but you run a real business with real software. <SPOUSE/PARTNER> handles <ROLE>. You just <TRIGGERING EVENT>. Someone handed you a document that describes people like you — how you use [target surface], what you need from it. Tell them if it sounds right. Call out patronizing language. Flag missing things they don't know about your actual life. Talk plainly. Swear if you want. Don't bullet-point at the top; talk first, then summarize.

**Focus:**

- Does this sound like real me, or like someone who's never talked to someone in my role?
- What's patronizing? Soft? Over-explained?
- What's missing about my actual life? (spouse access, SMS vs email, what I'd show my advisor)
- What would I actually do on this surface vs what they think I'd do?

**Persona discipline:**

- Specific name, age, business details. "A business owner in the 30-55 age range" does not work. "Mike Delgado, 52, plumbing HVAC, 9 employees, $1.8M revenue, wife Elena does books" does.
- Give the persona permission to push back on the brief's description of them. The most common persona finding: the brief describes the user in ways no user would recognize.

### Round structure

**Round 1:** Each reviewer critiques v1 from their role. Output format:

```
## Overall assessment
[2-3 sentences, not diplomatic]

## Critical issues (ranked)
1. <issue + why it matters + specific fix>
2. ...

## Specific changes I'd make
<concrete rewrites with proposed wording>

## What's missing
<things the brief does not address that it needs to>
```

Persona reviewer uses a different format — see their prompt above. They talk first, then summarize in three sections: got right / got wrong / still missing.

**Between rounds:** Synthesize the feedback into a delta. Apply clear fixes directly to the brief. Surface disagreements or decisions that require user input as **open questions** — these become Phase 4 checkpoints.

**Round 2:** Reviewers see v2 plus a summary of what changed from v1. They critique v2 specifically — what's still weak, what new issues emerged, what v2 got wrong in addressing Round 1 feedback.

**Round 3:** Final polish pass. Reviewers are asked whether v3 is ready to ship. Format tightens to:

```
## Ready for Stitch? (Yes / Yes with caveats / No)
## Any remaining critical issues?
## Any last surgical edits?
```

**IMPORTANT**: All three reviewers in a round must be launched in a single Task tool message to run in parallel.

---

## Phase 4 — Decision checkpoints

After each round, surface any decisions the reviewers flagged that require user judgment. Common examples:

- An SLA promise in copy — is it an operational commitment or just marketing?
- A user mode that was named but doesn't get its own surface — fold it into another mode, or design for it?
- A concept that has an internal contradiction on a specific viewport — which way does it resolve?

Present decisions as a short numbered list with your recommendation and rationale. Do NOT present 10 decisions — filter to the ones that will actually change the brief.

Wait for the user's answers before proceeding.

---

## Phase 5 — Final brief saved

Apply the last round's fixes and the user's decisions. Save the final brief to `.stitch/<target>-ux-brief.md`. Present the user a summary of:

- Total rounds run
- Major things that shifted v1 → final
- Open decisions resolved
- The final brief is ready to hand to Stitch

Ask: **"Ready to generate? Or pause here?"**

If `--generate=false` or the user pauses, stop. The brief is the deliverable.

---

## Phase 6 — Stitch project resolution

Determine the venture code from the current repo (e.g., `ss-console` → `ss`).

**Resolve project ID:**

1. Call `crane_ventures` MCP tool. Find the venture. Read `stitchProjectId`.
2. If it's a non-null string: use it.
3. If it's `null`: tell the user no Stitch project exists for this venture, and ask if you should create one.

**To create:**

```
Create a Stitch project titled "<Venture Name> — <Target>" and capture the returned project ID.
```

Use `mcp__stitch__create_project` if available. If the Stitch MCP errors with `Incompatible auth server: does not support dynamic client registration` or similar (known transient state), fall back to the curl pattern:

```bash
KEY=$(infisical secrets get STITCH_API_KEY --path /vc --env prod --plain)
curl -sS -X POST https://stitch.googleapis.com/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "X-Goog-Api-Key: $KEY" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"create_project","arguments":{"title":"<title>"}}}'
```

Extract the project ID from `result.structuredContent.name` (format: `projects/<id>`).

**Write the ID back:** Edit `crane-console/config/ventures.json` — find the venture entry by `code`, set `stitchProjectId` to the new ID.

---

## Phase 7 — Three structurally distinct concepts

Generate three concepts at the **primary surface × mobile viewport only**. This is the convergence check — you verify the concepts actually diverge before spending on a full matrix.

For each concept, write a prompt file to `/tmp/stitch-prompt-<concept>.txt`. The prompt template:

```
<Concept letter and axis> — <viewport description, e.g., "Mobile home dashboard for client portal at 390×844">

<Paragraph explaining the concept's structural philosophy. What does it prioritize? What IS the page?>

NAV CONTRACT (REQUIRED):
[Only if .stitch/NAVIGATION.md exists. Read the matching surface-class
 appendix + archetype contract from NAVIGATION.md. Concatenate shared
 sections (a11y, states, anti-patterns). Inject using the template at
 ~/.agents/skills/nav-spec/references/injection-snippet-template.md.
 Size budget: ≤600 tokens.
 If NAVIGATION.md absent: OMIT this entire block. Describe header/footer
 inline in PAGE STRUCTURE as before (legacy behavior).]

DESIGN SYSTEM (REQUIRED):
- Platform: Web, mobile-first, 390x844 viewport only (or desktop 1280px)
- Palette: <hex values from the brief's Appendix>
- Typography: <families, weights, sizes>
- Shape: <rounding, pills>
- Density: <rhythm, tap targets, hover rules>
- Tone: <voice rules — no hype, no AI copy, etc>
- COLOR RULE (intentional): <any harnessed drift — e.g., "muted indigo family for metadata, not neutral slate">

PAGE STRUCTURE:
[When NAV CONTRACT is present, do NOT describe header/footer/back-button
 chrome here — the contract owns chrome. PAGE STRUCTURE describes content.]

1. <Dominant element — the concept's defining feature>
2. <Supporting elements>
3. <Consultant/contact block spec>
4. <What is explicitly absent>

Anti-patterns: <per-surface anti-patterns — from NAVIGATION.md §8 if present, otherwise from the brief>

Mood: <one sentence that captures the intent>
```

Fire all three generations in parallel (three Bash calls in one message, each with its own timeout of 330000ms for 5-minute Stitch generation).

**JSON-RPC payload:**

```json
{
  "jsonrpc": "2.0",
  "id": <n>,
  "method": "tools/call",
  "params": {
    "name": "generate_screen_from_text",
    "arguments": {
      "projectId": "<resolved ID>",
      "prompt": "<contents of prompt file>",
      "deviceType": "MOBILE"
    }
  }
}
```

Save each response to `/tmp/stitch-screen-<concept>.json`. Extract:

- Screen ID: `result.structuredContent.outputComponents[design].screens[0].id`
- Title: `result.structuredContent.outputComponents[design].screens[0].title`
- HTML URL: `result.structuredContent.outputComponents[design].screens[0].htmlCode.downloadUrl`
- PNG URL: `result.structuredContent.outputComponents[design].screens[0].screenshot.downloadUrl`
- Description: any `outputComponents[text]` entry

Download HTML and PNG for each to `.stitch/designs/<target>-v1/concept-<letter>.{html,png}`.

---

## Phase 8 — Convergence check

View the three PNGs. Compare.

**Question to answer, rigorously:** Are the three concepts structurally distinct, or have they collapsed into visual variations of the same layout?

Signal of structural divergence:

- Height ratios differ noticeably (e.g., one concept is ~60% the height of the others)
- Information hierarchy differs (what dominates the viewport is genuinely different)
- The "what IS the page?" answer differs per concept

Signal of collapse:

- All three are the same layout with different colors or card treatments
- The heights are within 10% of each other
- The dominant element is the same across all three

If collapse: the brief failed to force divergence. Iterate the concept prompts with harder axis constraints, regenerate. Do not proceed until divergence is real.

If divergence is good:

Write a review to the user:

```
## Concept A — <axis>
<summary of what it does, what worked, what didn't>

## Concept B — <axis>
<same>

## Concept C — <axis>
<same>

## My read
<lean toward one or a hybrid, with reasoning>
```

Ask the user: **"Which concept wins, or which hybrid do you want? Or do you want to re-commission the concepts with a different axis?"**

Wait for the user's answer.

---

## Phase 9 — Winner iteration

Once the user picks (single concept or hybrid), write an **edit** prompt that targets the existing screen(s) via `edit_screens`. The edit should:

- Explicitly preserve everything that works (action dominance, consultant block, etc.)
- Explicitly add the hybrid elements from other concepts
- Not redesign from scratch

Use `mcp__stitch__edit_screens` if MCP works, otherwise the curl equivalent:

```json
{
  "jsonrpc": "2.0",
  "id": 10,
  "method": "tools/call",
  "params": {
    "name": "edit_screens",
    "arguments": {
      "projectId": "<id>",
      "selectedScreenIds": ["<winner screen ID>"],
      "prompt": "<edit prompt>",
      "deviceType": "MOBILE"
    }
  }
}
```

Download the updated preview. Present to user. Confirm the iteration landed before expanding.

---

## Phase 10 — Matrix expansion

Generate the remaining surfaces × viewports for the winning concept.

Typical matrix:

- Remaining surfaces (2 at minimum — e.g., deep-link invoice landing, deep-link proposal landing) × 2 viewports (mobile, desktop) = 4 new screens
- Plus the desktop variant of the primary surface = 1 more

Total = usually 5 new screens beyond the winning mobile primary.

For each, write a prompt file following the template from Phase 7. **Key move**: include the winning concept's DNA in each prompt. If the winner is "C-hybrid" (action-centric with a timeline below), make sure every surface in the matrix follows that DNA, not a fresh Stitch reading of the brief.

Fire all generations in parallel (5 Bash calls in one message, each timeout 330000ms).

Extract, download, save to `.stitch/designs/<target>-v1/<surface>-<viewport>.{html,png}`.

---

## Phase 11 — Gold-plating strip pass

Stitch reliably adds default-web chrome that doesn't belong on an authenticated surface:

- Global navigation tabs ("Dashboard | Documents | Billing")
- Testimonial pull quotes and italicized customer-voice paragraphs
- Copyright footers and legal link rows (Privacy | Terms | Contact)
- Marketing CTAs ("Schedule a call", "Book a demo")
- Hero imagery and decorative illustrations
- Duplicated bottom-sticky action bars

Run a **batch edit** across all generated screens with a hardened strip directive. `edit_screens` supports an array of `selectedScreenIds` — pass all of them in one call.

**When `.stitch/NAVIGATION.md` exists:** generate the REMOVE IF PRESENT list from the spec's anti-patterns (§8) + the matching surface-class appendix's "Chrome forbidden" section, rather than hardcoding the list below. Generate the PRESERVE EXACTLY whitelist from the appendix's "Chrome allowed" section. This keeps strip-pass directives in sync with the spec — when the spec evolves, the strip pass follows automatically.

**When `.stitch/NAVIGATION.md` does NOT exist:** use the hardcoded strip directive below (legacy behavior).

**Strip directive template (customize per run):**

```
CONTEXT RESET: This is an authenticated <surface type> surface. The person viewing this is already logged in. <Describe their relationship to the product — existing customer, signed prospect, etc.>. This is NOT a marketing page. NOT a public website. NOT a landing page with a conversion goal. It is a working surface.

Your job: remove everything that was added as gold-plating or default web chrome, and touch nothing else. Do NOT redesign. Do NOT rebalance layouts. Do NOT reword preserved copy. Only remove.

REMOVE IF PRESENT:
[If NAVIGATION.md present: enumerate from §8 anti-patterns + surface-class
 appendix "Chrome forbidden" list. Each item is a concrete selector or
 description of what to remove.]
[If absent, use the legacy list:]
1. Global navigation tabs or menus
2. Testimonial paragraphs, pull quotes, italicized client-voice sentences
3. Copyright lines
4. Legal link rows
5. Marketing CTAs not in the original prompt
6. Hero imagery, illustrations, decorative graphics
7. Promotional banners, announcement bars, status indicators unrelated to the current task
8. Stickied bottom bars duplicating a visible primary action

PRESERVE EXACTLY:
[If NAVIGATION.md present: enumerate from the surface-class appendix
 "Chrome allowed" list. Each item is a concrete element description.]
[If absent:]
<Whitelist every element that should remain — the primary action, the amount display, every named section, the consultant block, all inline artifacts, the minimal header, every link that was explicit in the original prompt>

REPLACEMENT RULE: Do not replace what you remove. If removing creates empty space, leave it empty. The page ends where content ends.

BIAS: when in doubt, remove.
```

**Learning (hard-won):** the strip bias "when in doubt, remove" over-strips if you don't whitelist explicit preserves. Include the whitelist every time.

Download updated previews. Compare pre-strip and post-strip heights — a 15-30% reduction is expected when cruft was present. More than 40% means something legitimate was stripped.

Present the stripped set to the user. Note any over-strips (elements from the original brief that got removed). Decide: accept the cruft loss, targeted re-add, or accept the over-strip as the new baseline.

---

## Phase 12 — Artifact consolidation

Final state on disk:

```
<repo>/.stitch/
├── <target>-ux-brief.md             # Final brief
└── designs/
    └── <target>-v1/
        ├── concept-A.html           # Concepts from Phase 7
        ├── concept-A.png
        ├── concept-B.html
        ├── concept-B.png
        ├── concept-C.html
        ├── concept-C.png
        ├── <winner>-iterated.html   # Phase 9
        ├── <winner>-iterated.png
        ├── <surface>-<viewport>.html   # Matrix expansion from Phase 10
        ├── <surface>-<viewport>.png
        └── <surface>-<viewport>-stripped.html  # Phase 11 outputs
```

Write a short log to `.stitch/designs/<target>-v1/RUN-LOG.md` capturing:

- Date
- Target surface
- Rounds run
- Winning concept
- Decisions made by the user
- Known drifts / over-strips
- **nav-spec-version**: record the `spec-version` from `.stitch/NAVIGATION.md` front matter (or "absent" if no spec present). This anchors the generation to a specific spec version for future compatibility checks.
- Next follow-ups

Update `crane-console/config/ventures.json` with the `stitchProjectId` if it was created during this run.

Tell the user:

- How many screens were produced
- Where they live
- What's next (implementation, additional surfaces, another round of polish)

---

## Embedded learnings (tune these into every run)

1. **Personify the customer reviewer.** A named individual with real specifics produces sharper critique than a demographic. The persona should push back on the brief's description of them — that's usually where the brief is patronizing.

2. **Structural divergence must be commissioned explicitly.** "Three structurally distinct concepts — timeline-centric, archive-centric, action-centric" works. "Three concepts" collapses into variations.

3. **Tokens as hex, not vibes.** "Muted violet" is three different colors to three Stitch runs. Always include hex values from the venture's actual stylesheet.

4. **Mobile-first as constraint, not slogan.** Specify viewport (390×844), thumb zone (bottom 40%), tap target (44px), no-hover rule, no-horizontal-scroll rule. These are the constraints; "design mobile-first" alone is a wish.

5. **Harness drift, don't police it.** If Stitch's first pass does something unintended but coherent (muted-indigo dates instead of neutral slate), codify it in subsequent prompts rather than fighting it. The first-pass output is a design proposal you can accept into the spec.

6. **Anti-patterns need an affirmative frame.** "No nav tabs, no testimonials" is weaker than "This is an authenticated portal, not a marketing page. Chrome is strictly limited to what's listed below." Affirmative framing is what makes the strip pass work.

7. **Strip passes need whitelisted preserves.** Removal bias without a preserve whitelist over-strips. Always list every preserved element explicitly before stripping.

8. **Placeholder instructions are weak.** "Neutral portrait placeholder with caption 'consultant photo'" is routinely ignored — Stitch defaults to real faces. Either spec a solid shape ("solid indigo circle with initials") or a clearly abstract graphic ("geometric avatar, no face").

9. **Scope to 3 surfaces × 2 viewports.** A first Stitch pass generating more than ~6-8 screens is unreviewable. Converge on a concept first, then expand.

10. **Batch edits for consistency.** `edit_screens` supports arrays. One directive across many screens is cheaper and more consistent than per-screen edits.

11. **Desktop is an expansion, not a redesign.** Spec the right-rail pattern explicitly ("~340px wide, 160-200px from top, sticky, at eye level"). Otherwise Stitch improvises a desktop design that diverges from the mobile concept.

12. **MCP fallbacks matter.** The Stitch MCP occasionally enters a state where it reports connected but every call fails with an OAuth error. The endpoint works with the API key via direct HTTP. Always include the curl fallback path so a broken MCP doesn't block the run.

---

## Dependencies and conventions

- **Stitch project ID** resolved via `crane_ventures` MCP tool. Writes back to `crane-console/config/ventures.json`.
- **API key** stored in Infisical at `/vc` (prod env, key `STITCH_API_KEY`). Never echo the value.
- **Artifact directory** is `.stitch/designs/<target>-v<n>/` — use versioned subfolders so prior runs are preserved.
- **Brief filename** is `.stitch/<target>-ux-brief.md`.
- **Run log** is `.stitch/designs/<target>-v<n>/RUN-LOG.md`.

---

## Versioning

When iterating a target that already has `.stitch/<target>-ux-brief.md` and `.stitch/designs/<target>-v1/`:

- The new run creates `<target>-v2/`, `<target>-v3/`, etc.
- The brief overwrites (with the prior version moved to `.stitch/<target>-ux-brief-v1.md` if substantive changes).
- The run log records "v2 vs v1: what changed and why."
