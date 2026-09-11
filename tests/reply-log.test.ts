/**
 * Behavioural tests for POST /api/admin/entities/[id]/reply-log (issue #464).
 *
 * Until 2026-09-11 this file matched the endpoint's source text. The
 * endpoint now runs against a migrated D1 and the assertions are on the
 * context row it writes, the redirects it answers, and the stage it must
 * leave alone (review 2026-09-10, Testing 3).
 */

import { describe, it, expect, beforeEach } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'
import {
  POST,
  REPLY_NEXT_ACTIONS,
  REPLY_SENTIMENTS,
} from '../src/pages/api/admin/entities/[id]/reply-log'
import {
  adminSession,
  bindEnv,
  formRequest,
  locationOf,
  locationQuery,
  migratedDb,
  routeContext,
  seedEntity,
  seedOrg,
} from './_stubs/behavioural'

const ORG_A = 'org-a'
const ORG_B = 'org-b'
const ENT_A = 'ent-a'
const ENT_B = 'ent-b'

async function contextRows(db: D1Database, entityId: string) {
  const rows = await db
    .prepare('SELECT type, content, source, metadata FROM context WHERE entity_id = ?')
    .bind(entityId)
    .all<{ type: string; content: string; source: string; metadata: string | null }>()
  return rows.results
}

async function stageOf(db: D1Database, entityId: string): Promise<string> {
  const row = await db
    .prepare('SELECT stage FROM entities WHERE id = ?')
    .bind(entityId)
    .first<{ stage: string }>()
  return row?.stage ?? ''
}

describe('POST /api/admin/entities/[id]/reply-log', () => {
  let db: D1Database

  const call = (
    entityId: string | undefined,
    fields: Record<string, string>,
    session: ReturnType<typeof adminSession> | null = adminSession(ORG_A)
  ) =>
    POST(
      routeContext({
        request: formRequest(`http://test.local/api/admin/entities/${entityId}/reply-log`, fields),
        params: { id: entityId },
        session,
      }) as unknown as Parameters<typeof POST>[0]
    )

  beforeEach(async () => {
    db = await migratedDb()
    await seedOrg(db, ORG_A)
    await seedOrg(db, ORG_B)
    await seedEntity(db, { id: ENT_A, orgId: ORG_A, stage: 'prospect' })
    await seedEntity(db, { id: ENT_B, orgId: ORG_B, stage: 'prospect' })
    bindEnv({ DB: db })
  })

  it('the vocabularies are the four sentiments and four next actions from the issue', () => {
    expect([...REPLY_SENTIMENTS]).toEqual(['interested', 'declined', 'out_of_office', 'other'])
    expect([...REPLY_NEXT_ACTIONS]).toEqual([
      'book_meeting',
      'retry_later',
      'mark_lost',
      'continue_conversation',
    ])
  })

  it('answers 401 with no admin session and writes nothing', async () => {
    const res = await call(ENT_A, { sentiment: 'interested', next_action: 'book_meeting' }, null)
    expect(res.status).toBe(401)
    expect(await contextRows(db, ENT_A)).toEqual([])
  })

  it('a missing id is error=missing; an entity outside the org is not_found', async () => {
    expect(locationOf(await call(undefined, {}))).toBe('/admin/entities?error=missing')
    expect(
      locationOf(await call(ENT_B, { sentiment: 'interested', next_action: 'book_meeting' }))
    ).toBe('/admin/entities?error=not_found')
    expect(await contextRows(db, ENT_B)).toEqual([])
  })

  it('rejects a sentiment or next action outside the vocabulary, naming which', async () => {
    const badSentiment = await call(ENT_A, { sentiment: 'thrilled', next_action: 'book_meeting' })
    expect(locationQuery(badSentiment).get('error')).toBe('invalid_sentiment')
    const badAction = await call(ENT_A, { sentiment: 'interested', next_action: 'send_gift' })
    expect(locationQuery(badAction).get('error')).toBe('invalid_next_action')
    expect(await contextRows(db, ENT_A)).toEqual([])
  })

  it("writes one 'note' context row from source 'reply_log' carrying the structured signal", async () => {
    const res = await call(ENT_A, {
      sentiment: 'out_of_office',
      next_action: 'retry_later',
      notes: '  Back on the 20th, said to try then.  ',
    })
    expect(locationOf(res)).toBe(`/admin/entities/${ENT_A}?reply_logged=1`)
    const rows = await contextRows(db, ENT_A)
    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({
      type: 'note',
      source: 'reply_log',
      content: 'Back on the 20th, said to try then.',
    })
    expect(JSON.parse(rows[0].metadata ?? '{}')).toEqual({
      sentiment: 'out_of_office',
      next_action: 'retry_later',
    })
  })

  it('with no notes, the content records the signal without inventing prose', async () => {
    await call(ENT_A, { sentiment: 'declined', next_action: 'mark_lost' })
    const [row] = await contextRows(db, ENT_A)
    expect(row.content).toBe('Reply logged: declined / mark_lost')
  })

  it('never moves the entity stage: the admin picks the next move (AC #464)', async () => {
    await call(ENT_A, { sentiment: 'interested', next_action: 'book_meeting' })
    await call(ENT_A, { sentiment: 'declined', next_action: 'mark_lost' })
    expect(await stageOf(db, ENT_A)).toBe('prospect')
    const types = (await contextRows(db, ENT_A)).map((r) => r.type)
    expect(types).toEqual(['note', 'note'])
  })
})

// The dialog is an Astro component with no handler to invoke; what can drift
// is its option set relative to the endpoint's vocabulary. This guard derives
// the expected options from the exported constants rather than restating them.
describe('reply-log dialog options match the endpoint vocabulary (drift guard)', () => {
  const dialog = readFileSync(resolve('src/components/admin/LogReplyDialog.astro'), 'utf-8')

  it('posts to the reply-log route from a native <dialog>', () => {
    expect(dialog).toMatch(/<dialog[\s>]/)
    expect(dialog).toContain('/reply-log')
  })

  it('offers every sentiment and every next action, and nothing outside the vocabulary', () => {
    expect(dialog).toContain('name="sentiment"')
    expect(dialog).toContain('name="next_action"')
    for (const value of [...REPLY_SENTIMENTS, ...REPLY_NEXT_ACTIONS]) {
      expect(dialog).toContain(`value="${value}"`)
    }
    const offered = [...dialog.matchAll(/<option value="([a-z_]+)"/g)].map((m) => m[1])
    for (const value of offered) {
      expect([...REPLY_SENTIMENTS, ...REPLY_NEXT_ACTIONS]).toContain(value)
    }
  })
})
