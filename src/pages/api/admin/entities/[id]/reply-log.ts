import type { APIRoute } from 'astro'
import { appendContext } from '../../../../../lib/db/context'
import { getEntity } from '../../../../../lib/db/entities'
import { env } from 'cloudflare:workers'

/**
 * POST /api/admin/entities/[id]/reply-log
 *
 * Log a prospect reply received via Gmail (or other inbox). Creates a context
 * entry of type `note` with source `reply_log` and structured metadata capturing
 * sentiment + suggested next action.
 *
 * Does NOT auto-transition entity stage — admin picks the next move manually
 * (see issue #464 AC).
 */

export const REPLY_SENTIMENTS = ['interested', 'declined', 'out_of_office', 'other'] as const
export type ReplySentiment = (typeof REPLY_SENTIMENTS)[number]

export const REPLY_NEXT_ACTIONS = [
  'book_meeting',
  'retry_later',
  'mark_lost',
  'continue_conversation',
] as const
export type ReplyNextAction = (typeof REPLY_NEXT_ACTIONS)[number]

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

    const formData = await request.formData()
    const sentimentRaw = formData.get('sentiment')
    const nextActionRaw = formData.get('next_action')
    const notesRaw = formData.get('notes')

    const sentiment = typeof sentimentRaw === 'string' ? sentimentRaw : ''
    const nextAction = typeof nextActionRaw === 'string' ? nextActionRaw : ''
    const notes = typeof notesRaw === 'string' ? notesRaw.trim() : ''

    if (!REPLY_SENTIMENTS.includes(sentiment as ReplySentiment)) {
      return redirect(`/admin/entities/${entityId}?error=invalid_sentiment`, 302)
    }
    if (!REPLY_NEXT_ACTIONS.includes(nextAction as ReplyNextAction)) {
      return redirect(`/admin/entities/${entityId}?error=invalid_next_action`, 302)
    }

    // Content is a human-readable summary. If the admin provided notes, use them
    // verbatim; otherwise record the structured signal without inventing prose.
    const content = notes.length > 0 ? notes : `Reply logged: ${sentiment} / ${nextAction}`

    await appendContext(env.DB, session.orgId, {
      entity_id: entityId,
      type: 'note',
      content,
      source: 'reply_log',
      metadata: {
        sentiment,
        next_action: nextAction,
      },
    })

    return redirect(`/admin/entities/${entityId}?reply_logged=1`, 302)
  } catch (err) {
    console.error('[api/admin/entities/reply-log] POST Error:', err)
    return redirect(`/admin/entities/${entityId}?error=server`, 302)
  }
}
