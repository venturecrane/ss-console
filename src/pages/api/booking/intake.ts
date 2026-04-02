import type { APIRoute } from 'astro'
import { createClient } from '../../../lib/db/clients'
import { createContact } from '../../../lib/db/contacts'
import { createAssessment } from '../../../lib/db/assessments'
import { ORG_ID } from '../../../lib/constants'

/**
 * POST /api/booking/intake
 *
 * Public endpoint for the post-booking intake form. Creates a client,
 * contact, and scheduled assessment from prospect-submitted data.
 *
 * Security:
 * - Honeypot field rejects bot submissions silently
 * - Email dedup prevents double-submit from creating duplicate records
 * - No auth required (prospect-facing)
 */
export const POST: APIRoute = async ({ request, locals }) => {
  const env = locals.runtime.env

  // Parse JSON body
  let body: Record<string, unknown>
  try {
    body = (await request.json()) as Record<string, unknown>
  } catch {
    return jsonResponse(400, { error: 'Invalid JSON' })
  }

  // Honeypot check — bots fill this hidden field, humans don't
  if (typeof body.website_url === 'string' && body.website_url.trim() !== '') {
    return jsonResponse(200, { ok: true })
  }

  // Validate required fields
  const name = trimString(body.name)
  const email = trimString(body.email)
  const businessName = trimString(body.business_name)

  if (!name || !email || !businessName) {
    return jsonResponse(400, { error: 'name, email, and business_name are required' })
  }

  // Optional fields
  const vertical = trimString(body.vertical) || null
  const employeeCount =
    typeof body.employee_count === 'string' ? parseInt(body.employee_count, 10) || null : null
  const yearsInBusiness =
    typeof body.years_in_business === 'string' ? parseInt(body.years_in_business, 10) || null : null
  const biggestChallenge = trimString(body.biggest_challenge)
  const howHeard = trimString(body.how_heard)

  try {
    // Dedup: check if a contact with this email already exists
    const existing = await env.DB.prepare(
      'SELECT id, entity_id FROM contacts WHERE org_id = ? AND email = ? LIMIT 1'
    )
      .bind(ORG_ID, email)
      .first<{ id: string; entity_id: string }>()

    if (existing) {
      return jsonResponse(200, { ok: true, client_id: existing.entity_id })
    }

    // Build notes from intake answers
    const noteParts: string[] = []
    if (biggestChallenge) noteParts.push(`What they're trying to accomplish: ${biggestChallenge}`)
    if (howHeard) noteParts.push(`How they found us: ${howHeard}`)
    const notes = noteParts.length > 0 ? noteParts.join('\n\n') : null

    // Create client record
    const client = await createClient(env.DB, ORG_ID, {
      business_name: businessName,
      vertical,
      employee_count: employeeCount,
      years_in_business: yearsInBusiness,
      source: 'Website Booking',
      notes,
    })

    // Create contact record
    await createContact(env.DB, ORG_ID, client.id, {
      name,
      email,
    })

    // Create assessment record (status defaults to 'scheduled')
    await createAssessment(env.DB, ORG_ID, client.id, {})

    return jsonResponse(201, { ok: true, client_id: client.id })
  } catch (err) {
    console.error('[api/booking/intake] Error:', err)
    return jsonResponse(500, { error: 'Internal server error' })
  }
}

function trimString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

function jsonResponse(status: number, data: unknown): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}
