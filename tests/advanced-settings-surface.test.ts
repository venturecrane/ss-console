/**
 * The Advanced-settings surface: the resolver that renders it, the single
 * reconstruction behind it, and the honesty of what it reports (#2089, #1965,
 * #1966).
 *
 * The regression this file exists to prevent is not subtle and it shipped
 * once: `resolveEditableConfigFromRow` ran the STRICT validator over a
 * reconstruction that seeded fields designed to fail it, so the editor
 * rendered CONFIGURATION ERROR for every customer, always, and #1966 unlinked
 * the page rather than fix it. So the load-bearing test here is not a fixture —
 * it is every real `customer.yaml` in the tree, projected the way CI projects
 * it and resolved the way the page resolves it. A future validator change that
 * breaks the editor for a live seat fails CI instead of a client.
 */

import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parse as parseYaml } from 'yaml'
import { describe, expect, it } from 'vitest'

import { validate } from '../src/lib/operator/customer-yaml'
import { projectCustomerYamlToConfigRow } from '../src/lib/portal/customer-config-projection'
import { projectRow } from '../src/lib/portal/customer-config'
import { resolveEditableConfigFromRow } from '../src/lib/portal/operator/customer-yaml-editor'
import { reconstructFromProjection } from '../src/lib/portal/operator/customer-config-reconstruct'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const CUSTOMERS_DIR = join(REPO_ROOT, 'operator', 'customers')

/** Every live customer directory. Template dirs carry deliberate placeholders. */
function liveSlugs(): string[] {
  return readdirSync(CUSTOMERS_DIR, { withFileTypes: true })
    .filter((e) => e.isDirectory() && !e.name.startsWith('_'))
    .map((e) => e.name)
    .sort()
}

/** git customer.yaml → the D1 row CI writes → the row shape the portal reads. */
function projectionFor(slug: string): ReturnType<typeof projectRow> {
  const raw: unknown = parseYaml(readFileSync(join(CUSTOMERS_DIR, slug, 'customer.yaml'), 'utf8'))
  const result = validate(raw)
  if (!result.ok) {
    throw new Error(
      `${slug}/customer.yaml does not validate: ${result.errors.map((e) => e.path).join(', ')}`
    )
  }
  const dbRow = projectCustomerYamlToConfigRow(
    result.value,
    { entityId: 'ent_test', orgId: 'org_test', gitSha: 'test', syncedAt: '2026-07-31T00:00:00Z' },
    null
  )
  return projectRow(dbRow)
}

describe('advanced settings: the editor resolves for real projections (#1965)', () => {
  const slugs = liveSlugs()

  it('there is at least one live seat to check', () => {
    expect(slugs.length).toBeGreaterThan(0)
  })

  for (const slug of slugs) {
    it(`${slug}: a provisioning-shaped projection resolves to the editor surface, not an error`, () => {
      const resolved = resolveEditableConfigFromRow(projectionFor(slug))
      if ('error' in resolved) {
        throw new Error(
          `${slug} resolved to CONFIGURATION ERROR at: ` +
            resolved.errors.map((e) => `${e.code}@${e.path}`).join(', ')
        )
      }
      expect(resolved.editable.personas.length).toBeGreaterThan(0)
    })
  }

  it('a law-firm seat with projected cron resolves — the three failure classes #1965 named', () => {
    // hermes_ref (an invalid sentinel by design), users (reconstructed empty),
    // and cron[].wake_policy (stripped by the projection). A&P carries all
    // three; the assertion is that none of them reaches the client.
    const resolved = resolveEditableConfigFromRow(projectionFor('ashton-price'))
    expect('error' in resolved).toBe(false)
  })

  it('shows the client no identity value the projection does not carry', () => {
    const resolved = resolveEditableConfigFromRow(projectionFor('ashton-price'))
    if ('error' in resolved) throw new Error('resolver failed')
    // The row carries the slug and the vertical, and nothing else in the
    // identity block. The old locked view invented `model: 'unknown'`,
    // `fly_region: 'iad'` and `vertical: 'mixed'` — for a law firm — and a
    // fabricated version pin, all of which would have rendered.
    expect(resolved.locked.customer_id).toBe('ashton-price')
    expect(resolved.locked.vertical).toBe('law-firm')
    expect(resolved.locked.hermes_ref).toBeNull()
    expect(resolved.locked.model).toBeNull()
    expect(resolved.locked.fly_region).toBeNull()
    expect(resolved.locked.machine).toBeNull()
  })

  it('the reconstruction itself validates — the resolver has nothing to tolerate', () => {
    for (const slug of slugs) {
      const result = validate(reconstructFromProjection(projectionFor(slug)))
      expect(result.ok, `${slug}: ${result.ok ? '' : JSON.stringify(result.errors)}`).toBe(true)
    }
  })
})

