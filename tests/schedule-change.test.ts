/**
 * customer_configs.schedule_changed_at (migration 0130): when a seat's SCHEDULE
 * last changed, which work-health grades boot adoption against.
 *
 * The defect: work-health used synced_at, which every publish moves. On
 * 2026-10-19 #3332 only removed `hermes_ref` from A&P's customer.yaml and A&P's
 * scheduled runs then went ungraded until the next rebuild. These tests pin that
 * a non-schedule revision leaves the answer where it was, and that a real
 * schedule change moves it.
 */
import { describe, expect, it } from 'vitest'
import { parse } from 'yaml'

import {
  buildProjectionSql,
  projectCustomerYamlToConfigRow,
} from '../src/lib/portal/customer-config-projection'
import {
  scheduleChangedAt,
  scheduleFingerprint,
  type Revision,
} from '../src/lib/portal/schedule-change'
import type { CustomerYaml } from '../src/lib/operator/customer-yaml/types'

const BASE = `customer_id: ashton-price
hermes_ref: abc123
business_hours:
  timezone: America/Phoenix
personas:
  - slug: ops
    cron:
      - skill: statute-watch
        schedule: '7 12 1 * *'
        wake_policy: pre_run_decides
      - skill: connector-auth-check
        schedule: '13 * * * *'
        wake_policy: pre_run_decides
`

// Newest first, as `git log -- <path>` lists them.
function history(...texts: string[]): Revision[] {
  return texts.map((text, i) => ({
    committedAt: new Date(Date.UTC(2026, 9, 20 - i)).toISOString(),
    text,
  }))
}

describe('scheduleChangedAt', () => {
  it('a republish that only removed hermes_ref (#3332) does not move it', () => {
    const after = BASE.replace('hermes_ref: abc123\n', '')
    expect(scheduleChangedAt(history(after, BASE))).toBe('2026-10-19T00:00:00.000Z')
  })

  it('a comment-only, reordered or reformatted revision does not move it', () => {
    const comment = `# a note\n${BASE}`
    const reordered = BASE.replace(
      /( {6}- skill: statute-watch\n.*\n.*\n)( {6}- skill: connector-auth-check\n.*\n.*\n)/,
      '$2$1'
    )
    expect(reordered).not.toBe(BASE)
    expect(scheduleChangedAt(history(comment, reordered, BASE))).toBe('2026-10-18T00:00:00.000Z')
  })

  it('a real schedule change moves it to that commit', () => {
    const hourly = BASE.replace("'7 12 1 * *'", "'7 * * * *'")
    const later = `# later, unrelated\n${hourly}`
    expect(scheduleChangedAt(history(later, hourly, BASE))).toBe('2026-10-19T00:00:00.000Z')
  })

  it('an added row, a timezone change and a wake_policy change each count', () => {
    const added = `${BASE}      - skill: new-routine\n        schedule: '0 9 * * 1-5'\n`
    const tz = BASE.replace('America/Phoenix', 'America/Los_Angeles')
    const wake = BASE.replace(/pre_run_decides/, 'always')
    for (const changed of [added, tz, wake]) {
      expect(scheduleFingerprint(parse(changed))).not.toBe(scheduleFingerprint(parse(BASE)))
      expect(scheduleChangedAt(history(changed, BASE))).toBe('2026-10-20T00:00:00.000Z')
    }
  })

  it('a schedule that never changed answers the first revision; unparseable revisions are skipped', () => {
    expect(scheduleChangedAt(history(BASE))).toBe('2026-10-20T00:00:00.000Z')
    expect(scheduleChangedAt(history(BASE, 'personas: [unclosed', BASE))).toBe(
      '2026-10-18T00:00:00.000Z'
    )
    expect(scheduleChangedAt([])).toBeNull()
  })
})

describe('the projection carries it', () => {
  it('stamps schedule_changed_at into the upsert', () => {
    const yaml = {
      customer_id: 'ashton-price',
      schema_version: 1,
      personas: [],
    } as unknown as CustomerYaml
    const row = projectCustomerYamlToConfigRow(yaml, {
      entityId: 'ent',
      orgId: 'org',
      gitSha: 'a'.repeat(40),
      syncedAt: '2026-10-20T01:00:00.000Z',
      scheduleChangedAt: '2026-10-13T20:35:03.000Z',
    })
    expect(row.schedule_changed_at).toBe('2026-10-13T20:35:03.000Z')
    const sql = buildProjectionSql(row, 'test')
    expect(sql).toContain("'2026-10-13T20:35:03.000Z'")
    expect(sql).toContain('schedule_changed_at = excluded.schedule_changed_at')
  })
})
