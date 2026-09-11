/**
 * Pre-merge gate: every SHIPPED seat config must pass the same validators the
 * D1 projection runs.
 *
 * Why this exists. `scripts/project-customer-config.ts` hard-validates both
 * `customer.yaml` and its sibling `routine-grid.yaml` and exits non-zero on any
 * error, and the deploy workflow's "Sync customer.yaml → D1 projection" job
 * runs it for every changed seat on push to main. Until this file, nothing ran
 * those validators over the real configs before merge — the narrow suites that
 * walk `operator/customers/` each assert one targeted property (materializable
 * backends, banned_tools display coverage, no PLACEHOLDER markers), never the
 * whole schema. So an invalid seat config merged GREEN and failed only after
 * the fact, which costs twice:
 *
 *   1. main goes red on a post-merge job (a Deploy failure page at 02:34),
 *   2. the seat's live `customer_configs` row silently keeps the PREVIOUS
 *      config — the exact stale-projection failure class the auto-sync job was
 *      built to close (ADR 0012 §5, #1308).
 *
 * That happened on 2026-07-24: the smd-staging msgraph Email binding (#1991,
 * ADR 0078) tripped a stale `UnknownWebhookSource` coupling rule that predated
 * poll-driven inbound. Both validators were run by hand on that PR and the
 * console's own suite was green, because no suite loaded the file.
 *
 * Scope note: `_`-prefixed dirs are templates, not seats — they carry authoring
 * placeholders (`[filevine / clio / no_pm]`) and are skipped by the CI sync
 * script for the same reason. This mirrors that rule so the two never diverge.
 */

import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, existsSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { parse as parseYaml } from 'yaml'
import { validate } from '../src/lib/operator/customer-yaml'
import { validateRoutineGrid } from '../src/lib/operator/routine-grid'
import { isCeiling, restrictiveness } from '../src/lib/portal/operator/config-governance'

const CUSTOMERS_DIR = resolve('operator/customers')

/**
 * Seat slugs the CI sync script would project: real dirs; `_`-prefixed
 * template dirs and `<slug>.decommissioned.<date>` tombstones skipped. The rule
 * is identical to the one in scripts/ci-publish-customer-configs.sh and
 * scripts/ci-reconcile-customer-configs.sh, on purpose.
 */
function shippedSlugs(): string[] {
  if (!existsSync(CUSTOMERS_DIR)) return []
  return readdirSync(CUSTOMERS_DIR, { withFileTypes: true })
    .filter(
      (d) => d.isDirectory() && !d.name.startsWith('_') && !d.name.includes('.decommissioned.')
    )
    .map((d) => d.name)
    .filter((slug) => existsSync(join(CUSTOMERS_DIR, slug, 'customer.yaml')))
    .sort()
}

const slugs = shippedSlugs()

function formatErrors(errors: { code: string; path: string; message: string }[]): string {
  return errors.map((e) => `  [${e.code}] ${e.path}: ${e.message}`).join('\n')
}

describe('shipped customer configs validate (projection parity)', () => {
  it('discovers at least one shipped seat', () => {
    expect(slugs.length).toBeGreaterThan(0)
  })

  it.each(slugs)('%s: customer.yaml passes the canonical validator', (slug) => {
    const path = join(CUSTOMERS_DIR, slug, 'customer.yaml')
    const result = validate(parseYaml(readFileSync(path, 'utf-8')))
    if (!result.ok) {
      throw new Error(
        `operator/customers/${slug}/customer.yaml would fail the D1 projection:\n` +
          formatErrors(result.errors)
      )
    }
  })

  it.each(slugs)('%s: routine-grid.yaml passes the canonical validator when present', (slug) => {
    const path = join(CUSTOMERS_DIR, slug, 'routine-grid.yaml')
    if (!existsSync(path)) return
    const result = validateRoutineGrid(parseYaml(readFileSync(path, 'utf-8')))
    if (!result.ok) {
      throw new Error(
        `operator/customers/${slug}/routine-grid.yaml would fail the D1 projection:\n` +
          formatErrors(result.errors)
      )
    }
  })
})

// ---------------------------------------------------------------------------
// Grid <-> config traceability, for EVERY seat that authors a grid
// ---------------------------------------------------------------------------