describe('advanced settings: one reconstruction, not two', () => {
  const SRC = join(REPO_ROOT, 'src')

  function walk(dir: string): string[] {
    return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
      const full = join(dir, e.name)
      if (e.isDirectory()) return walk(full)
      return e.isFile() && (full.endsWith('.ts') || full.endsWith('.astro')) ? [full] : []
    })
  }

  const sources = walk(SRC).map((path) => ({ path, text: readFileSync(path, 'utf8') }))

  it('defines the projection reconstruction exactly once', () => {
    // Two copies under two names (`reconstructFromProjection` and
    // `reconstructProjection`) had already drifted apart before anyone noticed.
    const definitions = sources.filter((f) =>
      /function\s+reconstruct(FromProjection|Projection)\s*\(/.test(f.text)
    )
    expect(definitions.map((f) => f.path.slice(REPO_ROOT.length))).toEqual([
      'src/lib/portal/operator/customer-config-reconstruct.ts',
    ])
  })

  it('nothing calls the retired second name', () => {
    // The header of the surviving module names it, in prose, as the thing it
    // replaced — so the check is on calls, not on mentions.
    const stragglers = sources.filter((f) => /\breconstructProjection\s*\(/.test(f.text))
    expect(stragglers.map((f) => f.path.slice(REPO_ROOT.length))).toEqual([])
  })
})

describe('advanced settings: no success state for a write that did not happen', () => {
  const endpoint = readFileSync(
    join(REPO_ROOT, 'src/pages/api/portal/operator/settings/customer-yaml-update.ts'),
    'utf8'
  )
  const page = readFileSync(
    join(REPO_ROOT, 'src/pages/portal/products/operator/[instance]/settings/advanced/index.astro'),
    'utf8'
  )

  it('the customer.yaml endpoint never redirects a success status', () => {
    // It authorizes, merges, validates and records — and writes nothing,
    // because customer.yaml is git-authoritative. `applied` was a success
    // state for a write that never happened.
    expect(endpoint).toContain(`redirectToAdvancedSettings(auth.customerSlug, 'submitted')`)
    expect(endpoint).not.toMatch(/redirectToAdvancedSettings\([^)]*'applied'\)/)
  })

  it('the durable ledger and the client are told the same word', () => {
    // The ledger honestly recorded `submitted` while the page said `applied`.
    expect(endpoint).toContain(`status: 'submitted'`)
    expect(endpoint).not.toContain(`status: 'applied'`)
  })

  it('the page has no banner claiming the settings were saved', () => {
    expect(page).not.toMatch(/applied:\s*\{\s*text:\s*'Settings saved\./)
    expect(page).toContain('Change request recorded.')
  })

  it('only the output-shape writer reports a save, and it writes first', () => {
    const writer = readFileSync(
      join(REPO_ROOT, 'src/pages/api/portal/operator/settings/output-class-specs.ts'),
      'utf8'
    )
    const writeIndex = writer.indexOf('writeSpecDocument(')
    const savedIndex = writer.indexOf(`'spec_saved'`)
    expect(writeIndex).toBeGreaterThan(-1)
    expect(savedIndex).toBeGreaterThan(writeIndex)
  })
})

describe('advanced settings: the surface is reachable (#1966)', () => {
  const settings = readFileSync(
    join(REPO_ROOT, 'src/pages/portal/products/operator/[instance]/settings/index.astro'),
    'utf8'
  )

  // The link list is authored in settings-page.ts since 2026-09-11; the page
  // calls settingsLinks(operatorBase) into its SETTINGS_LINKS constant.
  const linkModule = readFileSync(
    join(REPO_ROOT, 'src/lib/portal/operator/settings-page.ts'),
    'utf8'
  )

  it('Advanced is listed in SETTINGS_LINKS', () => {
    expect(settings).toContain('const SETTINGS_LINKS = settingsLinks(operatorBase)')
    const links = linkModule.slice(linkModule.indexOf('export function settingsLinks'))
    expect(links).toContain('/settings/advanced')
    expect(links).toContain(`label: 'Advanced'`)
  })

  it('the stale note calling the editor unlinked is gone', () => {
    expect(settings).not.toContain('is unlinked pending a resolver fix')
    expect(settings).not.toContain('Until then it stays unlinked')
  })
})
