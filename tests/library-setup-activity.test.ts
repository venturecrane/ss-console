import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { clientSummaryFor } from '../src/lib/portal/operator/activity-language'
import type { AuditEntry } from '../src/lib/portal/operator/audit'

/**
 * The portal activity lines of a "set up our ..." job are the words of
 * operator/workspace_broker/library_setup_wording.py ACTIVITY, the one file
 * every firm-facing sentence of that piece lives in and the Captain reads before
 * any seat is switched on. This fails when the two disagree, in either direction.
 */
const WORDING = resolve(__dirname, '..', 'operator/workspace_broker/library_setup_wording.py')

function activity(): Record<string, string> {
  const source = readFileSync(WORDING, 'utf8')
  const block = source.split('ACTIVITY: dict[str, str] = {')[1]?.split('\n}')[0] ?? ''
  const out: Record<string, string> = {}
  for (const m of block.matchAll(/"(LIBRARY_SETUP_[A-Z_]+)":\s*"([^"]+)"/g)) out[m[1]] = m[2]
  return out
}

describe('library setup portal activity', () => {
  it('reads six lines from the wording file', () => {
    expect(Object.keys(activity()).sort()).toEqual([
      'LIBRARY_SETUP_ACCEPTED',
      'LIBRARY_SETUP_CHOSEN',
      'LIBRARY_SETUP_DELIVERED',
      'LIBRARY_SETUP_FAILED',
      'LIBRARY_SETUP_HELD',
      'LIBRARY_SETUP_SUBMITTED',
    ])
  })

  it.each(Object.entries(activity()))('%s says exactly the wording file line', (action, line) => {
    const entry = { action, metadata: {} } as unknown as AuditEntry
    expect(clientSummaryFor(entry)).toBe(line)
  })
})
