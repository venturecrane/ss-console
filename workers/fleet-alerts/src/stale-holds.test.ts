/**
 * The stale-holds query, executed (ss#2316).
 *
 * WHY THIS FILE EXISTS. `index.test.ts` covers stale_holds through a fake D1
 * whose `prepare(...).all()` returns a TypeScript REIMPLEMENTATION of the query
 * (`computeStaleHolds`, index.test.ts) for any SQL containing
 * 'LEFT JOIN fleet_status'. The SQL string is never executed, so no assertion
 * there can observe a wrong offset, a wrong column, or a malformed JSON path —
 * and the reimplementation has no clause at all for the two payload-carrying
 * connector conditions. That is the "instrument that cannot observe the layer it
 * claims to check" class the #2280 roll-up named.
 *
 * This suite runs the real `STALE_HOLDS_SQL` against real SQLite (`node:sqlite`)
 * with the real bindings, so the query is the thing under test. D1 is SQLite and
 * supports `json_each` (Cloudflare D1 SQL API, "Query JSON"), which is the
 * function the fix depends on.
 */

import { DatabaseSync } from 'node:sqlite'
import { describe, it, expect } from 'vitest'
import {
  CONNECTOR_DOWN_PREFIX,
  CONNECTOR_TOKEN_EXPIRING_PREFIX,
  SPEC_CONTROL_BROKEN_PREFIX,
  WEBHOOK_SURFACE_MISSING_PREFIX,
  CONDITION_PREFIXES,
} from './conditions'
import { STALE_HOLDS_SQL, STALE_HOLDS_BINDINGS } from './stale-holds'

interface StatusSeed {
  customer_slug: string
  connectors_json?: string | null
  connector_token_age_json?: string | null
  spec_control_json?: string | null
  webhook_surface_json?: string | null
  // ss#2488 part 2. Default null = the seat reports nothing for that field,
  // which is the stranding condition; tests set a value to prove NOT stranded.
  gateway_loop_ok?: number | null
  gateway_loop_age_seconds?: number | null
  gateway_supervisor_state?: string | null
  gateway_restarts_last_hour?: number | null
}

/**
 * Run the real query. `bindings` is injectable so a test can prove the SQL
 * follows the constants rather than a memorized offset: pass a prefix of a
 * DIFFERENT length and the slice must move with it.
 */
function runQuery(
  openConditions: Array<{ customer_slug: string; condition: string }>,
  statuses: StatusSeed[],
  bindings: readonly string[] = STALE_HOLDS_BINDINGS
): Array<{ customer_slug: string; condition: string }> {
  const db = new DatabaseSync(':memory:')
  db.exec(`CREATE TABLE fleet_alert_state (
             customer_slug TEXT, condition TEXT, status TEXT)`)
  db.exec(`CREATE TABLE fleet_status (
             customer_slug TEXT,
             last_heartbeat_ts TEXT,
             sticky_stop_level TEXT,
             scheduler_ok INTEGER,
             scheduler_max_overdue_seconds INTEGER,
             connector_check_ok INTEGER,
             spec_control_ok INTEGER,
             webhook_surface_ok INTEGER,
             connectors_json TEXT,
             connector_token_age_json TEXT,
             spec_control_json TEXT,
             webhook_surface_json TEXT,
             gateway_loop_ok INTEGER,
             gateway_loop_age_seconds INTEGER,
             gateway_supervisor_state TEXT,
             gateway_restarts_last_hour INTEGER)`)

  const insertAlert = db.prepare(
    `INSERT INTO fleet_alert_state (customer_slug, condition, status) VALUES (?, ?, 'open')`
  )
  for (const a of openConditions) insertAlert.run(a.customer_slug, a.condition)

  const insertStatus = db.prepare(
    `INSERT INTO fleet_status (customer_slug, last_heartbeat_ts, sticky_stop_level,
       scheduler_ok, scheduler_max_overdue_seconds, connector_check_ok, spec_control_ok,
       webhook_surface_ok, connectors_json, connector_token_age_json, spec_control_json,
       webhook_surface_json, gateway_loop_ok, gateway_loop_age_seconds,
       gateway_supervisor_state, gateway_restarts_last_hour)
     VALUES (?, '2026-08-11T00:00:00Z', 'OK', 1, 0, 1, 1, 1, ?, ?, ?, ?, ?, ?, ?, ?)`
  )
  for (const s of statuses) {
    insertStatus.run(
      s.customer_slug,
      s.connectors_json ?? null,
      s.connector_token_age_json ?? null,
      s.spec_control_json ?? null,
      s.webhook_surface_json ?? null,
      s.gateway_loop_ok ?? null,
      s.gateway_loop_age_seconds ?? null,
      s.gateway_supervisor_state ?? null,
      s.gateway_restarts_last_hour ?? null
    )
  }

  return db.prepare(STALE_HOLDS_SQL).all(...bindings) as Array<{
    customer_slug: string
    condition: string
  }>
}

