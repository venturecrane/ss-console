/**
 * A D1Database-shaped adapter over `wrangler d1 execute --remote`.
 *
 * WHY THIS EXISTS. A CI script cannot hold a Worker's D1 binding, so the
 * obvious move is to write the SQL inline in the script. That is what the first
 * draft of the obligation reconciler did, and it produced two implementations
 * of the same rules: the state machine and the upsert semantics existed once in
 * src/lib/db/obligations.ts (which the console reads) and again as string SQL in
 * the script (which the reconciler wrote). This repo has a standing lesson about
 * exactly that shape — mirrored artifacts drift, and the drift is silent until
 * the two sides disagree about a client's data.
 *
 * So the script keeps using the real module, and this adapter supplies the
 * handle. One definition of what `verified` means, one upsert, one place to fix.
 *
 * SCOPE, STATED HONESTLY. This implements the slice of the D1 interface the
 * register's queries actually use: prepare/bind/first/all/run. It does NOT
 * implement batch, exec, dump, or the withSession API. A caller reaching for
 * those gets a clear throw rather than a silent no-op, because a database
 * adapter that quietly does nothing is the worst possible failure here.
 *
 * Binding is done by substitution, not by a parameterized protocol: the wrangler
 * CLI takes one SQL string. Values are escaped through sqlLiteral, which is the
 * same treatment the existing ci-sync-customer-configs.sh path uses. Every value
 * that reaches it in this codebase originates from D1, GitHub's API, or an
 * agent-supplied argument that has already passed the CLI's validation.
 */

import { execFileSync } from 'node:child_process'

export interface WranglerD1Options {
  /** D1 database name, e.g. 'ss-console-db'. */
  database: string
  /**
   * Override the executable, for tests. Called as `<cmd> <database> <sql>` so a
   * stub needs no wrangler flag parsing.
   */
  commandOverride?: string | null
  /** Max bytes of stdout to accept from one query. */
  maxBuffer?: number
}

export function sqlLiteral(value: unknown): string {
  if (value === null || value === undefined) return 'NULL'
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new Error(`refusing to bind a non-finite number: ${value}`)
    return String(value)
  }
  if (typeof value === 'boolean') return value ? '1' : '0'
  return `'${String(value).replaceAll("'", "''")}'`
}

/**
 * Substitute `?` placeholders left to right, ignoring any inside a string
 * literal.
 *
 * A naive global replace treats the `?` in `WHERE note = 'why?'` as a
 * placeholder, which corrupts the statement AND desynchronises the value count
 * so every later binding lands in the wrong column. No query in this repo
 * embeds one today, which is exactly why it would be found the hard way: the
 * first person to write one gets silently wrong data rather than an error.
 *
 * Only single-quoted literals matter — SQLite's string delimiter, with `''` as
 * the escape, which this walk handles by simply toggling back and forth.
 */
export function bindSql(sql: string, values: readonly unknown[]): string {
  let out = ''
  let index = 0
  let inString = false

  for (let i = 0; i < sql.length; i += 1) {
    const char = sql[i]
    if (char === "'") {
      inString = !inString
      out += char
      continue
    }
    if (char === '?' && !inString) {
      if (index >= values.length) throw new Error('more ? placeholders than bound values')
      out += sqlLiteral(values[index++])
      continue
    }
    out += char
  }

  if (inString) throw new Error('unterminated string literal in SQL')
  if (index !== values.length) {
    throw new Error(`bound ${values.length} values but the statement has ${index} placeholders`)
  }
  return out
}

export function parseWranglerJson(stdout: string): Record<string, unknown>[] {
  const parsed: unknown = JSON.parse(stdout)
  if (Array.isArray(parsed)) {
    const first = parsed[0] as { results?: Record<string, unknown>[] } | undefined
    return first?.results ?? []
  }
  const single = parsed as { results?: Record<string, unknown>[] }
  return single?.results ?? []
}

class WranglerStatement {
  constructor(
    private readonly options: WranglerD1Options,
    private readonly sql: string,
    private readonly values: readonly unknown[] = []
  ) {}

  bind(...values: unknown[]): WranglerStatement {
    return new WranglerStatement(this.options, this.sql, values)
  }

  private execute(): Record<string, unknown>[] {
    const sql = bindSql(this.sql, this.values)
    const override = this.options.commandOverride
    const maxBuffer = this.options.maxBuffer ?? 32 * 1024 * 1024
    const stdout = override
      ? execFileSync(override, [this.options.database, sql], { encoding: 'utf8', maxBuffer })
      : execFileSync(
          'npx',
          [
            'wrangler',
            'd1',
            'execute',
            this.options.database,
            '--remote',
            '--json',
            '--command',
            sql,
          ],
          { encoding: 'utf8', maxBuffer }
        )
    return parseWranglerJson(stdout)
  }

  async first<T>(): Promise<T | null> {
    const rows = this.execute()
    return (rows[0] as T) ?? null
  }

  async all<T>(): Promise<{ results: T[]; success: true }> {
    return { results: this.execute() as T[], success: true }
  }

  async run(): Promise<{ success: true }> {
    this.execute()
    return { success: true }
  }

  raw(): never {
    throw new Error('wrangler-d1 adapter: raw() is not implemented')
  }
}

/**
 * A D1Database-compatible handle backed by the wrangler CLI.
 *
 * Typed as `unknown` at the boundary and cast by the caller: this is a genuine
 * structural stand-in for a runtime binding that cannot exist in a CLI process,
 * and pretending otherwise in the type system would be the lie.
 */
export function wranglerD1(options: WranglerD1Options) {
  return {
    prepare(sql: string) {
      return new WranglerStatement(options, sql)
    },
    batch(): never {
      throw new Error('wrangler-d1 adapter: batch() is not implemented')
    },
    exec(): never {
      throw new Error('wrangler-d1 adapter: exec() is not implemented')
    },
    dump(): never {
      throw new Error('wrangler-d1 adapter: dump() is not implemented')
    },
  }
}
