/**
 * Tests for the fleet-roster reader + pure derivations
 * (src/lib/admin/fleet-roster.ts) — admin Operator console §4.1.
 *
 * Two properties matter most and are exercised here:
 *
 *   1. Fleet-view resilience (ADR 0043, foundations §7): one corrupt
 *      `customer_configs` row degrades to a flagged `config_error` row — it
 *      never throws and blanks the whole fleet view.
 *   2. Posture + health derivations never read calmer than reality: posture
 *      labels follow the switch pattern (foundations §4.1) and `rosterHealth`
 *      takes the more alarming of heartbeat vs summary rollup.
 *
 * DB-touching tests seed `customer_configs` + `entities` directly via the test
 * D1 (same posture as customer-config.test.ts — CI sync lands separately).
 */

import { describe, it, expect, beforeEach } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'
import { ORG_ID } from '../src/lib/constants'
import {
  listFleetRoster,
  derivePostureLabel,
  posturePill,
  rosterHealth,
  failingConnectorNames,
  rosterHealthDotClass,
  personaSummary,
  type RosterPersona,
} from '../src/lib/admin/fleet-roster'
import { DEFAULT_AUTHORITY_POSTURE, type AuthorityPosture } from '../src/lib/operator/authority'

const migrationsDir = resolve(process.cwd(), 'migrations')

async function freshDb(): Promise<D1Database> {
  const db = createTestD1()
  await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
  return db
}

async function seedEntity(db: D1Database, id: string, name: string, slug: string): Promise<void> {
  await db
    .prepare('INSERT INTO entities (id, org_id, name, slug) VALUES (?, ?, ?, ?)')
    .bind(id, ORG_ID, name, slug)
    .run()
}

function persona(overrides: Partial<RosterPersona> = {}): RosterPersona {
  return { slug: 'marcus', name: 'Marcus', status: 'active', ...overrides }
}

interface SeedConfig {
  entity_id: string
  customer_slug: string
  personas_json?: string
  authority_json?: string | null
  vertical?: string | null
}

async function seedConfig(db: D1Database, c: SeedConfig): Promise<void> {
  await db
    .prepare(
      `INSERT INTO customer_configs
        (entity_id, org_id, customer_slug, schema_version, personas_json,
         authority_json, vertical, git_sha, synced_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`
    )
    .bind(
      c.entity_id,
      ORG_ID,
      c.customer_slug,
      '1.0.0',
      c.personas_json ?? JSON.stringify([persona()]),
      c.authority_json === undefined ? null : c.authority_json,
      c.vertical ?? null,
      'a1b2c3d4',
      '2026-06-08T12:00:00Z'
    )
    .run()
}

