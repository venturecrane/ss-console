import type { APIContext, APIRoute } from 'astro'
import {
  getAssessment,
  updateAssessment,
  updateAssessmentStatus,
} from '../../../../lib/db/assessments'
import type { AssessmentStatus } from '../../../../lib/db/assessments'
import { uploadTranscript, getTranscript } from '../../../../lib/storage/r2'
import { extractAssessment } from '../../../../lib/claude/extract'
import { env } from 'cloudflare:workers'
import { requireAdminSession } from '../../../../lib/auth/admin-session'
import { errorResponse } from '../../../../lib/api/helpers'

type Redirect = APIContext['redirect']

async function handleTransitionStatus(
  redirect: Redirect,
  orgId: string,
  assessmentId: string,
  entityId: string,
  formData: FormData
): Promise<Response> {
  const newStatus = formData.get('new_status')
  if (!newStatus || typeof newStatus !== 'string') {
    return redirect(
      `/admin/entities/${entityId}/meetings/${assessmentId}?error=invalid_status`,
      302
    )
  }
  try {
    await updateAssessmentStatus(env.DB, orgId, assessmentId, newStatus as AssessmentStatus)
  } catch (err) {
    console.error('[api/admin/assessments/[id]] Status transition error:', err)
    return redirect(
      `/admin/entities/${entityId}/meetings/${assessmentId}?error=invalid_transition`,
      302
    )
  }
  return redirect(`/admin/entities/${entityId}/meetings/${assessmentId}?saved=1`, 302)
}

async function handleExtract(
  redirect: Redirect,
  orgId: string,
  assessmentId: string,
  entityId: string,
  transcriptPath: string | null
): Promise<Response> {
  const meetingUrl = `/admin/entities/${entityId}/meetings/${assessmentId}`
  if (!transcriptPath) return redirect(`${meetingUrl}?error=no_transcript`, 302)
  const apiKey = env.ANTHROPIC_API_KEY
  if (!apiKey) return redirect(`${meetingUrl}?error=no_api_key`, 302)
  const transcriptObject = await getTranscript(env.STORAGE, transcriptPath)
  if (!transcriptObject) return redirect(`${meetingUrl}?error=transcript_missing`, 302)
  const transcriptText = await transcriptObject.text()
  try {
    const result = await extractAssessment(apiKey, transcriptText)
    await updateAssessment(env.DB, orgId, assessmentId, { extraction: JSON.stringify(result) })
    return redirect(`${meetingUrl}?extracted=1`, 302)
  } catch (err) {
    console.error('[api/admin/assessments/[id]] Extraction error:', err)
    return redirect(`${meetingUrl}?error=extraction_failed`, 302)
  }
}

interface GeneralUpdateArgs {
  redirect: Redirect
  orgId: string
  assessmentId: string
  entityId: string
  existingExtraction: string | null
  formData: FormData
}

async function handleGeneralUpdate({
  redirect,
  orgId,
  assessmentId,
  entityId,
  existingExtraction,
  formData,
}: GeneralUpdateArgs): Promise<Response> {
  const scheduledAt = formData.get('scheduled_at')
  const durationMinutes = formData.get('duration_minutes')
  const extraction = formData.get('extraction')

  let transcriptPath: string | undefined
  const transcriptFile = formData.get('transcript')
  if (transcriptFile && transcriptFile instanceof File && transcriptFile.size > 0) {
    transcriptPath = await uploadTranscript(env.STORAGE, orgId, assessmentId, transcriptFile)
  }

  const updateData: Record<string, string | number | null | undefined> = {
    scheduled_at:
      scheduledAt && typeof scheduledAt === 'string' && scheduledAt.trim()
        ? new Date(scheduledAt.trim()).toISOString()
        : null,
    duration_minutes:
      durationMinutes && typeof durationMinutes === 'string' && durationMinutes.trim()
        ? parseInt(durationMinutes, 10) || null
        : null,
    extraction:
      extraction && typeof extraction === 'string' && extraction.trim()
        ? extraction.trim()
        : existingExtraction,
  }
  if (transcriptPath !== undefined) {
    updateData.transcript_path = transcriptPath
  }

  await updateAssessment(env.DB, orgId, assessmentId, updateData)
  return redirect(`/admin/entities/${entityId}/meetings/${assessmentId}?saved=1`, 302)
}

/**
 * POST /api/admin/assessments/:id
 *
 * Updates an existing assessment from form data.
 * Handles transcript upload, extraction JSON, problem mapping, champion info,
 * disqualification flags, and status transitions.
 *
 * Protected by auth middleware (requires admin role).
 */
async function handlePost({ request, locals, redirect, params }: APIContext): Promise<Response> {
  const auth = requireAdminSession(locals)
  if (!auth.ok) return auth.response
  const { session } = auth

  const assessmentId = params.id
  if (!assessmentId) {
    return errorResponse(400, 'validation_failed', 'Assessment ID required.')
  }

  try {
    const existing = await getAssessment(env.DB, session.orgId, assessmentId)
    if (!existing) return redirect('/admin/entities?error=not_found', 302)

    const formData = await request.formData()
    const action = formData.get('action')

    if (action === 'transition_status') {
      return handleTransitionStatus(
        redirect,
        session.orgId,
        assessmentId,
        existing.entity_id,
        formData
      )
    }

    if (action === 'extract') {
      return handleExtract(
        redirect,
        session.orgId,
        assessmentId,
        existing.entity_id,
        existing.transcript_path ?? null
      )
    }

    return handleGeneralUpdate({
      redirect,
      orgId: session.orgId,
      assessmentId,
      entityId: existing.entity_id,
      existingExtraction: existing.extraction ?? null,
      formData,
    })
  } catch (err) {
    console.error('[api/admin/assessments/[id]] Update error:', err)
    return redirect(`/admin/entities?error=server`, 302)
  }
}

export const POST: APIRoute = (ctx) => handlePost(ctx)
