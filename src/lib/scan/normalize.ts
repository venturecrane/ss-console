/**
 * Input normalization for the public /scan form (#598).
 *
 * Three jobs:
 *   1. Validate format. Reject malformed input with a specific reason.
 *   2. Normalize aggressively (lowercase, strip protocol/path, trim) so
 *      our rate-limit and dedupe queries see a canonical key — otherwise a
 *      single attacker can run scans for `https://X.com`, `http://x.com/`,
 *      and `www.x.com` and dodge per-domain limits.
 *   3. Filter the obvious abuse vectors at submit (disposable-email
 *      patterns) so we never pay an Anthropic call to scan via a
 *      throwaway address.
 *
 * What we deliberately don't do here:
 *   - DNS lookup. A working MX record proves nothing about whether the
 *     prospect actually owns the email; the magic-link click is the only
 *     reliable proof of intent.
 *   - WHOIS. Slow, rate-limited, and irrelevant to our use case.
 *   - Aggressive TLD filtering. Plenty of legit Phoenix businesses use
 *     `.co`, `.us`, `.biz`. We trust the magic-link gate for filtering.
 */

export const CONTROL_CHAR_RE = /[\r\n\0\t]/

/** Disposable email providers known to be used for abuse. Case-insensitive
 *  exact match on the email domain. Not exhaustive — defense in depth, the
 *  magic-link gate is the actual filter. */
const DISPOSABLE_EMAIL_DOMAINS = new Set([
  'mailinator.com',
  'tempmail.com',
  'guerrillamail.com',
  'guerrillamail.net',
  'guerrillamail.org',
  '10minutemail.com',
  'yopmail.com',
  'sharklasers.com',
  'throwawaymail.com',
  'fakeinbox.com',
  'maildrop.cc',
  'getnada.com',
  'trashmail.com',
  'temp-mail.org',
  'temp-mail.io',
  'dispostable.com',
])

export type NormalizeResult<T> = { ok: true; value: T } | { ok: false; reason: string }

/**
 * Normalize an email. Lowercases, trims, validates structurally, and
 * rejects disposable providers.
 */
export function normalizeEmail(input: unknown): NormalizeResult<string> {
  if (typeof input !== 'string') return { ok: false, reason: 'email_required' }
  const raw = input.trim()
  if (!raw) return { ok: false, reason: 'email_required' }
  if (raw.length > 254) return { ok: false, reason: 'email_too_long' }
  if (CONTROL_CHAR_RE.test(raw)) return { ok: false, reason: 'email_invalid_chars' }
  const lower = raw.toLowerCase()
  const at = lower.indexOf('@')
  if (at <= 0 || at === lower.length - 1) {
    return { ok: false, reason: 'email_invalid_format' }
  }
  // Exactly one @. The local-part can contain '@' only when quoted
  // (RFC 5321 obscure path); we reject those by simple count.
  if (lower.indexOf('@', at + 1) !== -1) {
    return { ok: false, reason: 'email_invalid_format' }
  }
  const local = lower.slice(0, at)
  const domain = lower.slice(at + 1)
  if (!local || !domain) return { ok: false, reason: 'email_invalid_format' }
  if (domain.indexOf('.') === -1) return { ok: false, reason: 'email_invalid_format' }
  if (domain.startsWith('.') || domain.endsWith('.')) {
    return { ok: false, reason: 'email_invalid_format' }
  }
  if (DISPOSABLE_EMAIL_DOMAINS.has(domain)) {
    return { ok: false, reason: 'email_disposable' }
  }
  return { ok: true, value: lower }
}

/**
 * Extract the domain part of a normalized email. Caller must already have
 * passed the email through `normalizeEmail()`.
 */
export function emailDomain(normalizedEmail: string): string {
  const at = normalizedEmail.indexOf('@')
  return at === -1 ? '' : normalizedEmail.slice(at + 1)
}