const healthy = (server: string) => JSON.stringify({ [server]: { consecutive_failures: 0 } })

describe('stale-holds SQL (executed against real SQLite)', () => {
  it('strands a connector_down whose server key is absent from the map', () => {
    const rows = runQuery(
      [{ customer_slug: 'smd', condition: `${CONNECTOR_DOWN_PREFIX}gmail` }],
      [{ customer_slug: 'smd', connectors_json: healthy('smokeball') }]
    )
    expect(rows).toEqual([{ customer_slug: 'smd', condition: `${CONNECTOR_DOWN_PREFIX}gmail` }])
  })

  it('does NOT strand a connector_down whose server key is present and live', () => {
    const rows = runQuery(
      [{ customer_slug: 'smd', condition: `${CONNECTOR_DOWN_PREFIX}gmail` }],
      [{ customer_slug: 'smd', connectors_json: healthy('gmail') }]
    )
    expect(rows).toEqual([])
  })

  it('strands a key present with a JSON null value (preserves json_extract semantics)', () => {
    const rows = runQuery(
      [{ customer_slug: 'smd', condition: `${CONNECTOR_DOWN_PREFIX}gmail` }],
      [{ customer_slug: 'smd', connectors_json: JSON.stringify({ gmail: null }) }]
    )
    expect(rows).toHaveLength(1)
  })

  it('strands every open condition when the seat has no fleet_status row at all', () => {
    const rows = runQuery([{ customer_slug: 'ghost', condition: 'heartbeat_red' }], [])
    expect(rows).toEqual([{ customer_slug: 'ghost', condition: 'heartbeat_red' }])
  })

  it('applies the same slice to connector_token_expiring', () => {
    const rows = runQuery(
      [
        { customer_slug: 'smd', condition: `${CONNECTOR_TOKEN_EXPIRING_PREFIX}gmail` },
        { customer_slug: 'smd', condition: `${CONNECTOR_TOKEN_EXPIRING_PREFIX}smokeball` },
      ],
      [
        {
          customer_slug: 'smd',
          connector_token_age_json: JSON.stringify({ smokeball: { age_days: 3 } }),
        },
      ]
    )
    expect(rows.map((r) => r.condition)).toEqual([`${CONNECTOR_TOKEN_EXPIRING_PREFIX}gmail`])
  })

  // The two prefix-only clauses: bound LIKE, no slice. A key that merely
  // VANISHES from the map is a withdrawn declaration/expectation and resolves
  // through openSpecControlKeys / openWebhookSurfaceKeys, so only a whole-map
  // NULL strands these. Exercised here because bindings 5 and 6 are otherwise
  // never executed by any test.
  it('strands spec_control_broken only when the whole map is NULL', () => {
    const cond = `${SPEC_CONTROL_BROKEN_PREFIX}staff.voice_spec`
    expect(
      runQuery([{ customer_slug: 'smd', condition: cond }], [{ customer_slug: 'smd' }])
    ).toEqual([{ customer_slug: 'smd', condition: cond }])

    // Map present but this key absent: NOT stranded (withdrawn declaration).
    expect(
      runQuery(
        [{ customer_slug: 'smd', condition: cond }],
        [{ customer_slug: 'smd', spec_control_json: JSON.stringify({ other: {} }) }]
      )
    ).toEqual([])
  })

  it('strands webhook_surface_missing only when the whole map is NULL', () => {
    const cond = `${WEBHOOK_SURFACE_MISSING_PREFIX}operator_seat_facts`
    expect(
      runQuery([{ customer_slug: 'smd', condition: cond }], [{ customer_slug: 'smd' }])
    ).toEqual([{ customer_slug: 'smd', condition: cond }])

    expect(
      runQuery(
        [{ customer_slug: 'smd', condition: cond }],
        [{ customer_slug: 'smd', webhook_surface_json: JSON.stringify({ other: {} }) }]
      )
    ).toEqual([])
  })

  it('strands each ss#2488 gateway condition on its OWN source column, and only that one', () => {
    // Each condition knows its source column. A wedge alert is stranded by a
    // NULL age, not by a NULL supervisor state; a refusing alert is stranded by
    // a NULL state, not by a NULL age. Cross-wiring would strand the wrong
    // alerts -- the 2026-07-08 pilot-smokeball shape, where an unresolvable
    // alert sat open for 16 days.
    const cases: Array<[string, StatusSeed, StatusSeed]> = [
      // [condition, seed that STRANDS it, seed that does NOT]
      [
        'gateway_loop_wedged',
        { customer_slug: 'smd', gateway_supervisor_state: 'armed' },
        { customer_slug: 'smd', gateway_loop_age_seconds: 400 },
      ],
      [
        'gateway_loop_unprovable',
        { customer_slug: 'smd', gateway_loop_age_seconds: 5 },
        { customer_slug: 'smd', gateway_loop_ok: 0 },
      ],
      [
        'gateway_restarted',
        { customer_slug: 'smd', gateway_loop_ok: 1 },
        { customer_slug: 'smd', gateway_restarts_last_hour: 0 },
      ],
      [
        'gateway_supervisor_refusing',
        { customer_slug: 'smd', gateway_loop_age_seconds: 5 },
        { customer_slug: 'smd', gateway_supervisor_state: 'armed' },
      ],
      [
        'gateway_supervisor_inert',
        { customer_slug: 'smd', gateway_restarts_last_hour: 0 },
        { customer_slug: 'smd', gateway_supervisor_state: 'inert' },
      ],
    ]
    for (const [cond, strands, keeps] of cases) {
      expect(runQuery([{ customer_slug: 'smd', condition: cond }], [strands]), cond).toEqual([
        { customer_slug: 'smd', condition: cond },
      ])
      expect(runQuery([{ customer_slug: 'smd', condition: cond }], [keeps]), cond).toEqual([])
    }
  })

  // --- (a) the SQL follows the constant, not a memorized offset --------------

  describe('prefix rename resilience (ss#2316 defect 1, hazard 1)', () => {
    it('slices correctly when the prefix is RENAMED to a different length', () => {
      // The falsifier for the old code: it sliced at a hardcoded column 16,
      // which is `'connector_down:'.length + 1`. Bind a shorter prefix and a
      // query that memorized 16 cuts in the wrong place, producing a key that
      // matches nothing and reporting the alert stranded.
      const renamed = 'cd:'
      expect(renamed.length).not.toBe(CONNECTOR_DOWN_PREFIX.length)
      const bindings = [renamed, renamed, ...STALE_HOLDS_BINDINGS.slice(2)]

      const stranded = runQuery(
        [{ customer_slug: 'smd', condition: `${renamed}gmail` }],
        [{ customer_slug: 'smd', connectors_json: healthy('smokeball') }],
        bindings
      )
      expect(stranded.map((r) => r.condition)).toEqual([`${renamed}gmail`])

      // And the healthy case must still be silent under the renamed prefix.
      const quiet = runQuery(
        [{ customer_slug: 'smd', condition: `${renamed}gmail` }],
        [{ customer_slug: 'smd', connectors_json: healthy('gmail') }],
        bindings
      )
      expect(quiet).toEqual([])
    })

    it('writes no prefix literal and no offset into the SQL text', () => {
      // Structural guard: the query must carry neither the prefix strings nor a
      // substr() with a numeric literal. Both are the shapes that drifted.
      for (const prefix of CONDITION_PREFIXES) {
        expect(STALE_HOLDS_SQL).not.toContain(prefix)
      }
      expect(STALE_HOLDS_SQL).not.toMatch(/substr\s*\([^)]*,\s*\d+/)
    })

    it('binds one value per placeholder', () => {
      const placeholders = (STALE_HOLDS_SQL.match(/\?/g) ?? []).length
      expect(placeholders).toBe(STALE_HOLDS_BINDINGS.length)
    })
  })

  // --- (b) hostile key characters -------------------------------------------

  describe('server names that are not path-safe (ss#2316 defect 1, hazard 2)', () => {
    // The old clause built '$."' || key || '"' and handed it to json_extract.
    // A key containing a double quote made that path malformed, and SQLite
    // answers a malformed path with NULL rather than an error — NULL being this
    // query's "stranded" signal, so a HEALTHY connector reported stranded
    // forever. json_each compares the key as a value, so nothing is syntax.
    const hostile = ['we"ird', 'has.dot', 'has space', "quote'single", 'br[ack]et', '$dollar']

    for (const server of hostile) {
      it(`does not strand a healthy connector named ${JSON.stringify(server)}`, () => {
        const rows = runQuery(
          [{ customer_slug: 'smd', condition: `${CONNECTOR_DOWN_PREFIX}${server}` }],
          [{ customer_slug: 'smd', connectors_json: healthy(server) }]
        )
        expect(rows).toEqual([])
      })

      it(`still strands a genuinely absent connector named ${JSON.stringify(server)}`, () => {
        const rows = runQuery(
          [{ customer_slug: 'smd', condition: `${CONNECTOR_DOWN_PREFIX}${server}` }],
          [{ customer_slug: 'smd', connectors_json: healthy('other') }]
        )
        expect(rows).toHaveLength(1)
      })
    }
  })
})
