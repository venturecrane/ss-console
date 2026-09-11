/**
 * POST /api/portal/operator/settings/output-class-specs
 *
 * The write half of the authoring surface (ADR 0083, #2089). An Operator admin
 * says, in plain speech, how an output of a given class should sound or be
 * shaped; this endpoint stores that statement as a class property in the
 * customer's own vault object, where the seat's spec applier picks it up and
 * installs it root-owned.
 *
 * THE PORTAL NO LONGER POSTS HERE (ADR 0085 §7, #2163). The Advanced page's
 * spec-authoring form was demoted to a read-only window: establishment is a
 * conversational act performed against the Operator itself, and the portal's
 * role contracted to visibility and audit. What that ADR demoted was the FORM,
 * not this route. Everything below it — server-side hashing, merge-preserve,
 * the read-back proof, the promotion record — is the write seam the mediated
 * establishment path lands on, and `readSpecDocument` beside it is what the
 * demoted view reads. The remaining writer is that establishment intake; the
 * status redirects still point at the Advanced page, which still carries a
 * banner for each of them.
 *
 * WHAT MAKES THIS DIFFERENT FROM ITS NEIGHBOUR. `customer-yaml-update` in the
 * same directory validates and records and writes nothing, because
 * `customer.yaml` is git-authoritative. This one writes. That is the whole
 * point of the split ADR 0083 draws: the DECLARATION that a class expects a
 * spec is a commitment and moves through a PR; the spec CONTENT is prose the
 * customer edits, and a portal edit cannot reach git — so it goes to
 * `vaults/<slug>/output-classes.json`, a key space the git publisher is
 * structurally barred from touching, and vice versa.
 *
 * WHICH CLASSES ARE AUTHORABLE. Exactly the ones the customer's own
 * `output_classes:` block declares. Nothing here invents a class vocabulary:
 * a form field naming a class the seat did not declare is dropped, not stored,
 * so a hand-crafted POST cannot mint a spec for a class the engagement never
 * agreed to.
 *
 * THE DECLARED HASH IS COMPUTED HERE. The request carries bodies and nothing
 * else; the sha256 beside each body in the written document is computed
 * server-side over the bytes about to be written. A submitted digest is never
 * read. See `src/lib/operator/output-class-specs.ts`.
 *
 * STATUS IS AN OUTCOME, NOT AN INTENTION. `saved` is redirected only after the
 * object has been written AND read back byte-identical. Every other path says
 * what actually happened.
 */

import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { getCustomerConfigBySlug } from '../../../../../lib/portal/customer-config'
import { authorizeAdvancedSettings } from '../../../../../lib/portal/operator/advanced-settings-auth'
import { recordPortalActionEvent } from '../../../../../lib/portal/operator/action-events'
import { checkOutputClasses } from '../../../../../lib/operator/customer-yaml/sections-output-classes'
import type { ValidationError } from '../../../../../lib/operator/customer-yaml'
import {
  buildSpecDocument,
  collectAuthoredBodies,
  readSpecDocument,
  writeSpecDocument,
  SPEC_PROPERTIES,
  type SpecBuildFailure,
  type SpecDocument,
} from '../../../../../lib/operator/output-class-specs'
import {
  citationKey,
  collectCitations,
  promoteCorrection,
  type CorrectionCitation,
} from '../../../../../lib/portal/operator/voice-corrections'
import { buildAssertions, type Assertions } from '../../../../../lib/operator/format-assertions'

const OPERATOR_ROOT = '/portal/products/operator'

/**
 * One refusal, one sentence the person can act on.
 *
 * THE SERVER IS WHERE THE CEILING IS HELD. The form carries a `maxlength`, but
 * that attribute is absent from a hand-crafted POST and it counts characters
 * while the ceiling is in bytes, so it can only ever be a courtesy. When a body
 * really is over the ceiling the client is told to shorten it — not shown the
 * catch-all, which would leave them resubmitting the same text.
 *
 * Every value here has a matching banner in the Advanced page's
 * `STATUS_BANNERS`; `tests/output-class-specs.test.ts` pins that parity, because
 * a status with no banner renders as no message at all.
 */
const BUILD_FAILURE_STATUS: Record<SpecBuildFailure, string> = {
  body_too_long: 'spec_too_long',
  no_bodies: 'spec_empty',
  invalid_class: 'spec_invalid',
}

