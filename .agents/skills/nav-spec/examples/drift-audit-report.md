# Drift audit — ss-console — 2026-04-15

spec-version checked against: _no spec yet (pre-authoring)_

## Live code matrix

| File                                   | Primary chrome                                                                 | Back/breadcrumb                           | Auth level          | Mobile pattern                                            | Shared layout?                |
| -------------------------------------- | ------------------------------------------------------------------------------ | ----------------------------------------- | ------------------- | --------------------------------------------------------- | ----------------------------- |
| `src/components/Nav.astro`             | Sticky header, logo left, Contact + Book CTA right                             | None                                      | Public              | Responsive max-w                                          | Yes — used by marketing pages |
| `src/components/PortalHeader.astro`    | Minimal band, avatar + client name, optional SMS link on mobile                | None                                      | Session-auth-client | Single-column mobile                                      | Yes — `/portal/*`             |
| `src/layouts/AdminLayout.astro`        | Top-band header with inline nav tabs + optional breadcrumbs + email + Sign out | Breadcrumbs (optional, hierarchical)      | Session-auth-admin  | No mobile-specific pattern                                | Yes — `/admin/*`              |
| `src/pages/portal/invoices/[id].astro` | Uses `PortalHeader`                                                            | None — no back affordance, no breadcrumbs | Session-auth-client | Two-column (md breakpoint), action card above fold mobile | Yes                           |
| `src/pages/portal/quotes/[id].astro`   | Uses `PortalHeader`                                                            | None — login-check only                   | Session-auth-client | Two-column (lg breakpoint), left-main layout              | Yes                           |
| `src/pages/scorecard.astro`            | Uses `Nav.astro`                                                               | None                                      | Public              | Responsive, no mobile nav                                 | Yes                           |

## Generated artifact matrix

| File                                                | Primary chrome                                         | Back/breadcrumb | Auth level implied | Mobile pattern              |
| --------------------------------------------------- | ------------------------------------------------------ | --------------- | ------------------ | --------------------------- |
| `.stitch/designs/portal-v1/home-desktop.html`       | Fixed sticky header (logo + 3 nav tabs)                | None            | Guest (no auth UI) | Bottom-tab nav (4 items)    |
| `.stitch/designs/portal-v1/proposal-desktop.html`   | Sticky header (logo + status badge)                    | None            | Guest              | No mobile (desktop-primary) |
| `.stitch/designs/portal-v1/invoice-desktop.html`    | Fixed top header (logo + SMS button)                   | Back link (↵)   | Guest              | No mobile                   |
| `.stitch/designs/portal-v1/home-mobile-v2.html`     | Minimal header (avatar + client name)                  | None            | Guest              | Centered layout             |
| `.stitch/designs/portal-v1/invoice-mobile-v2.html`  | Sticky header (arrow_back + "Portal" wordmark + title) | Back only       | Guest              | Sticky-bottom action bar    |
| `.stitch/designs/portal-v1/proposal-mobile-v2.html` | Sticky header                                          | None            | Guest              | Centered                    |
| `.stitch/*.html` (scorecard scratch)                | Sticky nav (logo + links + CTA)                        | None            | Guest              | No mobile pattern           |

## Spec compliance matrix

_(Not applicable — no spec exists yet. This audit informs the first spec.)_

## Drift summary (6 bullets)

1. **Three distinct live headers, three live breakpoints.** `Nav.astro` uses `sticky` marketing chrome with logo+CTA. `PortalHeader.astro` is a minimalist band with avatar+name. `AdminLayout` header has inline nav tabs + breadcrumbs + auth affordances. No shared underlying component. Portal uses `md:` breakpoints, invoice detail uses `lg:`. Breakpoint fragmentation is real.

2. **Back navigation is absent from 5 of 6 Stitch portal artifacts.** Deep-link pages (`/portal/invoices/[id]`, `/portal/proposals/[token]`) have no return path. `PortalHeader.astro` in live code has no back affordance either. The first user to hit an invoice via email has no clear way to the dashboard.

3. **Header stickiness inconsistent across Stitch artifacts.** `home-desktop` uses `fixed top-0`. `proposal-desktop` uses `sticky`. `invoice-desktop` uses `fixed`. No clear rule. Each run invents.

4. **Mobile navigation strategy undefined in both code and Stitch output.** `home-desktop.html` has a bottom-tab nav with 4 items (forbidden per our anti-patterns, but shipped). `home-mobile-v2.html` has no mobile nav at all. `scorecard-quiz.html` has a `bottom-0` bar. Portal code has no mobile nav strategy. Scaling Stitch to production requires inventing mobile nav for every screen.

5. **Auth-level presentation missing from every Stitch artifact.** All 6 Stitch portal designs render as "guest" — no sign-out, no user context. Live `PortalHeader.astro` and `AdminLayout` both have logout affordances. Stitch output can't serve as a production template without auth-chrome layered in afterwards.

6. **Token-auth is silently collapsed into "portal."** Proposal landings (`/portal/proposals/[token]`) and invoice landings (`/invoice/[id]`) are token-auth — neither fully public nor session-auth. Current designs and code treat them inconsistently: some have auth-style chrome, some have marketing-style chrome. Spec must carve out a fourth surface class for these.

## Follow-ups

1. **Author NAVIGATION.md v1** (owner: user + nav-spec) — resolves all drift items above by defining the contract.
2. **Refactor `Nav.astro`, `PortalHeader.astro`, `AdminLayout.astro` to match spec** (owner: user, separate PR, post-spec-approval) — aligns shipped code with the contract. Likely touches breakpoint conventions, back affordances, and the portal/token-auth distinction.
3. **Regenerate stale portal-v1 designs** (owner: user, optional) — those were produced before the spec and don't benefit from the injection. Keep them as historical reference or regenerate; user's call.
4. **Revisit admin chrome specifically** (owner: IA reviewer in Phase 4) — the current admin layout has nav tabs, which our default anti-pattern list forbids. Decision needed: align spec to code (admin tabs allowed) or align code to spec (remove tabs from admin).
