# Classification rubric

Every generation must carry **five** tags: `surface=`, `archetype=`, `viewport=`, `task=`, `pattern=`. The pipeline fails fast if any is missing. This rubric is the manual lookup for users constructing prompts; it is not a runtime classifier.

The two new tags (`task=` and `pattern=`) bind the generation to the venture's task model and selected pattern from `pattern-catalog.md`. They are required because chrome alone never determines a navigation pattern — the same chrome can implement different patterns. Tagging makes the choice deterministic.

---

## Tag format

Case-sensitive. Lowercase values. Hyphens, not underscores. Multi-word values use `-` (no quoting needed in shell).

```
surface=<public|auth-gate|token-auth|session-auth-client|session-auth-admin>
archetype=<dashboard|list|detail|form|wizard|empty|error|modal|drawer|transient>
viewport=<mobile|desktop>
task=<short-name-from-task-model>
pattern=<pattern-name-from-catalog>
```

Example prompt prefix:

```
/product-design target=portal-home surface=session-auth-client archetype=dashboard \
  viewport=mobile task=see-whats-happening pattern=hub-and-spoke
```

---

## Decision tree — surface class

1. **Is the URL publicly linkable without auth?**
   - Yes → node 2.
   - No → node 3.

2. **Does the URL contain a signed token or opaque identifier as a path segment** (e.g., `/portal/proposals/[token]`, `/invoice/[id]`)?
   - Yes → `token-auth`.
   - No → node 2a.

2a. **Is the page itself a sign-in / sign-up form** (e.g., `/auth/login`, `/auth/portal-login`)?

- Yes → `auth-gate`.
- No → `public`.

3. **Does the route require admin role?**
   - Yes → `session-auth-admin`.
   - No → `session-auth-client`.

---

## Decision tree — archetype

1. **Is this the landing for an authenticated surface class** (a "home")?
   - Yes → `dashboard`.
   - No → node 2.

2. **Does the page show a list of items?**
   - Yes → `list`.
   - No → node 3.

3. **Does the page show a single item's details?**
   - Yes → `detail`.
   - No → node 4.

4. **Is the page an input form for creating/editing one entity?**
   - Yes, single-page → `form`.
   - Yes, multi-step → `wizard`.
   - No → node 5.

5. **Is this an overlay or panel?**
   - Overlay centered → `modal`.
   - Slide-in from edge → `drawer`.
   - No → node 6.

6. **Is this an error or empty state?**
   - Error (404/500/401/etc.) → `error`.
   - Empty list/detail → `empty`.
   - Short-lived pass-through → `transient`.
   - Otherwise → STOP. Run `/nav-spec --revise --add-archetype`.

---

## Decision tree — viewport

Two values: `mobile` (390×844 reference) and `desktop` (1280 reference).

- If user prompt specifies → use it.
- If not → ask. Do not default. Explicit classification forces mobile-first thinking.
- For both viewports → run two generations.

---

## Decision tree — task

Look up the venture's task model (Section 1 of NAVIGATION.md). Each surface has 1–3 primary tasks. Pick the one this generation is designed to support.

If the surface supports multiple tasks (a dashboard supports several), pick the one that anchors this particular generation — the user's stated focus.

If you can't name the task without consulting the spec, the spec is incomplete OR the surface lacks a clear task. Stop and reconcile.

Examples (ss-console portal — for illustration; not exhaustive):

| Surface                               | Likely task tag                                                                      |
| ------------------------------------- | ------------------------------------------------------------------------------------ |
| `/portal`                             | `see-whats-happening`, `pay-pending-invoice` (state-dependent), `contact-consultant` |
| `/portal/quotes`                      | `review-past-proposals`                                                              |
| `/portal/quotes/[id]` (status=sent)   | `review-and-sign-proposal`                                                           |
| `/portal/invoices`                    | `review-past-invoices`                                                               |
| `/portal/invoices/[id]` (status=sent) | `pay-pending-invoice`                                                                |
| `/portal/documents`                   | `find-document`                                                                      |
| `/portal/engagement`                  | `check-progress`                                                                     |

---

## Decision tree — pattern

