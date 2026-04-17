---
description: Citation-anchored disqualifier conditions for each pattern in pattern-catalog.md. Read by validator rule R25. Every disqualifier cites a primary source (NN/g, Material Design 3, Apple HIG) or carries a HEURISTIC UNTESTED tag.
---

# Pattern Disqualifiers

The rule the skill enforces: **a pattern is disqualified for a given `{surface × archetype}` when its primary-source description is contradicted by the task model's declared inputs.** This is not a score, not a weight, not a heuristic table — it is a yes/no check against a cited source. If no catalog entry anchors a check, it carries the `HEURISTIC: UNTESTED` tag and is advisory only.

Read by validator rule R25 (`validate.py::check_pattern_disqualifiers`). Phase 4 of `workflows/author.md` runs `validate.py --check-pattern-fitness` against the draft spec before the four-reviewer pass.

## How a disqualifier is written

Each disqualifier is a tuple of:

- **ID** — `D1`, `D2`, ... unique across this document. IDs are stable; new disqualifiers append.
- **Predicate** — an expression over task-model inputs (`return_locus`, `destination_count`, `task_ordering`, `index_size`, `max_tree_depth`, `viewport`) that the validator can evaluate mechanically.
- **Rationale** — one sentence tying the predicate to the anchor's description.
- **Anchor citation** — specific source, section, and (where possible) quoted text.

Patterns without a disqualifier for a given input are silent on that input. The validator fires R25 only when an explicit disqualifier evaluates to true.

## Task-model input vocabulary

These terms appear across disqualifiers and are sourced from the task model (Section 1 of `NAVIGATION.md`):

- `return_locus` ∈ {hub, last-visited-surface, external, new-destination}
- `top-3-by-frequency` — the three tasks with the highest `frequency` values (ranked desc; ties broken by `criticality`). Stable against author reclassification (no `primary`/`secondary` flag to toggle).
- `destination_count` — count of top-level siblings in the sitemap under this surface class
- `task_ordering` ∈ {independent, mandatory_sequence, parallel}
- `index_size` — maximum collection size on list archetypes declared in Section 3
- `max_tree_depth` — longest ancestor chain declared in Section 3's reachability matrix
- `viewport` ∈ {mobile, desktop} — from the classification tag

---

## hub-and-spoke

**Anchor.** Nielsen Norman Group — Raluca Budiu, _Navigation You Can Stick With_, NN/g 2018; cross-referenced in NN/g _Mobile Navigation Patterns_ article series. Pattern also codified as "Hub-and-Spoke" in NN/g Mobile UX research (2015–2020).

**Source description (quoted).**

> "In a hub-and-spoke layout, the home page serves as a hub and links out to various sections of the site (the spokes). Users return to the hub to navigate between sections." — NN/g, Mobile Navigation Patterns

**Disqualified when:**

### D1. ≥2 of the top-3-by-frequency tasks declare `return_locus ∈ {external, last-visited-surface, new-destination}`

**Rationale.** The NN/g description explicitly requires that users "return to the hub to navigate between sections." If two or more of the most-frequent tasks terminate elsewhere (external vendor, prior surface, a new destination), the hub is not the common return point the pattern requires.

**Anchor citation.** NN/g _Mobile Navigation Patterns_ — "Hub-and-spoke navigation works when users tend to complete one task, then return to the home screen before beginning another." Contradicted by ≥2-of-3 non-hub return loci.

**Note on counting.** "Top-3-by-frequency" ranks tasks by the `frequency` column in Section 1's task table, descending. This is stable against the author demoting a task's nominal "primary" label — there is no primary flag; frequency alone determines inclusion.

### D2. `destination_count > 7`

**Rationale.** NN/g guidance caps hub affordance count before the hub loses its landing function. Above 7 destinations, the hub becomes a dense index — users scan rather than orient, and the pattern's core value (quick re-orientation between tasks) degrades.

**Anchor citation.** NN/g _Hub-and-Spoke_ guidance: "The pattern works best with a small number of peer destinations (typically 3–7)." Above the upper bound, NN/g recommends drawer or navigation rail instead.

---

## persistent-tabs

This entry covers both Material Design 3's bottom navigation bar (mobile) and top tabs / tab bar (desktop), and Apple HIG's Tab Bar component. Disqualifiers differ by viewport.

**Anchor.**

- Material Design 3 — _Bottom navigation_ component specification. `https://m3.material.io/components/navigation-bar/guidelines`
- Material Design 3 — _Primary navigation tabs_. `https://m3.material.io/components/tabs/guidelines`
- Apple Human Interface Guidelines — _Tab bars_. `https://developer.apple.com/design/human-interface-guidelines/tab-bars`

