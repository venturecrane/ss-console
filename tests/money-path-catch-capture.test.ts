/**
 * A caught failure on the webhook and money path reaches Sentry, not only the
 * Worker log.
 *
 * The Stripe webhook deliberately never lets an exception escape (a thrown
 * error would 500 every retry the same way), so neither the middleware's
 * Sentry wrapper nor a route-level helper sees what its handlers catch. Until
 * 2026-09-25 those catches wrote `console.error` and nothing else: a failed
 * payment mirror or a lost client email was a log line nobody pages on
 * (review 2026-09-25, Code Quality 4; carried from 2026-09-10).
 *
 * The rule, over every non-test module under the guarded roots: a `catch`
 * clause, or a function passed to `.catch(...)`, whose body calls
 * `console.error` must also call `captureError`, `captureWarning`, or one of
 * the two helpers that capture before they respond (`failedResponse`,
 * `misconfiguredResponse`). The walk is over the TypeScript AST, so a comment
 * or a string that mentions a capture call does not satisfy it; nested
 * function bodies are their own scope and are not counted for the outer one.
 *
 * What would make this false: a new catch on these paths that logs and does
 * not capture, or a capture call spelled in a way this list does not know.
 */

import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { resolve, relative } from 'node:path'
import ts from 'typescript'

const ROOTS = ['src/lib/webhooks', 'src/lib/stripe', 'src/lib/sow', 'src/pages/api/webhooks']
const CAPTURES = new Set([
  'captureError',
  'captureWarning',
  'failedResponse',
  'misconfiguredResponse',
])

function files(): string[] {
  return ROOTS.flatMap((root) =>
    readdirSync(resolve(root), { recursive: true })
      .map((entry) => resolve(root, String(entry)))
      .filter((p) => p.endsWith('.ts') && !p.endsWith('.test.ts') && statSync(p).isFile())
  )
}

function calls(node: ts.Node): { consoleError: boolean; capture: boolean } {
  let consoleError = false
  let capture = false
  const visit = (n: ts.Node): void => {
    if (n !== node && ts.isFunctionLike(n)) return
    if (ts.isCallExpression(n)) {
      const callee = n.expression
      if (
        ts.isPropertyAccessExpression(callee) &&
        ts.isIdentifier(callee.expression) &&
        callee.expression.text === 'console' &&
        callee.name.text === 'error'
      ) {
        consoleError = true
      }
      if (ts.isIdentifier(callee) && CAPTURES.has(callee.text)) capture = true
    }
    ts.forEachChild(n, visit)
  }
  visit(node)
  return { consoleError, capture }
}

/** Every catch body in a source text: `catch (e) {}` blocks and `.catch(fn)` handlers. */
function catchBodies(sf: ts.SourceFile): ts.Node[] {
  const out: ts.Node[] = []
  const visit = (n: ts.Node): void => {
    if (ts.isCatchClause(n)) out.push(n.block)
    if (
      ts.isCallExpression(n) &&
      ts.isPropertyAccessExpression(n.expression) &&
      n.expression.name.text === 'catch' &&
      n.arguments.length > 0 &&
      (ts.isArrowFunction(n.arguments[0]) || ts.isFunctionExpression(n.arguments[0]))
    ) {
      out.push(n.arguments[0])
    }
    ts.forEachChild(n, visit)
  }
  visit(sf)
  return out
}

function offenders(name: string, text: string): string[] {
  const sf = ts.createSourceFile(name, text, ts.ScriptTarget.Latest, true)
  return catchBodies(sf)
    .filter((body) => {
      const c = calls(body)
      return c.consoleError && !c.capture
    })
    .map((body) => `${name}:${sf.getLineAndCharacterOfPosition(body.getStart()).line + 1}`)
}

describe('webhook and money-path catches reach Sentry', () => {
  it('finds the guarded modules', () => {
    expect(files().length).toBeGreaterThan(15)
  })

  it('no catch on these paths logs to console.error without a capture', () => {
    const found = files().flatMap((file) =>
      offenders(relative(resolve('.'), file), readFileSync(file, 'utf8'))
    )
    expect(
      found,
      'add captureError(err, area) (or answer with failedResponse) beside the console.error'
    ).toEqual([])
  })

  it('the walk can fail: a logging-only catch, a .catch handler, and a comment mention are all caught', () => {
    const bad = `
      async function a() { try { await x() } catch (err) { console.error('boom', err) } }
      const b = p.catch((err) => { console.error(err) })
      async function c() { try { await x() } catch (err) {
        // captureError(err, 'area') would go here
        console.error('boom', "captureError(err)")
      } }
    `
    expect(offenders('bad.ts', bad)).toHaveLength(3)
  })

  it('the walk passes a capture beside the log, a failedResponse, and a catch that does not log', () => {
    const good = `
      async function a() { try { await x() } catch (err) { console.error(err); captureError(err, 'a') } }
      async function b() { try { await x() } catch (err) { return failedResponse(err, 'b') } }
      async function c() { try { await x() } catch { return errorResponse(400, 'invalid_json') } }
    `
    expect(offenders('good.ts', good)).toEqual([])
  })

  it('a capture inside a nested function does not satisfy the outer catch', () => {
    const nested = `
      async function a() { try { await x() } catch (err) {
        console.error(err)
        const later = () => captureError(err, 'a')
      } }
    `
    expect(offenders('nested.ts', nested)).toHaveLength(1)
  })
})
