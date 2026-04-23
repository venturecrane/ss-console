import type { APIRoute } from 'astro'
import { getMeeting } from '../../../../../../../lib/db/meetings'
import { getEntity, transitionStage } from '../../../../../../../lib/db/entities'
import { createQuote, type LineItem } from '../../../../../../../lib/db/quotes'
import { env } from 'cloudflare:workers'

/** Default hourly rate at launch (per Decision Stack #16, evolved). */
const DEFAULT_RATE = 175

/**
 * POST /api/admin/entities/:id/meetings/:meetingId/draft-quote
 *
 * Explicit admin action: draft a proposal quote from a meeting's notes
 * (#470). Separated from meeting completion so the same meeting UX works
 * for discovery calls, follow-ups, and check-ins without silently
 * producing a quote.
 *
 * Behavior:
 *   - Creates a new draft quote (line items start empty until the quote
 *     builder / Claude extraction fills them).
 *   - Transitions the entity to `proposing` if it's not already there.
 *   - Redirects to the new quote.
 *
 * Protected by auth middleware (requires admin role).
 */
export const POST: APIRoute = async ({ locals, redirect, params }) => {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const entityId = params.id
  const meetingId = params.meetingId
  if (!entityId || !meetingId) {
    return redirect('/admin/entities?error=missing', 302)
  }

  try {
    const meeting = await getMeeting(env.DB, session.orgId, meetingId)
    if (!meeting) {
      return redirect(`/admin/entities/${entityId}?error=meeting_not_found`, 302)
    }
    if (meeting.entity_id !== entityId) {
      return redirect(`/admin/entities/${meeting.entity_id}?error=entity_mismatch`, 302)
    }

    const entity = await getEntity(env.DB, session.orgId, entityId)
    if (!entity) {
      return redirect('/admin/entities?error=entity_not_found', 302)
    }

    // Line items start empty — the quote builder is the place to fill them.
    // Keeping this explicit (empty array) rather than invoking a Claude
    // extraction inline makes the action cheap and deterministic; the admin
    // can run extraction from the quote builder when ready.
    const lineItems: LineItem[] = []

    const quote = await createQuote(env.DB, session.orgId, {
      entityId,
      // meeting.id == assessment.id by construction; assessment_id is still
      // a NOT NULL column until the follow-up drop migration.
      assessmentId: meetingId,
      meetingId,
      lineItems,
      rate: DEFAULT_RATE,
    })

    // Advance to `proposing` unless the entity is already further along.
    try {
      await transitionStage(
        env.DB,
        session.orgId,
        entityId,
        'proposing',
        `Draft proposal generated from meeting ${meetingId}.`
      )
    } catch {
      // Transition may fail if entity is already proposing / engaged / etc. — fine.
    }

    return redirect(`/admin/entities/${entityId}/quotes/${quote.id}?saved=1`, 302)
  } catch (err) {
    console.error('[api/admin/entities/[id]/meetings/[meetingId]/draft-quote] Error:', err)
    return redirect(`/admin/entities/${entityId}?error=draft_quote_failed`, 302)
  }
}
