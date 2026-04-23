/**
 * Diagnostic Artifact — typed template scaffold.
 *
 * A diagnostic artifact is a real post-engagement deliverable that documents
 * SMD Services' thinking process on a completed engagement. It is the
 * pre-launch compliant substitute for case studies: we cannot write case
 * studies until we have signed, completed, consented engagements, and
 * CLAUDE.md forbids fabricated client-facing content. See the parent epic
 * #483 and child issue #487 for context.
 *
 * What this file is.
 *   - The typed shape of a populated artifact.
 *   - An unpopulated `EMPTY_ARTIFACT` scaffold with placeholder prompts on
 *     every slot, matched one-for-one to `docs/style/diagnostic-artifact-content-rules.md`.
 *   - Validation helpers that block obvious out-of-bounds content (fabricated
 *     names, unsourced observations, outcome metrics claimed after handoff).
 *
 * What this file is NOT.
 *   - A populated artifact. No example artifact ships until the first real
 *     engagement completes (CLAUDE.md "No fabricated client-facing content").
 *   - A PDF renderer. Rendering lands in Phase 2 — see the PDF rendering
 *     path notes at the bottom of this file and `TODO(phase-2)` markers.
 *
 * Authority anchors:
 *   - CLAUDE.md § "No fabricated client-facing content" — governing rule.
 *   - CLAUDE.md § "Tone & Positioning Standard" — voice, collaborative
 *     posture, "we" / "our team" never "I".
 *   - `docs/style/diagnostic-artifact-content-rules.md` — the in-bounds /
 *     out-of-bounds lists this scaffold enforces.
 *   - `docs/style/empty-state-pattern.md` — when a slot lacks authored data,
 *     the template renders nothing or a `TBD` marker, never a fallback
 *     sentence. Same rule applies here.
 *
 * @see docs/style/diagnostic-artifact-content-rules.md
 * @see CLAUDE.md
 */

// ---------------------------------------------------------------------------
// Section primitives
// ---------------------------------------------------------------------------

/**
 * An observed signal — something SMD Services saw, heard, or directly
 * inspected during the engagement. Every observation MUST trace to a
 * source. Sources are the audit trail: if a reader asks "how do you know
 * that," the source answers.
 *
 * In-bounds sources (non-exhaustive):
 *   - "Assessment call, 2026-05-03" — a conversation we were in
 *   - "QuickBooks walkthrough" — a system we directly inspected
 *   - "Shared Drive: 'SOP - Intake.docx'" — a document the client gave us
 *   - "Owner's own words" — a direct quote, used only with written consent
 *
 * Out-of-bounds sources:
 *   - "Industry benchmark" — we do not publish benchmark claims we cannot verify
 *   - "Common pattern in {vertical}" — speculation about other businesses
 *   - "Owner mentioned off-hand" — if it is worth citing, get consent to cite it
 */
export interface ObservedSignal {
  /**
   * The observation itself. Neutral language, descriptive, non-judgmental.
   * CAN: describe what was observed ("Quotes are drafted in Google Docs
   * and re-typed into QuickBooks").
   * CANNOT: characterize the owner's judgment ("The owner never built a
   * real system"). Respect-owners rule applies — engagements are not
   * vehicles for shaming clients.
   */
  observation: string

  /**
   * Where the observation came from. A reader should be able to ask "how
   * do you know that" and the source answers. See ObservedSignal docstring
   * for the in-bounds / out-of-bounds source list.
   */
  source: string
}

/**
 * A pattern diagnosis — the problem(s) SMD Services identified from the
 * observed signals. Diagnoses are observations about the business, never
 * claims about the owner's character or past decisions.
 *
 * A diagnosis must be supported by observations. If a diagnosis has no
 * signals behind it, it is speculation and belongs elsewhere (or nowhere).
 */
export interface PatternDiagnosis {
  /**
   * The pattern. Descriptive, specific, traceable to one or more signals.
   * CAN: name a structural problem ("Intake and production systems do not
   * share a customer identifier, so handoffs happen by memory").
   * CANNOT: attribute the pattern to the owner's judgment ("The owner
   * hasn't prioritized data hygiene"). Patterns live in the business,
   * not in people.
   */
  pattern: string

  /**
   * Which observed signals support this diagnosis. Reference by the
   * signals' order in the ObservedSignal[] array (0-indexed), or leave
   * empty if the diagnosis is too broad for a single-signal trace.
   * Empty support arrays are a yellow flag — most diagnoses should cite
   * at least one signal.
   */
  supportingSignalIndices: number[]
}

