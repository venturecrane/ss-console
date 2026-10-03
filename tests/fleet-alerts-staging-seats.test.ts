/**
 * Pins the fleet-alerts Worker's SMOKEBALL_STAGING_SEATS to the seats whose
 * customer.yaml puts the smokeball connector on Smokeball's STAGING tenant.
 *
 * Why (2026-10-03): the staging tenant still issues 30-day refresh tokens after
 * the production 180-day cutover. With one fleet-wide 180, pilot-smokeball's
 * connector_token_expiring warning sat at day 175 and the token died at ~30d
 * as an unwarned SEV1. The Worker now judges listed seats by the staging
 * lifetime, and this test is what keeps a new staging seat from silently
 * inheriting 180.
 *
 * An absent `environment` counts as staging: that is the connector default
 * (bin/connect-smokeball.sh header, "default us / staging").
 */
import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, existsSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { parse as parseYaml } from 'yaml'

const CUSTOMERS_DIR = resolve('operator/customers')
const WRANGLER = resolve('workers/fleet-alerts/wrangler.toml')

function stagingSmokeballSeats(): string[] {
  const out: string[] = []
  for (const d of readdirSync(CUSTOMERS_DIR, { withFileTypes: true })) {
    if (!d.isDirectory() || d.name.startsWith('_')) continue
    const file = join(CUSTOMERS_DIR, d.name, 'customer.yaml')
    if (!existsSync(file)) continue
    const doc: unknown = parseYaml(readFileSync(file, 'utf8'))
    if (typeof doc !== 'object' || doc === null) continue
    // `connectors` is a map keyed by role (PracticeManagement, ...).
    const connectors = (doc as { connectors?: unknown }).connectors
    if (typeof connectors !== 'object' || connectors === null) continue
    for (const c of Object.values(connectors)) {
      if (typeof c !== 'object' || c === null) continue
      const entry = c as { adapter?: unknown; environment?: unknown }
      if (entry.adapter !== 'smokeball') continue
      if (entry.environment === undefined || entry.environment === 'staging') out.push(d.name)
    }
  }
  return out.sort()
}

function tomlVar(name: string): string | undefined {
  for (const line of readFileSync(WRANGLER, 'utf8').split('\n')) {
    const eq = line.indexOf('=')
    if (eq < 0 || line.slice(0, eq).trim() !== name) continue
    const value = line.slice(eq + 1).trim()
    if (value.startsWith('"') && value.endsWith('"')) return value.slice(1, -1)
  }
  return undefined
}

describe('fleet-alerts staging-tenant token lifetime', () => {
  it('finds at least one smokeball seat on each side (the scan can fail)', () => {
    const staging = stagingSmokeballSeats()
    expect(staging).toContain('pilot-smokeball')
    expect(staging).not.toContain('ashton-price')
  })

  it('SMOKEBALL_STAGING_SEATS equals the customer.yaml staging set', () => {
    const listed = (tomlVar('SMOKEBALL_STAGING_SEATS') ?? '')
      .split(',')
      .map((s) => s.trim())
      .filter((s) => s.length > 0)
      .sort()
    expect(listed).toEqual(stagingSmokeballSeats())
  })

  it('the staging lifetime is shorter than the fleet lifetime', () => {
    const staging = Number(tomlVar('SMOKEBALL_STAGING_REFRESH_TOKEN_LIFETIME_DAYS'))
    const fleet = Number(tomlVar('SMOKEBALL_REFRESH_TOKEN_LIFETIME_DAYS'))
    expect(staging).toBe(30)
    expect(fleet).toBeGreaterThan(staging)
  })
})
