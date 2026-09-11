/**
 * Shared API-route helpers.
 *
 * Canonical home for the small request/response utilities that the public
 * API routes (booking reserve, intake, intake send) previously each carried
 * as byte-identical private copies (2026-06-12 code review dedup).
 * `src/pages/api/booking/reserve-helpers.ts` re-exports these so its
 * existing import surface keeps working.
 */

export function trimString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

export function isValidEmail(email: string): boolean {
  if (email.length > 254) return false
  const parts = email.split('@')
  if (parts.length !== 2) return false
  const [local, domain] = parts
  if (!local || !domain) return false
  if (domain.indexOf('.') === -1) return false
  return true
}

/**
 * Narrow an `unknown` to a plain object (not null, not an array). The one
 * shared guard for the parse-don't-cast sites across src/; eight modules each
 * carried this exact function until the 2026-09-10 review dedup.
 */
export function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

export function escapeHtml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

export function jsonResponse(status: number, data: unknown): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/**
 * Standard error response: a JSON body of `{ error: message }` at the given
 * status. The single canonical shape for API-route error bodies, so error
 * responses across `src/pages/api/**` are uniform (code review 2026-07-02 §1.7).
 */
export function errorResponse(status: number, message: string): Response {
  return jsonResponse(status, { error: message })
}
