/**
 * The wrangler-backed D1 adapter's binding (ADR 0088).
 *
 * This adapter exists so the reconciler and the admin console share one
 * implementation of the register's rules rather than keeping two. That only
 * holds if binding is faithful: the module substitutes `?` itself, because the
 * wrangler CLI takes a single SQL string and cannot carry parameters.
 *
 * The case worth testing is the one no current query hits — a `?` inside a
 * string literal. A naive global replace eats it, which corrupts the statement
 * AND shifts every later value into the wrong column. Nothing in this repo
 * writes such a query today, which is precisely why the first person to write
 * one would get silently wrong data instead of an error.
 */

import { describe, expect, it } from 'vitest'
import { bindSql, parseWranglerJson, sqlLiteral } from '../scripts/lib/wrangler-d1'

describe('sqlLiteral', () => {
  it('escapes an apostrophe rather than breaking out of the literal', () => {
    expect(sqlLiteral("the firm's matters")).toBe("'the firm''s matters'")
  })

  it('refuses a non-finite number instead of emitting NaN into SQL', () => {
    expect(() => sqlLiteral(Number.NaN)).toThrow()
    expect(() => sqlLiteral(Number.POSITIVE_INFINITY)).toThrow()
  })

  it('writes null, not the string "null"', () => {
    expect(sqlLiteral(null)).toBe('NULL')
    expect(sqlLiteral(undefined)).toBe('NULL')
  })
})

describe('bindSql', () => {
  it('substitutes placeholders in order', () => {
    expect(bindSql('SELECT * FROM t WHERE a = ? AND b = ?', ['x', 2])).toBe(
      "SELECT * FROM t WHERE a = 'x' AND b = 2"
    )
  })

  it('does not treat a ? inside a string literal as a placeholder', () => {
    // The landmine. Without literal-awareness this returns a corrupted
    // statement and consumes the caller's first value for a character that was
    // never a placeholder.
    const sql = bindSql("SELECT * FROM t WHERE note = 'why?' AND id = ?", ['abc'])
    expect(sql).toBe("SELECT * FROM t WHERE note = 'why?' AND id = 'abc'")
  })

  it('handles an escaped quote inside a literal', () => {
    const sql = bindSql("SELECT * FROM t WHERE a = 'it''s fine' AND b = ?", [1])
    expect(sql).toBe("SELECT * FROM t WHERE a = 'it''s fine' AND b = 1")
  })

  it('refuses a placeholder/value count mismatch in both directions', () => {
    // Falsifier for the two tests above: silently tolerating a mismatch is how
    // a shifted binding reaches the database looking like valid SQL.
    expect(() => bindSql('SELECT ? , ?', ['only-one'])).toThrow()
    expect(() => bindSql('SELECT ?', ['a', 'b'])).toThrow()
  })

  it('refuses an unterminated string literal', () => {
    expect(() => bindSql("SELECT * FROM t WHERE a = 'oops", [])).toThrow()
  })
})

describe('parseWranglerJson', () => {
  it('reads the array-wrapped shape wrangler actually returns', () => {
    expect(parseWranglerJson(JSON.stringify([{ results: [{ a: 1 }], success: true }]))).toEqual([
      { a: 1 },
    ])
  })

  it('returns empty for a result set with no rows, and throws on garbage', () => {
    // Garbage must throw rather than read as zero rows: an unparseable response
    // and an empty table demand opposite responses from every caller.
    expect(parseWranglerJson(JSON.stringify([{ results: [], success: true }]))).toEqual([])
    expect(() => parseWranglerJson('not json')).toThrow()
  })
})
