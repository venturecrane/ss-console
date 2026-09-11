/**
 * operator_voice_corrections (0102) — the console-side correction lifecycle
 * (ADR 0083 §4, #2091).
 *
 * A correction is an EDIT TO AN OUTPUT CLASS's property, which is what gives
 * "you correct it once and it stays corrected" a mechanism: auditable, visible,
 * surviving restarts, applying identically to every subsequent run. This module
 * owns the record of that edit — who made it, over which class property, and
 * which earlier correction it replaced.
 *
 * THE ONE INVARIANT THIS MODULE EXISTS TO HOLD. **Nothing here turns captured
 * text into spec bytes.** A row carries two texts and the difference between
 * them is the whole property: `statement` is what the Operator heard a customer
 * say, and is read by humans only; `promoted_body` is what a Named
 * Administrator authored and this console already wrote to R2. Restoring a
 * superseded correction replays `promoted_body` back to a person, who submits
 * it again through the reviewed form — it is never an automatic rewrite, and
 * `statement` is never a byte source under any path.
 *
 * That is not defensive habit. #2084 established that `read_file` is READ-class,
 * unfenced, and does not taint, so a spec the agent could write would be a
 * persistent, untainted, self-authored prompt-injection channel surviving
 * restarts. An agent that could promote its own captured correction into a spec
 * has exactly that, one step removed. The gap between capture and spec is the
 * feature; a convenience helper that closed it would be the vulnerability.
 * `tests/voice-corrections.test.ts` asserts the absence rather than trusting
 * this comment.
 *
 * WHERE CAPTURE LIVES, AND WHY THAT IS A DECISION RATHER THAN A DETAIL. Not
 * here. A captured correction is an append-only `CORRECTION_PROPOSED` row in the
 * seat's own audit ledger, written through the uid-gated `correction_propose`
 * broker verb (`operator/workspace_broker/corrections.py`).
 *
 * **Capture belongs where the agent is and cannot escalate; promotion belongs
 * where the human is.** The seat ledger is broker-uid-owned and the agent cannot
 * open it read-write, so "the agent cannot forge a promotion" is a filesystem
 * fact rather than a property of a credential it holds and might leak. Moving
 * capture into this database would mean putting a console-write credential in
 * the agent's environment — strictly weaker, and reopening what ADR 0023
 * locked-decision #10 closed by stripping exactly such a key in bootstrap.sh.
 * Two stores is the design; one store is the regression. The full argument is in
 * the header of `migrations/0102_operator_voice_corrections.sql`.
 *
 * OPEN GAP, NAMED SO IT IS NOT READ AS DONE. A capture reaches the console on
 * the existing `audit_log` runtime-read kind, but nothing yet PRESENTS the queue
 * of captures awaiting a decision. That needs a dedicated kind in
 * `hermes-smd-overlay` (`shared/runtime_read.py`) — a follow-up in that repo.
 * The promotion half below is complete; the visibility half is not, and no
 * `(runtime)` row of #2091 is closed by this module.
 */

/** The spec properties an output class carries. Mirrors SPEC_PROPERTIES. */
const PROPERTIES = ['voice', 'format'] as const

/** Bound on captured provenance text, mirroring the broker's own ceilings. */
const MAX_STATEMENT = 4000
const MAX_SHORT_TEXT = 200

/** Which property of the output class the correction edits (ADR 0083 §2-3). */
export type CorrectionProperty = 'voice' | 'format'

/**
 * Where the record came from. `agent_capture` is a witnessed statement;
 * `portal` is an administrator authoring directly with no capture behind it.
 * A column rather than an inference, because the distinction changes how a
 * reviewer should read the row.
 */
export type CorrectionOrigin = 'agent_capture' | 'portal'

/**
 * What a promotion records.
 *
 * `specSha256` and `specKey` are OUTCOMES: the caller passes the digest the
 * spec writer computed over the bytes it actually wrote, and the key it wrote
 * them to. Neither may originate in a request body. A digest that arrives from
 * a client authenticates nothing — whoever can write the object can write a
 * matching hash beside it — so trusting one would let a caller record a
 * promotion of bytes nobody wrote.
 */
export interface PromoteCorrectionInput {
  entityId: string
  customerSlug: string
  outputClass: string
  specProperty: CorrectionProperty
  /** NULL means firm-wide: the property holds regardless of who is reviewing. */
  reviewerUserId: string | null
  /**
   * What was captured, when a capture is being promoted. Provenance for the
   * reader; never the promoted bytes. NULL when an administrator authored
   * directly, with nothing witnessed behind it.
   */
  statement: string | null
  statedBy: string | null
  sourceRef: string | null
  /**
   * The bytes that were written — the administrator's authored text, the same
   * string `specSha256` digests. Kept so a superseded correction can be shown
   * back to a person and re-submitted; the supersession chain is otherwise a
   * list of ids recording that something changed without recording to what.
   */
  promotedBody: string
  origin: CorrectionOrigin
  priority: number
  promotedByUserId: string
  promotedByEmail: string
  /** The R2 key the spec writer proved it wrote. */
  specKey: string
  /** The digest the spec writer computed over those bytes, server-side. */
  specSha256: string
}

