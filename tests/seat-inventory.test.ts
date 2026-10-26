/**
 * The seat inventory (scripts/lib/seat-inventory.ts) and everything derived
 * from it.
 *
 * Before this, the set of Operator seats was enumerated by hand in several
 * places with different rules: the Fly app registry, the release rings, the
 * obligation register's rollup, the since-removed overlay-ref-drift (which
 * excluded only `_template`, so it counted the other two scaffolds as seats), and the CI
 * projection scripts (one of which had no tombstone case). This file holds the
 * three guarantees that make "one inventory" true rather than aspirational:
 *
 *  1. DRIFT. Every generated file is byte-identical to what the inventory
 *     renders. Adding a seat dir, or editing a seat.yaml, without
 *     `npm run gen:seat-registries` fails here.
 *  2. ONE RULE. The TypeScript, Python and bash homes of the seat-dir rule agree
 *     on a fixture tree holding every kind of non-seat, a tombstone included.
 *  3. AUTHORED, NOT GUESSED. A seat cannot load without a seat.yaml that names
 *     its Fly app (or says why it has none) and its ring; two seats cannot claim
 *     one app.
 */

import { describe, expect, it, beforeAll, afterAll } from 'vitest'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync, cpSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import {
  loadSeatInventory,
  seatSlugs,
  skipReason,
  SeatInventoryError,
} from '../scripts/lib/seat-inventory'
import { renderRingsYaml, renderSeatRegistries } from '../scripts/lib/seat-registries'

const ROOT = resolve('.')
const CUSTOMERS = resolve('operator/customers')

const SEAT_YAML = (app: string | null, ring: number, authored: string, extra = '') =>
  (app === null ? 'fly_app: null\nunprovisioned_reason: not stood up yet\n' : `fly_app: ${app}\n`) +
  `ring: ${ring}\nauthored: '${authored}'\n${extra}`

function seatDir(root: string, name: string, seatYaml: string | null, customerYaml = true) {
  mkdirSync(join(root, name), { recursive: true })
  if (customerYaml) writeFileSync(join(root, name, 'customer.yaml'), 'seat: {}\n')
  if (seatYaml !== null) writeFileSync(join(root, name, 'seat.yaml'), seatYaml)
}

describe('generated registries are current (drift)', () => {
  it('every generated file matches what the inventory renders', async () => {
    const files = await renderSeatRegistries(ROOT, loadSeatInventory(CUSTOMERS))
    const stale = files
      .filter((f) => readFileSync(resolve(ROOT, f.path), 'utf8') !== f.text)
      .map((f) => f.path)
    expect(
      stale,
      `stale generated file(s): ${stale.join(', ')}. A seat dir or seat.yaml changed without ` +
        'regeneration. Run `npm run gen:seat-registries` and commit the result.'
    ).toEqual([])
  })

  it('the live inventory is the seven seats, each with its authored app or null', () => {
    // A tripwire for the PR that introduced the inventory: the generated
    // registry was proven value-identical to the hand-kept one it replaced.
    // Adding or retiring a seat updates this list in the same change.
    expect(Object.fromEntries(loadSeatInventory(CUSTOMERS).map((s) => [s.slug, s.flyApp]))).toEqual(
      {
        'ashton-price': 'hermes-ashton-price',
        // Authored, not yet provisioned (the firm creates its mailbox first).
        'ashton-price-marketing': null,
        brokery: 'hermes-brokery',
        'captain-mark': 'hermes-captain-mark',
        'pilot-smokeball': 'hermes-pilot-smokeball',
        scott: 'hermes-scott',
        'smd-staging': 'hermes-smd-staging',
      }
    )
  })
})

