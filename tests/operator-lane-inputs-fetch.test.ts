/**
 * The lane firm inputs reach the seat, or the boot says loudly that they did
 * not (2026-10-14, A&P v141).
 *
 * The litigation inputs were in the vault (uploaded 13:32Z) and the 13:43Z
 * boot's single `aws s3 cp` failed with its stderr thrown away. The lane
 * logged "No litigation firm inputs in the vault", which was false, boot smoke
 * ran 75 PASS, and every litigation job then stopped at config_missing until
 * a restart of the same image fetched them. Three things pinned here:
 *
 *  1. the demand, drafting and litigation lanes fetch through ONE helper
 *     (entrypoint-fetch-inputs.sh) that retries and tells absence from an
 *     unreachable vault, sourced before the lane files;
 *  2. no lane file keeps a raw single-shot copy of its inputs;
 *  3. boot smoke asserts each authored lane's inputs are on the seat, gated on
 *     the engagements checkout the way the medchron firm config already is.
 *
 * FALSIFIER: put the raw `aws s3 cp` back in a lane file, drop the helper from
 * the source loop or the Dockerfile, or delete the smoke loop, and the matching
 * assertion fails.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

const code = (s: string) =>
  s
    .split('\n')
    .filter((l) => !/^\s*#/.test(l))
    .join('\n')

const HELPER = readFileSync(resolve('operator/templates/entrypoint-fetch-inputs.sh'), 'utf8')
const ENTRYPOINT = code(readFileSync(resolve('operator/templates/entrypoint.sh'), 'utf8'))
const DOCKERFILE = code(readFileSync(resolve('operator/templates/Dockerfile'), 'utf8'))
const SMOKE = code(readFileSync(resolve('operator/bin/boot-smoke-test.sh'), 'utf8'))
// Literal paths, one per lane: the file set is fixed and named here.
const LANE_FILES = {
  demand: code(readFileSync(resolve('operator/templates/entrypoint-demand.sh'), 'utf8')),
  drafting: code(readFileSync(resolve('operator/templates/entrypoint-drafting.sh'), 'utf8')),
  litigation: code(readFileSync(resolve('operator/templates/entrypoint-litigation.sh'), 'utf8')),
} as const
const LANES = ['demand', 'drafting', 'litigation'] as const

describe('lane firm inputs: one retrying fetch, a loud failure, a smoke check', () => {
  it('the helper retries the copy and distinguishes an absent prefix from an unreachable vault', () => {
    const h = code(HELPER)
    expect(h).toMatch(/_fetch_lane_inputs\(\)\s*\{/)
    expect(h).toMatch(/for attempt in 1 2 3; do/)
    // The marker file must have ARRIVED; a copy that exits 0 with nothing in it is a failure.
    expect(h).toMatch(/&& \[ -f "\$\{dest\}\.r2\.tmp\/\$\{marker\}" \]; then\n\s*return 0/)
    // stderr is kept and logged, never thrown away.
    expect(h).toMatch(/err=\$\(/)
    expect(h).not.toMatch(/2>\/dev\/null\s*\\?\n?\s*&& \[ -f/)
    expect(h).toMatch(/_FETCH_INPUTS_STATE="unreachable"/)
    expect(h).toMatch(/_FETCH_INPUTS_STATE="absent"/)
    // Absence is proven only by a clean listing of the vault, never by a failed one.
    expect(h.indexOf('_FETCH_INPUTS_STATE="absent"')).toBeGreaterThan(h.indexOf('aws s3 ls'))
  })

  it('is sourced before the lane files and shipped in the image', () => {
    expect(ENTRYPOINT).toMatch(
      /for _lane in fetch-inputs demand drafting litigation [a-z -]*; do \. "\/app\/entrypoint-\$\{_lane\}\.sh"; done/
    )
    expect(DOCKERFILE).toContain(
      'COPY operator/templates/entrypoint-fetch-inputs.sh /app/entrypoint-fetch-inputs.sh'
    )
    expect(DOCKERFILE.indexOf('entrypoint-fetch-inputs.sh')).toBeLessThan(
      DOCKERFILE.indexOf('COPY operator/templates/entrypoint-demand.sh')
    )
  })

  for (const lane of LANES) {
    it(`the ${lane} lane fetches through the helper and names an unfetchable vault as an error`, () => {
      const f = LANE_FILES[lane]
      const V = `\\$\\{MEDCHRON_${lane.toUpperCase()}_INPUTS\\}`
      expect(f).toMatch(
        new RegExp(`if _fetch_lane_inputs ${lane} "${V}" ${lane}-firm\\.yaml; then`)
      )
      // No single-shot copy of the inputs remains in the lane file.
      expect(f).not.toMatch(new RegExp(`aws s3 cp[\\s\\S]*?vaults/\\$\\{CUSTOMER_SLUG\\}/${lane}/`))
      // "none in the vault" is said only when the vault was listed and had none.
      expect(f).toMatch(/elif \[ "\$\{_FETCH_INPUTS_STATE\}" = absent \]; then/)
      expect(f).toMatch(new RegExp(`ERROR: the ${lane} firm inputs could not be fetched`))
    })
  }

  it('boot smoke asserts each authored lane has its inputs on the seat, gated on the engagements checkout', () => {
    expect(SMOKE).toMatch(/for _lane in demand drafting litigation; do/)
    expect(SMOKE).toMatch(
      /\[ -f "\$\{ENGAGEMENTS_DIR\}\/operator\/customers\/\$\{SLUG\}\/\$\{_lane\}\/\$\{_lane\}-firm\.yaml" \]/
    )
    expect(SMOKE).toMatch(
      /ssh_exec "\$\{_lane\}-firm-inputs-present" "\[ \\"\\\$\(stat -c %U:%G:%a \/var\/lib\/smd-config\/\$\{_lane\}\/\$\{_lane\}-firm\.yaml\)\\" = root:medchron:640 \]"/
    )
    // The expectation source is defined before it is read.
    expect(SMOKE.indexOf('ENGAGEMENTS_DIR="${SS_ENGAGEMENTS_DIR:-')).toBeLessThan(
      SMOKE.indexOf('for _lane in demand drafting litigation; do')
    )
  })
})
