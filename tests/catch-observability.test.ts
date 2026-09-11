/**
 * A caught failure on a money path or a webhook reaches Sentry, not only a
 * Worker log nobody pages on (code review 2026-09-10, Code Quality 4:
 * captureError was imported by 5 of 174 files with a catch).
 *
 * Scope is deliberate: the three route directories where a swallowed error
 * costs money or loses a vendor event. Every `catch` block in them that logs
 * with console.error must also call captureError. A catch that logs nothing
 * (a JSON parse guard answering 400) is not in scope; it is a decision, not a
 * failure.
 *
 * The scanner is exercised against inline fixtures first, so a scanner that
 * matched nothing could not pass this file.
 */

import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const ROOTS = [
  'src/pages/api/webhooks',
  'src/pages/api/admin/clients',
  'src/pages/api/portal/products/operator',
]

/** Every `catch` block body in a source file, found by brace matching. */
export function catchBlocks(source: string): string[] {
  const out: string[] = []
  const re = /\bcatch\s*(?:\([^)]*\))?\s*\{/g
  let m: RegExpExecArray | null
  while ((m = re.exec(source)) !== null) {
    let depth = 1
    let i = m.index + m[0].length
    const start = i
    while (i < source.length && depth > 0) {
      const ch = source[i]
      if (ch === '{') depth++
      else if (ch === '}') depth--
      i++
    }
    out.push(source.slice(start, i - 1))
  }
  return out
}

/** Offending catch bodies: they log an error but never report it. */
export function unreportedCatches(source: string): string[] {
  return catchBlocks(source).filter(
    (body) => body.includes('console.error') && !body.includes('captureError(')
  )
}

function routeFiles(root: string): string[] {
  const abs = resolve(root)
  return readdirSync(abs, { recursive: true })
    .map(String)
    .filter((rel) => rel.endsWith('.ts') && !rel.endsWith('.test.ts'))
    .map((rel) => `${abs}/${rel}`)
}

describe('the scanner can fail', () => {
  it('flags a catch that logs without reporting', () => {
    const src = `try { a() } catch (err) {\n  console.error('x', err)\n  return back()\n}`
    expect(unreportedCatches(src)).toHaveLength(1)
  })

  it('accepts a catch that reports, and ignores a silent parse guard', () => {
    const reported = `try { a() } catch (err) {\n  console.error('x', err)\n  captureError(err, 'area')\n}`
    const silent = `try { JSON.parse(s) } catch {\n  return errorResponse(400, 'Invalid JSON')\n}`
    expect(unreportedCatches(reported)).toHaveLength(0)
    expect(unreportedCatches(silent)).toHaveLength(0)
  })

  it('handles nested braces inside the catch body', () => {
    const src = `try { a() } catch (err) {\n  if (x) { console.error('y', err) }\n}`
    expect(unreportedCatches(src)).toHaveLength(1)
  })
})

describe('money-path and webhook catches report to Sentry', () => {
  it('every catch that logs an error in the scoped routes also calls captureError', () => {
    const offenders: string[] = []
    let scanned = 0
    for (const root of ROOTS) {
      for (const file of routeFiles(root)) {
        scanned++
        const src = readFileSync(file, 'utf8')
        for (const body of unreportedCatches(src)) {
          offenders.push(`${file.replace(`${resolve('.')}/`, '')}: ${body.trim().split('\n')[0]}`)
        }
      }
    }
    expect(scanned).toBeGreaterThan(10)
    expect(offenders).toEqual([])
  })
})
