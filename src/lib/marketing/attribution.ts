/**
 * Ad-click attribution capture (ADR 0066 launch gate 1, #1722).
 *
 * First-touch capture of the enumerated paid-ad params into a first-party
 * cookie at landing (set by middleware), read server-side at the intake and
 * booking API boundaries, and persisted onto the D1 context row for the
 * lead/booking so a closed engagement can be attributed to a campaign.
 *
 * DESIGN EXCEPTION — enumerated params only. The first-party events
 * pipeline (src/pages/api/events.ts) deliberately never stores URL query
 * strings. This module is the one sanctioned exception to that posture:
 * it captures ONLY the enumerated ad-click keys below — never arbitrary
 * query strings — with values length-capped and control-chars stripped.
 *
 * UTM convention (ADR 0066 §§5-6): `utm_content` carries the creative-angle
 * key, mapped 1:1 to the ad creative under test; `utm_campaign` names the
 * campaign; `utm_source`/`utm_medium` follow platform defaults
 * (facebook/paid-social, google/cpc); `gclid`/`fbclid` are the platform
 * click ids consumed by offline conversion upload (#1723 follow-on).
 *
 * Attribution is internal-only data: it must never render on any
 * client-facing surface (calendar event descriptions, confirmation emails,
 * portal pages). Admin surfaces (team notification email, entity timeline)
 * are the only consumers.
 */

export const AD_ATTRIBUTION_KEYS = [
  'utm_source',
  'utm_medium',
  'utm_campaign',
  'utm_content',
  'utm_term',
  'gclid',
  'fbclid',
] as const

export type AdAttributionKey = (typeof AD_ATTRIBUTION_KEYS)[number]

export interface AdAttribution extends Partial<Record<AdAttributionKey, string>> {
  /** ISO timestamp of the first-touch landing that set the cookie. */
  landed_at?: string
  /** Pathname (no query) of the first-touch landing page. */
  landing_path?: string
}

export const ATTRIBUTION_COOKIE = 'ss_attr'

/** 90 days — long enough to cover a considered B2B purchase cycle. */
export const ATTRIBUTION_COOKIE_MAX_AGE_S = 90 * 24 * 60 * 60

const MAX_VALUE_CHARS = 200

// eslint-disable-next-line no-control-regex -- stripping control characters from a cookie value IS the rule
const CONTROL_CHARS = /[\u0000-\u001f\u007f]/g

function sanitizeValue(raw: string): string | null {
  const value = raw.trim().replace(CONTROL_CHARS, '').slice(0, MAX_VALUE_CHARS)
  return value.length > 0 ? value : null
}

/**
 * Cheap pre-check so the middleware skips URL parsing on the overwhelming
 * majority of requests that carry no ad params at all.
 */
export function urlHasAttributionParams(url: URL): boolean {
  const search = url.search
  if (!search) return false
  return search.includes('utm_') || search.includes('gclid') || search.includes('fbclid')
}

/**
 * Extract the enumerated ad params from a landing URL. Returns null when
 * none are present, so callers never store an empty attribution.
 */
export function parseAttributionFromUrl(url: URL, now: Date = new Date()): AdAttribution | null {
  const out: AdAttribution = {}
  let found = false
  for (const key of AD_ATTRIBUTION_KEYS) {
    const raw = url.searchParams.get(key)
    if (raw === null) continue
    const value = sanitizeValue(raw)
    if (!value) continue
    out[key] = value
    found = true
  }
  if (!found) return null
  out.landed_at = now.toISOString()
  out.landing_path = url.pathname.slice(0, MAX_VALUE_CHARS)
  return out
}

/**
 * Re-validate an untrusted parsed object (cookie JSON, context metadata)
 * into an AdAttribution. Enumerated keys only; anything else is dropped.
 * Fail-closed: returns null unless at least one ad param survives.
 */
export function attributionFromUnknown(value: unknown): AdAttribution | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
  const rec = value as Record<string, unknown>
  const out: AdAttribution = {}
  let found = false
  for (const key of AD_ATTRIBUTION_KEYS) {
    const raw = rec[key]
    if (typeof raw !== 'string') continue
    const sanitized = sanitizeValue(raw)
    if (!sanitized) continue
    out[key] = sanitized
    found = true
  }
  if (!found) return null
  if (typeof rec.landed_at === 'string') {
    const landedAt = sanitizeValue(rec.landed_at)
    if (landedAt) out.landed_at = landedAt
  }
  if (typeof rec.landing_path === 'string') {
    const landingPath = sanitizeValue(rec.landing_path)
    if (landingPath) out.landing_path = landingPath
  }
  return out
}

/**
 * Cookie payload is plain JSON — Astro's cookies.set() URL-encodes it on
 * write, and readAttributionFromCookieHeader decodes exactly once on read.
 * Do not pre-encode here (double encoding breaks the read path).
 */
export function encodeAttributionCookie(attr: AdAttribution): string {
  return JSON.stringify(attr)
}

/** Parse + fail-closed-validate a raw Cookie header into an AdAttribution. */
export function readAttributionFromCookieHeader(cookieHeader: string | null): AdAttribution | null {
  if (!cookieHeader) return null
  for (const part of cookieHeader.split(';')) {
    const eq = part.indexOf('=')
    if (eq === -1) continue
    if (part.slice(0, eq).trim() !== ATTRIBUTION_COOKIE) continue
    try {
      return attributionFromUnknown(JSON.parse(decodeURIComponent(part.slice(eq + 1).trim())))
    } catch {
      return null
    }
  }
  return null
}

/**
 * Compact one-line summary for ADMIN surfaces only (team notification
 * email, entity timeline). Never render this on a client-facing surface.
 */
export function attributionSummary(attr: AdAttribution | null | undefined): string | null {
  if (!attr) return null
  const parts: string[] = []
  const sourceMedium = [attr.utm_source, attr.utm_medium].filter(Boolean).join('/')
  if (sourceMedium) parts.push(sourceMedium)
  if (attr.utm_campaign) parts.push(`campaign: ${attr.utm_campaign}`)
  if (attr.utm_content) parts.push(`ad: ${attr.utm_content}`)
  if (attr.utm_term) parts.push(`term: ${attr.utm_term}`)
  if (!sourceMedium) {
    if (attr.gclid) parts.push('google ads click')
    else if (attr.fbclid) parts.push('meta ads click')
  }
  return parts.length > 0 ? parts.join(' | ') : null
}
