/**
 * ss#2287 — the heartbeat parity gate.
 *
 * THE CLASS THIS EXISTS TO STOP. The seat emitted `webhook_surface_ok` and
 * `webhook_surface` on every heartbeat from the day ss#2222 shipped. ss-console
 * had no column, no parser and no alert for either, so every beat carried the
 * warn tier to the ingest and the ingest threw it away. Silently. For as long as
 * the feature had existed. Nothing could have caught it: the emitter and the
 * receiver live in different repos and no test asserted they agree. The same gap
 * was noted for the scheduler_* fields — all optional, all absent-means-NULL,
 * nothing anywhere naming the set.
 *
 * WHAT THIS GATE CAN CATCH, honestly:
 *
 *  1. A field named in `operator/observability/heartbeat-fields.json` that the
 *     ingest does not actually store. Proven by BEHAVIOUR, not a source scan:
 *     each field is POSTed alone at the real handler and its column must come
 *     back non-NULL. Delete the parser, drop the bind, rename the column, forget
 *     the migration — all fail here. The negative control below proves the check
 *     can fail, so a green run means something (Law 12).
 *
 *  2. A field the emitter writes that the manifest does not name. The emitter is
 *     operator/hermes-smd-overlay/shared/heartbeat.py in this tree (one
 *     repository since the 2026-10-22 fold, ADR 0095), so the emitted key set is
 *     extracted from build_payload on every run, in CI too, and the manifest
 *     must be a superset of it. Before the fold this half ran only on a laptop
 *     with the overlay checked out, and CI relied on a pin re-stamp forcing a
 *     human read; both the pin and the skip are gone.
 *
 *  3. Two closed vocabularies the overlay forwards (supervisor states, sticky
 *     stop conditions) that the ingest must recognise, read from the same tree.
 *
 * WHAT IT CANNOT CATCH, equally honestly:
 *
 *  - It says nothing about whether a stored field is ALERTED on. Storage is a
 *    column; reaching a person is the fleet-alerts Worker's own suite.
 *  - It proves nothing about a running seat. Law 9: this is repo-layer until a
 *    real heartbeat lands on a deployed console.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
  installWorkerdPolyfills,
} from '@venturecrane/crane-test-harness'
import { seedMachineCredential } from './helpers/machine-credential'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { POST } from '../src/pages/api/internal/heartbeat'
import { env as testEnv } from 'cloudflare:workers'

installWorkerdPolyfills()

const repoRoot = path.resolve(__dirname, '..')
const migrationsDir = path.join(repoRoot, 'migrations')
const manifestPath = path.join(repoRoot, 'operator/observability/heartbeat-fields.json')
/** The seat runtime, in this tree since the fold (ADR 0095). */
const overlayRoot = path.join(repoRoot, 'operator/hermes-smd-overlay')
const overlaySource = (rel: string): string => readFileSync(path.join(overlayRoot, rel), 'utf8')

interface ManifestField {
  name: string
  column: string
  sample: unknown
}
interface Manifest {
  fields: ManifestField[]
}

const manifest: Manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))

const MACHINE_KEY = 'test-machine-heartbeat-key-32-chars'
const ORG_ID = 'org-parity'
const ENTITY = 'ent-parity'
const SLUG = 'parity-co'

async function seed(db: D1Database): Promise<void> {
  await db
    .prepare(
      `INSERT INTO organizations (id, name, slug, created_at, updated_at)
       VALUES (?, 'Parity Org', 'parity-org', datetime('now'), datetime('now'))`
    )
    .bind(ORG_ID)
    .run()
  await db
    .prepare(
      `INSERT INTO entities (id, org_id, name, slug, stage, stage_changed_at, created_at, updated_at)
       VALUES (?, ?, ?, ?, 'ongoing', datetime('now'), datetime('now'), datetime('now'))`
    )
    .bind(ENTITY, ORG_ID, SLUG, SLUG)
    .run()
  await db
    .prepare(
      `INSERT INTO customer_configs
         (entity_id, org_id, customer_slug, schema_version, personas_json, git_sha, synced_at)
       VALUES (?, ?, ?, '1.0.0', '[]', 'sha', '2026-08-11T00:00:00Z')`
    )
    .bind(ENTITY, ORG_ID, SLUG)
    .run()
}

