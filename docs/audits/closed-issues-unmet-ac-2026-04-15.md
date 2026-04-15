## Move 5 — retroactive AC sweep (2026-04-15)

Scanned **156 closed issues**. Found **76** with unchecked items in their `Acceptance criteria` section.

### Summary table

| #    | Title                                                                                                         | Closed     | Unchecked / Total | Labels                                                                    |
| ---- | ------------------------------------------------------------------------------------------------------------- | ---------- | ----------------- | ------------------------------------------------------------------------- |
| #341 | E2E lifecycle sprint walkthrough — sample entity signal through repeat customer                               | 2026-04-13 | 13/13             | prio:P1, status:ready, type:feature, layer:5-distribution                 |
| #219 | Booking: rewrite /book.astro with SlotPicker + intake form + Turnstile + 503 fallback panel                   | 2026-04-09 | 13/13             | prio:P1, status:ready, type:feature, layer:5-distribution                 |
| #222 | Booking: manage token flow — /book/manage/[token] page + GET/cancel/reschedule API                            | 2026-04-09 | 9/9               | prio:P1, status:ready, type:feature, layer:5-distribution                 |
| #220 | Booking: GET /api/booking/slots + POST /api/booking/reserve (atomic 3-phase critical path)                    | 2026-04-09 | 9/9               | prio:P1, status:ready, type:feature, layer:5-distribution                 |
| #227 | Booking: admin UI audit for cancelled assessment status                                                       | 2026-04-13 | 8/8               | prio:P1, status:ready, type:feature, layer:5-distribution                 |
| #224 | Booking: DAL modules + intake-core extraction (schedule.ts, integrations.ts, oauth-states.ts, intake-core.ts) | 2026-04-13 | 7/7               | prio:P1, status:ready, type:feature, layer:5-distribution                 |
| #228 | Booking: Google sync error alerting (email Scott on first error in 30-min window)                             | 2026-04-09 | 7/7               | prio:P1, status:triage, type:feature, layer:5-distribution                |
| #215 | Booking setup: create Google Cloud OAuth 2.0 client for Calendar API                                          | 2026-04-09 | 7/7               | prio:P1, status:ready, type:feature, layer:5-distribution                 |
| #225 | Booking: workers/booking-cleanup Cloudflare Worker with daily cron                                            | 2026-04-09 | 7/7               | prio:P2, status:ready, type:feature, layer:5-distribution                 |
| #74  | Portal: Client auth (magic links) + portal invitation                                                         | 2026-03-31 | 7/7               | prio:P0, type:feature, portal, phase:2, sprint:3, status:review           |
| #361 | feat(portal): redesign home dashboard — C-hybrid (action-centric + timeline)                                  | 2026-04-14 | 6/6               | prio:P2, status:ready, type:feature, layer:6-delivery                     |
| #363 | feat(portal): redesign proposal landing — sign surface                                                        | 2026-04-14 | 6/6               | prio:P2, status:ready, type:feature, layer:6-delivery                     |
| #362 | feat(portal): redesign invoice landing — deep-link target                                                     | 2026-04-14 | 6/6               | prio:P2, status:ready, type:feature, layer:6-delivery                     |
| #110 | Lead Gen: Pipeline 5 — Partner Nurture + Buttondown                                                           | 2026-04-13 | 6/6               | prio:P2, type:feature, layer:5-distribution, lead-gen, pipeline:5-nurture |
| #107 | Lead Gen: Pipeline 1 — Review Mining (full build)                                                             | 2026-04-13 | 6/6               | prio:P1, type:feature, layer:5-distribution, lead-gen, pipeline:1-reviews |
| #106 | Lead Gen: Pipeline 2 — Job Posting Monitor (full build)                                                       | 2026-04-13 | 6/6               | prio:P1, type:feature, layer:5-distribution, lead-gen, pipeline:2-jobs    |
| #105 | Lead Gen: Prompt library + shared schemas                                                                     | 2026-04-13 | 6/6               | prio:P1, type:feature, layer:5-distribution, lead-gen                     |
| #72  | Quote builder UI + SOW PDF generation                                                                         | 2026-04-10 | 6/6               | prio:P0, type:feature, layer:5-distribution, portal, phase:2, sprint:5    |
| #77  | Portal: Engagement management + milestones                                                                    | 2026-03-31 | 6/6               | prio:P0, type:feature, portal, phase:4, sprint:6                          |
| #70  | Portal: Assessment capture + transcript upload                                                                | 2026-03-31 | 6/6               | prio:P0, type:feature, portal, phase:1, sprint:4, status:review           |
| #360 | feat(portal): foundation — data model + shared portal components for redesign                                 | 2026-04-14 | 5/5               | prio:P1, status:ready, type:feature, layer:6-delivery                     |
| #108 | Lead Gen: Pipeline 3 — New Business Detection                                                                 | 2026-04-13 | 5/5               | prio:P2, type:feature, layer:5-distribution, lead-gen, pipeline:3-newbiz  |
| #226 | Booking: cutover test plan doc + delete legacy /book/thanks and intake route                                  | 2026-04-13 | 5/5               | prio:P1, status:ready, type:feature, layer:5-distribution                 |
| #202 | Attorney-specific one-liner and ROI hook                                                                      | 2026-04-13 | 5/5               | prio:P2, status:triage, type:feature                                      |
| #201 | Pool Services-specific one-liner and ROI hook                                                                 | 2026-04-13 | 5/5               | prio:P2, status:triage, type:feature                                      |
| #187 | HVAC-specific one-liner and ROI hook                                                                          | 2026-04-13 | 5/5               | prio:P2, status:triage, type:feature                                      |
| #232 | Admin 'Book a Meeting' inline form + endpoint                                                                 | 2026-04-13 | 5/5               | prio:P1, status:ready, type:feature, layer:5-distribution                 |
| #179 | test: route-level HTTP harness for inline-SQL admin endpoints                                                 | 2026-04-13 | 5/5               | prio:P2, type:tech-debt, source:code-review                               |
| #88  | Portal: Stripe account setup                                                                                  | 2026-04-10 | 5/5               | prio:P0, type:feature, portal, phase:3, manual                            |
| #223 | Booking: wire confirmation / reschedule / cancellation emails to Resend                                       | 2026-04-09 | 5/5               | prio:P1, status:ready, type:feature, layer:5-distribution                 |
| #81  | Portal: Follow-up cadence automation                                                                          | 2026-03-31 | 5/5               | prio:P1, type:feature, portal, phase:5, sprint:7                          |
| #75  | Portal: Client portal — quote view + signing flow                                                             | 2026-03-31 | 5/5               | prio:P0, type:feature, portal, phase:2, sprint:5                          |
| #73  | Portal: SignWell e-signature integration                                                                      | 2026-03-31 | 5/5               | prio:P0, type:feature, portal, phase:2, sprint:5                          |
| #68  | Portal: Client CRUD + pipeline view                                                                           | 2026-03-31 | 5/5               | prio:P0, type:feature, portal, phase:1, sprint:4, status:review           |
| #66  | Portal: D1 schema & migrations                                                                                | 2026-03-31 | 5/5               | prio:P0, type:feature, portal, phase:1, sprint:2, status:review           |
| #365 | feat(portal): error and edge states for all portal surfaces                                                   | 2026-04-14 | 4/4               | prio:P2, status:ready, type:feature, layer:6-delivery                     |
| #364 | feat(portal): tap-to-SMS contact affordance across portal surfaces                                            | 2026-04-14 | 4/4               | prio:P2, status:ready, type:feature, layer:6-delivery                     |
| #368 | feat(portal): consultant photo hosting for ConsultantBlock                                                    | 2026-04-14 | 4/4               | prio:P3, status:ready, type:feature, layer:6-delivery                     |
| #200 | Add Problems Not Solved section to internal assessment guide                                                  | 2026-04-13 | 4/4               | prio:P2, status:triage, type:feature                                      |
| #194 | Maneuver Map (negative outcome → conversion path)                                                             | 2026-04-13 | 4/4               | prio:P2, status:triage, type:feature                                      |
| #208 | Define quarterly Intelligence Synthesis session process                                                       | 2026-04-13 | 4/4               | prio:P3, status:triage, type:feature                                      |
| #203 | Terrain Tactics reference card (vertical + channel)                                                           | 2026-04-13 | 4/4               | prio:P2, status:triage, type:feature                                      |
| #196 | Terrain Evaluation checklist for new opportunities                                                            | 2026-04-13 | 4/4               | prio:P2, status:triage, type:feature                                      |
| #189 | Phase 1-4 campaign timeline with success signals                                                              | 2026-04-13 | 4/4               | prio:P2, status:triage, type:feature                                      |
| #209 | Create IPSSA Arizona contact list for Phase 3                                                                 | 2026-04-13 | 4/4               | prio:P3, status:triage, type:feature                                      |
| #188 | ACCA Arizona membership and event calendar                                                                    | 2026-04-13 | 4/4               | prio:P2, status:triage, type:feature                                      |
| #109 | Lead Gen: Pipeline 4 — Social Listening                                                                       | 2026-04-13 | 4/4               | prio:P2, type:feature, layer:5-distribution, lead-gen, pipeline:4-social  |
| #218 | Booking: apply migration 0011 to production D1                                                                | 2026-04-13 | 4/4               | prio:P1, status:ready, type:feature, layer:5-distribution                 |
| #210 | Document Roads Not Followed with rationale                                                                    | 2026-04-13 | 4/4               | prio:P3, status:triage, type:docs                                         |
| #205 | Hypothesis by Vertical quick reference                                                                        | 2026-04-13 | 4/4               | prio:P2, status:triage, type:feature                                      |
| #206 | Add Pool Services as secondary sub-vertical to CLAUDE.md                                                      | 2026-04-13 | 4/4               | prio:P2, status:triage, type:docs                                         |
| #197 | Add Moral Law / Heaven readiness signals to assessment qualification                                          | 2026-04-13 | 4/4               | prio:P2, status:triage, type:feature                                      |
| #207 | Define maximum call duration (45 min) and end-of-call protocol                                                | 2026-04-13 | 4/4               | prio:P3, status:triage, type:feature                                      |
| #199 | Graceful Exit scripts for each hard disqualifier                                                              | 2026-04-13 | 4/4               | prio:P2, status:triage, type:feature                                      |
| #217 | Booking setup: snapshot prod D1 to R2 before applying migration 0011                                          | 2026-04-09 | 4/4               | prio:P1, status:ready, type:feature, layer:5-distribution                 |
| #214 | Booking setup: create Cloudflare Turnstile site for /book                                                     | 2026-04-09 | 4/4               | prio:P1, status:ready, type:feature, layer:5-distribution                 |
| #216 | Booking setup: generate BOOKING_ENCRYPTION_KEY and store in 1Password                                         | 2026-04-09 | 4/4               | prio:P1, status:ready, type:feature, layer:5-distribution                 |
| #79  | Portal: Parking lot protocol                                                                                  | 2026-03-31 | 4/4               | prio:P0, type:feature, portal, phase:4, sprint:6                          |
| #83  | Portal: Claude API extraction integration                                                                     | 2026-03-31 | 4/4               | prio:P1, type:feature, portal, phase:5, sprint:7                          |
| #69  | Portal: Contact CRUD + engagement roles                                                                       | 2026-03-31 | 4/4               | prio:P0, type:feature, portal, phase:1, sprint:4, status:review           |
| #67  | Portal: Admin authentication                                                                                  | 2026-03-31 | 4/4               | prio:P0, type:feature, portal, phase:1, sprint:3, status:review           |
| #71  | Portal: Claude extraction prompt (Deliverable #34)                                                            | 2026-03-31 | 4/4               | prio:P0, type:feature, portal, phase:1, sprint:1, status:review           |
| #65  | Portal: Project scaffolding — Astro SSR + Cloudflare bindings                                                 | 2026-03-31 | 4/4               | prio:P0, type:feature, portal, phase:1, sprint:1, status:review           |
| #198 | Assessor's Internal Monologue training doc                                                                    | 2026-04-13 | 3/3               | prio:P2, status:triage, type:feature                                      |
| #184 | Problem Heat Map for post-assessment capture doc                                                              | 2026-04-13 | 3/3               | prio:P2, status:triage, type:feature                                      |
| #204 | Add Strategic Value check to engagement decision process                                                      | 2026-04-13 | 3/3               | prio:P2, status:triage, type:feature                                      |
| #193 | Add HVAC as explicit primary sub-vertical to CLAUDE.md                                                        | 2026-04-13 | 3/3               | prio:P2, status:triage, type:docs                                         |
| #190 | Ready for Battle checklist in CLAUDE.md                                                                       | 2026-04-13 | 3/3               | prio:P2, status:triage, type:docs                                         |
| #195 | Add zheng/qi markers to assessment call script                                                                | 2026-04-13 | 3/3               | prio:P2, status:triage, type:feature                                      |
| #192 | Signal Log section for post-assessment capture doc                                                            | 2026-04-13 | 3/3               | prio:P2, status:triage, type:feature                                      |
| #185 | ROI anchor questions with exact phrasing                                                                      | 2026-04-13 | 3/3               | prio:P2, status:triage, type:feature                                      |
| #213 | Booking setup: create Cloudflare KV namespace BOOKING_CACHE                                                   | 2026-04-09 | 3/3               | prio:P1, status:ready, type:feature, layer:5-distribution                 |
| #87  | Decision: Resend DNS configuration for smd.services                                                           | 2026-04-01 | 3/3               | prio:P0, type:feature, portal, phase:2, manual                            |
| #80  | Portal: Client portal — documents + engagement progress                                                       | 2026-03-31 | 3/3               | prio:P1, type:feature, portal, phase:4, sprint:6                          |
| #78  | Portal: Time tracking (estimated vs actual)                                                                   | 2026-03-31 | 3/3               | prio:P1, type:feature, portal, phase:4, sprint:6                          |
| #89  | Portal: SOW template design                                                                                   | 2026-03-31 | 3/3               | prio:P0, type:feature, portal, phase:2, sprint:1, status:review           |

