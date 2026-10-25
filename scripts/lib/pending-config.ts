/**
 * Pending values in an authored-but-unprovisioned seat, and the refusal that
 * keeps them off a Machine.
 *
 * A seat can be authored before the client has given us everything it needs
 * (a mailbox, an app registration, a site user, a property id). The validators
 * still require those fields to be well formed, so the seat carries a PENDING
 * marker in each: a value that passes the schema and cannot pass for a real
 * one. This module finds every marker; `scripts/refuse-pending-config.ts`
 * (run by operator/bin/lib/stage-seat-spec.sh before anything is uploaded)
 * refuses to provision a seat that still has one, naming each.
 *
 * The markers, in any string or number under customer.yaml:
 *   - the nil GUID 00000000-0000-0000-0000-000000000000 (never issued)
 *   - a string containing the upper-case word PENDING
 *     (skills' lower-case `version: pending` is a different, legitimate value)
 *   - a GA4 `property_id` of 0 (no property has it)
 * and, in seat.yaml, `fly_app: null` (the seat says it has no app). Not
 * `.invalid` on its own: smd-staging authors .invalid addresses on purpose.
 */

import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { parse as parseYaml } from 'yaml'

export const NIL_GUID = '00000000-0000-0000-0000-000000000000'

function isMarker(path: string, value: unknown): boolean {
  if (typeof value === 'number') return /(^|\.)property_id$/.test(path) && value === 0
  if (typeof value !== 'string') return false
  const v = value.trim()
  if (/(^|\.)property_id$/.test(path) && /^0+$/.test(v)) return true
  return v.toLowerCase() === NIL_GUID || /\bPENDING\b/.test(v)
}

/** Every `path: value` in the parsed customer.yaml that is a pending marker. */
export function pendingValues(config: unknown, path = ''): string[] {
  if (Array.isArray(config)) {
    return config.flatMap((item, i) => pendingValues(item, `${path}[${i}]`))
  }
  if (config !== null && typeof config === 'object') {
    return Object.entries(config as Record<string, unknown>).flatMap(([k, v]) =>
      pendingValues(v, path ? `${path}.${k}` : k)
    )
  }
  return isMarker(path, config) ? [`${path}: ${JSON.stringify(config)}`] : []
}

/**
 * Why `slug` cannot be provisioned yet, or [] when it can. Reads
 * operator/customers/<slug>/seat.yaml under `repo` and the given customer.yaml.
 */
export function provisioningRefusals(repo: string, slug: string, configText: string): string[] {
  const out: string[] = []
  const seatPath = join(repo, 'operator', 'customers', slug, 'seat.yaml')
  if (existsSync(seatPath)) {
    const seat = parseYaml(readFileSync(seatPath, 'utf8')) as Record<string, unknown> | null
    if (seat && 'fly_app' in seat && seat.fly_app === null) {
      const reason =
        typeof seat.unprovisioned_reason === 'string'
          ? seat.unprovisioned_reason.trim()
          : 'no reason given'
      out.push(
        `seat.yaml authors fly_app: null (${reason}). ` +
          'Author the app `fly apps create` made and delete unprovisioned_reason first.'
      )
    }
  }
  for (const value of pendingValues(parseYaml(configText))) {
    out.push(`customer.yaml still holds a PENDING value at ${value}`)
  }
  return out
}