function heartbeatRequest(body: Record<string, unknown>): Parameters<typeof POST>[0] {
  const request = new Request('http://test.local/api/internal/heartbeat', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${MACHINE_KEY}`,
      'X-Tenant-Slug': SLUG,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  })
  return { request, params: {}, locals: {} } as unknown as Parameters<typeof POST>[0]
}

/**
 * POST each field on its own and report the ones whose column did not land.
 *
 * Per-field rather than one fat beat on purpose: a single body would let a
 * field with no reader hide behind its neighbours' non-NULL columns. An unknown
 * column name throws out of `.first()`, which is also a failure — a manifest
 * entry naming a column no migration created is exactly the ss#2287 shape.
 */
async function unreadFields(db: D1Database, fields: ManifestField[]): Promise<string[]> {
  const unread: string[] = []
  for (const field of fields) {
    const body: Record<string, unknown> =
      field.name === 'heartbeat_ts'
        ? { heartbeat_ts: field.sample }
        : { heartbeat_ts: '2026-08-11T00:00:00.000Z', [field.name]: field.sample }
    await POST(heartbeatRequest(body))
    let stored: unknown
    try {
      const row = await db
        .prepare(`SELECT ${field.column} AS value FROM fleet_status WHERE customer_slug = ?`)
        .bind(SLUG)
        .first<{ value: unknown }>()
      stored = row?.value ?? null
    } catch (err) {
      unread.push(`${field.name} -> ${field.column} (query failed: ${String(err)})`)
      continue
    }
    if (stored === null || stored === undefined) {
      unread.push(`${field.name} -> ${field.column} (stored NULL)`)
    }
  }
  return unread
}

describe('heartbeat field parity — every emitted field has a reader (ss#2287)', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seed(db)
    for (const k of Object.keys(testEnv)) delete (testEnv as unknown as Record<string, unknown>)[k]
    Object.assign(testEnv, { DB: db })
    await seedMachineCredential(db, SLUG, MACHINE_KEY)
  })

  it('the manifest is non-trivial and internally well-formed', () => {
    // A manifest that emptied itself would make every other assertion here pass
    // while measuring nothing.
    expect(manifest.fields.length).toBeGreaterThanOrEqual(16)
    const names = manifest.fields.map((f) => f.name)
    expect(new Set(names).size).toBe(names.length)
    expect(names).toContain('webhook_surface_ok')
    expect(names).toContain('webhook_surface')
  })

  it('every manifest field lands in its fleet_status column', async () => {
    expect(await unreadFields(db, manifest.fields)).toEqual([])
  })

  it('NEGATIVE CONTROL: a field with no reader is reported (the check can fail)', async () => {
    // Without this, a silently-broken probe would look identical to a healthy
    // ingest. Two shapes of the real defect: an unread field name whose column
    // exists, and a manifest entry naming a column no migration created.
    const unread = await unreadFields(db, [
      { name: 'field_the_ingest_never_heard_of', column: 'version', sample: 'x' },
      { name: 'webhook_surface_ok', column: 'column_no_migration_created', sample: 1 },
    ])
    expect(unread).toHaveLength(2)
    expect(unread[0]).toContain('stored NULL')
    expect(unread[1]).toContain('query failed')
  })

  it('the ingest stores every supervisor state word, and NULLs one it does not know', async () => {
    // ss#2488 follow-up. gateway_supervisor_state is the one manifest field
    // whose VALUE vocabulary is load-bearing, and the per-field probe above
    // cannot see it: that probe drives from the manifest `sample` ("armed"),
    // which stays valid across every widening, so it reports a healthy column
    // while a brand-new word is being dropped to NULL beside it. That is
    // exactly what happened at overlay#339 — `starting` and `never-healthy`
    // reached an ingest that still carried the original five words.
    //
    // NULL is not a failure signal here, it is a HOLD, so a dropped word is
    // silence rather than a page. Assert the words, not the count.
    const words = [
      'armed',
      'not-armed',
      'starting',
      'inert',
      'not-watching',
      'never-healthy',
      'refusing',
    ]
    const dropped: string[] = []
    for (const word of words) {
      await POST(
        heartbeatRequest({
          heartbeat_ts: '2026-08-11T00:00:00.000Z',
          gateway_supervisor_state: word,
        })
      )
      const row = await db
        .prepare(
          `SELECT gateway_supervisor_state AS value FROM fleet_status WHERE customer_slug = ?`
        )
        .bind(SLUG)
        .first<{ value: unknown }>()
      if (row?.value !== word) dropped.push(`${word} -> ${String(row?.value ?? null)}`)
    }
    expect(
      dropped,
      'the seat writes these words and the alerter grades them; a word this parser drops becomes a NULL the console HOLDS on, which is indistinguishable from a healthy seat'
    ).toEqual([])

    // NEGATIVE CONTROL, in the same test so it cannot rot separately: the
    // vocabulary must still be CLOSED. A parser that widened to "any string"
    // would pass every assertion above while letting an unknown writer set a
    // state the alerter acts on.
    await POST(
      heartbeatRequest({
        heartbeat_ts: '2026-08-11T00:00:00.000Z',
        gateway_supervisor_state: 'a-word-no-supervisor-writes',
      })
    )
    const junk = await db
      .prepare(`SELECT gateway_supervisor_state AS value FROM fleet_status WHERE customer_slug = ?`)
      .bind(SLUG)
      .first<{ value: unknown }>()
    expect(junk?.value ?? null, 'the vocabulary must stay closed').toBeNull()
  })

  it('the ingest stores every stop condition the ladder can write, and NULLs one it cannot', async () => {
    // Same failure shape as gateway_supervisor_state above, one field over.
    // These are the overlay's StickyStopCondition members; the companion test
    // in the overlay-checkout block asserts this list still equals the pinned
    // enum, because a hand-copied vocabulary is exactly the thing that goes
    // stale silently (overlay#339).
    const words = [
      'consecutive_tool_failures',
      'refusal_cascade',
      'time_budget_exceeded',
      'cost_threshold',
      'captain_clear',
    ]
    const dropped: string[] = []
    for (const word of words) {
      await POST(
        heartbeatRequest({
          heartbeat_ts: '2026-08-11T00:00:00.000Z',
          sticky_stop_condition: word,
        })
      )
      const row = await db
        .prepare(`SELECT sticky_stop_condition AS value FROM fleet_status WHERE customer_slug = ?`)
        .bind(SLUG)
        .first<{ value: unknown }>()
      if (row?.value !== word) dropped.push(`${word} -> ${String(row?.value ?? null)}`)
    }
    expect(
      dropped,
      'the seat records these on the stop transition and the page renders them; a dropped word costs the reader the cause and sends them to the seat to find it'
    ).toEqual([])

    // NEGATIVE CONTROL, same test so it cannot rot separately: still CLOSED.
    await POST(
      heartbeatRequest({
        heartbeat_ts: '2026-08-11T00:00:00.000Z',
        sticky_stop_condition: 'a-meter-the-ladder-does-not-have',
      })
    )
    const junkCondition = await db
      .prepare(`SELECT sticky_stop_condition AS value FROM fleet_status WHERE customer_slug = ?`)
      .bind(SLUG)
      .first<{ value: unknown }>()
    expect(junkCondition?.value ?? null, 'the vocabulary must stay closed').toBeNull()
  })

  it('the self-test verdict is stored whole, and one malformed id NULLs the whole object', async () => {
    // Migration 0137. `fleet release` and the selftest_failing pager both act
    // on this object, so it is trusted whole or not at all: a verdict with one
    // id of the wrong shape must not be stored as a partial verdict.
    const sample = manifest.fields.find((f) => f.name === 'selftest')?.sample as Record<
      string,
      unknown
    >
    expect(sample, 'the manifest names the selftest field').toBeTruthy()
    const read = async (): Promise<string | null> => {
      const row = await db
        .prepare(`SELECT selftest_json AS value FROM fleet_status WHERE customer_slug = ?`)
        .bind(SLUG)
        .first<{ value: string | null }>()
      return row?.value ?? null
    }
    await POST(heartbeatRequest({ heartbeat_ts: '2026-08-11T00:00:00.000Z', selftest: sample }))
    const stored = await read()
    expect(stored).not.toBeNull()
    expect(JSON.parse(stored as string).failed).toEqual(['retired-skills-absent'])

    // NEGATIVE CONTROL: an 81-character id is one past the bound.
    await POST(
      heartbeatRequest({
        heartbeat_ts: '2026-08-11T00:00:00.000Z',
        selftest: { ...sample, failed: ['a'.repeat(81)] },
      })
    )
    expect(await read(), 'an 81-char id must NULL the object').toBeNull()
    // And an 80-character id is inside it, so the bound is the one stated.
    await POST(
      heartbeatRequest({
        heartbeat_ts: '2026-08-11T00:00:00.000Z',
        selftest: { ...sample, failed: ['a'.repeat(80)] },
      })
    )
    expect(await read()).not.toBeNull()
    // Plain overwrite including NULL: a beat without the field clears it.
    await POST(heartbeatRequest({ heartbeat_ts: '2026-08-11T00:00:00.000Z' }))
    expect(await read()).toBeNull()
  })

  it('the seat-spec verdict is stored whole; an unknown verdict or one long id NULLs it', async () => {
    // Migration 0139. The spec_unverified pager acts on this object, so a
    // verdict word outside the vocabulary, or one requirement id past its
    // bound, must not be stored as a partial verdict.
    const sample = manifest.fields.find((f) => f.name === 'spec')?.sample as Record<string, unknown>
    expect(sample, 'the manifest names the spec field').toBeTruthy()
    const read = async (): Promise<string | null> => {
      const row = await db
        .prepare(`SELECT spec_json AS value FROM fleet_status WHERE customer_slug = ?`)
        .bind(SLUG)
        .first<{ value: string | null }>()
      return row?.value ?? null
    }
    const beat = (spec: unknown) =>
      POST(heartbeatRequest({ heartbeat_ts: '2026-08-11T00:00:00.000Z', spec }))
    await beat(sample)
    const stored = await read()
    expect(stored).not.toBeNull()
    expect(JSON.parse(stored as string)).toMatchObject({
      verdict: 'unmet_requirement',
      unmet: ['lane:demand'],
      key_id: '15850dd6829dc7ee',
    })

    // NEGATIVE CONTROLS: a verdict outside the vocabulary, a 201-char id.
    await beat({ ...sample, verdict: 'trust-me' })
    expect(await read(), 'an unknown verdict must NULL the object').toBeNull()
    await beat({ ...sample, unmet: ['a'.repeat(201)] })
    expect(await read(), 'a 201-char id must NULL the object').toBeNull()
    // And a 200-character id is inside the bound.
    await beat({ ...sample, unmet: ['a'.repeat(200)] })
    expect(await read()).not.toBeNull()
    // Plain overwrite including NULL.
    await POST(heartbeatRequest({ heartbeat_ts: '2026-08-11T00:00:00.000Z' }))
    expect(await read()).toBeNull()
  })

  it('the ingest bounds the stop reason instead of trusting the seat', async () => {
    // The seat caps at 300, but "the writer promised" is not a bound.
    await POST(
      heartbeatRequest({
        heartbeat_ts: '2026-08-11T00:00:00.000Z',
        sticky_stop_reason: 'x'.repeat(5000),
      })
    )
    const row = await db
      .prepare(`SELECT sticky_stop_reason AS value FROM fleet_status WHERE customer_slug = ?`)
      .bind(SLUG)
      .first<{ value: string | null }>()
    expect(row?.value).toHaveLength(300)
  })
})

/**
 * The emitter half, read from the tree. Until the fold this ran only where the
 * overlay repo was checked out (never in CI); the runtime is in this repository
 * now, so the same commit that changes the emitter runs this check.
 */
describe('heartbeat field parity vs the overlay source', () => {
  it('the manifest covers every key build_payload emits', () => {
    const source = overlaySource('shared/heartbeat.py')
    const emitted = new Set(
      [...source.matchAll(/^\s*payload\["([a-z_]+)"\]\s*=/gm)].map((m) => m[1])
    )
    emitted.add('heartbeat_ts') // seeded at dict construction, not by assignment
    const manifestNames = new Set(manifest.fields.map((f) => f.name))
    // Superset, not equality: the console reading AHEAD of the pin is harmless
    // (cron_containment today). Reading BEHIND it is the ss#2287 defect.
    const missing = [...emitted].filter((name) => !manifestNames.has(name)).sort()
    expect(missing).toEqual([])
  })

  it('the ingest carries every supervisor state word the pinned overlay forwards', () => {
    // The other half of the four-link chain, and the link that actually broke.
    // The overlay's SUPERVISOR_STATES is a closed set that drops anything it
    // does not recognise; so is this repo's GATEWAY_SUPERVISOR_STATES. Two
    // closed sets in different repos, each failing silently to NULL, with
    // nothing asserting they agree — which is how overlay#339's two new words
    // reached a five-word ingest. One tree now: the source read here is the
    // source the image is built from.
    const block = /SUPERVISOR_STATES\s*=\s*frozenset\(\s*\{([^}]*)\}/.exec(
      overlaySource('shared/gateway_loop_check.py')
    )
    expect(
      block,
      'no SUPERVISOR_STATES frozenset in the pinned gateway_loop_check.py'
    ).not.toBeNull()
    const overlayWords = [...block![1].matchAll(/"([a-z-]+)"/g)].map((m) => m[1]).sort()
    // A parse that silently found nothing would make this pass while measuring
    // nothing — the exact failure this whole file is written against.
    expect(overlayWords.length).toBeGreaterThanOrEqual(5)

    const ingestSource = readFileSync(
      path.join(repoRoot, 'src/lib/operator/heartbeat-parsers.ts'),
      'utf8'
    )
    const ingestBlock = /const GATEWAY_SUPERVISOR_STATES = new Set\(\[([\s\S]*?)\]\)/.exec(
      ingestSource
    )
    expect(ingestBlock, 'no GATEWAY_SUPERVISOR_STATES in the heartbeat ingest').not.toBeNull()
    const ingestWords = [...ingestBlock![1].matchAll(/'([a-z-]+)'/g)].map((m) => m[1]).sort()

    const droppedInTransit = overlayWords.filter((w) => !ingestWords.includes(w))
    expect(
      droppedInTransit,
      'the pinned overlay forwards these and the ingest stores them as NULL, which the console holds on instead of paging'
    ).toEqual([])
  })

  it('the stop-condition vocabulary agrees with the pinned StickyStopCondition', () => {
    // Third closed set in two repos (after the audit vocabulary and the
    // supervisor states), same guard for the same reason. A meter added to the
    // ladder that this ingest has not heard of stores NULL, and the page loses
    // the cause silently -- which is the whole defect this field exists to fix.
    const block = /class StickyStopCondition\(str, enum\.Enum\):([\s\S]*?)\n\n\n/.exec(
      overlaySource('shared/sticky_stop.py')
    )
    expect(block, 'no StickyStopCondition enum in the pinned sticky_stop.py').not.toBeNull()
    const overlayWords = [...block![1].matchAll(/^\s+[A-Z_]+ = "(\w+)"$/gm)].map((m) => m[1]).sort()
    // A parse that silently found nothing would pass while measuring nothing.
    expect(overlayWords.length).toBeGreaterThanOrEqual(4)

    const ingestSource = readFileSync(
      path.join(repoRoot, 'src/lib/operator/heartbeat-parsers.ts'),
      'utf8'
    )
    const ingestBlock = /const STICKY_STOP_CONDITIONS = new Set\(\[([\s\S]*?)\]\)/.exec(
      ingestSource
    )
    expect(ingestBlock, 'no STICKY_STOP_CONDITIONS in the heartbeat ingest').not.toBeNull()
    const ingestWords = [...ingestBlock![1].matchAll(/'(\w+)'/g)].map((m) => m[1]).sort()

    expect(
      overlayWords.filter((w) => !ingestWords.includes(w)),
      'the pinned ladder can write these conditions and the ingest stores them as NULL, costing the page its cause'
    ).toEqual([])
  })
})