describe('one seat-dir rule across TypeScript, Python and bash', () => {
  let root: string

  beforeAll(() => {
    root = mkdtempSync(join(tmpdir(), 'seat-inventory-'))
    seatDir(root, 'acme', SEAT_YAML('hermes-acme', 1, '2026-01-02'))
    seatDir(root, 'beta-firm', SEAT_YAML('hermes-beta-firm', 0, '2026-01-01'))
    // Not seats, every kind, each WITH a customer.yaml so only the rule excludes it.
    seatDir(root, '_template', null)
    seatDir(root, '_hosted-template', null)
    seatDir(root, '_couples-template', null)
    seatDir(root, '.hidden', null)
    // A decommissioned tombstone keeps its customer.yaml AND its seat.yaml
    // (decommission.py renames the whole dir); it must still not be a seat.
    seatDir(root, 'gone.decommissioned.2026-10-01', SEAT_YAML('hermes-gone', 2, '2026-01-03'))
    seatDir(root, 'no-yaml', null, false)
    seatDir(root, 'Bad_Slug', null)
  })

  afterAll(() => rmSync(root, { recursive: true, force: true }))

  it('TypeScript: tombstones and scaffolds are not seats', () => {
    expect(seatSlugs(root)).toEqual(['acme', 'beta-firm'])
    expect(loadSeatInventory(root).map((s) => s.slug)).toEqual(['acme', 'beta-firm'])
    expect(skipReason('gone.decommissioned.2026-10-01')).toBe('tombstone')
    expect(skipReason('_couples-template')).toBe('scaffold')
  })

  it('Python (operator/bin/lib/seat_dirs.py) agrees', () => {
    const py = execFileSync('python3', ['-I', 'operator/bin/lib/seat_dirs.py', root], {
      encoding: 'utf8',
    })
    expect(JSON.parse(py)).toEqual(seatSlugs(root))
  })

  it('Python fleet rings (live_seats) agrees', () => {
    // Same stand-in as tests/handbook-integrity.test.ts: CI python has no pyyaml.
    const py = execFileSync(
      'python3',
      [
        '-I',
        '-c',
        'import sys, json, types; sys.modules.setdefault("yaml", types.ModuleType("yaml")); ' +
          'sys.path.insert(0, "operator"); from pathlib import Path; ' +
          'from bin.lib.fleet.rings import live_seats; ' +
          `print(json.dumps(live_seats(Path(${JSON.stringify(root)}))))`,
      ],
      { encoding: 'utf8' }
    )
    expect(JSON.parse(py)).toEqual(seatSlugs(root))
  })

  it('bash (scripts/lib/seat-dir.sh) classifies every name the same way', () => {
    const names = [
      'acme',
      'beta-firm',
      '_template',
      '_hosted-template',
      '_couples-template',
      '.hidden',
      'gone.decommissioned.2026-10-01',
      'no-yaml',
    ]
    const script =
      'source scripts/lib/seat-dir.sh; for n in "$@"; do ' +
      'if r=$(seat_dir_skip_reason "$n"); then echo "$n $r"; else echo "$n -"; fi; done'
    const out = execFileSync('bash', ['-c', script, 'seat-dir', ...names], { encoding: 'utf8' })
    const bash = Object.fromEntries(
      out
        .trim()
        .split('\n')
        .map((l) => l.split(' '))
    )
    expect(bash).toEqual(Object.fromEntries(names.map((n) => [n, skipReason(n) ?? '-'])))
  })
})