/**
 * A trade-off considered during the engagement. Shows thinking process.
 * The artifact's differentiator from a generic deliverables list is that
 * it documents the decisions, not just the outputs.
 */
export interface TradeoffConsidered {
  /**
   * The decision point. Phrased as the question that was open.
   * CAN: "Rebuild the intake form in HubSpot vs. patch the existing Google
   * Form with a Zapier route to QuickBooks."
   * CANNOT: rewrite history to make every decision look obvious in
   * retrospect. If the right answer was not clear at the time, say so.
   */
  decisionPoint: string

  /**
   * Why the chosen path was chosen. Reasoning that a reader can evaluate.
   * CAN: "We chose the patch because the HubSpot rebuild would have
   * displaced the office manager's workflow mid-season."
   * CANNOT: invent a rationale we did not actually apply at the time.
   */
  rationale: string
}

/**
 * A concrete deliverable that shipped at handoff. Descriptive, not
 * promotional. Names what was built, not what it will achieve.
 */
export interface ShippedDeliverable {
  /**
   * The deliverable.
   * CAN: "Zapier route from Google Form to QuickBooks customer record,
   * with deduplication on email match."
   * CANNOT: make efficacy claims ("Will save 5 hours/week" — that is a
   * Pattern B projection, not an observable).
   */
  description: string
}

/**
 * Something SMD Services deliberately chose NOT to do during the
 * engagement. This is the restraint signal that personas cite as the
 * trust-earning move — it demonstrates scope discipline and shows the
 * engagement was scoped around the objective, not around selling more
 * work.
 */
export interface DeliberatelyNotDone {
  /**
   * What was not done. Specific.
   * CAN: "Did not migrate historical quote records into QuickBooks."
   * CANNOT: frame the omission as a judgment on the client ("The owner
   * did not want to pay for data migration"). The omission is ours.
   */
  item: string

  /**
   * Why it was not done. The reasoning is the signal.
   * CAN: "Historical quotes would not reach the new intake flow anyway,
   * so the migration cost was not justified by the objective."
   * CANNOT: invent a post-hoc rationalization. If the reason was cost or
   * time, say so plainly.
   */
  rationale: string
}

/**
 * An outcome that was observable at the time of handoff. Tightly scoped.
 *
 * This is the slot most vulnerable to Pattern B violations. A reader
 * wants to know "did it work" and the temptation is to say "yes." The
 * honest answer at handoff is almost always "here is what we can see
 * today, here is what we cannot yet see."
 *
 * In-bounds (observable at handoff):
 *   - "New intake form went live on 2026-05-28 and processed 14 leads by
 *     handoff on 2026-06-02."
 *   - "Office manager completed training and demonstrated the new workflow
 *     end-to-end during the handoff session."
 *
 * Out-of-bounds (requires follow-up to verify, not in scope here):
 *   - "Revenue increased by X% in the following quarter."
 *   - "Response time improved from 2 days to 2 hours."
 *   - "Client reported higher team morale."
 *
 * If follow-up observations land later and are authored with the client's
 * consent, they belong in a follow-up artifact, not this one.
 */
export interface OutcomeObservedAtHandoff {
  /** The observable fact as of `observedAt`. */
  outcome: string

  /**
   * The handoff date. Anchors the claim in time — a reader should see
   * that this is what we observed on day X, not a forward-looking
   * projection.
   */
  observedAt: string
}

// ---------------------------------------------------------------------------
// Top-level artifact shape
// ---------------------------------------------------------------------------

export interface DiagnosticArtifact {
  /**
   * Engagement title. Descriptive phrase, not a product name.
   * CAN: "Intake rebuild for a Phoenix HVAC contractor."
   * CANNOT: brand the engagement ("SMD's Flagship HVAC Solution").
   */
  title: string

  /**
   * Engagement date range, pre-formatted. Both dates are required.
   * Example: "May 2026 – June 2026".
   */
  dateRange: string

  /**
   * Who the engagement was with. Renders only when explicit written
   * consent to name the client has been received. Without consent, the
   * field is `null` and the "Who we worked with" section renders with the
   * generic context line only — never a fabricated business name.
   *
   * See CLAUDE.md "No fabricated client-facing content" and
   * `docs/style/empty-state-pattern.md`.
   */
  client: ClientAttribution | null

