/**
 * Authored-versus-running drift (2026-10-22, migration 0134).
 *
 * Falsifiers, stated before the assertions. Each "fires" case is the thing that
 * would make the check page; each "silent" case is a page that would teach
 * people to ignore the pager.
 *
 *   image fires     a seat whose rollout of the blessed release is done reports
 *                   an older release: it lost the release.
 *   image silent    a seat the blessed release never reached; a seat on a newer
 *                   release (rollout in progress); a seat on the blessed image.
 *   unknown fires   an image no fleet_releases row carries.
 *   config fires    reported digest differs from sha256 of the R2 object, past
 *                   the grace window; or there is no R2 object at all.
 *   config holds    a fresh write inside the grace window, a NULL report, an
 *                   unread R2 object: none may open OR resolve.
 *   skills fires    a pointer the seat does not report past grace; a reported
 *                   version with no pointer.
 *   skills silent   no pointer and no reported version.
 *   stale holds     a seat whose heartbeat is red holds every drift signal.
 *   reads fail soft a broken D1 read holds the image signals, never resolves.
 *   end to end      readDriftInputs hashes the real R2 body the way the seat
 *                   does (sha256 of the bytes), proven against a digest of the
 *                   same bytes computed independently in the test.
 */

import { describe, it, expect } from 'vitest'
import {
  blessedRelease,
  digestInRef,
  driftConditions,
  evaluateDrift,
  imageMatches,
  pointerVersion,
  type Authored,
  type DriftInputs,
  type ImageRelease,
} from './drift'
import type { FleetStatusRow } from './fleet-status'
import { conditionLabel, type Env } from './index'

const NOW = Date.parse('2026-10-22T18:00:00.000Z')
const REPO = 'registry.fly.io/hermes-fleet'
const OPTS = { nowMs: NOW, redSeconds: 300, graceSeconds: 900 }

function release(id: string, tag: string, created: string, completed: string | null): ImageRelease {
  return {
    release_id: id,
    image_ref: `${REPO}:${tag}`,
    image_digest: `sha256:${id.length.toString(16).padStart(64, '0')}`,
    created_at: created,
    completed_last_ring_at: completed,
  }
}

const OLD = release('img-old', 'rel-old', '2026-10-21T01:00:00Z', '2026-10-22T00:05:20Z')
const BLESSED = release(
  'img-blessed',
  'rel-blessed',
  '2026-10-22T00:01:34Z',
  '2026-10-22T01:33:34Z'
)
const NEWER = release('img-newer', 'rel-newer', '2026-10-22T17:00:00Z', null)
const RELEASES = [OLD, BLESSED, NEWER]

function row(overrides: Partial<FleetStatusRow> = {}): FleetStatusRow {
  return {
    customer_slug: 'scott',
    last_heartbeat_ts: '2026-10-22T17:59:30Z',
    sticky_stop_level: 'OK',
    sticky_stop_reason: null,
    sticky_stop_condition: null,
    scheduler_ok: 1,
    scheduler_max_overdue_seconds: 0,
    connectors_json: null,
    connector_check_ok: 1,
    connector_token_age_json: null,
    spec_control_json: null,
    spec_control_ok: 1,
    webhook_surface_json: null,
    webhook_surface_ok: 1,
    gateway_loop_ok: 1,
    gateway_loop_age_seconds: 5,
    gateway_supervisor_state: 'ok',
    gateway_restarts_last_hour: 0,
    image_ref: `${REPO}:rel-blessed`,
    config_digest: 'a'.repeat(64),
    skills_version: null,
    ...overrides,
  }
}

function inputs(overrides: Partial<DriftInputs> = {}): DriftInputs {
  return {
    releases: RELEASES,
    blessedDoneSeats: new Set(['scott']),
    configs: new Map<string, Authored>([
      ['scott', { value: 'a'.repeat(64), uploadedMs: NOW - 86_400_000 }],
    ]),
    skills: new Map<string, Authored>([['scott', null]]),
    ...overrides,
  }
}

function pick(states: ReturnType<typeof evaluateDrift>, condition: string) {
  return states.filter((s) => s.condition === condition)
}