/**
 * The join above validates each grid's SCHEMA. Schema-valid and true are
 * different claims: a grid can parse perfectly while naming an exposure key the
 * seat never authors, or a skill the seat does not carry.
 *
 * Until ss#2313 that traceability join existed only in
 * `tests/customer-commitments.test.ts`, hardcoded to two slugs — `pilot-smokeball`
 * (test c) and `ashton-price` (test h). A third seat authoring a routine grid got
 * no traceability gate at all, in a file that was already enumerating seats
 * dynamically two describes above. This closes that: the enumeration is the same
 * one the CI sync script uses, so a seat cannot be added to the fleet and miss
 * the gate.
 *
 * The gap this class of gate hides is not theoretical. Test (h) exists because
 * `ashton-price` authored neither `external_send_client` nor `external_send_vendor`
 * while its own grid claimed both enforce the letter's prepare-and-route tiers —
 * and `resolve_ceiling` does NO recipient-class fallback, so an unauthored key is
 * REFUSED (ADR 0056). Those routines would have refused instead of drafting.
 *
 * DIRECTION MATTERS (inherited from (h), same rationale). A grid-claimed key the
 * seat does not author is a DEFECT. A seat value MORE restrictive than the grid
 * claims is the client's own posture and is allowed — running tighter than
 * committed is always the firm's right (ADR 0035). Only absence, or a value LESS
 * restrictive than claimed, fails.
 *
 * The seat-specific suites keep their own gates: they assert client-specific
 * commitments (letter-pinned values, the exposure_ceiling derivation) that this
 * generic join cannot know.
 */
const TIER_VOCAB = ['flag-only', 'prepare-and-route', 'auto-handle'] as const

function gridSlugs(): string[] {
  return slugs.filter((slug) => existsSync(join(CUSTOMERS_DIR, slug, 'routine-grid.yaml')))
}

const withGrid = gridSlugs()

describe('shipped routine grids trace to their seat config', () => {
  // A gate whose subject set is empty measured nothing (Law 12). If every grid
  // disappears, that is a fleet change worth failing on, not silently skipping.
  it('discovers at least one seat authoring a routine grid', () => {
    expect(withGrid.length).toBeGreaterThan(0)
  })

  it.each(withGrid)('%s: every grid row traces to the live seat config', (slug) => {
    const seatResult = validate(
      parseYaml(readFileSync(join(CUSTOMERS_DIR, slug, 'customer.yaml'), 'utf-8'))
    )
    if (!seatResult.ok) {
      throw new Error(
        `operator/customers/${slug}/customer.yaml no longer validates:\n` +
          formatErrors(seatResult.errors)
      )
    }
    const gridResult = validateRoutineGrid(
      parseYaml(readFileSync(join(CUSTOMERS_DIR, slug, 'routine-grid.yaml'), 'utf-8'))
    )
    if (!gridResult.ok) {
      throw new Error(
        `operator/customers/${slug}/routine-grid.yaml no longer validates:\n` +
          formatErrors(gridResult.errors)
      )
    }

    const persona = seatResult.value.personas.find((p) => p.slug === gridResult.value.persona)
    expect(
      persona,
      `${slug}: the grid names persona "${gridResult.value.persona}", which the seat does not author`
    ).toBeTruthy()
    const exposure = persona!.entitlements.exposure
    const seatSkills = new Set(
      seatResult.value.personas.flatMap((p) => p.skills.map((s) => s.name))
    )

    expect(
      gridResult.value.rows.length,
      `${slug}: a routine-grid.yaml with no rows traces nothing`
    ).toBeGreaterThan(0)

    for (const row of gridResult.value.rows) {
      expect(TIER_VOCAB, `${slug}/${row.routine}: start_tier`).toContain(row.start_tier)
      expect(TIER_VOCAB, `${slug}/${row.routine}: ceiling_tier`).toContain(row.ceiling_tier)

      for (const [key, claimed] of Object.entries(row.enforcement.exposure_keys)) {
        const authored = exposure[key as keyof typeof exposure] as string | undefined
        expect(
          authored,
          `${slug}/${row.routine}: the grid says ${key}=${claimed} enforces this row, but the seat authors no ${key} (unauthored = REFUSED, ADR 0056 — the routine would refuse instead of acting)`
        ).toBeTruthy()
        if (!authored || !isCeiling(authored) || !isCeiling(claimed)) continue
        expect(
          restrictiveness(authored) >= restrictiveness(claimed),
          `${slug}/${row.routine}: the seat authors ${key}=${authored}, LESS restrictive than the grid's ${claimed} — the seat exceeds what the grid commits`
        ).toBe(true)
      }

      for (const skill of row.skills) {
        expect(
          seatSkills.has(skill),
          `${slug}/${row.routine}: the grid names skill "${skill}", which no persona on this seat carries`
        ).toBe(true)
      }
    }
  })
})
