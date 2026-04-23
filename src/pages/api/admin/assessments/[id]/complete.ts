import type { APIRoute } from 'astro'
import {
  getAssessment,
  updateAssessment,
  updateAssessmentStatus,
} from '../../../../../lib/db/assessments'
import { getEntity, transitionStage } from '../../../../../lib/db/entities'
import { appendContext } from '../../../../../lib/db/context'
import { createQuote, type LineItem } from '../../../../../lib/db/quotes'
import { uploadTranscript } from '../../../../../lib/storage/r2'
import { env } from 'cloudflare:workers'

/** Default hourly rate at launch (per Decision Stack #16, evolved). */
const DEFAULT_RATE = 175

/**
 * POST /api/admin/assessments/:id/complete
 *
 * Completes an assessment: builds extraction JSON, transitions status,
 * appends context, creates a draft quote, and redirects to the new quote.
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

  const assessmentId = params.id
  if (!assessmentId) {
    return new Response(JSON.stringify({ error: 'Assessment ID required' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  try {
    const assessment = await getAssessment(env.DB, session.orgId, assessmentId)
    if (!assessment) {
      return redirect('/admin/entities?error=not_found', 302)
    }

    const entity = await getEntity(env.DB, session.orgId, assessment.entity_id)
    if (!entity) {
      return redirect('/admin/entities?error=entity_not_found', 302)
    }

    const formData = await request.formData()

    // 1. Build extraction JSON from form data
    const problemKeys = [
      'process_design',
      'tool_systems',
      'data_visibility',
      'customer_pipeline',
      'team_operations',
    ]
    const problems: string[] = []
    for (const key of problemKeys) {
      if (formData.get(key) === 'on') {
        problems.push(key)
      }
    }
    const otherProblem = formData.get('other_problem')
    if (otherProblem && typeof otherProblem === 'string' && otherProblem.trim()) {
      problems.push(`other: ${otherProblem.trim()}`)
    }

    const disqualified = formData.get('disqualified') === 'on'
    const disqualifyReason = formData.get('disqualify_reason')
    const durationStr = formData.get('duration_minutes')
    const duration =
      durationStr && typeof durationStr === 'string' ? parseInt(durationStr, 10) || null : null
    const notes = formData.get('notes')
    const notesStr = notes && typeof notes === 'string' ? notes.trim() : ''

    const extraction = {
      problems,
      disqualified,
      disqualify_reason:
        disqualified && disqualifyReason && typeof disqualifyReason === 'string'
          ? disqualifyReason.trim()
          : null,
      duration_minutes: duration,
      notes: notesStr,
      completed_at: new Date().toISOString(),
    }

    // Handle transcript upload if provided
    let transcriptPath: string | undefined
    const transcriptFile = formData.get('transcript')
    if (transcriptFile && transcriptFile instanceof File && transcriptFile.size > 0) {
      transcriptPath = await uploadTranscript(
        env.STORAGE,
        session.orgId,
        assessmentId,
        transcriptFile
      )
    }

    // 2. Update assessment status to completed (auto-sets completed_at)
    await updateAssessmentStatus(env.DB, session.orgId, assessmentId, 'completed')

    // 3. Write extraction JSON and duration to assessment
    await updateAssessment(env.DB, session.orgId, assessmentId, {
      extraction: JSON.stringify(extraction),
      duration_minutes: duration,
      ...(transcriptPath ? { transcript_path: transcriptPath } : {}),
    })

    // 4. Append extraction context entry on the entity
    await appendContext(env.DB, session.orgId, {
      entity_id: entity.id,
      type: 'extraction',
      content: JSON.stringify(extraction, null, 2),
      source: 'assessment_completion',
      source_ref: assessmentId,
    })

    // If disqualified, transition to lost and redirect back.
    // Disqualification during assessment maps to the `not-a-fit` lost
    // reason code — see src/lib/db/lost-reasons.ts. The extraction's
    // free-text explanation is carried in `lost_detail` so the admin
    // can still read the LLM's reasoning when reviewing the Lost tab.
    if (disqualified) {
      try {
        await updateAssessmentStatus(env.DB, session.orgId, assessmentId, 'disqualified')
        await transitionStage(
          env.DB,
          session.orgId,
          entity.id,
          'lost',
          `Disqualified during assessment: ${extraction.disqualify_reason ?? 'No reason provided'}`,
          {
            lostReason: {
              code: 'not-a-fit',
              detail: extraction.disqualify_reason ?? null,
            },
          }
        )
      } catch {
        // Stage transition may fail if already in lost state
      }
      return redirect(`/admin/entities/${entity.id}?assessment_completed=1`, 302)
    }

    // 5. Transition entity stage to proposing
    try {
      await transitionStage(env.DB, session.orgId, entity.id, 'proposing', 'Assessment completed.')
    } catch {
      // May fail if already proposing or further along
    }

    // 6. Generate quote line items (best-effort, #236 not yet built)
    let lineItems: LineItem[] = []
    try {
      // generateQuoteLineItems is #236 -- not yet implemented.
      // When it ships, import and call it here:
      // const { generateQuoteLineItems } = await import('../../../../../lib/claude/quote-lines')
      // lineItems = await generateQuoteLineItems(extraction, entityContext)
      lineItems = []
    } catch {
      lineItems = []
    }

    // 7. Create draft quote with pre-filled line items
    const quote = await createQuote(env.DB, session.orgId, {
      entityId: entity.id,
      assessmentId,
      lineItems,
      rate: DEFAULT_RATE,
    })

    // 8. Redirect to quote builder
    return redirect(`/admin/entities/${entity.id}?quote_created=${quote.id}`, 302)
  } catch (err) {
    console.error('[api/admin/assessments/[id]/complete] Error:', err)
    return redirect('/admin/entities?error=server', 302)
  }
}
