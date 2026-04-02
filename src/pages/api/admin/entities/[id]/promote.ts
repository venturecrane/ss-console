import type { APIRoute } from 'astro'
import { getEntity, transitionStage, updateEntity } from '../../../../../lib/db/entities'
import { appendContext, assembleEntityContext } from '../../../../../lib/db/context'
import { generateOutreachDraft } from '../../../../../lib/claude/outreach'
import { scheduleProspectCadence } from '../../../../../lib/follow-ups/scheduler'

/**
 * POST /api/admin/entities/[id]/promote
 *
 * One-click promote: signal → prospect.
 *
 * 1. Transition stage to prospect
 * 2. Assemble entity context → call Claude → generate outreach draft
 * 3. Append outreach draft as context entry
 * 4. Schedule prospect follow-up cadence
 * 5. Set next_action to "Send initial outreach"
 *
 * Outreach generation is best-effort — promote succeeds even if Claude call fails.
 */
export const POST: APIRoute = async ({ params, locals, redirect }) => {
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
    const env = locals.runtime.env

    // 1. Transition stage
    await transitionStage(env.DB, session.orgId, entityId, 'prospect', 'Promoted from signal.')

    // 2. Generate outreach draft (best-effort)
    try {
      const apiKey = env.ANTHROPIC_API_KEY
      if (apiKey) {
        const entity = await getEntity(env.DB, session.orgId, entityId)
        if (entity) {
          const context = await assembleEntityContext(env.DB, entityId, { maxBytes: 16_000 })

          if (context) {
            const draft = await generateOutreachDraft(apiKey, entity.name, context)

            // 3. Append outreach draft as context
            await appendContext(env.DB, session.orgId, {
              entity_id: entityId,
              type: 'outreach_draft',
              content: draft,
              source: 'claude',
              metadata: { model: 'claude-sonnet-4-20250514', trigger: 'promote' },
            })
          }
        }
      }
    } catch (outreachErr) {
      // Outreach generation is best-effort — log but don't fail the promote
      console.error('[promote] Outreach generation failed (non-blocking):', outreachErr)
    }

    // 4. Schedule prospect follow-up cadence
    try {
      await scheduleProspectCadence(env.DB, session.orgId, entityId, new Date().toISOString())
    } catch (cadenceErr) {
      console.error('[promote] Follow-up cadence scheduling failed (non-blocking):', cadenceErr)
    }

    // 5. Set next action
    await updateEntity(env.DB, session.orgId, entityId, {
      next_action: 'Review and send outreach email',
      next_action_at: new Date().toISOString(),
    })

    return redirect(`/admin/entities/${entityId}?promoted=1`, 302)
  } catch (err) {
    console.error('[api/admin/entities/promote] Error:', err)
    const message = err instanceof Error ? err.message : 'server'
    return redirect(`/admin/entities?error=${encodeURIComponent(message)}`, 302)
  }
}
