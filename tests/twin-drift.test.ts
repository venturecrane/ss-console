/**
 * Twin drift (ADR 0107): an exact twin authors its client's customer.yaml
 * exactly, except the paths its twin.yaml declares.
 *
 * The winning line: every change is rehearsed on an exact twin of the client
 * seat before the client sees it. A twin that drifted from its client rehearses
 * something else, and nothing would say so. This test says so: A&P changes and
 * the twin does not, and CI fails; the twin grows a difference nobody declared,
 * and CI fails; a declared difference stops existing, and CI fails, so the list
 * of differences stays exactly the list of differences.
 *
 * The falsifiers run in this file, on a copy of the real seats: a change to the
 * client alone, a change to the twin alone, and a stale declaration each fail.
 */

import { describe, expect, it } from 'vitest'
import { cpSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { parse as parseYaml, stringify } from 'yaml'
import { loadSeatInventory } from '../scripts/lib/seat-inventory'
import {
  covers,
  diffPaths,
  readTwinDeclaration,
  twinDrift,
  type TwinDeclaration,
} from '../scripts/lib/twin-drift'

const CUSTOMERS = resolve('operator/customers')

/**
 * Release twins that are NOT exact copies of their client, each with the reason.
 * smd-staging binds no Smokeball connector and is a different persona; ap-staging
 * replaces it at the flip (docs/handbook/deployment-release.md), and that PR
 * removes this entry, after which every release twin must declare twin.yaml.
 */
const STATED_GAPS: Record<string, string> = {
  'smd-staging': 'predates exact twins; retired at the ap-staging flip',
}

function declarations(): TwinDeclaration[] {
  return readdirSync(CUSTOMERS)
    .map((slug) => readTwinDeclaration(CUSTOMERS, slug))
    .filter((d): d is TwinDeclaration => d !== null)
}

describe('every declared twin matches its client', () => {
  const decls = declarations()

  it('at least one exact twin is declared (ap-staging of ashton-price)', () => {
    expect(decls.map((d) => [d.twin, d.twinOf])).toContainEqual(['ap-staging', 'ashton-price'])
  })

  for (const decl of decls) {
    it(`${decl.twin} equals ${decl.twinOf} except its declared paths`, () => {
      const report = twinDrift(CUSTOMERS, decl)
      expect(
        report.undeclared,
        `${decl.twin}/customer.yaml differs from ${decl.twinOf}/customer.yaml at paths ` +
          `operator/customers/${decl.twin}/twin.yaml does not declare. Make the same change ` +
          'in both files (a client change is rehearsed on its twin), or, only for a value ' +
          "that cannot be the client's, declare the path with its why."
      ).toEqual([])
      expect(
        report.stale,
        `operator/customers/${decl.twin}/twin.yaml declares paths where the two files no ` +
          'longer differ. Remove them: the list holds only differences that exist.'
      ).toEqual([])
    })

    it(`${decl.twin}'s seat says what it is for`, () => {
      const seats = new Map(loadSeatInventory(CUSTOMERS).map((s) => [s.slug, s]))
      const twin = seats.get(decl.twin)
      const client = seats.get(decl.twinOf)
      expect(client?.purpose, `${decl.twinOf} must be a client seat`).toBe('client')
      // Before the flip a declared twin is `internal` (a client has one release
      // twin, ADR 0107); after it, a release twin of exactly this client.
      if (twin?.purpose === 'release-twin') expect(twin.twinOf).toBe(decl.twinOf)
      else expect(twin?.purpose).toBe('internal')
    })
  }

  it('every release twin is an exact twin, or a stated gap', () => {
    const twins = loadSeatInventory(CUSTOMERS).filter((s) => s.purpose === 'release-twin')
    const declared = new Set(decls.map((d) => d.twin))
    for (const t of twins) {
      if (declared.has(t.slug)) continue
      expect(STATED_GAPS[t.slug], `${t.slug} is a release twin with no twin.yaml`).toBeTruthy()
    }
    // A stated gap that is no longer a release twin is a stale entry.
    for (const slug of Object.keys(STATED_GAPS))
      expect(
        twins.map((t) => t.slug),
        `${slug} is in STATED_GAPS but not a release twin`
      ).toContain(slug)
  })
})

describe('the falsifiers, on a copy of the real seats', () => {
  function copy(): string {
    const dir = mkdtempSync(join(tmpdir(), 'twin-drift-'))
    for (const slug of ['ashton-price', 'ap-staging'])
      cpSync(join(CUSTOMERS, slug), join(dir, slug), { recursive: true })
    return dir
  }
  function edit(dir: string, slug: string, fn: (doc: Record<string, unknown>) => void) {
    const file = join(dir, slug, 'customer.yaml')
    const doc = parseYaml(readFileSync(file, 'utf8')) as Record<string, unknown>
    fn(doc)
    writeFileSync(file, stringify(doc))
  }

  it('the client changing alone fails (its model)', () => {
    const dir = copy()
    try {
      edit(dir, 'ashton-price', (d) => {
        d.model = 'claude-opus-5-5'
      })
      const decl = readTwinDeclaration(dir, 'ap-staging') as TwinDeclaration
      expect(twinDrift(dir, decl).undeclared).toEqual(['model'])
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('the client gaining a skill the twin lacks fails', () => {
    const dir = copy()
    try {
      edit(dir, 'ashton-price', (d) => {
        const p = (d.personas as Array<Record<string, unknown>>)[0]
        ;(p.skills as unknown[]).push({ name: 'new-skill', version: 'pending', enabled: true })
      })
      const decl = readTwinDeclaration(dir, 'ap-staging') as TwinDeclaration
      expect(twinDrift(dir, decl).undeclared).toEqual([
        'personas[slug=operator].skills[name=new-skill]',
      ])
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('the twin changing alone fails (its model)', () => {
    const dir = copy()
    try {
      edit(dir, 'ap-staging', (d) => {
        d.model = 'claude-opus-5-5'
      })
      const decl = readTwinDeclaration(dir, 'ap-staging') as TwinDeclaration
      expect(twinDrift(dir, decl).undeclared).toEqual(['model'])
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('a declaration covering no difference is stale', () => {
    const decl = readTwinDeclaration(CUSTOMERS, 'ap-staging') as TwinDeclaration
    const padded = { ...decl, differences: [...decl.differences, { path: 'model', why: 'x' }] }
    expect(twinDrift(CUSTOMERS, padded).stale).toEqual(['model'])
  })
})

describe('diffPaths and covers', () => {
  it('addresses list elements by identity, else by index', () => {
    expect(diffPaths({ a: [{ name: 'x', v: 1 }] }, { a: [{ name: 'x', v: 2 }] })).toEqual([
      'a[name=x].v',
    ])
    expect(diffPaths({ a: [1, 2] }, { a: [1, 3] })).toEqual(['a[1]'])
    expect(diffPaths({ a: 1 }, { a: 1 })).toEqual([])
    expect(diffPaths({ a: 1 }, {})).toEqual(['a'])
  })
  it('a path covers itself and its subtree, nothing else', () => {
    expect(covers('scope.admins', 'scope.admins[0]')).toBe(true)
    expect(covers('scope.admins', 'scope.admins')).toBe(true)
    expect(covers('scope.admin', 'scope.admins')).toBe(false)
    expect(covers('memory', 'memory.d1_namespace')).toBe(true)
  })
})
