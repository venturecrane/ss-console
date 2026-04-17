# Anti-patterns

The navigation smells every NAVIGATION.md should explicitly forbid. Each has a rationale — understand it so you can apply judgment when a new anti-pattern emerges, not just copy the list.

## Structural anti-patterns

### 1. Global nav tabs / menu links in the header on authenticated surfaces

**Example:** "Dashboard | Invoices | Proposals | Settings" rendered as a horizontal tab bar in the top band.

**Why forbidden on portal and token-auth:** the client portal has a small, focused surface area; the user is there to check one thing or act on one thing. A tab bar suggests breadth of app, consumes vertical real estate, and imports SaaS-product conventions that don't fit a consulting relationship.

**Why forbidden on admin (usually):** depends on the venture's admin complexity. For ss-console's single-operator admin, a tab bar is overkill. For multi-team admins, a tab bar is allowed but must be specified in the surface-class appendix.

**Exception path:** if the admin truly needs tab-level navigation, define it in Appendix D with explicit tabs, never a free invention by Stitch.

### 2. Sidebar / hamburger / drawer as primary navigation

**Example:** collapsible sidebar with "Home | Invoices | Clients | ..." on desktop; hamburger icon in the header on mobile that opens a slide-out with the same menu.

**Why forbidden:** sidebars are a SaaS-product pattern. This isn't a SaaS product. A sidebar nav signals the wrong relationship (self-serve tool) vs what we're actually building (consulting engagement interface).

**Exception path:** none at the current scope. Re-visit if the admin surface grows past ~15 routes and tabs can't hold them.

### 3. Bottom-tab nav (mobile)

**Example:** fixed bottom bar with 3–5 icons + labels (Home / Invoices / Messages / Profile).

**Why forbidden universally:** mobile apps use bottom-tabs. This is a web surface, not a mobile app. Users expect web-style navigation. Bottom-tabs on mobile web are a conversion-killer on authenticated flows and feel "app-like" in a way that undermines the consulting positioning.

**Exception path:** none.

### 4. Sticky bottom action bar when the primary action is already visible above the fold

**Example:** Pay button visible at y=250 on the invoice detail, plus a duplicated "Pay $4,250" bar stuck to `bottom-0` for "thumb reachability."

**Why forbidden:** duplication signals insecurity — "I put the button twice because I'm not sure you saw it." Either the primary action is above the fold (trust the user) or it isn't (fix the layout). Both can't be true.

**Exception path:** if the content pushes the primary action below the fold on 390×844, the fix is tightening the layout, not adding a second button.

### 5. Footer / copyright / legal links on authenticated surfaces

**Example:** "© 2026 SMDurgan LLC | Privacy | Terms | Contact" at the bottom of `/portal/home`.

**Why forbidden:** the user has already agreed to terms; they have an ongoing relationship. Marketing-site chrome on an authenticated surface signals the wrong relationship. Legal links on authenticated surfaces are almost always out-of-date anyway.

**Exception path:** none.

## Content anti-patterns

### 6. Marketing CTAs on authenticated surfaces

**Example:** "Schedule a call" or "Book a demo" button on `/portal/home`.

**Why forbidden:** the user is already a customer. The CTA is addressed to a stranger. It patronizes the reader.

**Exception path:** a "schedule a check-in" action directed at an existing client is fine if explicit in the page prompt. "Book a demo" is not.

### 7. Testimonials, pull quotes, italicized client-voice text

**Example:** "'Scott transformed our business — Mike Delgado, Delgado Plumbing'" rendered as a pull quote on an invoice landing.

**Why forbidden universally:** marketing chrome. Testimonials live on public marketing pages, nowhere else.

**Exception path:** none.

### 8. Hero imagery / decorative illustrations

**Example:** a large stock-photo-style header image or a geometric illustration atop the portal home.

**Why forbidden on authenticated surfaces:** chrome. Consumes vertical real estate the user wants to use for actual data. Authenticated surfaces are working surfaces; there is no "brand moment" to protect.

**Exception path:** on `public` marketing surfaces, hero imagery is allowed but must be specified in the prompt. Never free invention.

### 9. Real-face photo placeholders

**Example:** `<img src="https://lh3.googleusercontent.com/aida/..." alt="professional headshot of a mature businessman">` as a consultant avatar placeholder.

