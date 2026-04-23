# Diagnostic artifact content rules

Content-scope rules governing what can and cannot appear in a diagnostic
artifact — the post-engagement deliverable that documents SMD Services'
thinking process on a completed engagement. Each rule is narrow, cited to
the project [CLAUDE.md](../../CLAUDE.md) or a public authority, and
documented with a concrete out-of-bounds phrase that would fail review.

This document exists because SMD Services is pre-launch with zero
completed engagements. Every target-client persona that reviewed
`smd.services` in April 2026 named "no case studies" as the credibility
gap. CLAUDE.md § "No fabricated client-facing content" forbids the
obvious fix (inventing case studies). The diagnostic artifact is the
compliant substitute: a real post-engagement deliverable that shows
thinking process rather than invented results. For the plan, see the
parent epic [#483](https://github.com/venturecrane/ss-console/issues/483)
and the child issue [#487](https://github.com/venturecrane/ss-console/issues/487).

The typed template scaffold lives at
[`src/lib/diagnostic/artifact-template.ts`](../../src/lib/diagnostic/artifact-template.ts).
Every rule in this document is reflected in that file's placeholder
comments and validation helpers.

It pairs with two existing specs:

- [`empty-state-pattern.md`](./empty-state-pattern.md) — when a section
  lacks authored data, render nothing or an explicit marker. The same
  rule applies to diagnostic artifacts: an observation slot without a
  real observation is omitted, never padded.
- [`UI-PATTERNS.md`](./UI-PATTERNS.md) — visual and component rules for
  rendered surfaces. When Phase 2 renders the artifact to PDF, the
  typography and spacing rules in that doc govern the visual treatment.

## Scope

These rules bind all diagnostic-artifact content authored in this repo,
including the typed template scaffold, any populated artifact TypeScript
modules, and the rendered PDFs. They do not govern internal engagement
notes, draft VCMS entries, or private working documents. The moment an
artifact is prepared for external distribution — shown to a prospect,
linked from the site, sent in a proposal — these rules apply.

## Authority anchors

Every rule cites either the project CLAUDE.md or a public,
URL-resolvable authority.

- **CLAUDE.md § "No fabricated client-facing content"** — the governing
  project rule. Pattern A (committed template sentences that imply
  uncontracted commitments) and Pattern B (runtime fabrication from
  non-authoritative fields) are the two violation shapes this document
  guards against.
- **CLAUDE.md § "Tone & Positioning Standard"** — voice rules (we / our
  team), collaborative posture, objectives-first framing.
- **CLAUDE.md § "Respect business owners"** (via global memory) — never
  shame the client whose engagement is being documented.
- **NN/g — Evidence-based design writing** —
  <https://www.nngroup.com/articles/evidence-based-design-writing/>.
  Traceable observations, not asserted conclusions.
- **Nielsen & Tahir, Homepage Usability, evidence principle** —
  <https://www.nngroup.com/articles/ten-usability-heuristics/>. Heuristic
  10, "Help and documentation," on the broader point that claims without
  traceable source lose trust on inspection.

---

## Rule 1 — Consent gates client attribution

**Rule.** A diagnostic artifact may name the client, describe the client's
business specifically, or quote the client only when written consent is on
file. Without written consent, the artifact is anonymous: no business
name, no identifiable details, no direct quotes.

**Authority.**

- CLAUDE.md § "No fabricated client-facing content": "Any information
  displayed to a client ... MUST come from data authored for that
  specific engagement."
- The template type `ClientAttribution` requires a `consent` record with
  `written: true`, `capturedAt`, and `storageRef`. See
  [`src/lib/diagnostic/artifact-template.ts`](../../src/lib/diagnostic/artifact-template.ts).

**In-bounds.**

- "Phoenix-area HVAC contractor, 12 field technicians" — when the
  client has given written consent to this level of identifiable detail.
- "A Phoenix-area home services business" — when consent has not been
  given. The artifact can describe the engagement without naming the
  business.

**Out-of-bounds.**

- Inventing a business name, even a plausible one ("Acme HVAC"). The
  empty-state pattern applies: no name is acceptable; a stand-in name
  is not.
- Naming the client in any section of the artifact when consent is not
  on file — not in the title, not in observations, not in quotes.
- Partial consent applied in full. If the client consented to be named
  but not quoted, the artifact can name them but cannot quote them.

**Detection.** `findConsentViolations()` in the template scaffold
enforces the type-level gate. A human reviewer verifies the consent
record matches what is actually on file before the artifact ships.

---

## Rule 2 — Observations must trace to a source

**Rule.** Every observed signal cites its source. A source is a
conversation (with date), a system that was directly inspected, a
document the client provided, or a direct quote (with written consent).
An observation without a source is speculation and does not belong in
the artifact.

**Authority.**

- CLAUDE.md Pattern B violation: "Values rendered from sources never
  authored as client-facing content." A sourceless observation is a
  Pattern B violation because the claim is ungrounded.
- NN/g on evidence-based writing: claims that cannot be traced to a
  source lose trust on inspection.

**In-bounds sources.**

- "Assessment call, 2026-05-03" — a conversation we were in.
- "QuickBooks walkthrough, 2026-05-10" — a system we directly
  inspected.
- "Shared Drive: 'SOP - Intake.docx'" — a document the client gave us.
- "Owner's own words, written consent on file" — a direct quote, used
  only when Rule 1's consent gate is open.

**Out-of-bounds sources.**

- "Industry benchmark" — we do not publish benchmark claims we cannot
  verify.
- "Common pattern in HVAC" — speculation about other businesses is
  out-of-bounds under Rule 5.
- "Owner mentioned off-hand" — if it is worth citing, get consent to
  cite it. If it is not worth citing, omit it.
- No source named at all. Any observation without a source fails
  review.

**Detection.** The template type `ObservedSignal` requires both
`observation` and `source` fields. The placeholder prompts name the
in-bounds and out-of-bounds source lists inline.

---

## Rule 3 — No character claims about the client

**Rule.** A pattern diagnosis describes the business, not the owner. A
structural problem lives in systems, workflows, and data flows. It does
not live in the owner's judgment, competence, or past decisions.

**Authority.**

- CLAUDE.md via global memory: "Respect business owners ... never write
  copy that judges owners; they're doing their best, frame gaps as
  normal growth pains."
- CLAUDE.md § "Tone & Positioning Standard" rule 2: "We are a peer
  working alongside the owner, not an expert arriving to audit them."

**In-bounds.**

- "Intake and production systems do not share a customer identifier, so
  handoffs happen by memory."
- "Quotes are drafted in Google Docs and re-typed into QuickBooks."
- "The team has no standard for what constitutes a qualified lead."

**Out-of-bounds.**

- "The owner never built a real system" — character claim.
- "The owner hasn't prioritized data hygiene" — judgment claim.
- "Leadership has been reactive for years" — a claim about the owner's
  track record, not about the business's current state.
- Any phrasing that implies the owner should have known better or done
  differently before we got there. Patterns live in the business; the
  artifact documents patterns, not blame.

**Detection.** Human review. No automated check catches tonal
violations — this is what the review gate in issue #487 AC #4 exists
for.

---

## Rule 4 — No outcome claims that post-date handoff

**Rule.** The artifact documents what was observable at the time of
handoff. Anything that requires follow-up verification — a month-later
metric, a quarter-later revenue change, a testimonial arriving after
the engagement closed — is out of scope for the artifact and belongs
in a follow-up artifact if it belongs anywhere.

**Authority.**

- CLAUDE.md Pattern B: "Runtime fabrication from non-authoritative
  fields." A forward-looking claim on a handoff artifact is a Pattern
  B violation because the claim is not yet observable.
- CLAUDE.md § "No fabricated client-facing content": we cannot verify
  what we did not observe, and an artifact that asserts verified
  outcomes we have not checked is the exact fabrication pattern that
  hotfix #378 removed from the proposal flow.

**In-bounds.**

- "New intake form went live on 2026-05-28 and processed 14 leads by
  handoff on 2026-06-02." A counted fact with a time anchor.
- "Office manager completed training and demonstrated the new workflow
  end-to-end during the handoff session." An observed event.
- "At handoff, the team reported the new process was operational; we
  did not observe a full production cycle before closing the
  engagement." An explicit statement of what was and was not
  observable.

**Out-of-bounds.**

- "Revenue increased by 30% the following quarter." Requires follow-up
  we did not perform.
- "Response time improved from 2 days to 2 hours." Requires a baseline
  and a post-measurement we did not take.
- "Client reported higher team morale." A follow-up testimonial, not a
  handoff observation.
- Any projection ("will reduce," "should improve," "expected to"). The
  artifact is a record of observation, not a forecast.

**Detection.** The template type `OutcomeObservedAtHandoff` requires
an `observedAt` date. Slot prompts explicitly call out projection
language as out-of-bounds. Human review catches the rest.

---

## Rule 5 — No speculation about other businesses or verticals

**Rule.** The artifact describes one engagement. It does not generalize
to other clients, other verticals, or other businesses "like this one."
Whatever pattern we observed, we observed in one place under one set of
conditions. Inference beyond that is out of scope.

**Authority.**

- CLAUDE.md § "No fabricated client-facing content": generalizations
  beyond the engagement are claims we cannot author from the engagement
  record.
- CLAUDE.md § "Tone & Positioning Standard" rule 1: we lead with the
  client's specific objectives, not with industry-wide claims.

**In-bounds.**

- "The contractor ran intake, scheduling, and quoting on three
  unconnected tools." One business's configuration, stated plainly.
- "The owner chose to keep QuickBooks rather than migrate, citing the
  six years of transaction history." One decision, with the reason
  given at the time.

**Out-of-bounds.**

- "Most HVAC contractors run this way." Speculation about other
  businesses.
- "In our experience, salons usually struggle with this." Generalization
  from one engagement to a vertical.
- "Similar patterns appear in professional services firms." Claims we
  cannot support from a single engagement.
- Any framing that positions this engagement as a proxy for all
  engagements in the vertical. One artifact is one artifact.

**Detection.** Human review. Vertical-generalization phrases are
recognizable on a read-through.

---

## Rule 6 — No invented advice

**Rule.** The artifact documents the advice we gave during the
engagement, not advice we would have given, could have given, or think
we should have given. If it was not in the engagement record, it does
not belong in the artifact.

**Authority.**

- CLAUDE.md Pattern A: "Committed template sentences that imply
  uncontracted commitments." Advice we did not give is uncontracted.
- Issue #487 scope: "Template must be ... specific enough to produce a
  document worth keeping" — kept by being accurate to the engagement,
  not by being rounded out with post-hoc recommendations.

**In-bounds.**

- "We recommended patching the existing intake form rather than
  rebuilding in HubSpot." A real decision point, a real
  recommendation, given during the engagement.
- "We deliberately did not propose a CRM migration. The objective was
  intake cleanup, not platform change." A scope choice we made at the
  time.

**Out-of-bounds.**

- "A further step would have been to automate the follow-up
  sequence." We did not give this advice; adding it here is invention.
- "In hindsight, we should have addressed X first." Hindsight is not
  engagement advice.
- "Future work could include ..." The artifact is about what happened,
  not about upsell.

**Detection.** The template types `TradeoffConsidered` and
`DeliberatelyNotDone` ask "what did we decide during the engagement,"
not "what might be done." Human review catches drift.

---

## Rule 7 — Voice discipline

**Rule.** The artifact uses SMD Services' voice standard. "We" and "our
team" throughout. Never "I" or "the consultant." Never "SMD Services
did X" (third-person self-reference). The tone is collaborative, not
diagnostic — we worked alongside the client, we did not audit them.

**Authority.**

- CLAUDE.md § "Tone & Positioning Standard" rule 6 and Decision Stack
  #20.
- CLAUDE.md § "Tone & Positioning Standard" rule 2: "We are a peer
  working alongside the owner, not an expert arriving to audit them."

**In-bounds.**

- "We observed that intake was manual from first touch to job site
  creation."
- "We chose the patch over the rebuild because the rebuild would have
  displaced the office manager mid-season."
- "We decided not to migrate historical quote records."

**Out-of-bounds.**

- "I noticed that ..." First-person singular.
- "SMD Services designed ..." Third-person self-reference.
- "The consultant recommended ..." Neither first-person nor natural
  voice.
- "We audited the intake process." Diagnostic framing; the collaborative
  verb is "worked through" or "observed," not "audited."

**Detection.** Human review. Grep for `\bI\b` and `consultant` on
populated artifacts as a soft check.

---

## Rule 8 — No AI prose

**Rule.** The artifact is written the way a human writes. No em dashes.
No parallel-structure sentences of the "not X, but Y" shape. No rhythmic
triplets ("fast, clean, and clear"). No corporate-polished phrases like
"leverage," "unlock," "synergy," "streamline," "empower." The artifact
sounds like someone who did the work wrote it.

**Authority.**

- CLAUDE.md via global memory: "No AI copy ... no em dashes, no parallel
  structures, no polished AI phrasing; copy must sound human-written."

**In-bounds.**

- "We saw quotes get drafted in one place and re-typed in another. That
  is where data was drifting."
- "The owner asked for an intake fix. We looked at the whole intake
  flow before touching anything."

**Out-of-bounds.**

- "We streamlined their intake, empowered their team, and unlocked new
  velocity." Corporate-polished verbs stacked.
- "Not just faster — smarter." The "not X, but Y" shape.
- "Observed. Diagnosed. Delivered." Rhythmic triplet.
- Any sentence with an em dash used as a rhetorical pivot. Use a period
  or a comma.

**Detection.** Human review. A read-aloud test surfaces most violations
within the first paragraph.

---

## In-bounds summary

A diagnostic artifact CAN contain:

- Observations traceable to a named source (conversation with date,
  system inspected, document seen, quote with written consent)
- Owner's own words, paraphrased or quoted, only with written consent
  on file
- Trade-offs considered during the engagement, including roads not
  taken
- Thinking process and reasoning applied at the time of the decision
- What was delivered, described neutrally and concretely
- Things observable at the time of handoff, anchored to the handoff
  date
- Explicit acknowledgment of what was not observable at handoff

## Out-of-bounds summary

A diagnostic artifact must never contain:

- Client name or identifiable details without written consent (Rule 1)
- Fabricated or stand-in client names (Rule 1, CLAUDE.md Pattern B)
- Observations without a traceable source (Rule 2)
- Character claims, judgment claims, or blame directed at the client
  (Rule 3)
- Outcome metrics requiring follow-up verification after handoff
  (Rule 4, CLAUDE.md Pattern B)
- Projections, forecasts, or "will improve" language (Rule 4)
- Generalizations to other clients, verticals, or "businesses like
  this one" (Rule 5)
- Advice we did not actually give during the engagement (Rule 6,
  CLAUDE.md Pattern A)
- Hindsight recommendations or "future work could include" upsell
  framing (Rule 6)
- First-person singular, third-person self-reference, or diagnostic
  verbs like "audited" (Rule 7)
- Em dashes, parallel structures, rhythmic triplets, or corporate
  polish verbs (Rule 8)
- Any other Pattern A (committed sentence implying uncontracted
  commitment) or Pattern B (runtime fabrication from non-authoritative
  fields) violation as defined in CLAUDE.md

## Enforcement

- **Type-level** — the TypeScript template at
  [`src/lib/diagnostic/artifact-template.ts`](../../src/lib/diagnostic/artifact-template.ts)
  requires sourced observations, consent records on client attribution,
  and handoff-dated outcomes. `findUnfilledPlaceholders`,
  `findSectionsBelowMinimum`, and `findConsentViolations` block the
  obvious mechanical failures.
- **Review gate** — no diagnostic artifact ships without Captain review
  against this document. Issue #487 AC #4 is the gate for first-time
  sign-off on the in-bounds / out-of-bounds lists.
- **Follow-up issue** — a child issue files after the first engagement
  completes to populate the first artifact. Population happens inside
  the bounds defined here; no relaxation of rules to fit whatever
  engagement comes first.

## Relationship to other specs

- [`empty-state-pattern.md`](./empty-state-pattern.md) — the
  render-nothing / marker pattern applies to unpopulated diagnostic
  artifact sections identically to how it applies to portal surfaces.
  An observation slot with no real observation is omitted, never
  padded.
- [`UI-PATTERNS.md`](./UI-PATTERNS.md) — when Phase 2 renders the
  artifact to PDF via the existing Forme pipeline, the typography and
  spacing rules govern the visual treatment.
- [`CLAUDE.md`](../../CLAUDE.md) — the project governing document.
  Pattern A and Pattern B violation definitions live there; this
  document applies them to the diagnostic-artifact surface.