describe('image matching (port of operator/bin/lib/fleet/ids.py)', () => {
  it('matches repo:tag, and lets a digest on both sides decide', () => {
    expect(imageMatches(`${REPO}:rel-blessed`, BLESSED.image_ref, BLESSED.image_digest)).toBe(true)
    expect(imageMatches(`${REPO}:rel-old`, BLESSED.image_ref, BLESSED.image_digest)).toBe(false)
    const pinned = `${REPO}:rel-blessed@${BLESSED.image_digest}`
    expect(imageMatches(pinned, BLESSED.image_ref, BLESSED.image_digest)).toBe(true)
    const wrongDigest = `${REPO}:rel-blessed@sha256:${'f'.repeat(64)}`
    expect(imageMatches(wrongDigest, BLESSED.image_ref, BLESSED.image_digest)).toBe(false)
    expect(digestInRef('no-digest:here')).toBeNull()
  })

  it('blesses the newest completed release, never an incomplete one', () => {
    expect(blessedRelease(RELEASES)?.release_id).toBe('img-blessed')
    expect(blessedRelease([NEWER])).toBeNull()
  })
})

describe('image drift', () => {
  it('fires for a seat that had the blessed release and now runs an older one', () => {
    const out = evaluateDrift([row({ image_ref: `${REPO}:rel-old` })], inputs(), OPTS)
    const [drift] = pick(out, 'image_drift')
    expect(drift.active).toBe(true)
    expect(drift.detail).toContain('img-old')
    expect(drift.detail).toContain('fleet release img-blessed --only scott')
    expect(pick(out, 'image_unknown')[0].active).toBe(false)
  })

  it('is silent for a seat the blessed release has not reached yet', () => {
    const out = evaluateDrift(
      [row({ image_ref: `${REPO}:rel-old` })],
      inputs({ blessedDoneSeats: new Set() }),
      OPTS
    )
    expect(pick(out, 'image_drift')[0].active).toBe(false)
  })

  it('is silent for a seat ahead of the blessed release (a rollout in progress)', () => {
    const out = evaluateDrift([row({ image_ref: `${REPO}:rel-newer` })], inputs(), OPTS)
    expect(pick(out, 'image_drift')[0].active).toBe(false)
  })

  it('is silent for a seat on the blessed image', () => {
    const out = evaluateDrift([row()], inputs(), OPTS)
    expect(out.filter((s) => s.active)).toEqual([])
  })

  it('fires image_unknown for an image no release carries, and holds image_drift', () => {
    const out = evaluateDrift(
      [row({ image_ref: 'registry.fly.io/hermes-scott:deployment-01ABC' })],
      inputs(),
      OPTS
    )
    expect(pick(out, 'image_unknown')[0].active).toBe(true)
    expect(pick(out, 'image_drift')).toEqual([])
  })

  it('holds both image signals on a NULL report or an unread release table', () => {
    expect(pick(evaluateDrift([row({ image_ref: null })], inputs(), OPTS), 'image_drift')).toEqual(
      []
    )
    const unread = evaluateDrift(
      [row({ image_ref: `${REPO}:rel-old` })],
      inputs({ releases: null }),
      OPTS
    )
    expect(unread.filter((s) => s.condition.startsWith('image_'))).toEqual([])
  })
})

