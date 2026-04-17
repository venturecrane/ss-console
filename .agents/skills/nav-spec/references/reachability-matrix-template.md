---
description: Template for the Reachability Matrix section of NAVIGATION.md. This is the central IA artifact — it declares which destinations are reachable from which surfaces, and by what mechanism. Validator rule R16 checks code against this matrix.
---

# Reachability Matrix

The reachability matrix is the IA's central deliverable. It declares, explicitly and exhaustively, which navigation transitions exist between surfaces and how they're mechanized. Orphan destinations and dead-end surfaces are visible as missing rows.

This matrix is validator-checkable (R16–R18). If the matrix says a dashboard links to `/portal/invoices`, the validator confirms the dashboard's HTML emits `<a href="/portal/invoices">`.

---

## Template structure

In `.stitch/NAVIGATION.md`, the matrix follows the Task Model and Sitemap sections.

```markdown
## 3. Reachability matrix

### 3.1 Invariants

- Every surface in the sitemap appears as a "From" at least once AND a "To" at least once.
  - Exception: entry-only surfaces (token-auth landings from email) have no "To" from internal surfaces; flag with **Entry-only**.
  - Exception: terminal surfaces (confirm + redirect to external) may have no "To"; flag with **Terminal**.
- Every `dashboard` archetype has "To" rows for every sibling list route in its surface class.
- Every `detail` archetype has a "To" row back to its parent list.
- Every surface has at least one "To" row (no dead ends) unless flagged **Terminal**.

### 3.2 Matrix

| From                             | To                      | Mechanism                         | Required?       | Pattern ref             |
| -------------------------------- | ----------------------- | --------------------------------- | --------------- | ----------------------- |
| `/portal` (dashboard)            | `/portal/invoices`      | Section card                      | Yes             | Hub-and-spoke           |
| `/portal` (dashboard)            | `/portal/quotes`        | Section card                      | Yes             | Hub-and-spoke           |
| `/portal` (dashboard)            | `/portal/documents`     | Section card                      | Yes             | Hub-and-spoke           |
| `/portal` (dashboard)            | `/portal/engagement`    | Section card                      | Yes             | Hub-and-spoke           |
| `/portal` (dashboard)            | `/portal/invoices/[id]` | ActionCard (when invoice pending) | Conditional     | Dominant-action variant |
| `/portal/invoices` (list)        | `/portal/invoices/[id]` | Row click                         | Yes             | Master-detail           |
| `/portal/invoices` (list)        | `/portal`               | Back button                       | Yes             | Hub-and-spoke           |
| `/portal/invoices/[id]` (detail) | `/portal/invoices`      | Back button (canonical URL)       | Yes             | Master-detail           |
| `/portal/invoices/[id]` (detail) | `<stripe>`              | Pay Now CTA                       | Yes (if unpaid) | External                |
| ...                              |                         |                                   |                 |                         |

### 3.3 Entry-only surfaces

| Surface                        | Entry vector    | Notes                                |
| ------------------------------ | --------------- | ------------------------------------ |
| `/portal/proposals/[token]`    | Email deep-link | Token-auth; no prior session assumed |
| `/invoice/[id]` (public token) | Email deep-link | Token-auth; no prior session assumed |

### 3.4 Terminal surfaces

| Surface                          | Terminal action       | Notes                  |
| -------------------------------- | --------------------- | ---------------------- |
| `/portal/quotes/[id]` after sign | Redirect to `/portal` | Post-signature         |
| Stripe hosted payment            | External              | Not part of venture IA |
```

---

## Field definitions

- **From** — canonical route (Astro path format, `[param]` preserved) plus archetype in parentheses.
- **To** — same format. If external, wrap in angle brackets (e.g., `<stripe>`, `<signwell>`, `<email>`).
- **Mechanism** — the named control that creates the link:
  - Section card, entry card, action card, row click, back button, breadcrumb, inline link, CTA, modal close, context switcher, form submit, redirect, SMS deep-link, email deep-link, etc.
  - Name mechanisms consistently. A "section card" is a specific pattern; don't also call it an "entry tile."
- **Required?** — `Yes` (must always render), `Conditional` (with the condition named, e.g., "when invoice pending"), or `No` (optional affordance).
- **Pattern ref** — one of the patterns from [pattern-catalog.md](pattern-catalog.md) that this link implements.

---

## Invariants (validator rules)

Validator rule R16 enforces each of the following on generated HTML:

1. **Dashboard completeness** — for every row where From is `dashboard` archetype and Required is `Yes`, the dashboard's HTML must emit an `<a href>` whose href matches the `To` route.
2. **Detail-to-parent** — for every row where From is `detail` archetype and Mechanism is `back button`, the detail's HTML must emit a back-affordance `<a href>` whose href matches the `To` route.
3. **No dead ends** — every surface that appears as a "From" must have at least one row with Required=Yes; surfaces flagged Terminal are exempt.
4. **No orphan destinations** — for every route in `src/pages/**/index.astro` or `src/pages/**/[param].astro` with the same surface class, there must exist at least one row where `To` matches the route.

Orphan destinations (item 4) are caught by walking `src/pages/` at audit time and diffing against the matrix.

