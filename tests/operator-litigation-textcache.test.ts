import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { readSeatScript } from './_support/seat-layout'

/**
 * The litigation text cache can be written on a real seat (2026-10-24 review
 * of #3473). The runner child (uid jobs) cannot make the cache dir itself:
 * its parent, ${JOBS_DATA_DIR}/litigation, is root:jobs 0750. Without
 * the boot making the dir and the lane handing its path to the child, every
 * cache write fails and the cache goes quiet on every job, which no unit test
 * of the Python cache could see. This pins the three halves of the wire:
 * the boot makes the dir child-only and exports its path, the lane passes
 * that env var to the child, and the boot smoke checks the dir's owner/mode.
 */
const entry = readFileSync(resolve('operator/templates/entrypoint-litigation.sh'), 'utf8')
const checks = readSeatScript('operator/templates/seat-checks.sh')
const lane = readFileSync(resolve('operator/runners/jobs/jobs/litigation_lane.py'), 'utf8')
const cache = readFileSync(resolve('operator/runners/jobs/jobs/litigation/textcache.py'), 'utf8')
/**
 * What the REAL job host launched for an ordinary litigation job: the host
 * matrix golden (operator/runners/jobs/tests/test_host_matrix_goldens.py)
 * records each child's argv and env exactly as the lane built them, and CI's
 * goldens job holds that file to the code's behaviour. So this reads what the
 * child got, not where in the source the line that sets it happens to live.
 */
interface MatrixStep {
  children: { argv: string[]; env: Record<string, string> }[]
}
const matrix: MatrixStep[] = JSON.parse(
  readFileSync(
    resolve('operator/runners/jobs/tests/goldens/host_matrix/litigation__delivered.json'),
    'utf8'
  )
)
const launched = matrix.flatMap((step) => step.children)

describe('litigation text cache wiring', () => {
  it('the boot makes the cache dir owned by the child, 0700, and exports its path', () => {
    expect(entry).toMatch(
      /^install -d -o jobs -g jobs -m 0700 "\$\{JOBS_DATA_DIR\}\/litigation\/textcache"$/m
    )
    expect(entry).toMatch(
      /^export JOBS_LITIGATION_TEXTCACHE_DIR="\$\{JOBS_RUN_DIR\}\/litigation\/textcache"$/m
    )
  })

  it('the lane hands that env var to the child, under the name the cache reads', () => {
    expect(lane).toMatch(/^TEXTCACHE_ENV = "JOBS_LITIGATION_TEXTCACHE_DIR"$/m)
    expect(cache).toMatch(/^ENV = "JOBS_LITIGATION_TEXTCACHE_DIR"$/m)
    // The host launched exactly one litigation child, and its env carries the
    // cache dir under that name (the test seat's dir, normalised to <TMP>).
    expect(launched).toHaveLength(1)
    expect(launched[0].argv).toContain('litigate')
    expect(launched[0].env.JOBS_LITIGATION_TEXTCACHE_DIR).toBe('<TMP>/litigation-textcache')
  })

  it('the boot smoke checks the dir is the child alone', () => {
    expect(checks).toContain(
      'check "litigation-textcache-dir-child-only" "[ \\"\\$(stat -c %U:%G:%a ${SEAT_ROOT}/run/smd-jobs/litigation/textcache)\\" = jobs:jobs:700 ]"'
    )
  })
})