describe('config drift', () => {
  it('fires when the reported digest is not sha256 of the published object, past grace', () => {
    const out = evaluateDrift([row({ config_digest: 'b'.repeat(64) })], inputs(), OPTS)
    const [drift] = pick(out, 'config_drift')
    expect(drift.active).toBe(true)
    expect(drift.detail).toContain('vaults/scott/customer.yaml')
    expect(drift.detail).toContain('bbbbbbbbbbbb')
  })

  it('holds inside the grace window after a publish', () => {
    const configs = new Map<string, Authored>([
      ['scott', { value: 'c'.repeat(64), uploadedMs: NOW - 60_000 }],
    ])
    expect(pick(evaluateDrift([row()], inputs({ configs }), OPTS), 'config_drift')).toEqual([])
  })

  it('resolves the moment the digests agree, grace or not', () => {
    const configs = new Map<string, Authored>([
      ['scott', { value: 'a'.repeat(64), uploadedMs: NOW - 1000 }],
    ])
    expect(pick(evaluateDrift([row()], inputs({ configs }), OPTS), 'config_drift')[0].active).toBe(
      false
    )
  })

  it('fires when R2 has no customer.yaml for a seat that runs one', () => {
    const configs = new Map<string, Authored>([['scott', null]])
    const [drift] = pick(evaluateDrift([row()], inputs({ configs }), OPTS), 'config_drift')
    expect(drift.active).toBe(true)
    expect(drift.detail).toContain('nobody publishes')
  })

  it('holds on a NULL report and on an unread object', () => {
    expect(
      pick(evaluateDrift([row({ config_digest: null })], inputs(), OPTS), 'config_drift')
    ).toEqual([])
    const configs = new Map<string, Authored>([['scott', undefined]])
    expect(pick(evaluateDrift([row()], inputs({ configs }), OPTS), 'config_drift')).toEqual([])
  })
})

describe('skills drift', () => {
  const pointer = (version: string | null, ageMs = 86_400_000) =>
    new Map<string, Authored>([['scott', { value: version, uploadedMs: NOW - ageMs }]])

  it('is silent with no pointer and no reported version', () => {
    expect(pick(evaluateDrift([row()], inputs(), OPTS), 'skills_drift')[0].active).toBe(false)
  })

  it('fires when the seat does not report the version its pointer names', () => {
    const out = evaluateDrift(
      [row({ skills_version: '202610191838-761efe20' })],
      inputs({ skills: pointer('202610200149-1c317589') }),
      OPTS
    )
    expect(pick(out, 'skills_drift')[0].active).toBe(true)
  })

  it('fires when a pointer exists and the seat reports no skills at all', () => {
    const out = evaluateDrift([row()], inputs({ skills: pointer('202610200149-1c317589') }), OPTS)
    expect(pick(out, 'skills_drift')[0].detail).toContain('no skills version')
  })

  it('fires when the seat reports skills with no pointer authored', () => {
    const out = evaluateDrift([row({ skills_version: '202610191838-761efe20' })], inputs(), OPTS)
    expect(pick(out, 'skills_drift')[0].active).toBe(true)
  })

  it('is silent when they agree, and holds inside the grace window', () => {
    const agree = evaluateDrift(
      [row({ skills_version: '202610191838-761efe20' })],
      inputs({ skills: pointer('202610191838-761efe20') }),
      OPTS
    )
    expect(pick(agree, 'skills_drift')[0].active).toBe(false)
    const fresh = evaluateDrift(
      [row()],
      inputs({ skills: pointer('202610200149-1c317589', 60_000) }),
      OPTS
    )
    expect(pick(fresh, 'skills_drift')).toEqual([])
  })

  it('reads a pointer version parsed, not cast', () => {
    expect(pointerVersion('{"version": "202610191838-761efe20", "manifest_key": "k"}')).toBe(
      '202610191838-761efe20'
    )
    expect(pointerVersion('{"version": 7}')).toBeNull()
    expect(pointerVersion('not json')).toBeNull()
  })
})

describe('holds', () => {
  it('a seat whose heartbeat is red holds every drift signal', () => {
    const stale = row({
      last_heartbeat_ts: '2026-10-22T17:00:00Z',
      image_ref: `${REPO}:rel-old`,
      config_digest: 'b'.repeat(64),
    })
    expect(evaluateDrift([stale], inputs(), OPTS)).toEqual([])
    expect(evaluateDrift([row({ last_heartbeat_ts: null })], inputs(), OPTS)).toEqual([])
  })

  it('every drift condition has a label of its own', () => {
    for (const c of ['image_drift', 'image_unknown', 'config_drift', 'skills_drift'] as const) {
      expect(conditionLabel(c)).not.toBe(c)
    }
  })
})

// ------------------------------------------------------------ read path

