import type { APIContext, APIRoute } from 'astro'
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
import { requireAdminSession } from '../../../../../lib/auth/admin-session'
import { errorResponse } from '../../../../../lib/api/helpers'

/** Default hourly rate at launch (per Decision Stack #16, evolved). */
const DEFAULT_RATE = 175

/**
 * POST /api/admin/assessments/:id/complete — legacy, kept for backward compat
 *
 * The Meetings generalization (#468, #470) split this endpoint into two new
 * routes scoped under the entity:
 *   - `/api/admin/entities/:id/meetings/:meetingId/complete` — captures
 *     outcome + explicit next-stage picker, no quote side-effects
 *   - `/api/admin/entities/:id/meetings/:meetingId/draft-quote` — explicit
 *     opt-in proposal drafting
 *
 * This legacy handler is unreachable from the admin UI (the
 * `/admin/assessments/[id]` page now 301-redirects to the meeting URL) but
 * stays in place for direct callers / external scripts during the monitoring
 * window. A follow-up issue tracks removing it alongside the assessments
 * table drop.
 *
 * Behavior below is unchanged: builds extraction JSON, marks completed,
 * drafts a quote, and transitions to proposing.
 *
 * Protected by auth middleware (requires admin role).
 */

type Redirect = APIContext['redirect']

interface ExtractionData {
  problems: string[]
  disqualified: boolean
  disqualify_reason: string | null
  duration_minutes: number | null
  notes: string
  completed_at: string
}

function buildExtraction(formData: FormData): ExtractionData {
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

  return {
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
}

async function maybeUploadTranscript(
  formData: FormData,
  orgId: string,
  assessmentId: string
): Promise<string | undefined> {
  const transcriptFile = formData.get('transcript')
  if (transcriptFile && transcriptFile instanceof File && transcriptFile.size > 0) {
    return uploadTranscript(env.STORAGE, orgId, assessmentId, transcriptFile)
  }
  return undefined
}

async function handleDisqualified(
  redirect: Redirect,
  orgId: string,
  entityId: string,
  assessmentId: string,
  extraction: ExtractionData
): Promise<Response> {
  try {
    await updateAssessmentStatus(env.DB, orgId, assessmentId, 'disqualified')
    await transitionStage(env.DB, orgId, entityId, 'lost', {
      reason: `Disqualified during assessment: ${extraction.disqualify_reason ?? 'No reason provided'}`,
      lostReason: {
        code: 'not-a-fit',
        detail: extraction.disqualify_reason ?? null,
      },
    })
  } catch {
    // Stage transition may fail if already in lost state
  }
  return redirect(`/admin/entities/${entityId}?assessment_completed=1`, 302)
}

async function handlePost({ request, locals, redirect, params }: APIContext): Promise<Response> {
  const auth = requireAdminSession(locals)
  if (!auth.ok) return auth.response
  const { session } = auth

  const assessmentId = params.id
  if (!assessmentId) {
    return errorResponse(400, 'validation_failed', 'Assessment ID required.')
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
    const extraction = buildExtraction(formData)
    const transcriptPath = await maybeUploadTranscript(formData, session.orgId, assessmentId)

    // 2. Update assessment status to completed (auto-sets completed_at)
    await updateAssessmentStatus(env.DB, session.orgId, assessmentId, 'completed')

    // 3. Write extraction JSON and duration to assessment
    await updateAssessment(env.DB, session.orgId, assessmentId, {
      extraction: JSON.stringify(extraction),
      duration_minutes: extraction.duration_minutes,
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
    if (extraction.disqualified) {
      return handleDisqualified(redirect, session.orgId, entity.id, assessmentId, extraction)
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
      lineItems = []
    } catch {
      lineItems = []
    }

    // 7. Create draft quote with pre-filled line items. meetingId is equal
    //    to assessmentId by construction (#469 preserved IDs during backfill).
    const quote = await createQuote(env.DB, session.orgId, {
      entityId: entity.id,
      assessmentId,
      meetingId: assessmentId,
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

export const POST: APIRoute = (ctx) => handlePost(ctx)
