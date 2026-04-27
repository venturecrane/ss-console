/**
 * 4-dimensional rate limit for the public /scan flow (#598).
 *
 * The diagnostic scan is the highest-cost public surface SMD Services
 * exposes — each completed scan burns ~$0.14 of Anthropic + Outscraper
 * spend, and each abuse run that gets past pre-verification still costs
 * us KV writes + a Resend send. The scoping doc mandates four dimensions:
 *
 *   1. Per IP            — 5 scans / 24h   — casual abuse cap
 *   2. Per email domain  — 1 scan / 7d     — same business / competitor
 *                                            re-scanning from the same
 *                                            organization's email
 *   3. Per scanned domain — 1 successful   — anti-competitor-intel; same
 *                          / 30d            target can't be re-scanned
 *                                            for fresh intel
 *   4. Global            — 200 / 24h       — viral/HN safety net
 *
 * Worst-case attacker capped at ~750 scans/wk = ~$200/wk Anthropic before
 * being blocked. Acceptable per the scoping doc.
 *
 * Storage backing — D1, not KV. The booking rate-limit uses KV because
 * its dimension is per-IP and a fixed-window counter is enough; the scan
 * limiter needs to reason about email-domain and scanned-domain counts
 * over windows of days/weeks, which means real queries against scan_requests.
 * The audit table is the source of truth for counts; no separate KV
 * counters to keep in sync.
 */

import {
  countScanRequestsByIp,
  countScanRequestsByEmailDomain,
  countCompletedScansByDomain,
  countScanRequestsSince,
} from '../db/scan-requests'

export const RATE_LIMITS = {
  per_ip_24h: 5,
  per_email_domain_7d: 1,
  per_domain_30d: 1,
  global_24h: 200,
} as const

export type RateLimitDimension = 'per_ip' | 'per_email_domain' | 'per_domain' | 'global'

export interface RateLimitCheckInput {
  ip: string | null
  emailDomain: string
  scannedDomain: string
  /** Defaults to Date.now(). Test-injectable. */
  now?: Date
}

export type RateLimitCheckResult =
  | { allowed: true }
  | {
      allowed: false
      dimension: RateLimitDimension
      /** Human-friendly limit description for logging only. Never shown
       *  to the prospect (the public response is generic per quality bar #6). */
      detail: string
    }

/**
 * Check all 4 dimensions. Returns allowed=true only if every dimension
 * has headroom. On block, returns the first dimension that tripped — used
 * for telemetry, not for prospect-facing UX.
 */
export async function checkScanRateLimits(
  db: D1Database,
  input: RateLimitCheckInput
): Promise<RateLimitCheckResult> {
  const now = input.now ?? new Date()
  const since24h = new Date(now.getTime() - 24 * 60 * 60 * 1000).toISOString()
  const since7d = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000).toISOString()
  const since30d = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000).toISOString()

  // 1. Per IP / 24h
  if (input.ip) {
    const n = await countScanRequestsByIp(db, input.ip, since24h)
    if (n >= RATE_LIMITS.per_ip_24h) {
      return {
        allowed: false,
        dimension: 'per_ip',
        detail: `ip ${input.ip} has ${n} scans in last 24h (limit ${RATE_LIMITS.per_ip_24h})`,
      }
    }
  }

  // 2. Per email domain / 7d. Skips if the email is from one of the major
  //    free providers (gmail.com, outlook.com, yahoo.com etc.) — those
  //    domains aren't a meaningful identity for rate-limiting and would
  //    incorrectly block any prospect after one gmail user submits. Per-IP
  //    + magic-link verification cover the abuse vector for free-mail.
  if (input.emailDomain && !FREE_EMAIL_DOMAINS.has(input.emailDomain)) {
    const n = await countScanRequestsByEmailDomain(db, input.emailDomain, since7d)
    if (n >= RATE_LIMITS.per_email_domain_7d) {
      return {
        allowed: false,
        dimension: 'per_email_domain',
        detail: `email domain ${input.emailDomain} has ${n} scans in last 7d (limit ${RATE_LIMITS.per_email_domain_7d})`,
      }
    }
  }

  // 3. Per scanned domain / 30d (only completed scans count — refused
  //    thin-footprint or pending rows don't lock out a legitimate retry).
  const m = await countCompletedScansByDomain(db, input.scannedDomain, since30d)
  if (m >= RATE_LIMITS.per_domain_30d) {
    return {
      allowed: false,
      dimension: 'per_domain',
      detail: `scanned domain ${input.scannedDomain} already has ${m} completed scans in last 30d (limit ${RATE_LIMITS.per_domain_30d})`,
    }
  }

  // 4. Global / 24h
  const g = await countScanRequestsSince(db, since24h)
  if (g >= RATE_LIMITS.global_24h) {
    return {
      allowed: false,
      dimension: 'global',
      detail: `global has ${g} scans in last 24h (limit ${RATE_LIMITS.global_24h})`,
    }
  }

  return { allowed: true }
}

/**
 * Free email providers that should NOT count against the per-email-domain
 * dimension (every gmail user shouldn't lock out every other gmail user
 * for 7 days). The per-IP and magic-link verification dimensions still
 * gate abuse from free-mail; this set only neutralizes the email-domain
 * dimension.
 */
const FREE_EMAIL_DOMAINS = new Set([
  'gmail.com',
  'googlemail.com',
  'outlook.com',
  'hotmail.com',
  'live.com',
  'msn.com',
  'yahoo.com',
  'ymail.com',
  'icloud.com',
  'me.com',
  'mac.com',
  'aol.com',
  'protonmail.com',
  'proton.me',
  'pm.me',
  'fastmail.com',
  'fastmail.fm',
  'zoho.com',
  'gmx.com',
  'gmx.us',
  'mail.com',
])
