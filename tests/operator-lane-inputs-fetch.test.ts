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
// The check list both the laptop smoke and the in-image self-test run.
const CHECKS = code(readFileSync(resolve('operator/templates/seat-checks.sh'), 'utf8'))
// Literal paths, one per lane: the file set is fixed and named here.
const LANE_FILES = {
  demand: code(readFileSync(resolve('operator/templates/entrypoint-demand.sh'), 'utf8')),
  drafting: code(readFileSync(resolve('operator/templates/entrypoint-drafting.sh'), 'utf8')),
  litigation: code(readFileSync(resolve('operator/templates/entrypoint-litigation.sh'), 'utf8')),
  casefile: code(readFileSync(resolve('operator/templates/entrypoint-casefile.sh'), 'utf8')),
} as const
const LANES = ['demand', 'drafting', 'litigation', 'casefile'] as const
// The env each lane file exports its inputs dir under (the runner reads it).
const INPUTS_ENV: Record<(typeof LANES)[number], string> = {
  demand: 'MEDCHRON_DEMAND_INPUTS',
  drafting: 'MEDCHRON_DRAFTING_INPUTS',
  litigation: 'MEDCHRON_LITIGATION_INPUTS',
  casefile: 'MEDCHRON_CASEFILE_INPUTS_DIR',
}

describe('lane firm inputs: one retrying fetch, a loud failure, a smoke check', () => {
  it('the helper retries the copy and distinguishes an absent prefix from an unreachable vault', () => {
    const h = code(HELPER)
    expect(h).toMatch(/_fetch_lane_inputs\(\)\s*\{/)
    expect(h).toMatch(/for attempt in 1 2 3; do/)
    // The marker file must have ARRIVED; a copy that exits 0 with nothing in it is a failure.
    expect(h).toMatch(
      /&& \[ -f "\$\{dest\}\.r2\.tmp\/\$\{marker\}" \]; then\n\s*_record_lane_inputs_state "\$\{prefix\}" fetched [^\n]*\n\s*return 0/
    )
    // The verdict the in-image self-test narrows on (selftest.sh lane_authored;
    // behavior pinned in operator/templates/tests/test_fetch_inputs_state.py):
    // the old file goes first, so a failed write never leaves a stale "absent".
    expect(h).toMatch(
      /rm -f "\$\{SMD_LANE_INPUTS_STATE_DIR:-\/run\/smd-lanes\}\/\$\{prefix\}\.inputs"/
    )
    expect(h).toMatch(/_record_lane_inputs_state "\$\{prefix\}" "\$\{state\}"/)
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
      /for _lane in fetch-inputs demand drafting litigation casefile [a-z -]*; do (case .*; esac; )?\. "\/app\/entrypoint-\$\{_lane\}\.sh"; done/
    )
    // ADR 0100: a lane's boot tree and fetch run only on a seat that authors the lane.
    expect(ENTRYPOINT).toMatch(
      /case "\$\{_lane\}" in demand\|drafting\|litigation\|casefile\|negotiation\) lane_on "\$\{_lane\}" \|\| continue ;; esac;/
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
      const V = `\\$\\{${INPUTS_ENV[lane]}\\}`
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
    expect(CHECKS).toMatch(/for _lane in demand drafting litigation casefile; do/)
    // The expectation source is the transport's lane_authored. Over ssh it is
    // the working tree OR engagements origin/main (a stale shared checkout must
    // not turn a lane "absent"); behavior is pinned in
    // boot-smoke-collects-failures.test.ts.
    expect(CHECKS).toMatch(/if lane_authored "\$\{_lane\}"; then/)
    expect(SMOKE).toMatch(/authored "operator\/customers\/\$\{SLUG\}\/\$1\/\$1-firm\.yaml"/)
    expect(SMOKE).toMatch(/cat-file -e "origin\/main:\$1"/)
    expect(CHECKS).toMatch(
      /check "\$\{_lane\}-firm-inputs-present" "\[ \\"\\\$\(stat -c %U:%G:%a \$\{SEAT_ROOT\}\/var\/lib\/smd-config\/\$\{_lane\}\/\$\{_lane\}-firm\.yaml\)\\" = root:medchron:640 \]"/
    )
    // The expectation source is defined before the list that reads it runs.
    expect(SMOKE.indexOf('ENGAGEMENTS_DIR="${SS_ENGAGEMENTS_DIR:-')).toBeLessThan(
      SMOKE.indexOf('\nseat_checks\n')
    )
  })

  it('a marketing seat fetches its advertising-gate registers through the helper, root:hermes 0640 (ADR 0098)', () => {
    const f = code(readFileSync(resolve('operator/templates/entrypoint-marketing.sh'), 'utf8'))
    expect(ENTRYPOINT).toMatch(/for _lane in fetch-inputs [a-z -]*\bmarketing\b[a-z -]*; do/)
    expect(DOCKERFILE).toContain(
      'COPY operator/templates/entrypoint-marketing.sh /app/entrypoint-marketing.sh'
    )
    // The path the gate reads by default (advertising_gate.py, wordpress gate.py).
    expect(f).toMatch(/SMD_MARKETING_INPUTS="\$\{CONFIG_DIR\}\/marketing"/)
    expect(f).toMatch(
      /elif _fetch_lane_inputs marketing "\$\{SMD_MARKETING_INPUTS\}" marketing-firm\.yaml; then/
    )
    expect(f).not.toMatch(/aws s3 cp/)
    expect(f).toMatch(/elif \[ "\$\{_FETCH_INPUTS_STATE\}" = absent \]; then/)
    expect(f).toMatch(/ERROR: the marketing registers could not be fetched/)
    // Both readers are the hermes uid; group medchron would lock the gate out.
    expect(f).toMatch(/chown -R root:hermes "\$\{SMD_MARKETING_INPUTS\}"/)
    expect(f).toMatch(/-type f -exec chmod 0640/)
    expect(f).not.toMatch(/root:medchron/)
    // Only a marketing seat pays the fetch; cannot-evaluate still fetches.
    expect(f).toMatch(/if \[ "\$\{_marketing_rc\}" = 3 \]; then/)
    // The smoke expects exactly that ownership on a seat that authors registers.
    expect(CHECKS).toMatch(/if lane_authored marketing; then/)
    expect(CHECKS).toContain('marketing/marketing-firm.yaml)\\" = root:hermes:640 ]')
  })

  it('provisioning stages the registers from engagements origin/main, validated by the gate loader', () => {
    const provision = code(readFileSync(resolve('operator/bin/provision-customer.sh'), 'utf8'))
    const stage = code(readFileSync(resolve('operator/bin/lib/stage-marketing-inputs.sh'), 'utf8'))
    expect(provision).toMatch(/for _stage in [a-z -]*\bmarketing\b[a-z -]*; do source/)
    expect(stage).toMatch(/show "origin\/main:\$\{_rel\}"/)
    expect(stage).toContain('operator/bin/lib/check-marketing-registers.py')
    expect(stage).toMatch(/vaults\/\$\{SLUG\}\/marketing\/" --delete/)
    expect(stage.indexOf('check-marketing-registers.py')).toBeLessThan(stage.indexOf('aws s3 sync'))
  })
})