  observedSignals: ObservedSignal[]
  patternDiagnoses: PatternDiagnosis[]
  tradeoffsConsidered: TradeoffConsidered[]
  shippedDeliverables: ShippedDeliverable[]
  deliberatelyNotDone: DeliberatelyNotDone[]
  outcomesObservedAtHandoff: OutcomeObservedAtHandoff[]
}

export interface ClientAttribution {
  /**
   * The client's business name. Required when `consent.written === true`.
   * Never render a placeholder or stand-in name.
   */
  businessName: string

  /**
   * One-line context. Vertical, size class, geography if relevant. No
   * client quotes go here — those live under observed signals with
   * source attribution.
   * Example: "Phoenix-area HVAC contractor, 12 field technicians."
   */
  contextLine: string

  /**
   * Consent record. `written` must be `true` for the client name and
   * context line to render. Anything less than written consent means
   * the consumer must treat this artifact as anonymous.
   */
  consent: {
    /** Did the client sign a consent form or email an explicit yes? */
    written: boolean
    /** ISO date of consent capture. */
    capturedAt: string
    /** Where the consent record is stored (VCMS ref, filename, etc.). */
    storageRef: string
  }
}

// ---------------------------------------------------------------------------
// Empty scaffold — the authored starting point for a new artifact
// ---------------------------------------------------------------------------

/**
 * The scaffold an author copies when starting a new artifact. Every slot
 * carries a visible placeholder prompt that names what CAN go there and
 * what CANNOT. The scaffold is not a template to fill — it is a checklist
 * of questions to answer with real observations from a real engagement.
 *
 * Slot counts follow the issue #487 spec:
 *   - observedSignals: 3–5 slots
 *   - patternDiagnoses: 2–3 slots
 *   - tradeoffsConsidered: 2–4 slots
 *   - shippedDeliverables: 3–5 slots
 *   - deliberatelyNotDone: 1–3 slots
 *   - outcomesObservedAtHandoff: 2–3 slots
 *
 * If an engagement does not produce enough material to fill the minimum
 * counts, the artifact is not ready to ship. That is a feature, not a
 * constraint — an artifact with two thin observations is not a proof of
 * thinking, it is a proof of nothing.
 */
export const EMPTY_ARTIFACT: DiagnosticArtifact = {
  // PLACEHOLDER — replace with descriptive engagement phrase.
  // CAN: "Intake rebuild for a Phoenix HVAC contractor."
  // CANNOT: invent a branded engagement name or a superlative.
  title: '{ENGAGEMENT TITLE — descriptive phrase, not a branded name}',

  // PLACEHOLDER — replace with the real engagement dates.
  // CAN: "May 2026 – June 2026"
  // CANNOT: leave as a month range we did not actually work.
  dateRange: '{START MONTH YEAR – END MONTH YEAR}',

  // PLACEHOLDER — leave as `null` until written consent is on file. If the
  // client has consented, replace with a populated ClientAttribution
  // object. Never render a stand-in name.
  client: null,

  observedSignals: [
    // Slot 1 of 3–5.
    // CAN: a specific, neutral observation tied to a source.
    // CANNOT: a character claim about the owner. The observation lives in
    // the business, not in people.
    {
      observation: '{OBSERVATION 1 — specific, neutral, descriptive}',
      source: '{SOURCE — conversation date, system inspected, document seen}',
    },
    {
      observation: '{OBSERVATION 2}',
      source: '{SOURCE}',
    },
    {
      observation: '{OBSERVATION 3}',
      source: '{SOURCE}',
    },
    // Add slots 4 and 5 as needed. If the engagement did not produce five
    // observations worth citing, three is fine — do not pad.
  ],

  patternDiagnoses: [
    // Slot 1 of 2–3.
    // CAN: a structural pattern supported by one or more observed signals.
    // CANNOT: a diagnosis of the owner's competence, judgment, or past
    // decisions. Respect-owners rule applies.
    {
      pattern: '{DIAGNOSIS 1 — structural, observation-supported}',
      supportingSignalIndices: [],
    },
    {
      pattern: '{DIAGNOSIS 2}',
      supportingSignalIndices: [],
    },
  ],

  tradeoffsConsidered: [
    // Slot 1 of 2–4.
    // CAN: a real decision point and the reasoning applied at the time.
    // CANNOT: a rewritten narrative where the chosen path was obvious. If
    // the answer was not clear at the time, say so in the rationale.
    {
      decisionPoint: '{DECISION 1 — the question that was open during the engagement}',
      rationale: '{RATIONALE — why we chose what we chose, at the time we chose it}',
    },
    {
      decisionPoint: '{DECISION 2}',
      rationale: '{RATIONALE}',
    },
  ],

  shippedDeliverables: [
    // Slot 1 of 3–5.
    // CAN: a concrete, descriptive name for what was built or set up.
    // CANNOT: an efficacy claim ("Will save X hours/week"). Projections
    // about the future are Pattern B violations.
    {
      description: '{DELIVERABLE 1 — descriptive, not promotional}',
    },
    {
      description: '{DELIVERABLE 2}',
    },
    {
      description: '{DELIVERABLE 3}',
    },
  ],

  deliberatelyNotDone: [
    // Slot 1 of 1–3.
    // CAN: something we chose not to do, with the reason we chose not to.
    // CANNOT: frame the choice as the client's fault or a limitation they
    // imposed. The choice was ours.
    {
      item: '{ITEM NOT DONE 1 — what was out of scope by design}',
      rationale: '{RATIONALE — why we decided against it, at the time}',
    },
  ],

  outcomesObservedAtHandoff: [
    // Slot 1 of 2–3.
    // CAN: an observable fact as of the handoff date. If a number can be
    // cited, cite the source (e.g., "processed 14 leads between 2026-05-28
    // and 2026-06-02 per the new intake log").
    // CANNOT: a claim about what happens next. "Will reduce intake time"
    // is a Pattern B violation. "Observed 14 leads in the first five days"
    // is in-bounds because it is a counted fact with a time anchor.
    {
      outcome: '{OUTCOME 1 — observable fact as of the handoff date}',
      observedAt: '{HANDOFF DATE — YYYY-MM-DD}',
    },
    {
      outcome: '{OUTCOME 2}',
      observedAt: '{HANDOFF DATE}',
    },
  ],
}