describe('listFleetRoster', () => {
  let db: D1Database
  beforeEach(async () => {
    db = await freshDb()
  })

  it('returns [] for an empty fleet', async () => {
    expect(await listFleetRoster(db)).toEqual([])
  })

  it('lists customers ordered by slug with entity name joined and personas parsed', async () => {
    await seedEntity(db, 'ent-b', 'Beta Firm', 'beta-firm')
    await seedEntity(db, 'ent-a', 'Alpha Firm', 'alpha-firm')
    await seedConfig(db, { entity_id: 'ent-b', customer_slug: 'beta-firm', vertical: 'law' })
    await seedConfig(db, {
      entity_id: 'ent-a',
      customer_slug: 'alpha-firm',
      personas_json: JSON.stringify([persona(), persona({ slug: 'nova', name: 'Nova' })]),
    })

    const roster = await listFleetRoster(db)
    expect(roster.map((r) => r.customer_slug)).toEqual(['alpha-firm', 'beta-firm'])

    const alpha = roster[0]
    expect(alpha.entity_name).toBe('Alpha Firm')
    expect(alpha.personas).toHaveLength(2)
    expect(alpha.personas[1]).toEqual({ slug: 'nova', name: 'Nova', status: 'active' })
    expect(alpha.config_error).toBeNull()

    const beta = roster[1]
    expect(beta.vertical).toBe('law')
    expect(beta.authority).toEqual(DEFAULT_AUTHORITY_POSTURE)
  })

  it('resolves an authored authority posture from authority_json', async () => {
    await seedEntity(db, 'ent-1', 'Gamma Firm', 'gamma-firm')
    await seedConfig(db, {
      entity_id: 'ent-1',
      customer_slug: 'gamma-firm',
      authority_json: JSON.stringify({
        default: 'managed',
        overrides: { people_access: 'client' },
      }),
    })
    const [row] = await listFleetRoster(db)
    expect(row.authority.overrides.people_access).toBe('client')
  })

  it('degrades a malformed personas_json row to a flagged config_error, never throwing', async () => {
    await seedEntity(db, 'ent-ok', 'OK Firm', 'ok-firm')
    await seedEntity(db, 'ent-bad', 'Bad Firm', 'bad-firm')
    await seedConfig(db, { entity_id: 'ent-ok', customer_slug: 'ok-firm' })
    await seedConfig(db, {
      entity_id: 'ent-bad',
      customer_slug: 'bad-firm',
      personas_json: '{not valid json',
    })

    const roster = await listFleetRoster(db)
    expect(roster).toHaveLength(2)
    const bad = roster.find((r) => r.customer_slug === 'bad-firm')!
    expect(bad.config_error).not.toBeNull()
    expect(bad.personas).toEqual([])
    expect(bad.authority).toEqual(DEFAULT_AUTHORITY_POSTURE)
    // The healthy row is unaffected.
    const ok = roster.find((r) => r.customer_slug === 'ok-firm')!
    expect(ok.config_error).toBeNull()
    expect(ok.personas).toHaveLength(1)
  })

  it('flags a personas_json that is valid JSON but not an array', async () => {
    await seedEntity(db, 'ent-x', 'X Firm', 'x-firm')
    await seedConfig(db, {
      entity_id: 'ent-x',
      customer_slug: 'x-firm',
      personas_json: JSON.stringify({ slug: 'oops' }),
    })
    const [row] = await listFleetRoster(db)
    expect(row.config_error).toMatch(/not an array/)
  })
})

describe('derivePostureLabel', () => {
  it('null / launch-default posture is Managed (every client switch off)', () => {
    expect(derivePostureLabel(null)).toBe('Managed')
    expect(derivePostureLabel(DEFAULT_AUTHORITY_POSTURE)).toBe('Managed')
  })

  it('a self_managed default with no overrides is Self-Managed', () => {
    const posture: AuthorityPosture = { default: 'self_managed', overrides: {} }
    expect(derivePostureLabel(posture)).toBe('Self-Managed')
  })

  it('a managed default with at least one client override is Co-Managed', () => {
    const posture: AuthorityPosture = { default: 'managed', overrides: { connectors: 'client' } }
    expect(derivePostureLabel(posture)).toBe('Co-Managed')
  })

  it('a self_managed default with one domain pinned back to managed is Co-Managed', () => {
    const posture: AuthorityPosture = { default: 'self_managed', overrides: { trust: 'managed' } }
    expect(derivePostureLabel(posture)).toBe('Co-Managed')
  })
})

describe('posturePill', () => {
  it('carries the label and a non-empty class string', () => {
    const pill = posturePill(DEFAULT_AUTHORITY_POSTURE)
    expect(pill.label).toBe('Managed')
    expect(pill.classes).toContain('rounded-[var(--ss-radius-badge)]')
  })
})

