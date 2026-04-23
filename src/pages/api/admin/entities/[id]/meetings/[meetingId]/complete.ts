import type { APIRoute } from 'astro'
import {
  getMeeting,
  updateMeeting,
  updateMeetingStatus,
} from '../../../../../../../lib/db/meetings'
import { getEntity, transitionStage, type EntityStage } from '../../../../../../../lib/db/entities'
import { appendContext } from '../../../../../../../lib/db/context'
import { env } from 'cloudflare:workers'

/**
 * POST /api/admin/entities/:id/meetings/:meetingId/complete
 *
 * Marks a meeting complete (#470). Captures outcome notes + duration, logs
 * the outcome to the entity context timeline, and optionally advances the
 * entity to an admin-chosen next stage.
 *
 * Deliberately does NOT draft a quote or force a stage transition —
 * those are separate explicit actions (see the draft-quote endpoint in
 * this same directory). This is the decoupling called for in issue #470.
 *
 * Protected by auth middleware (requires admin role).
 */
export const POST: APIRoute = async ({ request, locals, redirect, params }) => {
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

  const meetingUrl = `/admin/entities/${entityId}/meetings/${meetingId}`

  try {
    const meeting = await getMeeting(env.DB, session.orgId, meetingId)
    if (!meeting) {
      return redirect('/admin/entities?error=not_found', 302)
    }
    if (meeting.entity_id !== entityId) {
      return redirect(
        `/admin/entities/${meeting.entity_id}/meetings/${meetingId}?error=entity_mismatch`,
        302
      )
    }

    const entity = await getEntity(env.DB, session.orgId, entityId)
    if (!entity) {
      return redirect('/admin/entities?error=entity_not_found', 302)
    }

    const formData = await request.formData()

    const completionNotesRaw = formData.get('completion_notes')
    const completionNotes =
      typeof completionNotesRaw === 'string' && completionNotesRaw.trim()
        ? completionNotesRaw.trim()
        : null

    const durationRaw = formData.get('duration_minutes')
    const durationMinutes =
      typeof durationRaw === 'string' && durationRaw.trim()
        ? parseInt(durationRaw.trim(), 10) || null
        : null

    const nextStageRaw = formData.get('next_stage')
    const nextStage =
      typeof nextStageRaw === 'string' && nextStageRaw.trim()
        ? (nextStageRaw.trim() as EntityStage)
        : null

    // 1. Mark meeting completed (auto-sets completed_at)
    await updateMeetingStatus(env.DB, session.orgId, meetingId, 'completed')

    // Mirror to the legacy assessments table during the monitoring window.
    // meeting.id == assessment.id by construction.
    try {
      await env.DB.prepare(
        `UPDATE assessments
           SET status = 'completed',
               completed_at = COALESCE(completed_at, datetime('now'))
         WHERE id = ? AND org_id = ?`
      )
        .bind(meetingId, session.orgId)
        .run()
    } catch (err) {
      console.error(
        '[api/admin/entities/[id]/meetings/[meetingId]/complete] Legacy mirror failed:',
        err
      )
    }

    // 2. Persist completion notes + duration on the meeting
    await updateMeeting(env.DB, session.orgId, meetingId, {
      completion_notes: completionNotes,
      duration_minutes: durationMinutes,
    })

    // 3. Append outcome to the entity context timeline
    if (completionNotes) {
      await appendContext(env.DB, session.orgId, {
        entity_id: entityId,
        type: 'note',
        content: completionNotes,
        source: 'meeting_completion',
        source_ref: meetingId,
        metadata: {
          meeting_id: meetingId,
          meeting_type: meeting.meeting_type,
          duration_minutes: durationMinutes,
        },
      })
    }

    // 4. Transition stage only if the admin explicitly picked one. No default.
    if (nextStage && nextStage !== entity.stage) {
      try {
        await transitionStage(
          env.DB,
          session.orgId,
          entityId,
          nextStage,
          `Meeting completed — admin advanced stage from ${entity.stage} to ${nextStage}.`
        )
      } catch (stageErr) {
        const message = stageErr instanceof Error ? stageErr.message : 'stage_transition_failed'
        return redirect(`${meetingUrl}?error=${encodeURIComponent(message)}`, 302)
      }
    }

    return redirect(`${meetingUrl}?completed=1`, 302)
  } catch (err) {
    console.error('[api/admin/entities/[id]/meetings/[meetingId]/complete] Error:', err)
    return redirect(`${meetingUrl}?error=server`, 302)
  }
}
