import type { APIRoute } from 'astro'
import { getEntity } from '../../../../../lib/db/entities'
import { listAssessments } from '../../../../../lib/db/assessments'
import { createQuote, getQuote, hasOpenQuoteForEntity } from '../../../../../lib/db/quotes'
import { env } from 'cloudflare:workers'

/**
 * POST /api/admin/entities/[id]/quotes
 *
 * Creates a new draft quote shell for an existing entity (#472). Supports the
 * repeat-quote-after-decline sales flow without forcing a second assessment.
 *
 * Preconditions enforced here:
 * - Entity exists and is in stage `proposing` or earlier (signal, prospect,
 *   meetings, proposing). Further-along stages already have an accepted quote
 *   or are out of the sales motion.
 * - No open (draft or sent) quote exists for the entity.
 * - Entity has at least one prior assessment — `quotes.assessment_id` is
 *   NOT NULL in the schema, so a first quote must still flow through the
 *   assessment builder. The earliest assessment on file is reused.
 *
 * The resulting quote is intentionally empty: zero line items, default rate,
 * no schedule, no deliverables, no engagement overview. All client-facing
 * fields default to NULL/empty. The quote builder enforces authored content
 * before sending (see updateQuoteStatus send-gating). This preserves the
 * "no fabricated client-facing content" invariant — shells never ship.
 *
 * The admin may optionally pass `parent_quote_id` to link the new quote as
 * a supersede of a prior quote. This mirrors the portal's version lookup
 * (`parent_quote_id = ?`) and does NOT transition the parent to
 * `superseded` — that remains an explicit admin action.
 *
 * Protected by auth middleware (requires admin role).
 */

const ELIGIBLE_STAGES = ['signal', 'prospect', 'meetings', 'proposing']

/** Default hourly rate at launch (per Decision Stack #16, evolved). */
const DEFAULT_RATE = 175

export const POST: APIRoute = async ({ params, request, locals, redirect }) => {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const entityId = params.id
  if (!entityId) {
    return redirect('/admin/entities?error=missing', 302)
  }

  try {
    const entity = await getEntity(env.DB, session.orgId, entityId)
    if (!entity) {
      return redirect('/admin/entities?error=not_found', 302)
    }

    if (!ELIGIBLE_STAGES.includes(entity.stage)) {
      return redirect(
        `/admin/entities/${entityId}?error=${encodeURIComponent(
          'Cannot create a new quote from this stage. Entity must be in Proposing or earlier.'
        )}`,
        302
      )
    }

    const openQuote = await hasOpenQuoteForEntity(env.DB, session.orgId, entityId)
    if (openQuote) {
      return redirect(
        `/admin/entities/${entityId}?error=${encodeURIComponent(
          'Cannot create a new quote: an existing draft or sent quote is still active. Decline, expire, or supersede it first.'
        )}`,
        302
      )
    }

    // Reuse the most recent assessment on file. `assessment_id` is NOT NULL in
    // the schema, so we cannot create a quote for an entity that has never had
    // one. If we ever support assessment-less quotes, the schema will need a
    // migration first — not in scope for #472.
    const assessments = await listAssessments(env.DB, session.orgId, entityId)
    if (assessments.length === 0) {
      return redirect(
        `/admin/entities/${entityId}?error=${encodeURIComponent(
          'Cannot create a new quote: this entity has no assessment on file. Book an assessment before creating a quote.'
        )}`,
        302
      )
    }
    // listAssessments orders by created_at DESC, so [0] is the most recent.
    const latestAssessmentId = assessments[0].id

    // Parse optional supersede reference from form data. The admin explicitly
    // opts into supersede by picking a prior quote in the form; absence means
    // this is a standalone new quote and no supersede link is set.
    const formData = await request.formData().catch(() => null)
    const parentQuoteIdRaw = formData?.get('parent_quote_id')
    let parentQuoteId: string | null = null
    if (typeof parentQuoteIdRaw === 'string' && parentQuoteIdRaw.trim() !== '') {
      // Validate the referenced quote exists on this entity/org. Silently drop
      // if it doesn't — we don't want to persist a broken foreign key.
      const parent = await getQuote(env.DB, session.orgId, parentQuoteIdRaw.trim())
      if (parent && parent.entity_id === entityId) {
        parentQuoteId = parent.id
      }
    }

    const quote = await createQuote(env.DB, session.orgId, {
      entityId,
      assessmentId: latestAssessmentId,
      lineItems: [],
      rate: DEFAULT_RATE,
      parentQuoteId,
    })

    return redirect(`/admin/entities/${entityId}/quotes/${quote.id}?saved=1`, 302)
  } catch (err) {
    console.error('[api/admin/entities/[id]/quotes] Error:', err)
    const message = err instanceof Error ? err.message : 'server'
    return redirect(`/admin/entities/${entityId}?error=${encodeURIComponent(message)}`, 302)
  }
}
