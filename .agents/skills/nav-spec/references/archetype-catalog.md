# Archetype catalog

Ten archetypes. Each archetype maps to one or more named patterns from [pattern-catalog.md](pattern-catalog.md) and defines both IA contract (what the surface must link to) and chrome contract (what it must look like). Every generated or shipped page maps to exactly one archetype.

When a new screen doesn't fit any archetype, either (a) widen an existing archetype by revising the catalog, or (b) add a new one via `/nav-spec --revise --add-archetype`. Never generate an off-catalog archetype silently.

---

## Common contract (inherited by all archetypes)

### Chrome

- **Landmarks:** `<header role="banner">`, `<main role="main" id="main">`.
- **Skip link:** `<a href="#main" class="sr-only focus:not-sr-only ...">Skip to main content</a>` as first body child.
- **Focus rings:** 2px accent color, 2px offset, keyboard-focus only (not mouse).
- **Icon-only buttons/links:** always carry `aria-label`.
- **Tap targets:** 44×44px minimum.
- **Header height:** 56px mobile, 64px desktop.
- **Header background:** solid (no `backdrop-blur-*`, no opacity modifiers like `bg-white/85`).
- **Semantic headings:** exactly one `<h1>`; `<h2>` for major sections; no skipping levels.

### IA

- **No dead ends:** every surface has ≥1 navigation exit (primary action, back, or sibling link), unless archetype=terminal.
- **Front-door assumption:** every surface renders correctly on direct arrival with no prior context (per Dan Brown's Principle 5).
- **URL is canonical:** back-targets use hardcoded absolute URLs, never `history.back()`.

---

## 1. `dashboard`

### Definition

Landing surface of an authenticated surface class. The user's "home" within that class. Shows overview, pending actions, and entry points to all sibling sections.

### Recommended pattern

**Hub-and-spoke** (primary) — from [pattern-catalog.md §1.1](pattern-catalog.md#11-hub-and-spoke). Optionally combined with:

- **Dominant-action variant** — when the current state has a single actionable priority (e.g., pending invoice)
- **Recent-activity variant** — time-ordered feed of events alongside entry points

### IA contract

- **MUST link to every sibling list/detail section** in the same surface class (per reachability matrix). This is enforced by validator R16.
- MUST NOT require the user to drill into a detail to reach a list (the portal-home omission of v1).
- MAY surface a dominant action (ActionCard) when a specific task is high-priority.
- MAY include a recent-activity feed as secondary (not replacing entry points).

### Chrome contract

- Header: client/workspace name on left, optional contact controls and utility actions on right. No logo. No nav tabs (admin exception, see Appendix D).
- **No back button.** The user is already home.
- **No breadcrumbs.**
- **Section cards** (primary IA affordance) in a grid: 1 column mobile, 2×N on desktop depending on section count.
- Right rail on desktop (optional): quick actions, consultant block, status card.

### Allowed surface classes

`session-auth-client`, `session-auth-admin`.

---

## 2. `list`

### Definition

Index of items in a collection. May be filterable, sortable, paginated.

### Recommended pattern

- **Master-detail** (primary) — row-click navigates to detail, detail has back-to-list with state preservation.
- **Faceted** — when collection size >30 items or multi-dimensional filtering is needed.
- **Index + preview** — for lightweight detail that benefits from quick scan.

### IA contract

- MUST link to its detail archetype via row-click.
- MUST link back to its dashboard (hub) via back button.
- MAY have sibling filters/facets within the surface.
- Empty state (archetype `empty`) handled when no rows returned.

### Chrome contract

- Header: same minimal band as dashboard.
- **Back button:** to the parent dashboard. Hardcoded canonical URL. `aria-label` carries destination name.
- **No breadcrumbs** on portal; breadcrumbs **allowed** on admin (2 levels max).
- Filter/sort bar directly below header, before the list (if faceted).
- Item rows: clickable area ≥44px tall, primary label + secondary meta.

### Allowed surface classes

`session-auth-client`, `session-auth-admin`.

---

## 3. `detail`

### Definition

Single-item view. Invoice, proposal, client, audit event.

### Recommended pattern

- **Master-detail** (primary) — back to parent list, state preserved on return.
- **Nested-doll** — if the detail has sub-sections reachable by drilling (rare on portal, common on admin).
- **Pyramid** — if sibling prev/next navigation is useful (rare; usually deprioritized in favor of return-to-list).

### IA contract

- MUST link back to its parent list via back button.
- MAY link to external systems (Stripe, SignWell) via primary action.
- MUST handle each of its task states (see state-machine-template).
- MAY include contextual links to related entities (e.g., "View engagement" from invoice).

### Chrome contract

- Header: minimal band.
- **Back button:** canonical URL to parent list. Label includes parent's name ("All invoices", "All proposals", "Home").
- **No breadcrumbs** on portal and token-auth; **allowed** on admin for 3+ level hierarchies.
- Right rail on desktop: consultant/contact block, status summary, related items.
- Primary action (Pay, Review & Sign) visible above the fold on mobile.

### Allowed surface classes

All four auth classes.

---

## 4. `form`

### Definition

Surface for creating or editing an entity. Fields, validation, submit.

### Recommended pattern

- **Single-page form** (default) — all fields on one surface.
- **Sequential** (wizard) — if logical ordering is mandatory (see archetype 5).

### IA contract

- Entry from: parent list (New button) or detail (Edit button).
- Success exit: detail of the created/edited entity.
- Cancel exit: canonical origin (list or detail, whichever invoked the form).
- Dirty-state guard on navigation away.

### Chrome contract

- Header: minimal band.
- **Cancel + Save actions** — not a back button. Cancel returns to the canonical origin.
- **Dirty-state guard:** if fields have unsaved changes, Cancel triggers a confirm modal.
- **No breadcrumbs.**
- Keyboard: `Cmd/Ctrl+S` saves; `Esc` interpreted as Cancel.

### Allowed surface classes

`session-auth-client`, `session-auth-admin`.

---

## 5. `wizard`

### Definition

Multi-step flow with forward/back progress. Onboarding, guided intake, multi-page checkout.

### Recommended pattern

**Sequential** (from [pattern-catalog.md §1.3](pattern-catalog.md#13-sequential-step-by-step)).

### IA contract

- Strict ordering: step N+1 unreachable without completing step N.
- Cancel exit: the surface that invoked the wizard, with confirm modal if any step has been completed.
- Success exit: the created entity's detail, or a success confirmation page.

### Chrome contract

- Header: minimal band. **Progress indicator ("Step N of M")** centered or left-aligned within the header, not below.
- **Previous + Next** buttons in a sticky or in-flow action block. Previous disabled on step 1; Next disabled until current step validates.
- **Cancel** at far-left of action block or in overflow menu. Triggers confirm modal.
- **No breadcrumbs.**
- **No back button in header** — Previous is the back affordance within the wizard.

### Allowed surface classes

`session-auth-client`, `session-auth-admin`.

---

## 6. `empty`

### Definition

Default rendering of a list or detail when there is nothing to show. First-time state.

### Recommended pattern

Inherits from the parent archetype. An empty `list` keeps list-chrome; an empty `detail` keeps detail-chrome.

### IA contract

- MUST provide a clear "what is this" message (Principle of Exemplars).
- MUST NOT fabricate affordances to items that don't exist.
- MAY provide a single CTA if adding is the expected next step (rare for portal — most portal empties are "wait for consultant").

### Chrome contract

- Inherits from parent archetype's header (list or detail).
- **Body:** short "why it's empty" message; optional solid-shape illustration (not real image); optional single CTA.
- **No marketing copy.** No testimonials, no feature tour.

### Allowed surface classes

All four.

---

## 7. `error`

### Definition

404, 500, 401, validation-failure fallback screens.

### Recommended pattern

**Recovery-path** — minimal chrome with a clear return to safety.

### IA contract

- MUST provide ≥1 navigation exit to a known-good surface.
- MUST NOT assume valid app state (the user's state is broken).
- MAY include a contact CTA for persistent failures.

### Chrome contract

- **Header:** minimal band; no back button (the user's history is suspect).
- **Body:** error type, short human explanation, single primary action that returns to safety:
  - Authenticated surfaces: "Go to portal home" linking to the surface class's dashboard.
  - Public surfaces: "Go to home" linking to `/`.
  - Token-auth: "Contact Scott" CTA.
- **No support form embedded.** Link to a contact method, don't host the form here.

### Allowed surface classes

All four.

---

## 8. `modal`

### Definition

Overlay for confirmations, pickers, short-form interactions. Single decision or single task.

### Recommended pattern

**Modal** (from [pattern-catalog.md §3.4](pattern-catalog.md#34-modal)).

### IA contract

- MUST preserve the underlying surface's state while open.
- MUST return focus to the trigger element on close.
- MAY NOT contain primary navigation (see anti-patterns).

### Chrome contract

- `<dialog>` or `role="dialog"` with `aria-modal="true"` and `aria-labelledby` pointing to a visible title.
- **Close affordances:** Esc, click-outside on scrim, AND an X button in the top-right. All three work.
- Focus returns to the triggering element on close.
- Scrim: `bg-black/50` (50% opacity black).
- Body scroll locked while open.

### Allowed surface classes

All four.

---

## 9. `drawer`

### Definition

Side panel for secondary actions, filters, or in-context help. Slides in from the edge.

### Recommended pattern

**Drawer** (as implemented in Material Design 3 modal drawer, HIG "full-screen sheet").

### IA contract

- MUST NOT contain primary navigation.
- Same open/close rules as modal.

### Chrome contract

- Slide direction: right-side on desktop, bottom-sheet on mobile.
- Width on desktop: 400px standard, max 600px.
- Height on mobile: 80vh max.
- Draggable header area on mobile (bottom-sheet affordance).
- Esc, click-outside, X button all close.

### Allowed surface classes

All four.

---

## 10. `transient`

### Definition

Short-lived surface the user passes through — success confirmation, redirect notice, expired-link notice.

### Recommended pattern

**Recovery-path** when the transient is failure-flavored; **progress indicator** when it's success-flavored.

### IA contract

- MAY be terminal (no navigation away) if it auto-redirects within a short window.
- MUST render a manual navigation fallback if auto-redirect fails.

### Chrome contract

- Header: present, minimal.
- Body: short message + manual CTA + (optional) auto-redirect indicator.

### Allowed surface classes

All four.

---

## Archetype lookup table (machine-readable)

| Archetype | Default pattern          | Back         | Breadcrumbs (portal) | Breadcrumbs (admin) | Right rail    | Nav tabs        |
| --------- | ------------------------ | ------------ | -------------------- | ------------------- | ------------- | --------------- |
| dashboard | Hub-and-spoke            | no           | no                   | no                  | yes (desktop) | no (admin: yes) |
| list      | Master-detail            | yes          | no                   | yes (2 levels)      | no            | no (admin: yes) |
| detail    | Master-detail            | yes          | no                   | yes (3 levels)      | yes (desktop) | no (admin: yes) |
| form      | Single-page form         | cancel+save  | no                   | no                  | no            | no              |
| wizard    | Sequential               | prev/next    | no                   | no                  | no            | no              |
| empty     | inherit from parent      | inherit      | inherit              | inherit             | inherit       | inherit         |
| error     | Recovery-path            | no           | no                   | no                  | no            | no              |
| modal     | Modal                    | close button | no                   | no                  | no            | no              |
| drawer    | Drawer                   | close button | no                   | no                  | no            | no              |
| transient | Recovery-path / Progress | manual CTA   | no                   | no                  | no            | no              |

**Anti-pattern shorthand:** breadcrumbs are never rendered on portal (session-auth-client). Admin breadcrumbs are for list and detail only, capped at the item's hierarchical depth. Modals and drawers never carry primary navigation.

---

## Pattern specialization per archetype (summary)

For each archetype, the pattern from the catalog may be specialized with venture-specific variants:

```markdown
### Archetype: dashboard

Pattern: Hub-and-spoke (NN/g §1.1)
Variant: Dominant-action + Recent-activity feed
Rationale: Portal tasks are bounded (Pay invoice, Sign proposal, Browse
documents, Check progress); user returns to home between tasks;
time-ordered timeline surfaces actionable state at a glance.
Required elements:

- Section cards to every sibling list (R16)
- ActionCard when condition `hasPendingInvoice` holds
- Recent-activity timeline (≤5 entries)
- Right-rail consultant block on desktop
```

This specialization is recorded in the venture's `NAVIGATION.md` Section 4 (Patterns).