describe('rosterHealth', () => {
  it('takes the more alarming of heartbeat vs summary rollup', () => {
    // Heartbeat green but summary red → red.
    expect(rosterHealth('green', '20s ago', 'red').color).toBe('red')
    // Heartbeat red but summary green → red (heartbeat is worse).
    expect(rosterHealth('red', 'stale 9m', 'green').color).toBe('red')
    // Both benign → green.
    expect(rosterHealth('green', '5s ago', 'green').color).toBe('green')
  })

  it('keeps the heartbeat label and adds a note only for non-green summaries', () => {
    expect(rosterHealth('green', '20s ago', null).note).toBeNull()
    expect(rosterHealth('green', '20s ago', 'red').note).toMatch(/problem/)
    expect(rosterHealth('green', '20s ago', 'yellow').note).toMatch(/warning/)
    expect(rosterHealth('green', '20s ago', 'yellow').label).toBe('20s ago')
  })

  it('a missing summary (null) leaves the heartbeat color unchanged', () => {
    expect(rosterHealth('yellow', '3m ago', null).color).toBe('yellow')
    expect(rosterHealth('gray', 'no signal yet', null).color).toBe('gray')
  })

  it('a green summary never paints an un-heartbeating (gray) Machine green', () => {
    // The regression: no liveness signal must not read calmer than reality.
    // A stale/green summary can only leave the gray verdict standing, never
    // upgrade it — only a yellow/red summary escalates.
    expect(rosterHealth('gray', 'no signal yet', 'green').color).toBe('gray')
    expect(rosterHealth('gray', 'no signal yet', 'red').color).toBe('red')
    expect(rosterHealth('gray', 'no signal yet', 'yellow').color).toBe('yellow')
  })

  it('the cost breaker escalates: SOFT_STOP → yellow, HARD_STOP → red (ADR 0062)', () => {
    expect(rosterHealth('green', '5s ago', null, 'HARD_STOP').color).toBe('red')
    expect(rosterHealth('green', '5s ago', null, 'HARD_STOP').note).toMatch(/hard stop/)
    expect(rosterHealth('green', '5s ago', null, 'SOFT_STOP').color).toBe('yellow')
    expect(rosterHealth('green', '5s ago', null, 'SOFT_STOP').note).toMatch(/soft stop/)
    // OK / WARN / unknown / absent never change the dot.
    expect(rosterHealth('green', '5s ago', null, 'OK').color).toBe('green')
    expect(rosterHealth('green', '5s ago', null, 'WARN').color).toBe('green')
    expect(rosterHealth('green', '5s ago', null, 'unknown').color).toBe('green')
    // The breaker can never CALM a dot (a red heartbeat stays red at OK).
    expect(rosterHealth('red', 'stale 9m', null, 'OK').color).toBe('red')
    // Breaker note wins over the summary note when both fire.
    expect(rosterHealth('green', '5s ago', 'yellow', 'HARD_STOP').note).toMatch(/hard stop/)
  })

  it('the stop note names the meter that tripped, and no meter when none was reported', () => {
    // Four meters drive this ladder. On 2026-09-01 ashton-price stopped on a
    // bad credential and this note read "cost breaker hard stop" -- naming a
    // meter the roster had never measured.
    const withCause = rosterHealth('green', '5s ago', null, 'HARD_STOP', {
      ok: null,
      maxOverdueSeconds: null,
      stickyStopCondition: 'consecutive_tool_failures',
    })
    expect(withCause.note).toBe('hard stop: consecutive tool failures')

    const soft = rosterHealth('green', '5s ago', null, 'SOFT_STOP', {
      ok: null,
      maxOverdueSeconds: null,
      stickyStopCondition: 'cost_threshold',
    })
    expect(soft.note).toBe('soft stop: cost threshold')

    // A seat still running a pre-cause overlay reports no condition. The note
    // must degrade to the level and assert nothing about why.
    const noCause = rosterHealth('green', '5s ago', null, 'HARD_STOP')
    expect(noCause.note).toBe('hard stop')
    expect(noCause.note).not.toMatch(/cost|refusal|runtime|tool/)
  })

  it('the scheduler self-check escalates: ok=0 → red, overdue past threshold → yellow (WP-2)', () => {
    // scheduler_ok === 0 is a broken cron store: red, on an otherwise-live seat.
    const broken = rosterHealth('green', '5s ago', null, null, { ok: 0, maxOverdueSeconds: null })
    expect(broken.color).toBe('red')
    expect(broken.note).toMatch(/scheduler broken/)
    // Overdue past 900s → yellow.
    const overdue = rosterHealth('green', '5s ago', null, null, { ok: 1, maxOverdueSeconds: 901 })
    expect(overdue.color).toBe('yellow')
    expect(overdue.note).toMatch(/work overdue/)
    // Just under threshold stays calm. cronContainment is stated (0 = reported
    // "not contained") rather than omitted, so this asserts the overdue
    // threshold alone — an omitted field is its own yellow under ss#2295.
    expect(
      rosterHealth('green', '5s ago', null, null, {
        ok: 1,
        maxOverdueSeconds: 899,
        cronContainment: 0,
      }).color
    ).toBe('green')
  })

  it('scheduler NULLs participate in nothing and never calm a worse color', () => {
    // NULL scheduler_ok + NULL overdue leave the heartbeat verdict standing.
    expect(
      rosterHealth('yellow', '3m ago', null, null, { ok: null, maxOverdueSeconds: null }).color
    ).toBe('yellow')
    expect(rosterHealth('green', '5s ago', null, null, null).note).toBeNull()
    // A healthy scheduler (ok=1, no overdue) never downgrades a red heartbeat.
    expect(rosterHealth('red', 'stale 9m', null, null, { ok: 1, maxOverdueSeconds: 0 }).color).toBe(
      'red'
    )
    // A broken scheduler can only escalate, never calm — red stays red.
    expect(
      rosterHealth('red', 'stale 9m', null, null, { ok: 0, maxOverdueSeconds: null }).color
    ).toBe('red')
  })
})