/**
 * Normalize a website / business domain. Strips protocol, path, query,
 * leading `www.`, trailing slashes, lowercases. Rejects domains that
 * are too short, contain spaces, or are obviously not a real domain.
 *
 * Returns the bare hostname, e.g. `azperfectcomfort.com` (not
 * `https://www.azperfectcomfort.com/about`).
 */
export function normalizeDomain(input: unknown): NormalizeResult<string> {
  if (typeof input !== 'string') return { ok: false, reason: 'domain_required' }
  const raw = input.trim()
  if (!raw) return { ok: false, reason: 'domain_required' }
  if (raw.length > 253) return { ok: false, reason: 'domain_too_long' }
  if (CONTROL_CHAR_RE.test(raw)) return { ok: false, reason: 'domain_invalid_chars' }

  // Strip protocol + path + query. Tolerate scheme-less input by trying URL
  // with a fake scheme first.
  let hostname: string
  try {
    const withScheme = /^https?:\/\//i.test(raw) ? raw : `https://${raw}`
    const u = new URL(withScheme)
    hostname = u.hostname
  } catch {
    return { ok: false, reason: 'domain_invalid_format' }
  }

  hostname = hostname.toLowerCase()
  if (hostname.startsWith('www.')) hostname = hostname.slice(4)
  if (!hostname) return { ok: false, reason: 'domain_invalid_format' }

  // Must contain a dot and not start/end with dot or hyphen.
  if (hostname.indexOf('.') === -1) return { ok: false, reason: 'domain_invalid_format' }
  if (hostname.startsWith('.') || hostname.endsWith('.')) {
    return { ok: false, reason: 'domain_invalid_format' }
  }
  if (hostname.startsWith('-') || hostname.endsWith('-')) {
    return { ok: false, reason: 'domain_invalid_format' }
  }
  // Reject localhost, IPs, .local, .internal — not real public businesses.
  if (
    hostname === 'localhost' ||
    hostname.endsWith('.local') ||
    hostname.endsWith('.internal') ||
    /^\d+\.\d+\.\d+\.\d+$/.test(hostname)
  ) {
    return { ok: false, reason: 'domain_invalid_format' }
  }
  // Each label 1..63 chars and DNS-safe. Labels cannot start or end
  // with a hyphen (RFC 1035) — caught here so `trailing-hyphen-.com` is
  // rejected even though `new URL()` accepts it.
  for (const label of hostname.split('.')) {
    if (!label) return { ok: false, reason: 'domain_invalid_format' }
    if (label.length > 63) return { ok: false, reason: 'domain_invalid_format' }
    if (!/^[a-z0-9-]+$/.test(label)) {
      return { ok: false, reason: 'domain_invalid_format' }
    }
    if (label.startsWith('-') || label.endsWith('-')) {
      return { ok: false, reason: 'domain_invalid_format' }
    }
  }
  return { ok: true, value: hostname }
}

/**
 * Optional LinkedIn URL. The /scan path doesn't use LinkedIn data (per
 * the scoping doc scope cut), but we capture it for the internal pipeline
 * if the prospect converts to a booked call.
 */
export function normalizeLinkedinUrl(input: unknown): NormalizeResult<string | null> {
  if (input == null || input === '') return { ok: true, value: null }
  if (typeof input !== 'string') return { ok: false, reason: 'linkedin_invalid' }
  const raw = input.trim()
  if (!raw) return { ok: true, value: null }
  if (raw.length > 500) return { ok: false, reason: 'linkedin_too_long' }
  if (CONTROL_CHAR_RE.test(raw)) return { ok: false, reason: 'linkedin_invalid_chars' }
  let parsed: URL
  try {
    parsed = new URL(raw.startsWith('http') ? raw : `https://${raw}`)
  } catch {
    return { ok: false, reason: 'linkedin_invalid' }
  }
  const host = parsed.hostname.toLowerCase()
  if (host !== 'linkedin.com' && !host.endsWith('.linkedin.com')) {
    return { ok: false, reason: 'linkedin_wrong_host' }
  }
  return { ok: true, value: parsed.toString() }
}
