/**
 * One error vocabulary for the API (code review 2026-09-10, Architecture 5).
 *
 * Before 2026-09-11 the `error` key under src/pages/api carried 36 snake_case
 * codes and 33 human sentences at once. The shape is now fixed by
 * src/lib/api/errors.ts: `error` is a code from API_ERROR_CATALOG and
 * `message` is the prose. This file pins three things:
 *
 *   1. Every literal passed as the code argument of errorResponse() in a route
 *      or the middleware is a catalog member (so a new string cannot slip in).
 *   2. No route or the middleware builds an `{ error: ... }` body by hand
 *      through jsonResponse (the ESLint guard says the same; this is the test
 *      that cannot be disabled inline).
 *   3. errorResponse() itself emits both keys, with the catalog default when a
 *      site gives no message, and carries extras alongside.
 *
 * What would make it false: an errorResponse('...') call with a non-catalog
 * literal, a `jsonResponse(4xx, { error` anywhere under the API tree, or the
 * helper returning a body without `message`.
 */

import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'fs'
import { resolve } from 'path'
import { API_ERROR_CATALOG } from '../src/lib/api/errors'
import { errorResponse } from '../src/lib/api/helpers'

const API_ROOT = resolve('src/pages/api')
const FILES = [
  ...readdirSync(API_ROOT, { recursive: true })
    .map((entry) => `${API_ROOT}/${String(entry)}`)
    .filter((p) => p.endsWith('.ts') && !p.endsWith('.test.ts') && statSync(p).isFile()),
  resolve('src/middleware.ts'),
]

const API_ERROR_CODES = Object.keys(API_ERROR_CATALOG)
const isApiErrorCode = (value: string): boolean => Object.hasOwn(API_ERROR_CATALOG, value)

const CODE_ARG = /errorResponse\(\s*[\w.]+\s*,\s*'([^']*)'/g
const HAND_BUILT = /jsonResponse\(\s*[\w.]+\s*,\s*\{[^}]*\berror\s*:/g

describe('API error vocabulary', () => {
  it('every catalog code is snake_case with a non-empty default message', () => {
    for (const code of API_ERROR_CODES) {
      expect(code).toMatch(/^[a-z][a-z0-9_]*$/)
      expect(API_ERROR_CATALOG[code as keyof typeof API_ERROR_CATALOG].length).toBeGreaterThan(0)
    }
    expect(API_ERROR_CODES.length).toBeGreaterThan(30)
  })

  it('every literal code passed to errorResponse in a route is a catalog member', () => {
    const offenders: string[] = []
    let sites = 0
    for (const file of FILES) {
      const src = readFileSync(file, 'utf8')
      for (const m of src.matchAll(CODE_ARG)) {
        sites += 1
        if (!isApiErrorCode(m[1]))
          offenders.push(`${file.replace(`${resolve('.')}/`, '')}: '${m[1]}'`)
      }
    }
    expect(sites).toBeGreaterThan(150)
    expect(offenders).toEqual([])
  })

  it('no route or the middleware hand-builds an { error } body through jsonResponse', () => {
    const offenders: string[] = []
    for (const file of FILES) {
      const src = readFileSync(file, 'utf8')
      if (HAND_BUILT.test(src)) offenders.push(file.replace(`${resolve('.')}/`, ''))
      HAND_BUILT.lastIndex = 0
    }
    expect(offenders).toEqual([])
  })

  it('the scanner can fail: a non-catalog literal and a hand-built body are both caught', () => {
    const bad = "errorResponse(400, 'Not A Code') jsonResponse(400, { error: 'x' })"
    const codes = [...bad.matchAll(CODE_ARG)].map((m) => m[1])
    expect(codes).toEqual(['Not A Code'])
    expect(isApiErrorCode('Not A Code')).toBe(false)
    expect(HAND_BUILT.test(bad)).toBe(true)
    HAND_BUILT.lastIndex = 0
  })

  it('errorResponse emits the code and a message, defaulting from the catalog', async () => {
    const res = errorResponse(401, 'unauthorized')
    expect(res.status).toBe(401)
    expect(res.headers.get('Content-Type')).toBe('application/json')
    expect(await res.json()).toEqual({ error: 'unauthorized', message: 'Unauthorized.' })

    const custom = errorResponse(400, 'validation_failed', 'Photo file required.', {
      fields: { photo: 'required' },
    })
    expect(await custom.json()).toEqual({
      error: 'validation_failed',
      message: 'Photo file required.',
      fields: { photo: 'required' },
    })
  })
})