**Why forbidden universally:** Stitch's training prior is heavy with these. They look real but they're synthetic/stock. They signal "stock photo" aesthetic, which is corrosive to the guide positioning.

**Fix:** a solid-color circle with initials. Always. Never relax this, even "just for this one screen."

### 10. Breadcrumbs on portal

**Example:** "Home > Invoices > Invoice #1042" at the top of the invoice detail.

**Why forbidden on portal:** the portal is shallow (2 levels: dashboard → detail). A breadcrumb trail suggests a deeper hierarchy than exists. The back button is sufficient.

**Exception path:** admin may need breadcrumbs for 3+ level hierarchies (`Clients > Delgado Plumbing > Audit log`). Specify in Appendix D.

## Semantic anti-patterns (mostly caught by the validator)

### 11. `<nav aria-label="Breadcrumb">` wrapping a single back button

**Example:** `<nav aria-label="Breadcrumb"><a href="/home">← Home</a></nav>`

**Why forbidden:** a breadcrumb trail has multiple levels. A single back button is not a breadcrumb — it's a back button. The semantic wrapper lies to assistive tech.

### 12. `fixed top-0` instead of `sticky top-0` on the header

**Why forbidden:** `fixed` takes the header out of document flow. It plays badly with scrolling containers, right rails, and mobile keyboard-opens. `sticky` stays in flow and behaves correctly.

### 13. `backdrop-blur-*` / translucent header background

**Why forbidden:** glassmorphism is a 2021–2023 visual fad. It reads as "trying to look modern" and it's a common Stitch over-render. Solid white headers are cleaner, faster to render, and don't fight the user's reading of the content below.

### 14. Icon decoration before the client name in the header

**Example:** `<span class="material-symbols-outlined">water_drop</span> Delgado Plumbing` — a water drop next to a plumbing client's name.

**Why forbidden:** cute. Also noisy. The client name is identity enough. Decorative icons in the header accumulate over time ("just one more"). Forbid them universally.

### 15. Back href="#" or href="javascript:" or onclick="history.back()"