/**
 * Record a promotion, and retire whatever it replaced.
 *
 * Ordering is deliberate. The new row is inserted FIRST, then the previous live
 * correction for the same scope is pointed at it. Done the other way round, a
 * failure between the two statements would leave the scope with no live
 * correction at all — the portal would show nothing while the seat kept serving
 * the spec that was just superseded. Failing with two live rows is a visible,
 * recoverable state; failing with none is a silent one.
 *
 * Scope is (customer, class, property, reviewer). A firm-wide correction and a
 * per-reviewer one coexist rather than conflict, which is 0010's cross-cohort
 * rule carried forward onto the axis ADR 0083 actually uses.
 *
 * @returns the id of the newly promoted row.
 */
export async function promoteCorrection(
  db: D1Database,
  input: PromoteCorrectionInput
): Promise<string> {
  const id = crypto.randomUUID()
  const now = new Date().toISOString()

  await db
    .prepare(
      'INSERT INTO operator_voice_corrections ' +
        '(id, entity_id, customer_slug, output_class, spec_property, reviewer_user_id, ' +
        'statement, stated_by, source_ref, promoted_body, origin, priority, status, ' +
        'promoted_by_user_id, promoted_by_email, promoted_at, spec_key, spec_sha256, created_at) ' +
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'promoted', ?, ?, ?, ?, ?, ?)"
    )
    .bind(
      id,
      input.entityId,
      input.customerSlug,
      input.outputClass,
      input.specProperty,
      input.reviewerUserId,
      input.statement,
      input.statedBy,
      input.sourceRef,
      input.promotedBody,
      input.origin,
      input.priority,
      input.promotedByUserId,
      input.promotedByEmail,
      now,
      input.specKey,
      input.specSha256,
      now
    )
    .run()

  await supersedePriorPromotions(db, input, id)
  return id
}

/**
 * Point the previously promoted correction for this scope at its replacement.
 *
 * `superseded_by` is the restorable chain: nothing is deleted, so the earlier
 * text and the decision that retired it both stay readable. The `IS NOT
 * DISTINCT FROM` shape is written out longhand because SQLite's `=` does not
 * match NULL against NULL, and a firm-wide correction (reviewer NULL) must
 * supersede the previous firm-wide one rather than accumulate beside it.
 */
async function supersedePriorPromotions(
  db: D1Database,
  input: PromoteCorrectionInput,
  replacementId: string
): Promise<void> {
  await db
    .prepare(
      "UPDATE operator_voice_corrections SET status = 'superseded', superseded_by = ? " +
        "WHERE id != ? AND status = 'promoted' AND customer_slug = ? " +
        'AND output_class = ? AND spec_property = ? ' +
        'AND ((reviewer_user_id IS NULL AND ? IS NULL) OR reviewer_user_id = ?)'
    )
    .bind(
      replacementId,
      replacementId,
      input.customerSlug,
      input.outputClass,
      input.specProperty,
      input.reviewerUserId,
      input.reviewerUserId
    )
    .run()
}

// ---------------------------------------------------------------------------
// Citations
// ---------------------------------------------------------------------------

/**
 * Form field carrying one part of a capture citation.
 *
 * The authoring form may cite the capture a spec came from — the seat audit row
 * that recorded the statement, who stated it, and the statement itself as it
 * was shown to the administrator. One definition of the field name, for the
 * same reason `specFieldName` has one: a name two files spell independently is
 * a name that silently stops being submitted.
 */
export function citationFieldName(
  outputClass: string,
  property: CorrectionProperty,
  part: 'statement' | 'stated_by' | 'source_ref'
): string {
  return `correction[${outputClass}].${property}.${part}`
}

/** A cited capture, after parsing. Absent means the administrator authored directly. */
export interface CorrectionCitation {
  statement: string
  statedBy: string | null
  sourceRef: string | null
}

/** Key into the citation map: one output class property. */
export function citationKey(outputClass: string, property: CorrectionProperty): string {
  return `${outputClass}/${property}`
}

function boundedField(form: FormData, name: string, limit: number): string | null {
  const raw = form.get(name)
  if (typeof raw !== 'string') return null
  const text = raw.trim()
  if (text.length === 0) return null
  // Truncate rather than refuse. This is provenance beside a spec that was
  // already written and proven; discarding the whole save because a citation
  // ran long would lose the authored work over a footnote.
  return text.length > limit ? text.slice(0, limit) : text
}

/**
 * Read the capture citations off a submitted form.
 *
 * Iteration is over the DECLARED classes, never the form's own keys — the same
 * property that makes `collectAuthoredBodies` safe. A citation naming a class
 * the engagement never declared contributes nothing, because nothing here looks
 * for it.
 *
 * A citation without a statement is dropped: it would record that something was
 * witnessed without recording what, and the schema refuses that row anyway.
 */
export function collectCitations(
  form: FormData,
  classes: readonly string[]
): Map<string, CorrectionCitation> {
  const citations = new Map<string, CorrectionCitation>()
  for (const outputClass of classes) {
    for (const property of PROPERTIES) {
      const statement = boundedField(
        form,
        citationFieldName(outputClass, property, 'statement'),
        MAX_STATEMENT
      )
      if (statement === null) continue
      citations.set(citationKey(outputClass, property), {
        statement,
        statedBy: boundedField(
          form,
          citationFieldName(outputClass, property, 'stated_by'),
          MAX_SHORT_TEXT
        ),
        sourceRef: boundedField(
          form,
          citationFieldName(outputClass, property, 'source_ref'),
          MAX_SHORT_TEXT
        ),
      })
    }
  }
  return citations
}