// ---------------------------------------------------------------------------
// Validation helpers
// ---------------------------------------------------------------------------

/**
 * Flags that a populated artifact still contains unfilled placeholder
 * prompts. The validator looks for the literal `{` ... `}` brace pattern
 * used in `EMPTY_ARTIFACT` — if any remain in a populated artifact, the
 * artifact is not ready to ship.
 *
 * This is a presence check, not a fabrication check. It cannot stop an
 * author from inventing a quote and putting it in an observation — only
 * human review can do that. See content rules doc.
 */
export function findUnfilledPlaceholders(artifact: DiagnosticArtifact): string[] {
  const unfilled: string[] = []
  const placeholderPattern = /\{[A-Z][^}]*\}/

  if (placeholderPattern.test(artifact.title)) {
    unfilled.push('title')
  }
  if (placeholderPattern.test(artifact.dateRange)) {
    unfilled.push('dateRange')
  }
  artifact.observedSignals.forEach((s, i) => {
    if (placeholderPattern.test(s.observation)) unfilled.push(`observedSignals[${i}].observation`)
    if (placeholderPattern.test(s.source)) unfilled.push(`observedSignals[${i}].source`)
  })
  artifact.patternDiagnoses.forEach((d, i) => {
    if (placeholderPattern.test(d.pattern)) unfilled.push(`patternDiagnoses[${i}].pattern`)
  })
  artifact.tradeoffsConsidered.forEach((t, i) => {
    if (placeholderPattern.test(t.decisionPoint))
      unfilled.push(`tradeoffsConsidered[${i}].decisionPoint`)
    if (placeholderPattern.test(t.rationale)) unfilled.push(`tradeoffsConsidered[${i}].rationale`)
  })
  artifact.shippedDeliverables.forEach((d, i) => {
    if (placeholderPattern.test(d.description))
      unfilled.push(`shippedDeliverables[${i}].description`)
  })
  artifact.deliberatelyNotDone.forEach((x, i) => {
    if (placeholderPattern.test(x.item)) unfilled.push(`deliberatelyNotDone[${i}].item`)
    if (placeholderPattern.test(x.rationale)) unfilled.push(`deliberatelyNotDone[${i}].rationale`)
  })
  artifact.outcomesObservedAtHandoff.forEach((o, i) => {
    if (placeholderPattern.test(o.outcome)) unfilled.push(`outcomesObservedAtHandoff[${i}].outcome`)
    if (placeholderPattern.test(o.observedAt))
      unfilled.push(`outcomesObservedAtHandoff[${i}].observedAt`)
  })

  return unfilled
}