### Per-issue detail

#### #341 — E2E lifecycle sprint walkthrough — sample entity signal through repeat customer

Closed: 2026-04-13 · Labels: prio:P1, status:ready, type:feature, layer:5-distribution

- [ ] Entity walks through all 8 lifecycle stages (signal → prospect → assessing → proposing → engaged → delivered → prospect again)
- [ ] Enrichment modules produce context entries on promote
- [ ] Outreach draft generates and reads as human-written
- [ ] Assessment completion form transitions entity to proposing
- [ ] Quote totals calculate correctly ($175/hr × 30hrs = $5,250, 50% deposit)
- [ ] SOW PDF renders 3 pages with correct content
- [ ] SignWell receives and sends the document for signature
- [ ] Signing triggers atomic batch (9 records in single D1 transaction)
- [ ] Deposit invoice created with correct amount
- [ ] Stripe payment activates engagement (if test keys configured)
- [ ] Milestone completion with payment_trigger creates completion invoice
- [ ] Handoff sets safety_net_end (14 days), schedules follow-up cadence
- [ ] Re-engagement preserves full context timeline and prior engagement records

#### #219 — Booking: rewrite /book.astro with SlotPicker + intake form + Turnstile + 503 fallback panel

Closed: 2026-04-09 · Labels: prio:P1, status:ready, type:feature, layer:5-distribution

