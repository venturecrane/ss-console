/**
 * The record-store path fence reads its seat paths from the generated seat layout
 * (src/lib/operator/seat-layout.generated.ts) and refuses exactly what it refused
 * when they were written out by hand. Each case is a verdict captured from the
 * literal version, message included.
 */
import { describe, it, expect } from 'vitest'
import { recordStorePathProblem } from '../src/lib/operator/customer-yaml/sections-record-stores'

const CASES: Array<[string, string | null]> = [
  ['/opt/data', 'must be a directory under /opt/data, not /opt/data itself'],
  ['/opt/elsewhere/x', "must live under /opt/data (the seat's persistent volume)"],
  ['/opt/data/profiles', 'must not live under /opt/data/profiles (owned by the runtime)'],
  [
    '/opt/data/profiles/operator/x',
    'must not live under /opt/data/profiles (owned by the runtime)',
  ],
  [
    '/opt/data/attachment-spool/a',
    'must not live under /opt/data/attachment-spool (owned by the runtime)',
  ],
  [
    '/opt/data/smokeball-extract-cache',
    'must not live under /opt/data/smokeball-extract-cache (owned by the runtime)',
  ],
  ['/opt/data/.smd/x', 'must not live under /opt/data/.smd (owned by the runtime)'],
  ['/opt/data/medchron', 'must not live under /opt/data/medchron (owned by the runtime)'],
  ['/opt/data/jobs/records', 'must not live under /opt/data/jobs (owned by the runtime)'],
  ['/opt/data/jobsx', null],
  ['/opt/data/profilesx/a', null],
  ['/opt/data/visitors', null],
]

describe('record-store path fence on the generated seat layout', () => {
  it.each(CASES)('%s', (path, verdict) => {
    expect(recordStorePathProblem(path)).toBe(verdict)
  })
})
