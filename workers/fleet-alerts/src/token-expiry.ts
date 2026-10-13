/**
 * connector_token_expiring:<server> — pre-expiry warning for durable
 * credentials with a recorded lifetime (ss#2148, ADR 0080 amendment
 * 2026-08-09).
 *
 * The seat ships connector_token_age (the durable token file's mtime age,
 * overlay connector_check.token_ages()) as a heartbeat field SEPARATE from
 * the connector-health map — synthesizing a consecutive_failures=0 entry for
 * an idle connector would falsely RESOLVE an open connector_down alert.
 *
 * WHAT THIS CONDITION ACTUALLY IS (corrected 2026-09-02, ss#2148 follow-up).
 * The original text here said the daily auth-probe keepalive rotates the file,
 * so this condition never fires in normal operation and only fires when the
 * probe infrastructure has been dead for ~(lifetime - warn) days — "the
 * watcher's watcher". That premise is FALSE against a vendor that does not
 * rotate the refresh token on a refresh grant, and Smokeball's staging tenant
 * is one: the connector only rewrites the file when the token response carries
 * a NEW refresh_token (smokeball_connector/client.py::_mint_token), so on a
 * non-rotating tenant the mtime never moves and the credential ages to its
 * absolute expiry while every probe reports ok.
 *
 * pilot-smokeball, observed end to end (vfy_01M1H9RX214Q1EJDHJC87DYT3K):
 *   08-02 16:56Z  refresh token minted, file written — and never rewritten again
 *   08-22..09-01  ~12 consecutive daily auth_status probes, ALL outcome=ok
 *   08-27 16:57Z  THIS CONDITION fired at 25d ("warning at 25d")
 *   09-01 16:56Z  absolute expiry (mint + 30d)
 *   09-02 12:47Z  first HTTP 400 token mint; connector dead
 *
 * So on a non-rotating tenant this is not a backstop behind a working
 * keepalive. It is the ONLY thing between a live credential and a silent
 * expiry, and the keepalive's own green is structurally unable to contradict
 * it: auth_status reports refresh_token_persisted, which compares the
 * in-memory token to the on-disk one (client.py::_refresh_token_persisted) —
 * trivially equal when nothing ever rotates, so it reads true forever.
 *
 * Consequence for whoever tunes this next: the 08-27 page was correct, single,
 * and unactioned for 5 days. A condition carrying the last warning before a
 * hard outage should not go quiet for the whole warn window after one page.
 */

import { CONNECTOR_TOKEN_EXPIRING_PREFIX } from './conditions'
import type { ConditionState, FleetStatusRow } from './index'

function nonNegInt(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null
}

/** server → token-file age seconds. Corrupt/malformed → null (hold). */
export function parseTokenAgeMap(json: string | null): Record<string, number> | null {
  if (json === null) return null
  let raw: unknown
  try {
    raw = JSON.parse(json)
  } catch {
    return null
  }
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return null
  const out: Record<string, number> = {}
  for (const [server, value] of Object.entries(raw as Record<string, unknown>)) {
    const age = nonNegInt(value)
    if (age !== null) out[server] = age
  }
  return out
}