function redirectToAdvancedSettings(instance: string | null, status: string): Response {
  const base = instance ? `${OPERATOR_ROOT}/${instance}/settings/advanced` : OPERATOR_ROOT
  return new Response(null, {
    status: 303,
    headers: { Location: `${base}?status=${encodeURIComponent(status)}` },
  })
}

/**
 * The classes this customer declared, in `output_classes:`. Parsed with the
 * shared validator rather than cast — the projection column is JSON written by
 * the sync job, which is external input to this process.
 */
function declaredClasses(raw: unknown): string[] {
  const errors: ValidationError[] = []
  const declared = checkOutputClasses({ output_classes: raw }, errors)
  if (declared === null) return []
  return Object.keys(declared)
}

/**
 * Carry forward any class the form did not address.
 *
 * A class can leave `output_classes:` while its authored prose is still in the
 * vault. Rebuilding the document from the form alone would silently delete
 * that prose on the next unrelated save. Merging preserves it, and the write
 * stays idempotent for everything the form did address.
 */
function mergeUnaddressed(
  built: SpecDocument,
  existing: SpecDocument | null,
  addressed: readonly string[]
): SpecDocument {
  if (existing === null) return built
  const classes = { ...built.classes }
  for (const [slug, entry] of Object.entries(existing.classes)) {
    if (addressed.includes(slug)) continue
    classes[slug] = entry
  }
  return { schema_version: built.schema_version, classes }
}

/**
 * Every declared class's machine-checkable rules, or the first refusal.
 *
 * ALL CLASSES ARE VALIDATED BEFORE ANY IS ACCEPTED. Errors accumulate across
 * classes so one save reports everything wrong with it. A form that refused one
 * rule at a time would read as the surface moving the goalposts — the same
 * reasoning the seat's checker gives for returning all violations at once.
 */
function collectAssertions(
  form: FormData,
  classes: readonly string[]
): { ok: true; byClass: Map<string, Assertions> } | { ok: false; errors: readonly string[] } {
  const byClass = new Map<string, Assertions>()
  const errors: string[] = []
  for (const outputClass of classes) {
    const built = buildAssertions(form, outputClass)
    if (!built.ok) {
      errors.push(...built.errors.map((e) => `${outputClass}: ${e}`))
      continue
    }
    if (Object.keys(built.assertions).length > 0) byClass.set(outputClass, built.assertions)
  }
  if (errors.length > 0) return { ok: false, errors }
  return { ok: true, byClass }
}

async function recordAttempt(
  auth: { userId: string; userEmail: string; entityId: string; customerSlug: string },
  status: 'applied' | 'rejected',
  metadata: Record<string, unknown>
): Promise<void> {
  try {
    await recordPortalActionEvent(env.DB, {
      entity_id: auth.entityId,
      customer_slug: auth.customerSlug,
      action_type: 'output_class_spec_authored',
      actor_user_id: auth.userId,
      actor_email: auth.userEmail,
      actor_role: 'principal',
      source: 'portal',
      target: null,
      status,
      metadata,
    })
  } catch (err) {
    console.error('output-class-specs: failed to record portal_action_events row', err)
  }
}

/**
 * Record each written class property as a promoted correction (#2091).
 *
 * PROMOTION IS THIS ACT. ADR 0083 §4 makes a correction an edit to an output
 * class's property; this endpoint is where that edit is made, so this is where
 * it is recorded — with the person axis, the priority, and the supersession
 * chain that makes it restorable.
 *
 * THE PROMOTED BYTES ARE THE ADMINISTRATOR'S. `doc` was built from the form and
 * hashed server-side; the citation contributes provenance only — what was said
 * and where — and never a byte of the spec. An Operator's captured statement
 * therefore cannot reach a spec file by any path here, only by a person reading
 * it and choosing to write something.
 *
 * Best-effort, deliberately. The spec is already written and proven at this
 * point. A failure to record its provenance must not turn a completed write
 * into a reported failure — the caller would re-submit against a seat that
 * already has the spec.
 */
