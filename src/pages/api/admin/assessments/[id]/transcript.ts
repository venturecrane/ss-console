import type { APIRoute } from 'astro'
import { getAssessment } from '../../../../../lib/db/assessments'
import { getTranscript } from '../../../../../lib/storage/r2'
import { env } from 'cloudflare:workers'
import { requireAdminSession } from '../../../../../lib/auth/admin-session'
import { errorResponse } from '../../../../../lib/api/helpers'

/**
 * GET /api/admin/assessments/:id/transcript
 *
 * Streams the transcript file from R2.
 *
 * Protected by auth middleware (requires admin role).
 */
export const GET: APIRoute = async ({ locals, params }) => {
  const auth = requireAdminSession(locals)
  if (!auth.ok) return auth.response
  const { session } = auth

  const assessmentId = params.id
  if (!assessmentId) {
    return errorResponse(400, 'Assessment ID required')
  }

  const assessment = await getAssessment(env.DB, session.orgId, assessmentId)
  if (!assessment || !assessment.transcript_path) {
    return errorResponse(404, 'Transcript not found')
  }

  const object = await getTranscript(env.STORAGE, assessment.transcript_path)
  if (!object) {
    return errorResponse(404, 'Transcript file not found in storage')
  }

  const originalName = object.customMetadata?.originalName ?? 'transcript.txt'

  return new Response(object.body, {
    headers: {
      'Content-Type': object.httpMetadata?.contentType ?? 'application/octet-stream',
      'Content-Disposition': contentDispositionAttachment(originalName),
    },
  })
}

/**
 * Build an `attachment` Content-Disposition for a stored filename. The name is
 * client-uploaded metadata, so it is not trusted into a quoted header as-is:
 * CR/LF would split the header, a quote would end the parameter, and non-ASCII
 * is not valid in the plain `filename` form. The plain form gets an ASCII-safe
 * fallback; the RFC 5987 `filename*` form carries the full name for browsers
 * that read it.
 */
export function contentDispositionAttachment(name: string): string {
  const stripped = name.replace(/[\r\n"\\]/g, '')
  const asciiFallback = stripped.replace(/[^\x20-\x7e]/g, '_').trim() || 'transcript.txt'
  const encoded = encodeRFC5987(stripped || 'transcript.txt')
  return `attachment; filename="${asciiFallback}"; filename*=UTF-8''${encoded}`
}

function encodeRFC5987(value: string): string {
  return encodeURIComponent(value).replace(
    /['()*]/g,
    (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`
  )
}