/**
 * Vendor lifetime changes are keyed to a token's ISSUE date, not to today, so
 * one global "lifetime" constant is wrong whenever two seats straddle the
 * change. We can date the token because the reported age IS its file mtime age:
 * issued_at = now - age. The rule goes inert on its own once every pre-cutover
 * token has been re-consented, so it needs no cleanup.
 *
 * Smokeball's cutover for OUR app is 2026-09-21 00:22:30Z, not the 2026-08-24
 * date in their docs. The docs say "The 180-day expiry took effect on 24 August
 * 2026" as a DEFAULT; our production app client kept 30 days until Smokeball's
 * review team raised it when they approved the app (approval email, 2026-09-21
 * 00:22:30Z: "We have also increased your refresh token expiry to 180 days").
 * Measured: ashton-price's token, issued 2026-09-10 21:31Z (after 08-24, before
 * 09-21), died at exactly 30 days on 2026-10-10 with invalid_grant
 * (vfy_01M4RB4V0M8SQ28CGFZZBVEKH3, vfy_01M4RGNRQFFV7Y22VD06KGEZNH). Keyed to
 * 08-24 it was judged at 180 and would have warned at day 175; it died with no
 * warning at all.
 *
 * Why this is not cosmetic. Alerts here are edge-triggered (index.ts
 * processTransition: page on inactive->open, silence while open). Judge a
 * 180-day token by a 30-day constant and it opens at day 25, pages once, then
 * sits open and SILENT for the remaining ~155 days — including through the real
 * expiry, which produces no page at all. Judge a 30-day token by a 180-day
 * constant and it never warns before it dies. Both directions have now been
 * live: 2026-09-02 (pre-08-24 token) and 2026-10-10 (pre-09-21 token).
 */
/**
 * ANNOUNCED IS NOT OBSERVED (2026-10-13). `provenAtMs` records when a token
 * issued after the cutover was first seen alive past the old lifetime. Until
 * then the longer lifetime is the vendor's word, and their word has been wrong
 * for us twice: the docs' 08-24 date did not apply to our app (10-10 outage),
 * and the 180 days did not reach their staging tenant (10-02 outage). Both
 * times the token was judged by the announced lifetime and died with no
 * warning. So while `provenAtMs` is null EVERY token is judged by the old
 * lifetime: a warning at day 25 that turns out to be early costs one email; a
 * missing one costs the firm's Smokeball access with nobody told.
 *
 * ashton-price re-consented 2026-10-12 19:36:39Z, the first production token
 * issued after 09-21. It warns 2026-11-06 under this rule. If it is still
 * working after 2026-11-11 19:36Z, set `provenAtMs` to that observation (cite
 * the crane_verify id); the open warning then resolves on the next tick.
 */
export interface VendorLifetimeRule {
  cutoverMs: number
  days: number
  provenAtMs: number | null
}

export const VENDOR_LIFETIME_RULES: Record<string, VendorLifetimeRule> = {
  smokeball: { cutoverMs: Date.parse('2026-09-21T00:22:30Z'), days: 30, provenAtMs: null },
}

type Basis = 'configured' | 'pre-cutover' | 'unproven'

/**
 * The lifetime to judge THIS token by, and why: the configured (current)
 * value, unless the token predates a known vendor cutover or the current
 * value has never been observed on our app, in which case the older value.
 */
function effectiveLifetime(
  server: string,
  configuredDays: number,
  ageSeconds: number,
  nowMs: number,
  rules: Record<string, VendorLifetimeRule>
): { days: number; basis: Basis } {
  const rule = rules[server]
  if (rule === undefined || rule.days >= configuredDays) {
    return { days: configuredDays, basis: 'configured' }
  }
  const issuedAtMs = nowMs - ageSeconds * 1000
  if (issuedAtMs < rule.cutoverMs) return { days: rule.days, basis: 'pre-cutover' }
  if (rule.provenAtMs === null) return { days: rule.days, basis: 'unproven' }
  return { days: configuredDays, basis: 'configured' }
}

const PROVENANCE: Record<Basis, string> = {
  configured: '',
  'pre-cutover': ' — pre-cutover token, older vendor lifetime applies',
  unproven:
    " — the vendor's longer lifetime has never been observed on our app, so the older one applies until a token outlives it",
}

/**
 * Tri-state per server:
 *   age reported, >= (lifetime - warn) → active (dies in <= warn days unless
 *     the token file is rewritten).
 *   age reported, below threshold      → inactive (resolves — something reset
 *     the file's mtime; a real recovery signal).
 *   age absent (no field, no file, corrupt map) → push NOTHING (hold): a seat
 *     that stops reporting must not read as recovered.
 * Servers with no recorded lifetime are never evaluated — a guessed lifetime
 * would manufacture pages.
 *
 * "The file was rewritten" is the ONLY resolution, and what can cause it is
 * vendor-dependent. On a vendor that rotates refresh tokens, an ordinary
 * refresh rewrites it. On one that does NOT — Smokeball — nothing but a fresh
 * consent ever does, so no amount of traffic will clear this condition. The
 * detail text below must not imply otherwise: it is interpolated verbatim into
 * the ops email, and telling a responder to "rotate" a credential that cannot
 * be rotated sends them after a fix that does not exist.
 */