/**
 * Counts slots against the issue #487 minimums. An artifact below any
 * minimum is not ready to ship — see the `EMPTY_ARTIFACT` docstring for
 * why minimums exist.
 *
 * Returns the list of sections below their minimum, empty when the
 * artifact meets all minimums.
 */
export function findSectionsBelowMinimum(artifact: DiagnosticArtifact): string[] {
  const below: string[] = []
  if (artifact.observedSignals.length < 3) below.push('observedSignals (< 3)')
  if (artifact.patternDiagnoses.length < 2) below.push('patternDiagnoses (< 2)')
  if (artifact.tradeoffsConsidered.length < 2) below.push('tradeoffsConsidered (< 2)')
  if (artifact.shippedDeliverables.length < 3) below.push('shippedDeliverables (< 3)')
  if (artifact.deliberatelyNotDone.length < 1) below.push('deliberatelyNotDone (< 1)')
  if (artifact.outcomesObservedAtHandoff.length < 2) below.push('outcomesObservedAtHandoff (< 2)')
  return below
}

/**
 * Enforces the consent gate. When `client` is non-null, `consent.written`
 * must be `true`. Returns a list of consent violations; empty when the
 * artifact is compliant.
 */
export function findConsentViolations(artifact: DiagnosticArtifact): string[] {
  const violations: string[] = []
  if (artifact.client !== null) {
    if (!artifact.client.consent.written) {
      violations.push('client.consent.written is not true; client attribution must be removed')
    }
    if (!artifact.client.consent.capturedAt) {
      violations.push('client.consent.capturedAt is missing')
    }
    if (!artifact.client.consent.storageRef) {
      violations.push('client.consent.storageRef is missing')
    }
  }
  return violations
}

// ---------------------------------------------------------------------------
// PDF rendering path notes — Phase 2
// ---------------------------------------------------------------------------
//
// The diagnostic artifact will render to PDF through the same Forme WASM
// pipeline that produces SOWs and scorecard reports. The rendering
// implementation lands in Phase 2, after the first real engagement has
// populated an artifact and we can verify the visual treatment against
// real content.
//
// Integration shape (sketch):
//
//   1. New file: `src/lib/pdf/diagnostic-artifact-template.tsx`
//      - Mirrors `sow-template.tsx` structure: a React function component
//        that consumes `DiagnosticArtifact` props and emits Forme JSX
//        (`<Document>`, `<Page>`, `<View>`, `<Text>`).
//      - Reuses the shared color, font, margin, and heading styles from
//        `sow-template.tsx` and `scorecard-template.tsx` so all three
//        client-facing PDFs feel like one document family.
//      - Enforces the consent gate at render time: if `client !== null`
//        and `client.consent.written !== true`, throw before rendering.
//        The consent gate belongs in the renderer because that is the
//        last place the decision is reversible.
//
//   2. New export in `src/lib/pdf/render.ts`:
//
//      export async function renderDiagnosticArtifact(
//        artifact: DiagnosticArtifact
//      ): Promise<Uint8Array> {
//        await ensureWasm()
//        return renderDocument(DiagnosticArtifactTemplate(artifact))
//      }
//
//   3. Pre-render validation: call `findUnfilledPlaceholders`,
//      `findSectionsBelowMinimum`, and `findConsentViolations` before
//      handing the artifact to `renderDocument`. Any non-empty result
//      aborts the render with an error message pointing to the specific
//      slot. We have never shipped a PDF with a placeholder in it; we
//      should not start here.
//
//   4. Page layout (sketch — finalized against real content in Phase 2):
//      - Page 1: Title + date range. "Who we worked with" block. Observed
//        signals as a numbered list, each with its source line in muted
//        type underneath.
//      - Page 2: Pattern diagnoses. Trade-offs considered.
//      - Page 3: What shipped. What we decided not to do. Outcomes observed
//        at handoff with explicit "observed at: {date}" labels.
//      The artifact is deliberately short. If it runs past three pages,
//      the engagement's thinking is not being surfaced — it is being
//      buried.
//
// TODO(phase-2): implement `src/lib/pdf/diagnostic-artifact-template.tsx`.
// TODO(phase-2): implement `renderDiagnosticArtifact` in `render.ts`.
// TODO(phase-2): add pre-render validation calls that block unfilled /
//   below-minimum / consent-violating artifacts.
// TODO(phase-2): verify the layout against the first populated artifact.
//   Do not finalize the design against the empty scaffold.
// TODO(phase-2): file a follow-up issue to populate the first artifact
//   after the first engagement completes, per issue #487 AC #5.
