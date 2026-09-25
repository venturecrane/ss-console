/**
 * `case_manager:` (the case-manager deadline jobs) — console-side validation.
 *
 * The block is read at run time by the task-list-keeper, date-prep-brief and
 * deadline-miss-escalator pre_run scripts, straight off the seat's
 * customer.yaml. These pin that the shipped seats validate, that absence is a
 * real state (every job off), that ashton-price authors nothing, and that a
 * malformed block is refused rather than read as "that job is off".
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { parse as parseYaml } from 'yaml'
import { validate } from '../src/lib/operator/customer-yaml'

const CUSTOMERS = join(__dirname, '..', 'operator', 'customers')

function load(slug: string): Record<string, unknown> {
  return parseYaml(readFileSync(join(CUSTOMERS, slug, 'customer.yaml'), 'utf8')) as Record<
    string,
    unknown
  >
}

const pilot = load('pilot-smokeball')

function withBlock(value: unknown) {
  return validate({ ...pilot, case_manager: value })
}

function errorPaths(value: unknown): string[] {
  const result = withBlock(value)
  return result.ok ? [] : result.errors.map((e) => e.path)
}

describe('case_manager', () => {
  it('the pilot authors it and validates', () => {
    const result = validate(pilot)
    if (!result.ok) throw new Error(JSON.stringify(result.errors, null, 2))
    const cm = result.value.case_manager
    expect(cm).not.toBeNull()
    expect(cm?.date_prep?.window_days).toBeGreaterThan(0)
    expect(cm?.own_tasks?.legacy_task_ids.length).toBeGreaterThan(0)
  })

  it('ashton-price authors no case_manager block (a Schedule A-1 amendment is the Captain call)', () => {
    const ap = load('ashton-price')
    expect(ap['case_manager']).toBeUndefined()
    const result = validate(ap)
    expect(result.ok).toBe(true)
    if (result.ok) expect(result.value.case_manager).toBeNull()
  })

  it('is optional: absent resolves to null (every job off)', () => {
    const rest = { ...pilot }
    delete rest['case_manager']
    const result = validate(rest)
    expect(result.ok).toBe(true)
    if (result.ok) expect(result.value.case_manager).toBeNull()
  })

  it('an absent sub-block turns off only that job', () => {
    const result = withBlock({ quiet: { level: 'handles' } })
    expect(result.ok).toBe(true)
    if (result.ok) {
      expect(result.value.case_manager).toEqual({
        own_tasks: null,
        task_cleanup: null,
        date_prep: null,
        quiet: { level: 'handles' },
      })
    }
  })

  it('refuses an unknown key at the top and inside a job (a typo must not read as "off")', () => {
    expect(errorPaths({ date_perp: { level: 'handles', window_days: 10 } })).toEqual([
      'case_manager.date_perp',
    ])
    expect(errorPaths({ quiet: { level: 'handles', lvl: 'x' } })).toEqual([
      'case_manager.quiet.lvl',
    ])
  })

  it('refuses a level outside the tier vocabulary', () => {
    expect(errorPaths({ quiet: { level: 'does_it' } })).toEqual(['case_manager.quiet.level'])
    expect(errorPaths({ own_tasks: {} })).toEqual(['case_manager.own_tasks.level'])
  })

  it('date_prep requires window_days (no pack default) and bounds it', () => {
    expect(errorPaths({ date_prep: { level: 'prepares' } })).toEqual([
      'case_manager.date_prep.window_days',
    ])
    expect(errorPaths({ date_prep: { level: 'prepares', window_days: 0 } })).toEqual([
      'case_manager.date_prep.window_days',
    ])
    expect(errorPaths({ date_prep: { level: 'prepares', window_days: 91 } })).toEqual([
      'case_manager.date_prep.window_days',
    ])
  })

  it('date_prep steps come from the closed catalog and never exceed the job level', () => {
    expect(
      errorPaths({
        date_prep: { level: 'handles', window_days: 14, steps: { draft_brief: 'prepares' } },
      })
    ).toEqual(['case_manager.date_prep.steps.draft_brief'])
    expect(
      errorPaths({
        date_prep: { level: 'prepares', window_days: 14, steps: { records_refresh: 'handles' } },
      })
    ).toEqual(['case_manager.date_prep.steps.records_refresh'])
    const ok = withBlock({
      date_prep: { level: 'handles', window_days: 14, steps: { records_refresh: 'surfaces' } },
    })
    expect(ok.ok).toBe(true)
  })

  it('legacy_task_ids must be distinct non-empty strings', () => {
    expect(errorPaths({ own_tasks: { level: 'handles', legacy_task_ids: ['a', 'a'] } })).toEqual([
      'case_manager.own_tasks.legacy_task_ids',
    ])
    expect(errorPaths({ own_tasks: { level: 'handles', legacy_task_ids: [''] } })).toEqual([
      'case_manager.own_tasks.legacy_task_ids',
    ])
    expect(errorPaths({ own_tasks: { level: 'handles', legacy_task_ids: 'abc' } })).toEqual([
      'case_manager.own_tasks.legacy_task_ids',
    ])
  })

  it('task_cleanup bounds keep_quiet_days and max_lines', () => {
    expect(errorPaths({ task_cleanup: { level: 'prepares', max_lines: 31 } })).toEqual([
      'case_manager.task_cleanup.max_lines',
    ])
    expect(errorPaths({ task_cleanup: { level: 'prepares', keep_quiet_days: -1 } })).toEqual([
      'case_manager.task_cleanup.keep_quiet_days',
    ])
    const ok = withBlock({ task_cleanup: { level: 'prepares' } })
    expect(ok.ok && ok.value.case_manager?.task_cleanup).toEqual({
      level: 'prepares',
      keep_quiet_days: null,
      max_lines: null,
    })
  })

  it('refuses a non-mapping block', () => {
    expect(errorPaths(['own_tasks'])).toEqual(['case_manager'])
  })
})