- [ ] \`src/components/booking/SlotPicker.astro\` shipped and reused by both \`/book\` and \`/book/manage/[token]\`
- [ ] \`/book\` renders with live slot data, no Calendly
- [ ] Submit round-trip works end-to-end in local dev
- [ ] All result states tested manually
- [ ] Email-fallback panel renders when reserve returns 503
- [ ] Mobile layout verified
- [ ] No AI-sounding copy (run through \`feedback_no_ai_copy\` mental check)
- [ ] Honeypot field works silently
- [ ] Full keyboard navigation works (no mouse required)
- [ ] VoiceOver / NVDA walk-through completes without dead ends
- [ ] axe-core or Lighthouse a11y audit shows zero violations
- [ ] All text/background pairs meet WCAG AA contrast
- [ ] No hardcoded URL strings — all outbound URLs via \`buildAppUrl\`

#### #222 — Booking: manage token flow — /book/manage/[token] page + GET/cancel/reschedule API

Closed: 2026-04-09 · Labels: prio:P1, status:ready, type:feature, layer:5-distribution

- [ ] All 3 API routes implemented
- [ ] \`src/pages/book/manage/[token].astro\` renders all 4 states
- [ ] Reschedule does NOT rotate the manage token (test asserts this)
- [ ] Compensating rollback on Google patch/delete failure
- [ ] Original manage URL still works after a reschedule
- [ ] Cancel email includes METHOD=CANCEL ICS
- [ ] Reschedule email warns about Outlook/Apple side-by-side display
- [ ] All tests passing
- [ ] All outbound URLs in email templates built via \`buildAppUrl\`

#### #220 — Booking: GET /api/booking/slots + POST /api/booking/reserve (atomic 3-phase critical path)

Closed: 2026-04-09 · Labels: prio:P1, status:ready, type:feature, layer:5-distribution

- [ ] Both endpoints implemented
- [ ] 3-phase atomic flow with db.batch() commit
- [ ] rollbackPlan correctly tracks new-vs-existing rows
- [ ] Compensating rollback runs on Google failure
- [ ] Email-fallback payload shape matches spec
- [ ] All reserve integration tests passing (including all orphan-row assertions)
- [ ] \`/slots\` handles freebusy failure gracefully
- [ ] Manual two-browser concurrency test passes (409 clean, no duplicates)
- [ ] All outbound URLs built via \`buildAppUrl\` — no hardcoded \`smd.services\` strings

#### #227 — Booking: admin UI audit for cancelled assessment status

Closed: 2026-04-13 · Labels: prio:P1, status:ready, type:feature, layer:5-distribution

- [ ] Every admin view rendering status shows \"Cancelled\" with an appropriate badge color (suggest neutral gray, not red — red implies failure/error, which cancellation isn't)
- [ ] Status filter dropdowns include \`cancelled\` as an option
- [ ] Bulk action menus handle cancelled correctly (terminal — no onward transitions)
- [ ] Status transition UI reads from \`VALID_TRANSITIONS\` (not hardcoded)
- [ ] Dashboard widgets / funnel metrics treat cancelled explicitly (decide: exclude from funnel, count separately, or count as lost — make the decision and document it)
- [ ] CSV exports handle cancelled
- [ ] Manual smoke test: insert a cancelled assessment into local D1, click through every admin screen, verify nothing renders \"unknown\" or breaks
- [ ] Unit tests for any new badge helpers / status label helpers

#### #224 — Booking: DAL modules + intake-core extraction (schedule.ts, integrations.ts, oauth-states.ts, intake-core.ts)

Closed: 2026-04-13 · Labels: prio:P1, status:ready, type:feature, layer:5-distribution

- [ ] All 4 modules created
- [ ] Every public export is typed and documented
- [ ] \`buildScheduleInsertStatement\` returns a prepared statement (not executed)
- [ ] \`consumeOAuthState\` is provably atomic under concurrent callers (test included)
- [ ] \`prepareIntakeStatements\` returns accurate exists flags
- [ ] All 4 test files passing
- [ ] No import from \`src/lib/booking/google/\` or any HTTP client — pure DB layer

#### #228 — Booking: Google sync error alerting (email Scott on first error in 30-min window)

Closed: 2026-04-09 · Labels: prio:P1, status:triage, type:feature, layer:5-distribution

- [ ] Alert mechanism decision documented (Resend email + context row recommended)
- [ ] \`src/lib/booking/alerts.ts\` helper implemented
- [ ] All 3 failure sites wired via \`ctx.waitUntil\`
- [ ] Rate-limited to 1 email per kind per 30 min
- [ ] Manual test: stub \`createEvent\` to fail, verify Scott receives one email, verify second failure within 30 min does NOT send a second email
- [ ] Manual test: verify context row is written on every failure (not just the first)
- [ ] Resend dashboard shows the alert email template sending correctly

#### #215 — Booking setup: create Google Cloud OAuth 2.0 client for Calendar API

Closed: 2026-04-09 · Labels: prio:P1, status:ready, type:feature, layer:5-distribution

- [ ] Google Cloud project selected / created
- [ ] Calendar API enabled
- [ ] OAuth consent screen configured with correct scopes
- [ ] OAuth 2.0 client ID created with both origins + both redirect URIs
- [ ] \`GOOGLE_CLIENT_ID\` and \`GOOGLE_CLIENT_SECRET\` set as worker secrets
- [ ] Both values in \`.dev.vars\` for local dev
- [ ] Client ID + JSON backup stored in 1Password

#### #225 — Booking: workers/booking-cleanup Cloudflare Worker with daily cron

Closed: 2026-04-09 · Labels: prio:P2, status:ready, type:feature, layer:5-distribution

- [ ] Worker scaffolded under \`workers/booking-cleanup/\` matching existing pattern
- [ ] \`wrangler.toml\` binds \`DB\` to the same database id as the main app
- [ ] Cron trigger \`0 4 \* \* \*\` configured
- [ ] Scheduled handler runs the two DELETE statements in a single \`db.batch()\`
- [ ] Logs row counts so \`wrangler tail\` shows cleanup activity
- [ ] Manual invoke succeeds against local D1 (\`wrangler dev --test-scheduled\` + curl to \`/\_\_scheduled\`)
- [ ] Deployed to prod and first scheduled run logs expected output

#### #74 — Portal: Client auth (magic links) + portal invitation

Closed: 2026-03-31 · Labels: prio:P0, type:feature, portal, phase:2, sprint:3, status:review

- [ ] Client receives magic link email
- [ ] Magic link authenticates on first click
- [ ] Magic link expires after 15 minutes
- [ ] Used magic links cannot be reused
- [ ] Client session lasts 7 days
- [ ] Login page is branded (SMD Services)
- [ ] Admin can re-send invitation if email bounces

#### #361 — feat(portal): redesign home dashboard — C-hybrid (action-centric + timeline)

Closed: 2026-04-14 · Labels: prio:P2, status:ready, type:feature, layer:6-delivery

- [ ] Mobile layout matches design within token tolerance; [Pay invoice] button top edge at y ≤ 700px on 390x844 viewport
- [ ] Desktop layout honors two-column split with right-rail action
- [ ] No nav tabs, no footer chrome, no testimonial-style content
- [ ] Consultant photo uses placeholder component (real photo arrives in follow-up)
- [ ] Status checker can name next milestone + next touchpoint within 10s of landing
- [ ] Primary acceptance test passes: action responder taps pay within first viewport on 390px, no scroll, first visit

#### #363 — feat(portal): redesign proposal landing — sign surface

Closed: 2026-04-14 · Labels: prio:P2, status:ready, type:feature, layer:6-delivery

- [ ] Route handles direct deep-link from proposal email
- [ ] Deliverables and week-by-week schedule render from quote data (not hardcoded)
- [ ] Sign CTA integrates with existing SignWell flow (note: #331, #325 fixes for SignWell field placement)
- [ ] Mobile and desktop layouts match designs
- [ ] Already-signed state handled (see error states issue)
- [ ] No marketing CTAs, no testimonials

#### #362 — feat(portal): redesign invoice landing — deep-link target

Closed: 2026-04-14 · Labels: prio:P2, status:ready, type:feature, layer:6-delivery

- [ ] Route handles direct deep-link navigation
- [ ] Line items render from invoice data; total is computed not hardcoded
- [ ] Payment CTA integrates with existing Stripe flow
- [ ] Mobile and desktop layouts match designs
- [ ] No marketing chrome, no testimonials, no legal footer injected
- [ ] Already-paid state renders correctly (see error states issue for other edge cases)

#### #110 — Lead Gen: Pipeline 5 — Partner Nurture + Buttondown

Closed: 2026-04-13 · Labels: prio:P2, type:feature, layer:5-distribution, lead-gen, pipeline:5-nurture

- [ ] Referral Partners sheet populated with 22 partners + tracking columns
- [ ] Claude produces emails in "we" voice, no pricing, no timeframes
- [ ] Gmail DRAFTS created (not auto-sent)
- [ ] Summary email to self: "X partner check-in drafts ready in Gmail"
- [ ] Sheet updated with check-in dates
- [ ] Human reviews every email before sending (permanent)

#### #107 — Lead Gen: Pipeline 1 — Review Mining (full build)

Closed: 2026-04-13 · Labels: prio:P1, type:feature, layer:5-distribution, lead-gen, pipeline:1-reviews

- [ ] 200+ Phoenix businesses in master list
- [ ] Weekly scan pulls only new reviews (7-day cutoff)
- [ ] Claude distinguishes operational vs. service quality correctly
- [ ] Batch scoring mode works (5-10 businesses per call)
- [ ] Qualified leads (pain_score >= 7) appear in Sheet with evidence quotes
- [ ] New leads show up in daily digest email

#### #106 — Lead Gen: Pipeline 2 — Job Posting Monitor (full build)

Closed: 2026-04-13 · Labels: prio:P1, type:feature, layer:5-distribution, lead-gen, pipeline:2-jobs

- [ ] 8 job title queries monitored daily
- [ ] Dedup working — no duplicates across runs
- [ ] Qualified leads appear in Google Sheet with all columns
- [ ] Daily digest email arrives each morning with new leads summary
- [ ] Craigslist RSS supplementary source active
- [ ] 3-day test run: qualified leads are actually good prospects

#### #105 — Lead Gen: Prompt library + shared schemas

Closed: 2026-04-13 · Labels: prio:P1, type:feature, layer:5-distribution, lead-gen

- [ ] All 5 schema files + 4 prompt files compile without TypeScript errors
- [ ] Shared schema re-exports ProblemId from extraction-schema (import, not duplicate)
- [ ] Each prompt includes few-shot examples
- [ ] All prompts enforce "we" voice in outreach angles
- [ ] Review scoring prompt distinguishes operational vs. service quality signals
- [ ] No dollar amounts or fixed timeframes in any prompt output

#### #72 — Quote builder UI + SOW PDF generation

Closed: 2026-04-10 · Labels: prio:P0, type:feature, layer:5-distribution, portal, phase:2, sprint:5

- [ ] Admin can create quote with line items
- [ ] Price auto-calculates from hours × rate
- [ ] SOW PDF generates with correct layout and project price (no hourly breakdown)
- [ ] PDF stored in R2 and linked to quote record
- [ ] Quote expiration enforced at 5 days
- [ ] Rate is frozen at quote creation time

#### #77 — Portal: Engagement management + milestones

Closed: 2026-03-31 · Labels: prio:P0, type:feature, portal, phase:4, sprint:6

- [ ] Engagement created with milestones from quote
- [ ] Milestone status transitions work
- [ ] Contact roles assigned per engagement
- [ ] Safety net date auto-calculated on handoff
- [ ] Completion invoice triggers at handoff milestone
- [ ] Client sees milestone progress in portal

#### #70 — Portal: Assessment capture + transcript upload

Closed: 2026-03-31 · Labels: prio:P0, type:feature, portal, phase:1, sprint:4, status:review

- [ ] Admin can create an assessment for a client
- [ ] Admin can upload a transcript file (stored in R2)
- [ ] Admin can paste/store extraction JSON
- [ ] Problems mapped to standard categories via UI
- [ ] Assessment status transitions work correctly
- [ ] Soft warning shown for financial problem prerequisite

#### #360 — feat(portal): foundation — data model + shared portal components for redesign

Closed: 2026-04-14 · Labels: prio:P1, status:ready, type:feature, layer:6-delivery

- [ ] Migration created and applied for consultant + touchpoint fields
- [ ] Engagement ledger computation verified on the test client (Delgado Plumbing / Precision Plumbing AZ)
- [ ] All 5 shared components have typed props and render in isolation (dev storybook or test page)
- [ ] Color tokens match brief appendix; typography scale matches
- [ ] Follow-up issues linked for consultant photo hosting, secondary contact, SMS

#### #108 — Lead Gen: Pipeline 3 — New Business Detection

Closed: 2026-04-13 · Labels: prio:P2, type:feature, layer:5-distribution, lead-gen, pipeline:3-newbiz

- [ ] SODA dataset IDs identified for Phoenix, Scottsdale, Chandler
- [ ] Daily permit queries returning commercial TI permits
- [ ] Claude qualification producing correct vertical matches
- [ ] ACC public records request process tested
- [ ] Qualified leads in Google Sheet

#### #226 — Booking: cutover test plan doc + delete legacy /book/thanks and intake route

Closed: 2026-04-13 · Labels: prio:P1, status:ready, type:feature, layer:5-distribution

- [ ] \`docs/booking-cutover-testplan.md\` exists and every checkbox is either checked or has a note explaining why it was skipped
- [ ] Legacy \`/book/thanks.astro\` deleted (one week after cutover)
- [ ] Legacy \`/api/booking/intake.ts\` deleted or reduced to a thin adapter
- [ ] Post-cutover 7-day monitoring window completed without critical issues
- [ ] Epic #212 closed

#### #202 — Attorney-specific one-liner and ROI hook

Closed: 2026-04-13 · Labels: prio:P2, status:triage, type:feature

- [ ] One-liner finalized
- [ ] ROI hook with specific numbers
- [ ] Pain cluster documented
- [ ] Professional services characteristics noted
- [ ] Aligns with Tone & Positioning Standard

#### #201 — Pool Services-specific one-liner and ROI hook

Closed: 2026-04-13 · Labels: prio:P2, status:triage, type:feature

- [ ] One-liner finalized
- [ ] ROI hook with specific numbers
- [ ] Pain cluster documented
- [ ] Phoenix-specific advantage documented
- [ ] Aligns with Tone & Positioning Standard

#### #187 — HVAC-specific one-liner and ROI hook

Closed: 2026-04-13 · Labels: prio:P2, status:triage, type:feature

- [ ] One-liner finalized
- [ ] ROI hook with specific numbers
- [ ] Pain cluster documented
- [ ] Usage contexts defined
- [ ] Aligns with Tone & Positioning Standard (CLAUDE.md)

#### #232 — Admin 'Book a Meeting' inline form + endpoint

Closed: 2026-04-13 · Labels: prio:P1, status:ready, type:feature, layer:5-distribution

- [ ] Inline panel replaces the "Book Assessment" button for prospect-stage entities
- [ ] SlotPicker shows real availability (Google busy + holds + scheduled assessments subtracted)
- [ ] Booking creates assessment, schedule sidecar, context entry, and advances entity to `assessing`
- [ ] Confirmation email sent to selected contact with ICS attachment and Meet link
- [ ] Double-booking is impossible (same availability source as public page)

#### #179 — test: route-level HTTP harness for inline-SQL admin endpoints

Closed: 2026-04-13 · Labels: prio:P2, type:tech-debt, source:code-review

- [ ] Harness can boot a test D1/SQLite with seeded fixtures for two orgs.
- [ ] Helper to invoke an Astro API route handler with a mocked session.
- [ ] Cross-org assertion helpers.
- [ ] Convert `resend-invitation.ts` to use it as the first example test.
- [ ] Documented in `CLAUDE.md` or `tests/README.md`.

#### #88 — Portal: Stripe account setup

Closed: 2026-04-10 · Labels: prio:P0, type:feature, portal, phase:3, manual

- [ ] Stripe account created and verified
- [ ] Branding configured
- [ ] ACH enabled as primary payment method
- [ ] Webhook endpoints registered
- [ ] Test invoice sends and payment processes in test mode

#### #223 — Booking: wire confirmation / reschedule / cancellation emails to Resend

Closed: 2026-04-09 · Labels: prio:P1, status:ready, type:feature, layer:5-distribution

- [ ] All 4 callsites wired and tested
- [ ] ICS attachment received by guest and imports cleanly to Google Calendar / Apple Calendar
- [ ] Resend dashboard shows all 4 email types sending (no delivery errors)
- [ ] Manual smoke test complete (book → reschedule → cancel, all 4 emails received and styled correctly)
- [ ] Callsites pass \`buildAppUrl\`-generated URLs to template functions — no hardcoded origins

#### #81 — Portal: Follow-up cadence automation

Closed: 2026-03-31 · Labels: prio:P1, type:feature, portal, phase:5, sprint:7

- [ ] Follow-ups auto-schedule on quote send
- [ ] Follow-ups auto-schedule on engagement handoff
- [ ] Dashboard shows upcoming/overdue/completed
- [ ] Admin can mark complete with notes
- [ ] At least one email template sends via Resend

#### #75 — Portal: Client portal — quote view + signing flow

Closed: 2026-03-31 · Labels: prio:P0, type:feature, portal, phase:2, sprint:5

- [ ] Client sees dashboard after login
- [ ] Quote view shows scope and total price without hourly breakdown
- [ ] "Review & Sign" button opens SignWell iframe
- [ ] Mobile-responsive on iOS Safari and Android Chrome
- [ ] Empty state shown if no quote is ready yet

#### #73 — Portal: SignWell e-signature integration

Closed: 2026-03-31 · Labels: prio:P0, type:feature, portal, phase:2, sprint:5

- [ ] Signature request created from generated SOW PDF
- [ ] Client can sign within embedded iframe on portal
- [ ] Webhook correctly transitions quote to "accepted"
- [ ] Signed PDF stored in R2
- [ ] Duplicate webhooks handled idempotently

#### #68 — Portal: Client CRUD + pipeline view

Closed: 2026-03-31 · Labels: prio:P0, type:feature, portal, phase:1, sprint:4, status:review

- [ ] Admin can create a client with all fields
- [ ] Admin can edit client details
- [ ] Client status updates on lifecycle events
- [ ] Pipeline view shows clients grouped/filtered by status
- [ ] Soft warning shown for employee count outside 10-25

#### #66 — Portal: D1 schema & migrations

Closed: 2026-03-31 · Labels: prio:P0, type:feature, portal, phase:1, sprint:2, status:review

- [ ] All tables created with correct constraints and CHECK clauses
- [ ] Migration resolves forward-reference issue (users.client_id → clients)
- [ ] SMD Services org seed record exists
- [ ] All indexes created per API query patterns
- [ ] JSON column contracts documented in migration comments

#### #365 — feat(portal): error and edge states for all portal surfaces

Closed: 2026-04-14 · Labels: prio:P2, status:ready, type:feature, layer:6-delivery

- [ ] Every listed error state has a designed UI (not a default error page)
- [ ] Each error includes the consultant presence and a concrete next step
- [ ] Photo fallback rendered consistently across all surfaces
- [ ] Tested manually via query parameter or test harness

#### #364 — feat(portal): tap-to-SMS contact affordance across portal surfaces

Closed: 2026-04-14 · Labels: prio:P2, status:ready, type:feature, layer:6-delivery

- [ ] Mobile tap opens SMS compose with consultant number pre-filled
- [ ] Desktop renders number inline with tap-to-call on mobile-sized desktops
- [ ] SLA commitment is confirmed by the consultant before the copy ships (not a marketing claim)
- [ ] Fallback plan documented for when consultant is unreachable (e.g., vacation autoresponder)

#### #368 — feat(portal): consultant photo hosting for ConsultantBlock

Closed: 2026-04-14 · Labels: prio:P3, status:ready, type:feature, layer:6-delivery

- [ ] Storage solution chosen and documented
- [ ] Admin upload flow works end-to-end
- [ ] Photo renders in \`<ConsultantBlock />\` on all portal surfaces
- [ ] Scott's actual photo hosted and deployed

#### #200 — Add Problems Not Solved section to internal assessment guide

Closed: 2026-04-13 · Labels: prio:P2, status:triage, type:feature

- [ ] Problem types documented
- [ ] "Why not" rationale for each
- [ ] Redirect language for each
- [ ] Clear distinction from scope exclusions (Decision #10)

#### #194 — Maneuver Map (negative outcome → conversion path)

Closed: 2026-04-13 · Labels: prio:P2, status:triage, type:feature

- [ ] Map documented with all common negative outcomes
- [ ] Conversion paths are specific and actionable
- [ ] Integrates with Decision #11 (Scope Creep Protocol)
- [ ] Integrates with Decision #22 (Accountant Partnership)

#### #208 — Define quarterly Intelligence Synthesis session process

Closed: 2026-04-13 · Labels: prio:P3, status:triage, type:feature

- [ ] Session questions documented
- [ ] Output format defined
- [ ] Cadence defined (every 3 engagements or quarterly)
- [ ] Owner identified (Captain review)

#### #203 — Terrain Tactics reference card (vertical + channel)

Closed: 2026-04-13 · Labels: prio:P2, status:triage, type:feature

- [ ] Tactics documented for each primary vertical
- [ ] Tactics documented for each primary channel
- [ ] Specific language/framing for each
- [ ] Single-page format

#### #196 — Terrain Evaluation checklist for new opportunities

Closed: 2026-04-13 · Labels: prio:P2, status:triage, type:feature

- [ ] Checklist documented
- [ ] Terrain types defined with examples
- [ ] Decision criteria clear
- [ ] Can be completed in 5-10 minutes per opportunity

#### #189 — Phase 1-4 campaign timeline with success signals

Closed: 2026-04-13 · Labels: prio:P2, status:triage, type:feature

- [ ] Timeline documented with specific weeks
- [ ] Success signals defined and measurable
- [ ] Phase gates explicit
- [ ] Tracking mechanism identified

#### #209 — Create IPSSA Arizona contact list for Phase 3

Closed: 2026-04-13 · Labels: prio:P3, status:triage, type:feature

- [ ] Contact list compiled
- [ ] Event calendar documented
- [ ] Membership pathway identified
- [ ] Ready to execute when Phase 3 begins

#### #188 — ACCA Arizona membership and event calendar

Closed: 2026-04-13 · Labels: prio:P2, status:triage, type:feature

- [ ] ACCA Arizona membership active
- [ ] Event calendar documented
- [ ] First event on calendar
- [ ] Speaking slot pathway identified

#### #109 — Lead Gen: Pipeline 4 — Social Listening

Closed: 2026-04-13 · Labels: prio:P2, type:feature, layer:5-distribution, lead-gen, pipeline:4-social

- [ ] Reddit OAuth2 working, search returns results
- [ ] Google Alerts arriving in labeled Gmail
- [ ] Daily email digest with numbered list of relevant posts
- [ ] Dedup working across days

#### #218 — Booking: apply migration 0011 to production D1

Closed: 2026-04-13 · Labels: prio:P1, status:ready, type:feature, layer:5-distribution

- [ ] Migration applied to prod
- [ ] Verification script reports all checks passing
- [ ] Smoke test: create an assessment with status=scheduled, then transition to cancelled (should succeed)
- [ ] Smoke test: attempt to insert two active assessments with same \`(org_id, scheduled_at)\` — second should fail with UNIQUE constraint

#### #210 — Document Roads Not Followed with rationale

Closed: 2026-04-13 · Labels: prio:P3, status:triage, type:docs

- [ ] All rejected sub-verticals documented with rationale
- [ ] All rejected channels documented with rationale
- [ ] Terrain type noted for each
- [ ] Principle stated clearly

#### #205 — Hypothesis by Vertical quick reference

Closed: 2026-04-13 · Labels: prio:P2, status:triage, type:feature

- [ ] Matrix for Home Services by size
- [ ] Matrix for Professional Services by size
- [ ] Sub-vertical specific notes
- [ ] Usage instructions

#### #206 — Add Pool Services as secondary sub-vertical to CLAUDE.md

Closed: 2026-04-13 · Labels: prio:P2, status:triage, type:docs

- [ ] Pool Services added to sub-vertical table
- [ ] Rationale documented
- [ ] Phase 3 timing noted
- [ ] IPSSA Arizona mentioned as gatekeeper

#### #197 — Add Moral Law / Heaven readiness signals to assessment qualification

Closed: 2026-04-13 · Labels: prio:P2, status:triage, type:feature

- [ ] Moral law signals documented
- [ ] Heaven (timing) signals documented
- [ ] Probe questions defined
- [ ] Integrated into assessment call script

#### #207 — Define maximum call duration (45 min) and end-of-call protocol

Closed: 2026-04-13 · Labels: prio:P3, status:triage, type:feature

- [ ] 45-minute maximum documented
- [ ] Phase timing breakdown documented
- [ ] End-of-call scripts for 35/40/45 min marks
- [ ] Traps documented

#### #199 — Graceful Exit scripts for each hard disqualifier

Closed: 2026-04-13 · Labels: prio:P2, status:triage, type:feature

- [ ] Script for each hard disqualifier
- [ ] Soft exit script for edge cases
- [ ] Language aligned with Tone & Positioning Standard
- [ ] Integrated into assessment call training

#### #217 — Booking setup: snapshot prod D1 to R2 before applying migration 0011

Closed: 2026-04-09 · Labels: prio:P1, status:ready, type:feature, layer:5-distribution

- [ ] Local backup file exists and is ≥700KB
- [ ] \`CREATE TABLE assessments\` found in backup
- [ ] Backup uploaded to \`ss-console-storage/backups/\`
- [ ] Restore procedure documented in 1Password

#### #214 — Booking setup: create Cloudflare Turnstile site for /book

Closed: 2026-04-09 · Labels: prio:P1, status:ready, type:feature, layer:5-distribution

- [ ] Turnstile site exists in Cloudflare dashboard
- [ ] \`PUBLIC_TURNSTILE_SITE_KEY\` committed in \`wrangler.toml\`
- [ ] \`TURNSTILE_SECRET_KEY\` set via \`wrangler secret put\` (verify with \`wrangler secret list\`)
- [ ] \`.dev.vars\` has the secret for local dev

#### #216 — Booking setup: generate BOOKING_ENCRYPTION_KEY and store in 1Password

Closed: 2026-04-09 · Labels: prio:P1, status:ready, type:feature, layer:5-distribution

- [ ] Key generated via \`openssl rand -base64 32\`
- [ ] Key stored in 1Password (durable, not just in terminal scrollback)
- [ ] \`BOOKING_ENCRYPTION_KEY\` set via \`wrangler secret put\` (verify with \`wrangler secret list\`)
- [ ] Key present in \`.dev.vars\` for local dev

#### #79 — Portal: Parking lot protocol

Closed: 2026-03-31 · Labels: prio:P0, type:feature, portal, phase:4, sprint:6

- [ ] Admin can log parking lot items during engagement
- [ ] Admin can disposition items at pre-handoff review
- [ ] Follow-on quote can be initiated from parking lot item
- [ ] Disposition notes captured

#### #83 — Portal: Claude API extraction integration

Closed: 2026-03-31 · Labels: prio:P1, type:feature, portal, phase:5, sprint:7

- [ ] Admin can trigger extraction from assessment view
- [ ] Claude API called with transcript + extraction prompt
- [ ] Extraction output shown for review before saving
- [ ] Admin can edit extraction before committing

#### #69 — Portal: Contact CRUD + engagement roles

Closed: 2026-03-31 · Labels: prio:P0, type:feature, portal, phase:1, sprint:4, status:review

- [ ] Admin can add contacts to a client
- [ ] Admin can assign contacts to engagement roles
- [ ] Same contact can be assigned multiple roles
- [ ] Primary POC flag is settable per engagement

#### #67 — Portal: Admin authentication

Closed: 2026-03-31 · Labels: prio:P0, type:feature, portal, phase:1, sprint:3, status:review

- [ ] Admin can log in with email + password
- [ ] Unauthenticated requests to `/admin/*` redirect to login
- [ ] Session persists across page loads (KV-backed)
- [ ] Session expires after 7 days of inactivity

#### #71 — Portal: Claude extraction prompt (Deliverable #34)

Closed: 2026-03-31 · Labels: prio:P0, type:feature, portal, phase:1, sprint:1, status:review

- [ ] Prompt template documented and version-controlled
- [ ] Output schema matches assessments.extraction JSON contract
- [ ] Tested against at least 1 sample transcript with realistic output
- [ ] Admin can paste extraction output into assessment record

#### #65 — Portal: Project scaffolding — Astro SSR + Cloudflare bindings

Closed: 2026-03-31 · Labels: prio:P0, type:feature, portal, phase:1, sprint:1, status:review

- [ ] `npm run dev` starts local dev server with D1/R2/KV bindings available
- [ ] `npm run build` produces a deployable Cloudflare Pages artifact
- [ ] Deployment pipeline pushes to Cloudflare Pages on PR merge
- [ ] `portal.smd.services` resolves to the deployed Pages project

#### #198 — Assessor's Internal Monologue training doc

Closed: 2026-04-13 · Labels: prio:P2, status:triage, type:feature

- [ ] Dual-track model documented
- [ ] Phase-by-phase internal monologue examples
- [ ] Training format (can be read in 10 min)

#### #184 — Problem Heat Map for post-assessment capture doc

Closed: 2026-04-13 · Labels: prio:P2, status:triage, type:feature

- [ ] Heat map section added to capture doc template
- [ ] Rating criteria documented
- [ ] Links to scope decisions in SOW

#### #204 — Add Strategic Value check to engagement decision process

Closed: 2026-04-13 · Labels: prio:P2, status:triage, type:feature

- [ ] Strategic value criteria documented
- [ ] Scoring guidance defined
- [ ] Integrated into solution design workflow

#### #193 — Add HVAC as explicit primary sub-vertical to CLAUDE.md

Closed: 2026-04-13 · Labels: prio:P2, status:triage, type:docs

- [ ] Sub-vertical tables added to CLAUDE.md
- [ ] Rationale documented for each
- [ ] Aligns with Decision #3 (Launch Verticals)

#### #190 — Ready for Battle checklist in CLAUDE.md

Closed: 2026-04-13 · Labels: prio:P2, status:triage, type:docs

- [ ] Checklist added to CLAUDE.md
- [ ] Links to relevant deliverables
- [ ] Clear "do not proceed until" language

#### #195 — Add zheng/qi markers to assessment call script

Closed: 2026-04-13 · Labels: prio:P2, status:triage, type:feature

- [ ] Every question in script marked as zheng or qi
- [ ] Purpose documented for each
- [ ] Assessor training includes understanding of direct/indirect force

#### #192 — Signal Log section for post-assessment capture doc

Closed: 2026-04-13 · Labels: prio:P2, status:triage, type:feature

- [ ] Signal Log section added to capture doc template
- [ ] Reference guide documented
- [ ] 3-5 signals required per call

#### #185 — ROI anchor questions with exact phrasing

Closed: 2026-04-13 · Labels: prio:P2, status:triage, type:feature

- [ ] All 6 questions documented with exact phrasing
- [ ] Integrated into assessment call script (Deliverable #36)
- [ ] Pause/silence technique documented

#### #213 — Booking setup: create Cloudflare KV namespace BOOKING_CACHE

Closed: 2026-04-09 · Labels: prio:P1, status:ready, type:feature, layer:5-distribution

- [ ] Namespace created in Cloudflare
- [ ] \`wrangler.toml\` has the real id committed
- [ ] \`npx wrangler kv namespace list \| grep BOOKING_CACHE\` shows the namespace

#### #87 — Decision: Resend DNS configuration for smd.services

Closed: 2026-04-01 · Labels: prio:P0, type:feature, portal, phase:2, manual

- [ ] DNS records added and verified
- [ ] Test email sends successfully from team@smd.services
- [ ] Email does not land in spam

#### #80 — Portal: Client portal — documents + engagement progress

Closed: 2026-03-31 · Labels: prio:P1, type:feature, portal, phase:4, sprint:6

- [ ] Client can view and download documents via presigned URLs
- [ ] Client sees milestone progress for active engagement
- [ ] Works on mobile

#### #78 — Portal: Time tracking (estimated vs actual)

Closed: 2026-03-31 · Labels: prio:P1, type:feature, portal, phase:4, sprint:6

- [ ] Admin can log time entries per engagement
- [ ] Estimated vs. actual hours comparison shown on engagement
- [ ] Aggregate view shows accuracy across all engagements

#### #89 — Portal: SOW template design

Closed: 2026-03-31 · Labels: prio:P0, type:feature, portal, phase:2, sprint:1, status:review

- [ ] SOW layout designed (sections, hierarchy, branding)
- [ ] Content template with placeholder fields mapped to quote data
- [ ] Approved for Forme template implementation

---

### Triage required

Each surfaced issue needs one of:

1. **Reopen** if the AC genuinely was not met and the work matters
2. **Apply `force-close` retro-label + comment** explaining why the AC is no longer applicable (scope change, superseded, etc.)
3. **Edit the issue body** to check the AC if it actually was satisfied and the box was just left unchecked

Move 2 (issue-close gate) will prevent this pattern going forward.
