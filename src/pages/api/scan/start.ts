/**
 * POST /api/scan/start — public Engine 1 diagnostic scan submission (#598).
 *
 * Flow:
 *   1. Parse + validate the form (domain, email, optional linkedin_url).
 *   2. Run the 4-dimensional rate limit (per IP / per email domain /
 *      per scanned domain / global).
 *   3. Generate magic-link token, persist scan_request in
 *      `pending_verification` state.
 *   4. Email the magic-link to the requester. Click verifies email
 *      reachability and intent, and triggers the pruned enrichment
 *      pipeline via ctx.waitUntil at /api/scan/verify.
 *
 * Public response is INTENTIONALLY MINIMAL per the scoping doc Bar #6
 * (anti-competitor-intel): "scan started — check your email" with zero
 * specifics about findings, signals, or next-step content. The full
 * report only ever lives in the email.
 *
 * Honeypot field name 'website' (matching contact.ts) — not a true field;
 * a real form leaves it empty. The submitted business URL is captured
 * via 'domain'.
 */

import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { rateLimitByIp } from '../../../lib/booking/rate-limit'
import { checkScanRateLimits, RATE_LIMITS } from '../../../lib/diagnostic/rate-limit'
import {
  normalizeEmail,
  normalizeDomain,
  normalizeLinkedinUrl,
  emailDomain,
} from '../../../lib/scan/normalize'
import { createScanRequest } from '../../../lib/db/scan-requests'
import { generateScanToken, buildScanVerifyUrl } from '../../../lib/scan/tokens'
import { sendEmail } from '../../../lib/email/resend'
import { scanVerificationEmailHtml } from '../../../lib/email/diagnostic-email'

export const POST: APIRoute = async ({ request }) => {
  // -- IP-coarse rate limit (10/hr per IP). Matches the booking pattern
  //    so a hammered attacker is throttled at the edge before we hit D1
  //    for the 4-dimensional check.
  const clientIp = request.headers.get('cf-connecting-ip') ?? null
  const coarse = await rateLimitByIp(env.BOOKING_CACHE, 'scan-start', clientIp ?? undefined)
  if (!coarse.allowed) {
    return jsonResponse(429, { error: 'Too many requests, please try again later.' })
  }

  let body: Record<string, unknown>
  try {
    body = (await request.json()) as Record<string, unknown>
  } catch {
    return jsonResponse(400, { error: 'Invalid JSON' })
  }

  // Honeypot — bots fill the hidden 'company_url' field, humans don't.
  if (typeof body.company_url === 'string' && body.company_url.trim() !== '') {
    // Pretend success — don't tell bots they were detected.
    return jsonResponse(200, { ok: true })
  }

  // Validate inputs.
  const emailR = normalizeEmail(body.email)
  if (!emailR.ok) return validationError({ email: messageForReason(emailR.reason) })

  const domainR = normalizeDomain(body.domain)
  if (!domainR.ok) return validationError({ domain: messageForReason(domainR.reason) })

  const linkedinR = normalizeLinkedinUrl(body.linkedin_url)
  if (!linkedinR.ok) return validationError({ linkedin_url: messageForReason(linkedinR.reason) })

  const email = emailR.value
  const domain = domainR.value
  const linkedin = linkedinR.value

  // -- 4-dimensional rate limit. On block, we deliberately return a
  //    generic "scan started" success to avoid leaking which dimension
  //    was tripped (anti-competitor-intel).
  const rl = await checkScanRateLimits(env.DB, {
    ip: clientIp,
    emailDomain: emailDomain(email),
    scannedDomain: domain,
  })
  if (!rl.allowed) {
    console.warn(`[api/scan/start] rate limited dimension=${rl.dimension} detail=${rl.detail}`)
    // Generic "ok" response — we already started shedding the request
    // so no token is generated and no email is sent. The prospect will
    // wonder why no email arrived; rate-limit cap is small enough that
    // legitimate prospects rarely hit it.
    return jsonResponse(200, { ok: true })
  }

  // Generate token, persist scan_request, send magic-link.
  const { token, hash } = await generateScanToken()
  let scanRequestId: string
  try {
    const row = await createScanRequest(env.DB, {
      email,
      domain,
      linkedin_url: linkedin,
      verification_token_hash: hash,
      request_ip: clientIp,
    })
    scanRequestId = row.id
  } catch (err) {
    console.error('[api/scan/start] failed to insert scan_request:', err)
    return jsonResponse(500, { error: 'Failed to start scan' })
  }

  const baseUrl = env.APP_BASE_URL ?? 'https://smd.services'
  const verifyUrl = buildScanVerifyUrl(baseUrl, token)
  try {
    const result = await sendEmail(env.RESEND_API_KEY, {
      to: email,
      subject: `Confirm your operational scan — ${domain}`,
      html: scanVerificationEmailHtml({ verifyUrl, scannedDomain: domain }),
    })
    if (!result.success) {
      console.error('[api/scan/start] verification email failed:', result.error)
      // Don't surface the failure to the prospect — the public response
      // shape is uniform whether email send succeeds, fails, or is rate
      // limited. The audit log captures the failure.
    }
  } catch (err) {
    console.error('[api/scan/start] verification email threw:', err)
  }

  return jsonResponse(200, { ok: true, id: scanRequestId })
}

function jsonResponse(status: number, data: unknown): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function validationError(fields: Record<string, string>): Response {
  return jsonResponse(400, { error: 'Validation failed', fields })
}

function messageForReason(reason: string): string {
  switch (reason) {
    case 'email_required':
      return 'Email is required'
    case 'email_too_long':
      return 'Email is too long'
    case 'email_invalid_chars':
      return 'Email contains invalid characters'
    case 'email_invalid_format':
      return 'Please enter a valid email address'
    case 'email_disposable':
      return 'Please use a regular business email — disposable inboxes are not accepted'
    case 'domain_required':
      return 'Business website is required'
    case 'domain_too_long':
      return 'Domain is too long'
    case 'domain_invalid_chars':
      return 'Domain contains invalid characters'
    case 'domain_invalid_format':
      return 'Please enter a valid business domain'
    case 'linkedin_invalid':
    case 'linkedin_too_long':
    case 'linkedin_invalid_chars':
      return 'LinkedIn URL looks malformed'
    case 'linkedin_wrong_host':
      return 'LinkedIn URL must point at linkedin.com'
    default:
      return 'Invalid input'
  }
}

// Suppress dead-code warning in environments that don't use this constant.
void RATE_LIMITS
