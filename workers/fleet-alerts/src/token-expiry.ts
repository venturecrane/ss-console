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
 * change. Smokeball is the live case (verified 2026-09-02 against
 * https://docs.smokeball.com/docs/api-docs/2t26gcuuqf1wk-authorization-code-grant):
 *
 *   "The 180-day expiry took effect on 24 August 2026 and applies to refresh
 *    tokens issued on or after that date. Refresh tokens issued before then
 *    remain valid for 30 days."
 *
 * The configured lifetime is therefore the CURRENT one (180 for smokeball), and
 * a token issued before the cutover is evaluated against the older, shorter
 * one. We can date the token because the reported age IS its file mtime age:
 * issued_at = now - age. The rule goes inert on its own once every pre-cutover
 * token has been re-consented, so it needs no cleanup.
 *
 * Why this is not cosmetic. Alerts here are edge-triggered (index.ts
 * processTransition: page on inactive->open, silence while open). Judge a
 * 180-day token by a 30-day constant and it opens at day 25, pages once, then
 * sits open and SILENT for the remaining ~155 days — including through the real
 * expiry, which produces no page at all. Judge a 30-day token by a 180-day
 * constant and it never warns before it dies. Both directions were live on
 * 2026-09-02: ashton-price held a pre-cutover token, pilot-smokeball was about
 * to receive a post-cutover one.
 */
const PRE_CUTOVER_LIFETIMES: Record<string, { cutoverMs: number; days: number }> = {
  smokeball: { cutoverMs: Date.parse('2026-08-24T00:00:00Z'), days: 30 },
}

/**
 * The lifetime to judge THIS token by: the configured (current) value, unless
 * the token predates a known vendor cutover, in which case the older value.
 */
function effectiveLifetimeDays(
  server: string,
  configuredDays: number,
  ageSeconds: number,
  nowMs: number
): number {
  const rule = PRE_CUTOVER_LIFETIMES[server]
  if (rule === undefined) return configuredDays
  const issuedAtMs = nowMs - ageSeconds * 1000
  return issuedAtMs < rule.cutoverMs ? rule.days : configuredDays
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
export function tokenExpiryConditions(
  row: FleetStatusRow,
  lifetimesDays: Record<string, number>,
  warnDays: number,
  nowMs: number
): ConditionState[] {
  const ages = parseTokenAgeMap(row.connector_token_age_json)
  if (ages === null) return []
  const out: ConditionState[] = []
  for (const [server, configuredDays] of Object.entries(lifetimesDays)) {
    const age = ages[server]
    if (age === undefined) continue
    const lifetimeDays = effectiveLifetimeDays(server, configuredDays, age, nowMs)
    const thresholdSeconds = Math.max(0, lifetimeDays - warnDays) * 86400
    const ageDays = Math.floor(age / 86400)
    const provenance =
      lifetimeDays === configuredDays ? '' : ' — pre-cutover token, older vendor lifetime applies'
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