export interface TokenExpiryOptions {
  lifetimesDays: Record<string, number>
  warnDays: number
  nowMs: number
  seatLifetimesBySlug?: Record<string, Record<string, number>>
  /** Defaults to VENDOR_LIFETIME_RULES; tests pass a proven variant. */
  rules?: Record<string, VendorLifetimeRule>
}

export function tokenExpiryConditions(
  row: FleetStatusRow,
  opts: TokenExpiryOptions
): ConditionState[] {
  const { lifetimesDays, warnDays, nowMs, seatLifetimesBySlug = {} } = opts
  const rules = opts.rules ?? VENDOR_LIFETIME_RULES
  const ages = parseTokenAgeMap(row.connector_token_age_json)
  if (ages === null) return []
  const out: ConditionState[] = []
  // A seat override REPLACES the fleet lifetime for that server on that seat;
  // it is an authored fact about the seat's vendor tenant (see
  // parseSeatLifetimes), not a second opinion to reconcile.
  const effective = { ...lifetimesDays, ...seatLifetimesBySlug[row.customer_slug] }
  for (const [server, configuredDays] of Object.entries(effective)) {
    const age = ages[server]
    if (age === undefined) continue
    const { days: lifetimeDays, basis } = effectiveLifetime(
      server,
      configuredDays,
      age,
      nowMs,
      rules
    )
    const thresholdSeconds = Math.max(0, lifetimeDays - warnDays) * 86400
    const ageDays = Math.floor(age / 86400)
    const provenance = PROVENANCE[basis]
    out.push({
      customer_slug: row.customer_slug,
      condition: `${CONNECTOR_TOKEN_EXPIRING_PREFIX}${server}`,
      active: age >= thresholdSeconds,
      detail:
        `${server} durable credential is ${ageDays}d old ` +
        `(recorded lifetime ${lifetimeDays}d, warning at ${lifetimeDays - warnDays}d${provenance}). ` +
        'Re-consent before it expires. Resolves only when the token file is ' +
        'rewritten, which for a vendor that does not rotate refresh tokens ' +
        '(Smokeball) means a fresh consent — ordinary traffic will not clear this.',
    })
  }
  return out
}

/**
 * Per-seat lifetime overrides (2026-10-03). Smokeball's 180-day cutover reached
 * its PRODUCTION tenant only: the STAGING tenant still issues 30-day refresh
 * tokens. pilot-smokeball (staging) re-consented 2026-09-02 ~22:03Z, after the
 * cutover, and its connector died with HTTP 400 token-mint rejections on
 * 2026-10-02/03, ~30 days later. Judged by the fleet-wide 180 it would have
 * warned at day 175, so the outage arrived as a SEV1 with no warning at all.
 *
 * `seatsCsv` names the seats on the shorter-lived tenant; tests pin it to the
 * customer.yaml files declaring `environment: staging`, so a new staging seat
 * cannot silently inherit the production lifetime. Unset, empty, or an invalid
 * day count yields no overrides (no guessed lifetime, no manufactured page).
 */
export function parseSeatLifetimes(
  server: string,
  seatsCsv: string | undefined,
  daysRaw: string | undefined
): Record<string, Record<string, number>> {
  const days = Number(daysRaw)
  if (!Number.isFinite(days) || days <= 0) return {}
  const out: Record<string, Record<string, number>> = {}
  for (const slug of (seatsCsv ?? '').split(',')) {
    const s = slug.trim()
    if (s.length > 0) out[s] = { [server]: Math.floor(days) }
  }
  return out
}