describe('rosterHealthDotClass', () => {
  it('maps every color to a token-based background class', () => {
    expect(rosterHealthDotClass('green')).toContain('--ss-color-complete')
    expect(rosterHealthDotClass('yellow')).toContain('--ss-color-attention')
    expect(rosterHealthDotClass('red')).toContain('--ss-color-error')
    expect(rosterHealthDotClass('gray')).toContain('--ss-color-border')
  })
})

describe('personaSummary', () => {
  it('renders count with archived suffix and an honest zero state', () => {
    expect(personaSummary([])).toBe('no personas')
    expect(personaSummary([persona()])).toBe('1 persona')
    expect(personaSummary([persona(), persona({ slug: 'n', name: 'N' })])).toBe('2 personas')
    expect(personaSummary([persona(), persona({ slug: 'a', status: 'archived' })])).toBe(
      '2 personas (1 archived)'
    )
  })
})

describe('connector health signals (ADR 0080)', () => {
  const failing = JSON.stringify({
    smokeball: { consecutive_failures: 4, run_age_seconds: 400, conn_evidence: true },
    agentmail: { consecutive_failures: 0 },
  })

  it('failingConnectorNames applies the same open predicates as the alerter', () => {
    expect(failingConnectorNames(failing)).toEqual(['smokeball'])
    // Young run: held, not failing.
    expect(
      failingConnectorNames(
        JSON.stringify({
          smokeball: { consecutive_failures: 4, run_age_seconds: 60, conn_evidence: true },
        })
      )
    ).toEqual([])
    // Signature-free backstop.
    expect(
      failingConnectorNames(
        JSON.stringify({ vendor_mcp: { consecutive_failures: 12, run_age_seconds: 1200 } })
      )
    ).toEqual(['vendor_mcp'])
    // Junk participates in nothing.
    expect(failingConnectorNames('{nope')).toEqual([])
    expect(failingConnectorNames(null)).toEqual([])
    expect(failingConnectorNames(undefined)).toEqual([])
  })

  it('a failing connector escalates the dot to red with a named note', () => {
    const health = rosterHealth('green', '20s ago', null, null, {
      ok: 1,
      maxOverdueSeconds: null,
      connectorsJson: failing,
    })
    expect(health.color).toBe('red')
    expect(health.note).toBe('connector failing: smokeball')
  })

  it('a broken connector CHECK escalates to red (outages not being counted)', () => {
    const health = rosterHealth('green', '20s ago', null, null, {
      ok: 1,
      maxOverdueSeconds: null,
      connectorCheckOk: 0,
    })
    expect(health.color).toBe('red')
    expect(health.note).toBe('connector health check broken')
  })

  it('NULL connector signals participate in nothing', () => {
    const health = rosterHealth('green', '20s ago', null, null, {
      ok: 1,
      maxOverdueSeconds: null,
      connectorCheckOk: null,
      connectorsJson: null,
      // Stated, not omitted: this test is about the CONNECTOR NULLs. An
      // omitted cron_containment is separately visible under ss#2295.
      cronContainment: 0,
    })
    expect(health.color).toBe('green')
    expect(health.note).toBeNull()
  })

  it('note precedence: scheduler broken outranks connector failing', () => {
    const health = rosterHealth('green', '20s ago', null, null, {
      ok: 0,
      maxOverdueSeconds: null,
      connectorsJson: failing,
    })
    expect(health.note).toBe('cron scheduler broken')
    expect(health.color).toBe('red')
  })
})