---

## Mechanism naming conventions

Mechanism is a free-text field, but naming should be consistent across a spec. Recommended canonical names:

| Canonical            | Use                                                         |
| -------------------- | ----------------------------------------------------------- |
| **Section card**     | Dashboard entry to a section's list view                    |
| **Action card**      | Dashboard dominant-action affordance (pending invoice)      |
| **Entry card**       | Non-hub surface linking to a related area                   |
| **Row click**        | Index row click navigating to detail                        |
| **Back button**      | Single-affordance ascent using canonical URL                |
| **Breadcrumb**       | Multi-level ascent via full parent chain                    |
| **Inline link**      | Contextual in-prose or in-body link                         |
| **CTA**              | Primary call-to-action button                               |
| **Contact icon**     | Email / SMS / phone icon in chrome                          |
| **Context switcher** | Selector that changes a persistent context (client, tenant) |
| **Tab**              | Category switch within a detail surface (not navigation)    |
| **Modal open**       | Open a modal overlay                                        |
| **Modal close**      | Close and return to trigger surface                         |
| **Redirect**         | Auto-navigation post-action                                 |
| **Email deep-link**  | Arrival via email URL                                       |
| **SMS deep-link**    | Arrival via SMS URL                                         |

If none of these fit, invent a new mechanism name AND document it in this file so future specs use the same term.

---

## Conditional logic

When a mechanism is conditional (e.g., "ActionCard renders only when an invoice is pending"), the condition must be stated in the Mechanism or Required column, not hidden in prose elsewhere in the spec. Validators treat conditional mechanisms as "render when condition holds; absence is allowed otherwise."

Example:

```
| `/portal` | `/portal/invoices/[pending-id]` | ActionCard (only when `invoices.some(i => i.status === 'sent' || i.status === 'overdue')`) | Conditional | Dominant-action |
```

---

## Keeping the matrix current

The matrix must be maintained as the IA changes:

- Adding a new route → add rows for every surface that should link to it and every surface it should return to
- Removing a route → remove all rows referencing it
- Restructuring a section → delete+recreate rows rather than partially editing

The `/nav-spec --ia-audit` workflow reads `src/pages/**` and diffs against the matrix. A route added to the code without a matrix update surfaces as an orphan destination. A matrix row pointing to a non-existent route surfaces as a broken link.

---

## Example — ss-console portal (abbreviated)

```markdown
### 3.2 Matrix

| From                             | To                            | Mechanism                                   | Required?                 | Pattern                       |
| -------------------------------- | ----------------------------- | ------------------------------------------- | ------------------------- | ----------------------------- |
| `/portal` (dashboard)            | `/portal/quotes`              | Section card                                | Yes                       | Hub-and-spoke                 |
| `/portal` (dashboard)            | `/portal/invoices`            | Section card                                | Yes                       | Hub-and-spoke                 |
| `/portal` (dashboard)            | `/portal/documents`           | Section card                                | Yes                       | Hub-and-spoke                 |
| `/portal` (dashboard)            | `/portal/engagement`          | Section card                                | Yes                       | Hub-and-spoke                 |
| `/portal` (dashboard)            | `/portal/invoices/[id]`       | ActionCard (condition: has pending invoice) | Conditional               | Dominant-action               |
| `/portal` (dashboard)            | `mailto:consultant@...`       | Contact icon                                | Conditional               | Contact-control               |
| `/portal` (dashboard)            | `sms:+1...`                   | Contact icon                                | Conditional               | Contact-control               |
| `/portal` (dashboard)            | `tel:+1...`                   | Contact icon                                | Conditional               | Contact-control               |
| `/portal` (dashboard)            | `/api/auth/logout`            | Logout button                               | Yes                       | —                             |
| `/portal/quotes` (list)          | `/portal/quotes/[id]`         | Row click                                   | Yes                       | Master-detail                 |
| `/portal/quotes` (list)          | `/portal`                     | Back button                                 | Yes                       | Hub-and-spoke                 |
| `/portal/quotes/[id]` (detail)   | `/portal/quotes`              | Back button                                 | Yes                       | Master-detail                 |
| `/portal/quotes/[id]` (detail)   | `<signwell>`                  | Review & Sign CTA                           | Conditional (status=sent) | External                      |
| `/portal/invoices` (list)        | `/portal/invoices/[id]`       | Row click                                   | Yes                       | Master-detail                 |
| `/portal/invoices` (list)        | `/portal`                     | Back button                                 | Yes                       | Hub-and-spoke                 |
| `/portal/invoices/[id]` (detail) | `/portal/invoices`            | Back button                                 | Yes                       | Master-detail                 |
| `/portal/invoices/[id]` (detail) | `<stripe>`                    | Pay Now CTA                                 | Conditional (status≠paid) | External                      |
| `/portal/documents` (list)       | `/api/portal/documents/[key]` | Row click (download/view)                   | Yes                       | Master-detail (external view) |
| `/portal/documents` (list)       | `/portal`                     | Back button                                 | Yes                       | Hub-and-spoke                 |
| `/portal/engagement` (detail)    | `/portal`                     | Back button                                 | Yes                       | Hub-and-spoke                 |
```
