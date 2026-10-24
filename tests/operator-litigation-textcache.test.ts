import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

/**
 * The litigation text cache can be written on a real seat (2026-10-24 review
 * of #3473). The runner child (uid medchron) cannot make the cache dir itself:
 * its parent, ${MEDCHRON_DATA_DIR}/litigation, is root:medchron 0750. Without
 * the boot making the dir and the lane handing its path to the child, every
 * cache write fails and the cache goes quiet on every job, which no unit test
 * of the Python cache could see. This pins the three halves of the wire:
 * the boot makes the dir child-only and exports its path, the lane passes
 * that env var to the child, and the boot smoke checks the dir's owner/mode.
 */
const entry = readFileSync(resolve('operator/templates/entrypoint-litigation.sh'), 'utf8')
const checks = readFileSync(resolve('operator/templates/seat-checks.sh'), 'utf8')
const lane = readFileSync(resolve('operator/runners/medchron/medchron/litigation_lane.py'), 'utf8')
const cache = readFileSync(
  resolve('operator/runners/medchron/medchron/litigation/textcache.py'),
  'utf8'
)

describe('litigation text cache wiring', () => {
  it('the boot makes the cache dir owned by the child, 0700, and exports its path', () => {
    expect(entry).toMatch(
      /^install -d -o medchron -g medchron -m 0700 "\$\{MEDCHRON_DATA_DIR\}\/litigation\/textcache"$/m
    )
    expect(entry).toMatch(
      /^export MEDCHRON_LITIGATION_TEXTCACHE_DIR="\$\{MEDCHRON_RUN_DIR\}\/litigation\/textcache"$/m
    )
  })

  it('the lane hands that env var to the child, under the name the cache reads', () => {
    expect(lane).toMatch(/^TEXTCACHE_ENV = "MEDCHRON_LITIGATION_TEXTCACHE_DIR"$/m)
    expect(lane).toMatch(/env\[TEXTCACHE_ENV\] = str\(self\.textcache_dir\)/)
    expect(cache).toMatch(/^ENV = "MEDCHRON_LITIGATION_TEXTCACHE_DIR"$/m)
  })

  it('the boot smoke checks the dir is the child alone', () => {
    expect(checks).toContain(
      'check "litigation-textcache-dir-child-only" "[ \\"\\$(stat -c %U:%G:%a ${SEAT_ROOT}/run/smd-medchron/litigation/textcache)\\" = medchron:medchron:700 ]"'
    )
  })
})