async function recordPromotions(
  auth: { userId: string; userEmail: string; entityId: string; customerSlug: string },
  doc: SpecDocument,
  addressed: readonly string[],
  citations: Map<string, CorrectionCitation>,
  specKey: string
): Promise<void> {
  for (const outputClass of addressed) {
    const entry = doc.classes[outputClass]
    if (!entry) continue
    for (const property of SPEC_PROPERTIES) {
      const written = entry[property]
      if (!written) continue
      const cited = citations.get(citationKey(outputClass, property)) ?? null
      try {
        await promoteCorrection(env.DB, {
          entityId: auth.entityId,
          customerSlug: auth.customerSlug,
          outputClass,
          specProperty: property,
          // Firm-wide. The per-reviewer axis exists in the schema and has no
          // authoring surface yet; inventing a reviewer here would be a
          // fabricated scope, so the column stays honestly NULL.
          reviewerUserId: null,
          statement: cited?.statement ?? null,
          statedBy: cited?.statedBy ?? null,
          sourceRef: cited?.sourceRef ?? null,
          // The administrator's authored bytes — the same string the digest
          // below covers — so a superseded correction can be shown back to a
          // person and re-submitted. Never `cited.statement`, which is only
          // ever read by a human and never becomes a spec.
          promotedBody: written.body,
          origin: cited === null ? 'portal' : 'agent_capture',
          priority: 0,
          promotedByUserId: auth.userId,
          promotedByEmail: auth.userEmail,
          specKey,
          // The digest computed over the bytes written, never a submitted one.
          specSha256: written.sha256,
        })
      } catch (err) {
        console.error(
          'output-class-specs: failed to record correction promotion for',
          `${auth.customerSlug}/${outputClass}.${property}`,
          err
        )
      }
    }
  }
}

export const POST: APIRoute = async ({ request, locals }) => {
  const form = await request.formData()
  const rawInstance = form.get('instance')
  const instance = typeof rawInstance === 'string' ? rawInstance : ''
  if (!instance) {
    return new Response(null, { status: 303, headers: { Location: OPERATOR_ROOT } })
  }

  const auth = await authorizeAdvancedSettings(env.DB, locals, instance)
  if (auth === null) return redirectToAdvancedSettings(instance, 'forbidden')

  const row = await getCustomerConfigBySlug(env.DB, auth.customerSlug)
  if (row === null) return redirectToAdvancedSettings(auth.customerSlug, 'no_config')

  const classes = declaredClasses(row.output_classes)
  if (classes.length === 0) return redirectToAdvancedSettings(auth.customerSlug, 'spec_no_classes')

  // Rules before prose. A refused rule must not be reported as a saved spec,
  // and the two halves come from one submission, so neither is written unless
  // both are acceptable.
  const rules = collectAssertions(form, classes)
  if (!rules.ok) {
    await recordAttempt(auth, 'rejected', { reason: 'invalid_rule', errors: rules.errors })
    return redirectToAdvancedSettings(auth.customerSlug, 'spec_invalid_rule')
  }

  const built = await buildSpecDocument(collectAuthoredBodies(form, classes), rules.byClass)
  if (!built.ok) {
    await recordAttempt(auth, 'rejected', { reason: built.reason, errors: built.errors })
    return redirectToAdvancedSettings(auth.customerSlug, BUILD_FAILURE_STATUS[built.reason])
  }

  // Read before write. An unparseable existing document must not be
  // overwritten: whatever prose it holds was authored by someone, and we could
  // not show it back to them. An R2 fault at either end is a refusal, never a
  // partial claim — the key-space guard throws rather than addressing a
  // neighbouring object, and that throw must land here, not as a 500.
  try {
    const existing = await readSpecDocument(env.CUSTOMER_CONFIG, auth.customerSlug)
    if (existing.kind === 'unreadable') {
      await recordAttempt(auth, 'rejected', { reason: 'existing_document_unreadable' })
      return redirectToAdvancedSettings(auth.customerSlug, 'spec_unreadable')
    }

    const merged = mergeUnaddressed(
      built.doc,
      existing.kind === 'document' ? existing.doc : null,
      classes
    )

    const written = await writeSpecDocument(env.CUSTOMER_CONFIG, auth.customerSlug, merged)
    if (!written.ok) {
      await recordAttempt(auth, 'rejected', { errors: written.errors })
      return redirectToAdvancedSettings(auth.customerSlug, 'spec_write_failed')
    }

    await recordPromotions(auth, merged, classes, collectCitations(form, classes), written.key)
    await recordAttempt(auth, 'applied', {
      key: written.key,
      bodies: written.bodies,
      classes: Object.keys(merged.classes).sort(),
    })
    return redirectToAdvancedSettings(auth.customerSlug, 'spec_saved')
  } catch (err) {
    console.error('output-class-specs: vault write failed for', auth.customerSlug, err)
    await recordAttempt(auth, 'rejected', { reason: 'vault_error' })
    return redirectToAdvancedSettings(auth.customerSlug, 'spec_write_failed')
  }
}
