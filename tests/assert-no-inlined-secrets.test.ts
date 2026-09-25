/**
 * The post-build secret detector (scripts/assert-no-inlined-secrets.mjs).
 *
 * A local build in a shell carrying CLERK_SECRET_KEY inlined the key into the
 * Worker bundle (review 2026-09-25, Security 2). These cases plant exactly that
 * shape in a temporary bundle and prove the detector finds it, finds a secret
 * by value whatever its shape, reports without echoing the value, and stays
 * quiet on a clean bundle and on public keys.
 *
 * DO NOT write Vite's env accessor (the `import.meta` property named `env`)
 * anywhere in this file or in the script under test, not even inside a string
 * or a comment. Vite rewrites that token in every module it transforms into an
 * object literal of the ENTIRE process environment; when the rewrite lands in
 * a string it breaks the parse, and vitest's parse error prints the object,
 * secrets included. That happened while this file was written (2026-09-25).
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { execFileSync } from 'child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'fs'
import { tmpdir } from 'os'
import { join, resolve } from 'path'
import { findInlinedSecrets, secretValues } from '../scripts/assert-no-inlined-secrets.mjs'

// Synthetic: shaped like a key, belongs to nothing.
const FAKE = 'sk_test_' + 'x9'.repeat(16)

let dir: string

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'inlined-secrets-'))
  mkdirSync(join(dir, 'chunks'), { recursive: true })
  writeFileSync(join(dir, 'chunks', 'clean.mjs'), 'export const mode = "production"\n')
})

afterEach(() => rmSync(dir, { recursive: true, force: true }))

describe('findInlinedSecrets', () => {
  it('finds the shape Astro emits for an inlined Clerk secret', () => {
    writeFileSync(
      join(dir, 'chunks', 'server_x.mjs'),
      `Object.assign({}, { _: "/bin/astro", CLERK_SECRET_KEY: "${FAKE}" })\n`
    )
    const hits = findInlinedSecrets(dir, {})
    expect(hits).toEqual([expect.objectContaining({ name: 'CLERK_SECRET_KEY', check: 'name' })])
  })

  it('finds a secret-named variable of the build environment by value, in any shape', () => {
    writeFileSync(join(dir, 'chunks', 'server_y.mjs'), `const k = ["${FAKE}"]\n`)
    const hits = findInlinedSecrets(dir, { SOME_API_TOKEN: FAKE })
    expect(hits).toEqual([expect.objectContaining({ name: 'SOME_API_TOKEN', check: 'value' })])
  })

  it('is quiet on a clean bundle', () => {
    expect(findInlinedSecrets(dir, { SOME_API_TOKEN: FAKE })).toEqual([])
  })

  it('ignores public keys, which belong in the bundle', () => {
    expect(
      secretValues({
        PUBLIC_CLERK_PUBLISHABLE_KEY: FAKE,
        CLERK_PUBLISHABLE_KEY: FAKE,
        SHORT_KEY: 'abc',
        CLERK_SECRET_KEY: FAKE,
      }).map(([name]) => name)
    ).toEqual(['CLERK_SECRET_KEY'])
  })
})

describe('the CLI', () => {
  const script = resolve(process.cwd(), 'scripts/assert-no-inlined-secrets.mjs')

  it('fails on a planted secret and never prints its value', () => {
    writeFileSync(join(dir, 'chunks', 'server_z.mjs'), `x = { CLERK_SECRET_KEY: "${FAKE}" }\n`)
    let code = 0
    let output = ''
    try {
      execFileSync('node', [script, dir], {
        encoding: 'utf8',
        stdio: ['ignore', 'pipe', 'pipe'],
        env: { PATH: process.env.PATH ?? '' },
      })
    } catch (err) {
      const e = err as { status?: number; stdout?: string; stderr?: string }
      code = e.status ?? -1
      output = `${e.stdout ?? ''}${e.stderr ?? ''}`
    }
    expect(code).toBe(1)
    expect(output).toContain('CLERK_SECRET_KEY')
    expect(output).not.toContain(FAKE)
  })

  it('passes a clean bundle', () => {
    const out = execFileSync('node', [script, dir], {
      encoding: 'utf8',
      env: { PATH: process.env.PATH ?? '' },
    })
    expect(out).toMatch(/carries no inlined secret/)
  })
})