**Source descriptions (quoted).**

> "Navigation bars offer a persistent and convenient way to switch between primary destinations in an app. Use three to five destinations that are of equal importance." — Material Design 3, Bottom navigation

> "Use a tab bar to organize information in your app at a high level. A tab bar appears at the bottom of the screen and lets people quickly switch among the main sections of your app." — Apple HIG, Tab bars

**Disqualified when:**

### D3. `destination_count > 5` AND `viewport = mobile`

**Rationale.** Material 3 caps bottom navigation at 5 destinations. Beyond 5, Material 3 recommends navigation drawer.

**Anchor citation.** Material Design 3 _Bottom navigation_ — "Use three to five destinations." Apple HIG _Tab bars_ — "Avoid displaying more than five tabs at the top level."

### D4. `destination_count > 7` AND `viewport = desktop`

**Rationale.** Desktop top-tab patterns tolerate more destinations than mobile bottom-nav but still degrade above ~7 when tab labels begin to truncate or wrap. Above 7, Material 3 recommends navigation rail or drawer.

**Anchor citation.** Material Design 3 _Primary tabs_ — "Primary tabs should represent a small number of related destinations."

### D5. `task_ordering = mandatory_sequence`

**Rationale.** Tabs imply peer destinations with independent access order. A mandatory sequence (wizard) requires ordered progression; exposing all steps as tabs contradicts the pattern's premise of free navigation.

**Anchor citation.** Material Design 3 _Tabs_ guidelines — "Tabs organize content into categories at the same level of hierarchy, allowing users to switch between them." HIG _Tab bars_ — "Don't use a tab bar to present a set of sequential steps."

---

## sequential

**Anchor.** Nielsen Norman Group — _Wizard Design Pattern_ / _Multistep forms_. Also codified in Apple HIG _Multistep Tasks_ and Material 3 _Stepper_ component.

**Source description (quoted).**

> "A wizard is a linear, step-by-step form that leads users through a complex task or process." — NN/g, Wizards: Definition and Usability Guidelines

**Disqualified when:**

### D6. `task_ordering ≠ mandatory_sequence`

**Rationale.** The sequential pattern's defining property is that steps must be completed in order. Without that constraint, sequential is pure friction — use hub-and-spoke (for independent tasks) or nested-doll (for hierarchical browsing).

**Anchor citation.** NN/g _Wizard Design_ — "Wizards are appropriate only when the task has a mandatory sequence and each step depends on the previous."

---

## nested-doll

**Anchor.** Nielsen Norman Group — _Mobile Navigation Patterns_ — "Nested Doll" (hierarchical / drill-down).

**Source description (quoted).**

> "In the nested-doll navigation, the user moves in and out of increasingly specific content, along a single path." — NN/g, Mobile Navigation Patterns

**Disqualified when:**

### D7. `max_tree_depth < 3`

**Rationale.** Nested-doll's value is in traversing deep hierarchies. A tree with depth <3 (hub → section → item) has only two meaningful levels and is better served by hub-and-spoke (for ≤7 siblings) or faceted (for large flat indexes).

**Anchor citation.** NN/g — "The nested-doll pattern is best for deep content hierarchies where users need to drill in several levels."

---

## faceted

**Anchor.** Nielsen Norman Group — _Filters vs. Facets_; _Faceted Search/Browse Interfaces_.

**Source description (quoted).**

> "Faceted navigation provides users multiple filter dimensions that can be combined to narrow a large content set." — NN/g, Filters vs. Facets

**Disqualified when:**

### D8. `index_size < 30` — `HEURISTIC: UNTESTED`

**Rationale.** Faceted navigation's overhead (filter UI, facet counts, filter state management) is justified only when the index is large enough that scanning is impractical. Below ~30 items, a sortable list with inline filters is typically sufficient.

**Anchor citation.** NN/g _Filters vs. Facets_ discusses when faceted search is appropriate but does not cite a specific numeric threshold. The `<30` threshold is a heuristic drawn from common industry practice (Amazon, Booking, Zillow all surface facets only at scale) but is NOT anchored to a published number. Tag: `HEURISTIC: UNTESTED`.

---

## pyramid

**Anchor.** Nielsen Norman Group — _Mobile Navigation Patterns_ — "Pyramid Pattern" (previous/next within a siblings set, with index above).

**Source description (quoted).**

> "The pyramid pattern allows users to navigate between siblings in a linear set without returning to the index." — NN/g, Mobile Navigation Patterns

**Disqualified when:**