**Why forbidden:** `#` scrolls to top (broken). `javascript:` breaks right-click context menu (broken). `history.back()` breaks deep-links (user arrived via email, there's nothing to go back to). Always a canonical hardcoded URL.

## IA anti-patterns (v2)

These are the IA-level smells the chrome-only v1 of nav-spec failed to catch. Each maps to a validator rule R16–R24.

### 16. Orphan destination

**Definition:** A route exists in `src/pages/**` (or in deployed code) but no `<a href>` in any navigated surface points to it. The only way to reach it is to know the URL.

**Real example (ss-console v1, April 2026):** `/portal/quotes`, `/portal/invoices`, `/portal/documents`, `/portal/engagement` all existed as list views but none were linked from `/portal`. Users could reach a specific item via Recent Activity then back-navigate to the list — a contortion. This is what triggered nav-spec v2.

**Why forbidden:** orphans break Dan Brown's Front Doors principle (#5). They guarantee that any user not arriving through the bookmarked URL will not find the surface.

**Fix:** Add the link to the reachability matrix; render the affordance on the appropriate hub. Validated by R16.

### 17. Dead-end surface

**Definition:** A surface with no navigation exit other than the browser back button. Not an `error` archetype intentionally pointing to safety; just an oversight.

**Why forbidden:** every surface needs a deliberate next step. Even terminal surfaces (post-purchase, post-sign) need an explicit "Return to home" or auto-redirect with manual fallback.

**Fix:** Add a primary action, back affordance, or sibling link. Validated by R18.

### 18. Pattern-impersonation

**Definition:** A surface that looks like one pattern (e.g., dashboard with section cards) but doesn't implement that pattern's required elements (e.g., the cards link to non-canonical destinations or have no labels).

**Why forbidden:** pattern semantics carry user expectations. Fake patterns confuse users in ways they can't articulate.

**Fix:** Either implement the pattern fully or use a different, simpler control. Validated by R17.

### 19. Token-auth amnesia

**Definition:** A token-auth surface that assumes prior context (logged-in state, prior page, recent actions). Renders broken when arrived-at cold from email.

**Why forbidden:** token-auth is by definition cold-arrival (no prior session). Assumptions about state break front-door (#5).

**Fix:** Render self-contained context — name the entity, name the action, provide the next step without depending on referrer or session. Validated by R19.

### 20. Taxonomy drift

**Definition:** Same concept rendered with different labels across surfaces. "Proposal" on the home, "Quote" on the email, "Estimate" in the PDF.

**Why forbidden:** users must learn three vocabularies for one concept. Trust erodes.

**Fix:** Pick one term in `content-taxonomy-template.md` and use it everywhere. Validated by R20.

### 21. State omission

**Definition:** A surface that handles only the populated state and renders broken (blank, half-rendered, or with affordances to non-existent items) in empty/loading/error states.

**Why forbidden:** every surface has multiple states; designing for only one is incomplete.

**Fix:** Define every state in `state-machine-template.md` and render explicitly for each. Validated by R21.

### 22. Heading hierarchy violation

**Definition:** Multiple `<h1>` on one surface, skipped levels (h1 → h3), or no `<h1>` at all.

**Why forbidden:** screen readers rely on heading hierarchy to convey IA structure. Broken hierarchy = broken IA for non-sighted users.

**Fix:** Single `<h1>` per surface, `<h2>` for major sections, no level skipping. Validated by R22.

### 23. Search affordance missing

**Definition:** A surface declared in the spec to have search, but the rendered HTML has no search input.

**Why forbidden:** if search is in the spec, users expect it. Silent omission = expectation violation.

**Fix:** Render the search input as specified. Validated by R23.

### 24. Cross-surface context loss

**Definition:** A persistent-context pattern (selected client, selected engagement) where the context indicator is missing on one or more surfaces in the workspace.

**Why forbidden:** persistent context only works if it persists. Missing context indicator on any surface breaks the pattern.

**Fix:** Render context indicator (header chip, breadcrumb prefix) on every surface within the workspace scope. Validated by R24.

---

## Classification (machine-readable)

| #   | Anti-pattern                                       | Severity   | Validator rule   | Layer   |
| --- | -------------------------------------------------- | ---------- | ---------------- | ------- |
| 1   | Global nav tabs in header (non-admin)              | structural | R6               | Chrome  |
| 2   | Sidebar / hamburger / drawer nav                   | structural | R6               | Chrome  |
| 3   | Bottom-tab nav                                     | structural | R7               | Chrome  |
| 4   | Sticky bottom action bar                           | structural | R7               | Chrome  |
| 5   | Footer on auth surface                             | structural | R8               | Chrome  |
| 6   | Marketing CTAs on auth                             | structural | R10              | Chrome  |
| 7   | Testimonials / pull quotes on auth                 | structural | (content filter) | Chrome  |
| 8   | Hero imagery on auth                               | structural | (content filter) | Chrome  |
| 9   | Real-face photo placeholder                        | structural | R9               | Chrome  |
| 10  | Breadcrumbs on portal                              | structural | R4               | Chrome  |
| 11  | `<nav aria-label="Breadcrumb">` around single back | semantic   | R4               | Chrome  |
| 12  | `fixed top-0` on header                            | semantic   | R1               | Chrome  |
| 13  | `backdrop-blur-*` / translucent header bg          | cosmetic   | R2               | Chrome  |
| 14  | Icon before client name                            | cosmetic   | R3               | Chrome  |
| 15  | Back `href="#"` / `history.back()`                 | semantic   | R5               | Chrome  |
| 16  | Orphan destination                                 | structural | R16              | IA      |
| 17  | Dead-end surface                                   | structural | R18              | IA      |
| 18  | Pattern-impersonation                              | structural | R17              | Pattern |
| 19  | Token-auth amnesia                                 | structural | R19              | IA      |
| 20  | Taxonomy drift                                     | semantic   | R20              | IA      |
| 21  | State omission                                     | structural | R21              | IA      |
| 22  | Heading hierarchy violation                        | semantic   | R22              | A11y    |
| 23  | Search affordance missing                          | semantic   | R23              | IA      |
| 24  | Cross-surface context loss                         | structural | R24              | Pattern |

Severity determines retry behavior in the validator: **structural violations always retry**; **semantic violations retry once**; **cosmetic violations warn but pass by default** (can be elevated per venture).