interface FakeObject {
  body: string
  uploaded: Date
}

function fakeEnv(opts: {
  releases?: ImageRelease[]
  rollouts?: Array<{ customer_slug: string; state: string }>
  objects?: Record<string, FakeObject>
  d1Throws?: boolean
}): Env {
  const db = {
    prepare(sql: string) {
      const run = (args: unknown[]) => {
        if (opts.d1Throws) throw new Error('d1 down')
        if (sql.includes('FROM fleet_releases')) return { results: opts.releases ?? [] }
        if (sql.includes('FROM fleet_rollouts')) {
          expect(args[0]).toBe('img-blessed')
          return { results: opts.rollouts ?? [] }
        }
        throw new Error(`unexpected SQL: ${sql}`)
      }
      return {
        all: async () => run([]),
        bind: (...args: unknown[]) => ({ all: async () => run(args) }),
      }
    },
  }
  const bucket = {
    async get(key: string) {
      const o = opts.objects?.[key]
      if (!o) return null
      return {
        uploaded: o.uploaded,
        arrayBuffer: async () => new TextEncoder().encode(o.body).buffer,
      }
    },
  }
  return {
    DB: db as unknown as D1Database,
    CUSTOMER_CONFIG: bucket as unknown as R2Bucket,
  }
}

describe('driftConditions (reads + evaluation)', () => {
  const yaml = 'customer:\n  slug: scott\n'
  // `printf 'customer:\n  slug: scott\n' | shasum -a 256`, what the seat's
  // read_config_digest computes over these bytes. A literal, so the test does
  // not share the hashing code it checks.
  const seatDigest = '62e8cabc966ae7dc260ec01fc33d136d43c5f767f392391ddbcce9a84dd92b27'

  it('hashes the R2 body exactly as the seat does, so a faithful seat is clean', async () => {
    const env = fakeEnv({
      releases: RELEASES,
      rollouts: [{ customer_slug: 'scott', state: 'done' }],
      objects: {
        'vaults/scott/customer.yaml': { body: yaml, uploaded: new Date(NOW - 86_400_000) },
      },
    })
    const out = await driftConditions(env, [row({ config_digest: seatDigest })], NOW, 300)
    expect(out.filter((s) => s.active)).toEqual([])
    expect(pick(out, 'config_drift')[0].active).toBe(false)
  })

  it('pages config_drift when the published bytes change and the seat does not follow', async () => {
    const env = fakeEnv({
      releases: RELEASES,
      rollouts: [{ customer_slug: 'scott', state: 'done' }],
      objects: {
        'vaults/scott/customer.yaml': {
          body: yaml + 'extra: 1\n',
          uploaded: new Date(NOW - 3_600_000),
        },
      },
    })
    const out = await driftConditions(env, [row({ config_digest: seatDigest })], NOW, 300)
    expect(pick(out, 'config_drift')[0].active).toBe(true)
  })

  it('takes the LATEST rollout per seat: done then failed is not done', async () => {
    const env = fakeEnv({
      releases: RELEASES,
      rollouts: [
        { customer_slug: 'scott', state: 'done' },
        { customer_slug: 'scott', state: 'failed' },
      ],
    })
    const out = await driftConditions(env, [row({ image_ref: `${REPO}:rel-old` })], NOW, 300)
    expect(pick(out, 'image_drift')[0].active).toBe(false)
  })

  it('a D1 failure holds the image signals and never throws', async () => {
    const env = fakeEnv({ d1Throws: true })
    const out = await driftConditions(env, [row({ image_ref: `${REPO}:rel-old` })], NOW, 300)
    expect(out.filter((s) => s.condition.startsWith('image_'))).toEqual([])
  })

  it('no bucket binding holds config and skills', async () => {
    const env = { ...fakeEnv({ releases: RELEASES }), CUSTOMER_CONFIG: undefined }
    const out = await driftConditions(env, [row({ config_digest: 'b'.repeat(64) })], NOW, 300)
    expect(
      out.filter((s) => s.condition === 'config_drift' || s.condition === 'skills_drift')
    ).toEqual([])
  })
})