### D9. Siblings have no canonical ordering (`task_ordering ∈ {independent, parallel}`) AND `destination_count > 5` — `HEURISTIC: UNTESTED`

**Rationale.** Pyramid's previous/next affordance is useful when siblings form an ordered sequence (chapters of a book, steps of an article series). Without ordering, "next" is arbitrary; above ~5 siblings, the ordering becomes opaque.

**Anchor citation.** NN/g describes the pyramid pattern qualitatively. No numeric threshold for destination_count is published. Tag: `HEURISTIC: UNTESTED`.

---

## persistent-context / workspace

Composite pattern: a global scoping control (entity selector) combined with a pattern nested underneath it. Used in admin tools where all operations are scoped to a selected entity across the session.

**Anchor.** Not a named NN/g / Material / HIG pattern. Industry composite, documented with a HEURISTIC tag.

**Disqualified when:**

### D10. `entity_scope = false` — `HEURISTIC: UNTESTED`

**Rationale.** Persistent-context assumes users operate on one entity across a session. Without entity-scoping, the global scoping control is dead weight.

**Anchor citation.** No primary source. Tag: `HEURISTIC: UNTESTED`.

---

## Worked example — ss-console portal `session-auth-client/dashboard`

Declared pattern in `.stitch/NAVIGATION.md` §4.4 (v2): `hub-and-spoke with dominant-action + recent-activity variants`.

Task-model inputs from §1.4.1 (task model, v3-migrated):

| Task                 | Frequency | return_locus         | return_locus_evidence                                                                      |
| -------------------- | --------- | -------------------- | ------------------------------------------------------------------------------------------ |
| pay-invoice          | high      | external             | vendor URL: `https://checkout.stripe.com/*` (cited SOW §4)                                 |
| review-sign-proposal | high      | external             | vendor URL: `https://app.signwell.com/*` (cited SOW §3)                                    |
| see-whats-happening  | high      | hub                  | redirect_to /portal cited at src/pages/portal/index.astro:332 (structural evidence type 1) |
| find-document        | medium    | last-visited-surface | no auto-return implemented                                                                 |
| check-progress       | medium    | hub                  | redirect_to /portal cited at src/pages/portal/engagement/index.astro:101                   |
| contact-consultant   | low       | external             | mailto/tel/sms schemes                                                                     |

Top-3-by-frequency: pay-invoice, review-sign-proposal, see-whats-happening.

- pay-invoice: `return_locus = external` ✗
- review-sign-proposal: `return_locus = external` ✗
- see-whats-happening: `return_locus = hub` ✓

Count of top-3 with `return_locus ≠ hub`: **2**. D1 threshold is `≥2`. **D1 fires.**

D2 check: `destination_count = 4` (quotes, invoices, documents, engagement). D2 threshold is `>7`. D2 does not fire.

**R25 output:**

```
R25 Pattern disqualifier fired on session-auth-client/dashboard
  Declared pattern: hub-and-spoke
  Disqualifier: D1 (NN/g Mobile Navigation Patterns) —
    2 of the top-3-by-frequency tasks (pay-invoice, review-sign-proposal)
    have return_locus=external, not hub
  Surviving patterns evaluated:
    - persistent-tabs: D3 false (destination_count=4 ≤ 5), D4 n/a (viewport=mobile),
      D5 false (task_ordering=independent) → SURVIVES
    - nested-doll: D7 fires (max_tree_depth=2) → DISQUALIFIED
    - faceted: D8 fires (index_size < 30 across sections) → DISQUALIFIED (HEURISTIC)
    - sequential: D6 fires (task_ordering=independent) → DISQUALIFIED
  Surviving patterns: persistent-tabs
```

Override paths (per R25 spec):

- (a) Defense + ≥2/3 reviewer consensus — defense must cite specific return_locus values that a disqualifier miscounted. In this case the miscounting claim is hard: Stripe and SignWell URLs are unambiguous external terminals.
- (b) Switch declared pattern to `persistent-tabs`.
- (c) In provisional mode only: file `.stitch/provisional-override-<date>.md` with deferred validation event + date ≤90 days.

---

## Amending this document

New disqualifiers append at the end with the next D-number. Existing IDs never change (R25 may reference them in violations recorded in prior spec versions).

When a disqualifier tagged `HEURISTIC: UNTESTED` becomes anchored (e.g., a new NN/g article codifies the threshold), remove the tag and update the citation. Keep a brief change log at the bottom of this file.

## Change log

- 2026-04-16 — initial v3 authoring. D1–D10 established. D8, D9, D10 carry HEURISTIC: UNTESTED tags pending better sourcing.