Look up the surface's archetype in [archetype-catalog.md](archetype-catalog.md) — each archetype has a recommended pattern. If the venture's spec specializes that pattern (e.g., dashboard with dominant-action variant), use the specialized name.

Common patterns by archetype:

| Archetype | Default pattern (catalog reference)     |
| --------- | --------------------------------------- |
| dashboard | `hub-and-spoke` (NN/g §1.1)             |
| list      | `master-detail` or `faceted`            |
| detail    | `master-detail` or `nested-doll`        |
| form      | `single-page-form`                      |
| wizard    | `sequential` (NN/g §1.3)                |
| empty     | inherits from parent                    |
| error     | `recovery-path`                         |
| modal     | `modal`                                 |
| drawer    | `drawer`                                |
| transient | `recovery-path` or `progress-indicator` |

If the venture's pattern selection diverges from the default, the venture spec must justify it. If the prompt's `pattern=` tag doesn't match the spec's selection for the surface, the pipeline fails.

---

## Disambiguation — common edge cases

### A "detail" page with an inline form

`/portal/invoices/[id]` shows the invoice and has a Pay button that opens payment fields inline (no navigation).

Classification: `detail`. The form is content. `task=pay-pending-invoice`, `pattern=master-detail`.

### A "list" with a prominent filter drawer

`/admin/audit-log` with a slide-in filter panel.

Classification: `list` for the main page, `drawer` if generating the filter panel separately.

### Dashboard + list

`/admin/home` shows metric cards and a recent-activity list.

Classification: `dashboard`. Lists embedded in dashboards are content; the archetype is determined by the surface's role.

### Public marketing page with a form

`/contact` with a contact form.

Classification: either `public` + `detail` (form is one section of a larger page) OR `public` + `form` (form IS the page). Use `form` when it's the primary purpose.

### Token-auth landing → session-auth after action

`/portal/proposals/[token]` where accepting signs the client in.

Classification: two different pages. `/portal/proposals/[token]` is `token-auth` + `detail`. Post-acceptance redirect target is `session-auth-client` + `dashboard`.

### Same URL, different chrome by role

`/invoice/[id]` — logged-in user gets session chrome, anonymous gets token-auth chrome.

Classification: two generations, one per surface class. The implementation branches at render; design specs for each path are independent.

---

## Fallback when classification is ambiguous

```
Cannot classify target. Add these tags to your prompt:
  surface=<public|auth-gate|token-auth|session-auth-client|session-auth-admin>
  archetype=<dashboard|list|detail|form|wizard|empty|error|modal|drawer|transient>
  viewport=<mobile|desktop>
  task=<short-name>     (look up Section 1 of NAVIGATION.md)
  pattern=<name>        (look up Section 4 of NAVIGATION.md or pattern-catalog.md)

Run /nav-spec --classify-help to see the decision rubric.
```

Never guess. Never infer from natural language. Probabilistic classification produces exactly the drift this system is designed to prevent.

---

## Test cases

| Prompt                                        | Correct classification                                                                                           |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| "Portal home dashboard, mobile"               | `surface=session-auth-client archetype=dashboard viewport=mobile task=see-whats-happening pattern=hub-and-spoke` |
| "Invoice detail page, mobile" (authenticated) | `surface=session-auth-client archetype=detail viewport=mobile task=pay-pending-invoice pattern=master-detail`    |
| "Invoice link from email" (deep-link)         | `surface=token-auth archetype=detail viewport=<ask> task=pay-pending-invoice pattern=master-detail`              |
| "Admin clients list"                          | `surface=session-auth-admin archetype=list viewport=<ask> task=manage-clients pattern=master-detail`             |
| "New engagement intake wizard"                | `surface=session-auth-admin archetype=wizard viewport=<ask> task=create-engagement pattern=sequential`           |
| "Marketing home page"                         | `surface=public archetype=dashboard viewport=<ask> task=convert-visitor pattern=hub-and-spoke`                   |
| "Portal sign-in"                              | `surface=auth-gate archetype=form viewport=<ask> task=sign-in pattern=single-page-form`                          |
| "404 page on portal"                          | `surface=session-auth-client archetype=error viewport=<ask> task=recover pattern=recovery-path`                  |
