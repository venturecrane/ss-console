---
description: Named navigation patterns drawn from Nielsen Norman Group, Material Design 3, Apple Human Interface Guidelines, and other established design systems. nav-spec chooses from this catalog; it never invents.
---

# Pattern Catalog

Every navigation choice in a venture's `NAVIGATION.md` must be a specialization of a pattern from this catalog. Inventing a new pattern without rationale against these is a violation of the `nav-spec` principle of **anchoring**.

When a pattern is selected, record:

- **Pattern name** (from this catalog, verbatim)
- **Source** (NN/g, Material, HIG, or industry convention)
- **Rationale** (why this pattern fits this `{surface × archetype}`)
- **Variant** (if a subpattern is being applied)
- **Required elements** (what this pattern mandates — copied into the spec's chrome contract)

If two patterns are combined (e.g., "hub-and-spoke within a persistent-context workspace"), record both and describe how they compose.

---

## 1. Structural patterns (NN/g taxonomy)

These describe how destinations relate to each other.

### 1.1 Hub-and-spoke

> Central surface (the hub) links to several peer destinations (spokes). User returns to the hub between tasks. Spokes do not link to each other directly.

**Source:** Nielsen Norman Group — "Hub and Spoke: A Better Option for Mobile Navigation" (Mobile UX research).

**Use when:**

- The product has 3–7 top-level tasks that users switch between
- Users tend to complete one task then return to orientation before starting another
- Mobile-first: preserves simplicity without a persistent bottom nav

**Don't use when:**

- Users need to rapidly switch between sections mid-task (prefer tabs/bottom nav)
- > 7 top-level destinations (prefer drawer or rail)

**Required elements:**

- Hub surface (typically a dashboard archetype) with visible entry points to every top-level spoke
- Each spoke has a back affordance targeting the canonical hub URL (not `history.back()`)
- No sibling-to-sibling links between spokes in the base nav

**Common variants:**

- **Hub with dominant action** — hub additionally surfaces a single contextually primary action (e.g., "Pay invoice" card on portal home)
- **Hub with recent activity** — hub surfaces a time-ordered list of recent events alongside entry points

**Reference implementations:** iOS Settings app (first-level), typical SaaS client portals, Gmail's folder view on mobile.

---

### 1.2 Nested doll (hierarchical)

> Destinations organized as a strict tree. User drills in and uses back/breadcrumbs to ascend. Each level contains its children and nothing else.

**Source:** Nielsen Norman Group — "Mobile Navigation Patterns."

**Use when:**

- Content has a clear parent-child relationship
- Destination count is large but organized by category
- Desktop admin consoles with section > subsection > detail

**Don't use when:**

- Users need to jump between distant branches (prefer faceted or tag)
- Hierarchy is artificial — forcing a tree creates false affordances

**Required elements:**

- Every non-root surface carries either a back affordance OR breadcrumbs
- Breadcrumbs reflect the actual parent chain, not a synthetic one
- Ascent from a leaf must be possible in one action (click on parent in breadcrumb, or back button)

**Common variants:**

- **Breadcrumb-dominant** — ascent is via breadcrumbs, no standalone back button
- **Back-dominant** — ascent is via back button; breadcrumbs absent (portal convention)

---

### 1.3 Sequential (step-by-step)

> Linear flow with explicit forward/back. Each step depends on the previous. Progress indicator shows position.

**Source:** Nielsen Norman Group — "Wizard Design Pattern."

**Use when:**

- Task has mandatory ordering (onboarding, multi-step form, checkout)
- Users shouldn't be able to skip ahead without consequence
- Completion state matters ("you finished step 3 of 7")

**Don't use when:**

- Steps are independent (prefer single form with sections)
- Users need to revise any step at any time (prefer single-page form)

**Required elements:**

- Progress indicator showing N of M
- Previous button (except on step 1)
- Next/Submit button
- Cancel affordance returning to parent context with confirmation if data entered
- Dirty-state guard on navigation away

**Common variants:**

- **Linear wizard** — strict sequence
- **Branching wizard** — step selection depends on prior answers

---

### 1.4 Pyramid (pagewise)

> List or collection where each item links prev/next among siblings and up to the parent. Think article series or a photo gallery.

**Source:** Nielsen Norman Group — "Mobile Navigation Patterns."

**Use when:**

- Content is a sibling set with meaningful order (chronological, alphabetical, category-internal)
- Users benefit from iteration (read article 1, then 2, then 3)

**Don't use when:**

- Siblings have no meaningful order
- The content is bite-sized and the list view is sufficient

**Required elements:**

- Parent (list) link, typically as a back affordance
- Previous sibling link (if applicable)
- Next sibling link (if applicable)
- Position indicator (e.g., "2 of 12")

---

### 1.5 Faceted

> A large index filtered by orthogonal dimensions (facets). User selects filter/sort values; results update. URL captures filter state so results are shareable.

**Source:** Nielsen Norman Group — "Filters vs. Facets," Endeca research.

**Use when:**

- The index has >30 items
- Users search by multiple independent properties (status, date, type, assignee)
- Results are worth sharing (filter state in URL)

**Don't use when:**

- The index has <10 items (a simple list is better)
- Facets are highly correlated (overlap confuses filtering)

**Required elements:**

- Filter chips or sidebar showing active facets
- Clear state preservation in URL
- "Clear all" affordance
- Result count visible
- Empty-state handling when filters eliminate all results

**Common variants:**

- **Inline facets** (chips above list)
- **Sidebar facets** (collapsible left rail on desktop)
- **Bottom-sheet facets** (mobile, invoked from a filter button)

---

### 1.6 Tag (flat)

> All destinations are siblings at a single level. No hierarchy.

**Source:** Nielsen Norman Group — "Mobile Navigation Patterns."

**Use when:**

- The product has ≤5 top-level destinations
- All destinations are used with similar frequency
- No meaningful grouping exists

**Don't use when:**

- > 5 destinations (creates cognitive load)
- Destinations have meaningful hierarchy (prefer nested-doll or hub-and-spoke)

**Required elements:**

- A persistent nav (tab bar, bottom nav, or navigation rail) with all top-level destinations visible
- Active-state indication via `aria-current="page"`

---

## 2. Material Design 3 navigation components

Material specifies which component to use based on destination count and device class.

### 2.1 Top app bar

> The persistent band at the top of every surface. Contains title, contextual actions, and optionally a navigation icon.

**Source:** Material Design 3 — Top app bar guidelines.

**Always used.** Every surface has a top app bar unless it's a fullscreen task (camera, media playback, transient modal).

**Variants:**

- **Center-aligned** — title centered; best for simple hierarchy
- **Small** — default; title left-aligned
- **Medium/Large** — title wraps to second row; use for detail surfaces where context matters

### 2.2 Bottom navigation bar

> Fixed band at the bottom of mobile screens showing 3–5 top-level destinations.

**Source:** Material Design 3 — Bottom navigation.

**Use when:**

- Mobile viewport
- 3–5 top-level destinations
- Users switch frequently between sections

**Don't use when:**

- Desktop viewport (prefer rail or drawer)
- <3 or >5 destinations
- Sections are hub-and-spoke (bottom nav forces flat thinking)

**Required elements:**

- All destinations labeled
- Active state obvious (filled icon, color, indicator line)
- `role="navigation"` and `aria-label`

### 2.3 Navigation rail

> Narrow vertical strip on the side of tablet/desktop showing 3–7 top-level destinations.

**Source:** Material Design 3 — Navigation rail.

**Use when:**

- Viewport ≥600px wide
- 3–7 top-level destinations
- Alternative to bottom nav at larger sizes

### 2.4 Navigation drawer

> Side panel (typically left) listing 5+ top-level destinations. May be persistent (always visible on desktop) or modal (slides in from edge on mobile).

**Source:** Material Design 3 — Navigation drawer.

**Use when:**

- > 5 top-level destinations
- Destinations benefit from grouping or dividers
- Desktop: persistent drawer; mobile: modal drawer invoked from menu icon

**Don't use when:**

- <5 destinations (prefer tabs, rail, or bottom nav)
- Destinations are hub-and-spoke (drawer hides the hub relationship)

### 2.5 Tabs

> Horizontal bar of ≤5 categories within a single destination. NOT for top-level app navigation.

**Source:** Material Design 3 — Tabs.

**Use when:**

- Within a detail surface, for related content categories (Activity / History / Payments on an invoice detail)
- Categories are peers that share context

**Don't use when:**

- Switching changes the surface (that's nav, not tabs)
- > 5 categories

### 2.6 Segmented button

> A button group selecting between views/filters within a single destination.

**Source:** Material Design 3 — Segmented button.

**Use when:**

- ≤4 mutually exclusive view modes (e.g., Day / Week / Month)
- Switching doesn't cause navigation

---

## 3. Apple Human Interface Guidelines

For native iOS/iPadOS feel when relevant.

### 3.1 Tab bar

> Fixed bottom bar with 2–5 top-level destinations.

**Source:** Apple HIG — Tab bars.

**Use when:** Same rules as Material's bottom navigation, with iOS visual conventions.

### 3.2 Split view

> Master-detail layout on tablet/desktop: list on the left, detail on the right. Both always visible.

**Source:** Apple HIG — Split views.

**Use when:**

- Viewport ≥768px
- Detail depends on a current selection from the list
- Mail, messaging, file browsers

**Don't use when:**

- Mobile viewport (collapse to stacked list-then-detail with back)

### 3.3 Navigation stack

> Drill-down sequence with automatic back button showing parent title.

**Source:** Apple HIG — Navigation bars and Navigation stacks.

**Use when:** Hierarchical content on iOS/iPadOS native feel.

### 3.4 Modal

> Focused task presented over the current surface, dismissible with close button or swipe-down.

**Source:** Apple HIG — Modality.

**Required elements:**

- Escape (Esc key) closes
- Click-outside closes
- Close button visible
- Focus returns to the trigger element on close
- Body scroll locked while open

---

## 4. Industry-standard composite patterns

These are widely adopted compositions of the above.

### 4.1 Master-detail

> List (master) and detail views. Mobile: stacked — tap list item to navigate to detail, back to list. Desktop: split-view.

**Composes:** Nested doll + Split view.

**Required elements:**

- List has stable sort
- Detail has back-to-list affordance with canonical URL
- Selection state preserved on return (scroll position, highlighted item)

### 4.2 Index + preview

> List with inline preview pane. Clicking an item updates the preview without navigating.

**Composes:** Master-detail + Progressive disclosure.

**Use when:**

- Detail is lightweight and doesn't need its own URL
- User benefits from scanning multiple items quickly

### 4.3 Overlay / modal-on-list

> List view where "open" uses a modal overlay instead of navigating.

**Composes:** Modal + list archetype.

**Use when:**

- Detail is short-lived (confirm, compose, quick-edit)
- Preserving list scroll/filter state is important

### 4.4 Persistent-context workspace

> A selected entity (client, tenant, project) persists across navigation. All subsequent surfaces operate within that context. Context is visible in chrome and in URL.

**Composes:** Hub-and-spoke or nested-doll, scoped to a current entity.

**Use when:**

- Multi-tenant admin consoles
- Project-scoped tools (Linear, Jira, Notion)

**Required elements:**

- Context selector/switcher
- Context visible in header
- Context in URL path (e.g., `/admin/clients/<id>/...`)
- Context-switch invalidates in-flight state

### 4.5 Progressive disclosure

> Show summary by default; expand to reveal detail on demand. Accordion, "show more" link, expanded card.

**Source:** Nielsen Norman Group — Progressive Disclosure.

**Use when:**

- Detail is nice-to-have, not always needed
- Page would be overwhelming with everything expanded
- Common actions first, secondary actions behind a menu

### 4.6 Command palette / search-first

> Global search (typically invoked with `Cmd+K`) acts as primary navigation. Users navigate by typing.

**Use when:**

- Product has many destinations
- Power users benefit from keyboard-first nav
- Linear, Notion, VS Code

**Required elements:**

- Keyboard shortcut to invoke (platform-appropriate)
- Fuzzy match with ranking
- Recent items surfaced
- Destinations and actions both searchable

---

## 5. Selecting a pattern

For each `{surface class × archetype}` in the venture, the author chooses ONE primary pattern from this catalog, names a runner-up, and defends the choice against task-model inputs.

**Pattern selection is governed by disqualifiers, not by a free-form decision table.** See [pattern-disqualifiers.md](pattern-disqualifiers.md) for the citation-anchored disqualifier conditions that the validator (rule R25) applies to every declared pattern against the task-model inputs (`return_locus`, `destination_count`, `task_ordering`, etc.).

Workflow:

1. Draft the task model per [task-model-template.md](task-model-template.md) — including the required `evidence_source` and `return_locus` columns.
2. For the target `{surface × archetype}`, propose a pattern from this catalog.
3. Run `python3 ~/.agents/skills/nav-spec/validate.py --check-pattern-fitness --spec .stitch/NAVIGATION.md` against the draft.
4. If R25 fires, either switch to a surviving pattern or mount an override (cited input values + ≥2-of-3 reviewer consensus; see [workflows/author.md](../workflows/author.md) Phase 4).

The decision guide below remains as a hint for the initial proposal only. **It is not authoritative** — pattern-disqualifiers.md is. Patterns flagged here as "start with" may still be disqualified by R25 given specific task-model inputs.

Initial-proposal hints (non-authoritative):

| Situation                                                                               | Start with                             |
| --------------------------------------------------------------------------------------- | -------------------------------------- |
| 3–7 top-level destinations, mobile-first, task-centered, hub is the common return point | Hub-and-spoke                          |
| Strict tree of content with depth ≥3                                                    | Nested doll                            |
| Task with mandatory order                                                               | Sequential                             |
| Index with ≥30 items, filterable                                                        | Faceted                                |
| ≤5 top-level destinations, frequent switching between sections                          | Persistent-tabs (bottom nav / tab bar) |
| >5 top-level destinations                                                               | Drawer (mobile) / Rail (desktop)       |
| Category within a detail                                                                | Tabs (inside detail)                   |
| Mutually exclusive views of the same data                                               | Segmented button                       |
| Detail that shouldn't leave list context                                                | Modal-on-list                          |
| Scoped-by-entity admin tool                                                             | Persistent-context workspace           |
| Heavy-keyboard power-user tool                                                          | Command palette                        |

If nothing in the catalog fits, stop and question the design — not invent. Re-read the task model; most "novel" patterns are specializations of entries above.

---

## 6. Anti-patterns (do not use)

These patterns appear repeatedly in misaligned designs and are forbidden unless explicitly ratified by the venture with rationale:

- **Hamburger menu as primary nav on desktop** (NN/g research: reduces discoverability)
- **Both bottom nav AND drawer** (redundancy that confuses)
- **Tabs that change the URL beyond a hash fragment** (that's navigation, not tabs)
- **Breadcrumbs that synthesize from category tags** (not a real hierarchy)
- **Back button using `history.back()`** (breaks for deep-linked arrivals)
- **Infinite scroll as the only navigation** (loss of place, no URL for specific items)
- **Carousels as primary navigation** (low discoverability, accessibility issues)

See also: [anti-patterns.md](anti-patterns.md) for chrome-level anti-patterns.