describe('a seat is authored, not guessed', () => {
  const roots: string[] = []
  const fresh = () => {
    const r = mkdtempSync(join(tmpdir(), 'seat-inventory-'))
    roots.push(r)
    return r
  }
  afterAll(() => roots.forEach((r) => rmSync(r, { recursive: true, force: true })))

  it('refuses a seat with no seat.yaml, naming the file', () => {
    const r = fresh()
    seatDir(r, 'acme', null)
    expect(() => loadSeatInventory(r)).toThrow(/operator\/customers\/acme\/seat\.yaml: missing/)
  })

  it('refuses a seat.yaml with no fly_app, and says not to assume hermes-<slug>', () => {
    const r = fresh()
    seatDir(r, 'acme', "ring: 1\nauthored: '2026-01-01'\n")
    expect(() => loadSeatInventory(r)).toThrow(/Do not assume hermes-acme/)
  })

  it('refuses fly_app: null without a reason, and accepts it with one', () => {
    const r = fresh()
    seatDir(r, 'acme', "fly_app: null\nring: 1\nauthored: '2026-01-01'\n")
    expect(() => loadSeatInventory(r)).toThrow(SeatInventoryError)
    writeFileSync(join(r, 'acme', 'seat.yaml'), SEAT_YAML(null, 1, '2026-01-01'))
    expect(loadSeatInventory(r)[0]).toMatchObject({
      flyApp: null,
      unprovisionedReason: 'not stood up yet',
    })
  })

  it('refuses two seats claiming one Fly app (cross-tenant, ADR 0036)', () => {
    const r = fresh()
    seatDir(r, 'acme', SEAT_YAML('hermes-shared', 1, '2026-01-01'))
    seatDir(r, 'beta', SEAT_YAML('hermes-shared', 1, '2026-01-02'))
    expect(() => loadSeatInventory(r)).toThrow(/already acme's app/)
  })

  it('refuses unknown keys and an unquoted or malformed date', () => {
    const r = fresh()
    seatDir(r, 'acme', SEAT_YAML('hermes-acme', 1, '2026-01-01', 'lifecycle: live\n'))
    expect(() => loadSeatInventory(r)).toThrow(/unknown key\(s\): lifecycle/)
    writeFileSync(join(r, 'acme', 'seat.yaml'), 'fly_app: hermes-acme\nring: 1\nauthored: soon\n')
    expect(() => loadSeatInventory(r)).toThrow(/authored is required/)
  })

  it('defaults client to the seat itself', () => {
    const r = fresh()
    seatDir(r, 'acme', SEAT_YAML('hermes-acme', 1, '2026-01-01'))
    seatDir(r, 'acme-two', SEAT_YAML('hermes-acme-two', 1, '2026-01-02', 'client: acme\n'))
    expect(loadSeatInventory(r).map((s) => [s.slug, s.client])).toEqual([
      ['acme', 'acme'],
      ['acme-two', 'acme'],
    ])
  })
})

describe('renderers', () => {
  it('orders a ring by authored date, then slug, and caps only the last ring', () => {
    const seat = (slug: string, ring: number, authored: string) => ({
      slug,
      flyApp: `hermes-${slug}`,
      unprovisionedReason: null,
      ring,
      authored,
      client: slug,
    })
    const yaml = renderRingsYaml([
      seat('zeta', 2, '2026-01-01'),
      seat('alpha', 2, '2026-03-01'),
      seat('mid', 2, '2026-01-01'),
      seat('first', 0, '2026-05-01'),
    ])
    expect(yaml.slice(yaml.indexOf('\nrings:') + 1)).toBe(
      'rings:\n  - ring: 0\n    seats: [first]\n    bake_minutes: 10\n  - ring: 2\n    seats: [mid, zeta, alpha]\n    max_parallel: 1\n'
    )
  })

  it('puts a seat with no Fly app in no ring (fleet release has nothing to deploy to)', () => {
    const seat = (slug: string, flyApp: string | null) => ({
      slug,
      flyApp,
      unprovisionedReason: flyApp === null ? 'not stood up yet' : null,
      ring: 1,
      authored: '2026-01-01',
      client: slug,
    })
    const yaml = renderRingsYaml([seat('live', 'hermes-live'), seat('pending', null)])
    // FALSIFIER: drop the flyApp filter in renderRingsYaml and `pending` joins ring 1.
    expect(yaml.slice(yaml.indexOf('\nrings:') + 1)).toBe(
      'rings:\n  - ring: 1\n    seats: [live]\n    max_parallel: 1\n'
    )
  })

  it('bakes a ring for the longest bake_minutes any of its seats authors (ADR 0096)', () => {
    const seat = (slug: string, ring: number, bakeMinutes: number | null) => ({
      slug,
      flyApp: `hermes-${slug}`,
      unprovisionedReason: null,
      ring,
      authored: '2026-01-01',
      client: slug,
      bakeMinutes,
    })
    const yaml = renderRingsYaml([
      seat('a', 0, 10),
      seat('b', 0, 45),
      seat('c', 0, null),
      seat('d', 1, null),
      seat('e', 2, 99),
    ])
    // FALSIFIER: take the first or the smallest value and ring 0 bakes 10.
    expect(yaml).toContain('  - ring: 0\n    seats: [a, b, c]\n    bake_minutes: 45\n')
    expect(yaml).toContain('  - ring: 1\n    seats: [d]\n    bake_minutes: 10\n')
    // The last ring has no next ring to gate.
    expect(yaml).toContain('  - ring: 2\n    seats: [e]\n    max_parallel: 1\n')
  })

  it('refuses a bake_minutes that is not a whole minute count', () => {
    const r = mkdtempSync(join(tmpdir(), 'seat-inventory-bake-'))
    try {
      seatDir(r, 'acme', SEAT_YAML('hermes-acme', 1, '2026-01-01', 'bake_minutes: -5\n'))
      expect(() => loadSeatInventory(r)).toThrow(/bake_minutes/)
      writeFileSync(
        join(r, 'acme', 'seat.yaml'),
        SEAT_YAML('hermes-acme', 1, '2026-01-01', 'bake_minutes: 45\n')
      )
      expect(loadSeatInventory(r)[0].bakeMinutes).toBe(45)
    } finally {
      rmSync(r, { recursive: true, force: true })
    }
  })

  it('a new seat dir without regeneration makes the committed files stale', async () => {
    // The drift guarantee, proven on a copy so the repo tree is untouched:
    // render from the real inventory plus one new seat, and the result differs
    // from what is committed in exactly the registry and the rings.
    const root = mkdtempSync(join(tmpdir(), 'seat-inventory-'))
    try {
      cpSync(CUSTOMERS, root, { recursive: true })
      seatDir(root, 'new-seat', SEAT_YAML('hermes-new-seat', 2, '2026-12-01'))
      const files = await renderSeatRegistries(ROOT, loadSeatInventory(root))
      const stale = files
        .filter((f) => readFileSync(resolve(ROOT, f.path), 'utf8') !== f.text)
        .map((f) => f.path)
      expect(stale).toEqual([
        'src/lib/operator/fly-app-registry.generated.ts',
        'operator/fleet/rings.yaml',
      ])
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })
})
