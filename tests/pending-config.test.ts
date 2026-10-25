/**
 * An authored-but-unprovisioned seat carries PENDING markers, and provisioning
 * refuses it while any remain (scripts/lib/pending-config.ts, run by
 * operator/bin/lib/stage-seat-spec.sh before anything reaches R2).
 *
 * FALSIFIER: drop a marker rule from isMarker and the marketing seat's
 * expected list loses that line; drop the seat.yaml check and the first
 * refusal disappears; drop the call from stage-seat-spec.sh and the last
 * assertion fails.
 */

import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

import { loadSeatInventory } from '../scripts/lib/seat-inventory'
import { pendingValues, provisioningRefusals } from '../scripts/lib/pending-config'

const ROOT = resolve(__dirname, '..')
const CUSTOMERS = resolve(ROOT, 'operator/customers')
const config = (slug: string) => readFileSync(resolve(CUSTOMERS, slug, 'customer.yaml'), 'utf8')

describe('pending config', () => {
  it('finds each marker kind and nothing else', () => {
    expect(
      pendingValues({
        a: '00000000-0000-0000-0000-000000000000',
        b: 'PENDING-firm-user',
        c: 'x@nowhere.invalid', // not a marker alone: smd-staging authors .invalid on purpose
        connectors: { Analytics: { property_id: 0 }, Real: { property_id: 123 } },
        skills: [{ version: 'pending' }],
        fine: 'e4ad47eb-14d8-4131-9e13-9573a09a7d69',
      })
    ).toEqual([
      'a: "00000000-0000-0000-0000-000000000000"',
      'b: "PENDING-firm-user"',
      'connectors.Analytics.property_id: 0',
    ])
  })

  it('refuses the marketing seat, naming the missing app and every pending value', () => {
    // Paths only: the values are the seat's authored data (client-identity-gate).
    const refusals = provisioningRefusals(
      ROOT,
      'ashton-price-marketing',
      config('ashton-price-marketing')
    )
    expect(refusals[0]).toMatch(/^seat\.yaml authors fly_app: null \(The firm has not yet created/)
    expect(refusals.slice(1).map((r) => r.replace(/: .*$/, ''))).toEqual([
      'customer.yaml still holds a PENDING value at connectors.Email.msgraph_auth.client_id',
      'customer.yaml still holds a PENDING value at connectors.Email.msgraph_auth.mailbox',
      'customer.yaml still holds a PENDING value at connectors.Website.username',
      'customer.yaml still holds a PENDING value at connectors.Analytics.property_id',
    ])
  })

  it('refuses no provisioned seat (the guard cannot block a live seat reprovision)', () => {
    for (const seat of loadSeatInventory(CUSTOMERS).filter((s) => s.flyApp !== null)) {
      expect(provisioningRefusals(ROOT, seat.slug, config(seat.slug)), seat.slug).toEqual([])
    }
  })

  it('the CLI exits 1 on the marketing seat and 0 on a provisioned one', () => {
    const run = (slug: string) => {
      try {
        execFileSync(
          'npx',
          [
            'tsx',
            'scripts/refuse-pending-config.ts',
            '--slug',
            slug,
            '--config',
            `operator/customers/${slug}/customer.yaml`,
          ],
          { cwd: ROOT, stdio: 'pipe' }
        )
        return 0
      } catch (err) {
        return (err as { status: number }).status
      }
    }
    expect(run('ashton-price-marketing')).toBe(1)
    expect(run('brokery')).toBe(0)
  })

  it('provisioning runs the refusal before the compiler and the upload', () => {
    const stage = readFileSync(resolve(ROOT, 'operator/bin/lib/stage-seat-spec.sh'), 'utf8')
    const refuse = stage.indexOf('scripts/refuse-pending-config.ts')
    expect(refuse).toBeGreaterThan(-1)
    expect(refuse).toBeLessThan(stage.indexOf('scripts/compile-seat-spec.ts compile'))
    expect(refuse).toBeLessThan(stage.indexOf('_r2_put "${CUSTOMER_YAML}"'))
  })
})