describe('gateway loop + supervisor signals (ss#2488 part 2)', () => {
  const base = { ok: 1, maxOverdueSeconds: null, connectorCheckOk: 1, cronContainment: 0 }

  it('a wedged loop is red and outranks every other note, including the breaker', () => {
    // The Operator is not answering on any channel. That is worse than a
    // breaker stop, which at least leaves a seat that can explain itself.
    const health = rosterHealth('green', '20s ago', null, 'HARD_STOP', {
      ...base,
      gatewayLoopOk: 1,
      gatewayLoopAgeSeconds: 400,
    })
    expect(health.color).toBe('red')
    expect(health.note).toBe('gateway loop wedged (Operator not answering)')
  })

  it('ok=1 with a NULL age is NOT a wedge (arming latch / boot suppression)', () => {
    const health = rosterHealth('green', '20s ago', null, null, {
      ...base,
      gatewayLoopOk: 1,
      gatewayLoopAgeSeconds: null,
    })
    expect(health.color).toBe('green')
    expect(health.note).toBeNull()
  })

  it('a fresh beat leaves the dot green', () => {
    const health = rosterHealth('green', '20s ago', null, null, {
      ...base,
      gatewayLoopOk: 1,
      gatewayLoopAgeSeconds: 7,
    })
    expect(health.color).toBe('green')
  })

  it('ok=0 is attention-yellow, named as unreadable, never as a wedge', () => {
    const health = rosterHealth('green', '20s ago', null, null, {
      ...base,
      gatewayLoopOk: 0,
      gatewayLoopAgeSeconds: 5,
    })
    expect(health.color).toBe('yellow')
    expect(health.note).toBe('gateway loop heartbeat unreadable')
  })

  it('a refusing supervisor is red and names the human', () => {
    const health = rosterHealth('green', '20s ago', null, null, {
      ...base,
      gatewaySupervisorState: 'refusing',
    })
    expect(health.color).toBe('red')
    expect(health.note).toBe('seat supervisor stopped restarting (needs a human)')
  })

  it('inert and not-watching are attention-yellow: self-recovery is silently absent', () => {
    for (const state of ['inert', 'not-watching']) {
      const health = rosterHealth('green', '20s ago', null, null, {
        ...base,
        gatewaySupervisorState: state,
      })
      expect(health.color, state).toBe('yellow')
      expect(health.note, state).toBe('seat supervisor cannot act (a wedge would not self-recover)')
    }
  })

  it('armed / not-armed / NULL participate in nothing', () => {
    for (const state of ['armed', 'not-armed', null]) {
      const health = rosterHealth('green', '20s ago', null, null, {
        ...base,
        gatewaySupervisorState: state,
      })
      expect(health.color, String(state)).toBe('green')
      expect(health.note, String(state)).toBeNull()
    }
  })

  it('escalate-only: a wedge cannot be calmed, and NULL cannot calm a red seat', () => {
    const red = rosterHealth('red', 'stale 12m', null, null, {
      ...base,
      gatewayLoopOk: 1,
      gatewayLoopAgeSeconds: 7,
    })
    expect(red.color).toBe('red')
  })
})
