/**
 * provision-customer.sh step 2c stages the chronology runner's install-level
 * tree (2026-09-04, #2696 review).
 *
 * On a seat the runner resolves its scanned-page controls and ICD tables under
 * install_root/controls/, which the entrypoint pulls from
 * vaults/<slug>/medchron-controls/ into a root-owned 0750 tree. The medchron uid
 * can read it and never write it, so the runner's own ICD fetch can NEVER run
 * on a seat: before this step, a seat worked only if someone uploaded icd/ to
 * the vault by hand. The provisioner now (a) refuses when a firm config is
 * authored without controls, (b) validates that controls.json names pages the
 * authored set carries, (c) uploads the authored controls, and (d) vendors the
 * ICD tables on the console and stages them when the vault lacks them.
 *
 * The block is sentinel-delimited and driven VERBATIM here (the
 * provisioner-gates-parse-not-grep idiom): an `aws` stub on PATH records every
 * call and answers `s3 ls` from a fixture file, and BIN_DIR points at a stub
 * vendoring helper that writes a VERSION.json and a marker. Each case asserts a
 * distinct verdict token; the negative cases assert that NOTHING was uploaded.
 */
import { execFileSync } from 'node:child_process'
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterAll, describe, expect, it } from 'vitest'

const SCRIPT = fileURLToPath(new URL('../operator/bin/provision-customer.sh', import.meta.url))
const HELPER = fileURLToPath(new URL('../operator/bin/lib/medchron-vendor-icd.sh', import.meta.url))
const src = readFileSync(SCRIPT, 'utf8')

function stageBlock(): string {
  const start = src.indexOf('# >>> medchron-controls-stage')
  const end = src.indexOf('# <<< medchron-controls-stage')
  expect(start).toBeGreaterThan(-1)
  expect(end).toBeGreaterThan(start)
  return src.slice(start, end)
}

describe('structural', () => {
  it('the block exists, runs after the firm-config upload and before the fly.toml render', () => {
    const block = src.indexOf('# >>> medchron-controls-stage')
    expect(block).toBeGreaterThan(src.indexOf('medchron-firm.yaml'))
    expect(block).toBeLessThan(src.indexOf('# ---------- Step 3: render fly.toml'))
  })

  it("the console-side vendoring helper exists, is executable, and calls the runner's own vendor()", () => {
    expect(existsSync(HELPER)).toBe(true)
    const helper = readFileSync(HELPER, 'utf8')
    expect(helper).toContain('from medchron.stages.icd_fetch import vendor')
    // The three-path install the tree already uses (CI and the seat image).
    expect(helper).toMatch(/connectors\/_sdk.*connectors\/smokeball.*runners\/medchron/s)
    expect(stageBlock()).toContain('"${BIN_DIR}/lib/medchron-vendor-icd.sh"')
  })

  it('a seat that authors a firm config without controls is a FATAL, not a warning', () => {
    const block = stageBlock()
    expect(block).toMatch(/\|\| die "medchron firm config is authored for \$\{SLUG\} but/)
    expect(block).not.toMatch(/log "WARN[^"]*controls/)
  })
})

// ---------------------------------------------------------------------------
// Behavioural: the extracted block, driven.
// ---------------------------------------------------------------------------

const scratchDirs: string[] = []
afterAll(() => scratchDirs.forEach((d) => rmSync(d, { recursive: true, force: true })))

interface Scene {
  firmAuthored: boolean
  controls?: { json: unknown; pdfs: string[] }
  vaultHasIcd: boolean
}

interface Verdict {
  code: number
  out: string
  awsCalls: string[]
  vendorCalled: boolean
}

function drive(scene: Scene): Verdict {
  const dir = mkdtempSync(join(tmpdir(), 'medchron-controls-'))
  scratchDirs.push(dir)
  // The engagements-shaped authored tree: <eng>/operator/customers/<slug>/medchron/{firm.yaml,controls/}
  const medchron = join(dir, 'engagements', 'operator', 'customers', 'example-seat', 'medchron')
  mkdirSync(join(medchron, 'controls'), { recursive: true })
  if (scene.firmAuthored) writeFileSync(join(medchron, 'firm.yaml'), 'firm: {slug: example-seat}\n')
  if (scene.controls) {
    writeFileSync(join(medchron, 'controls', 'controls.json'), JSON.stringify(scene.controls.json))
    for (const p of scene.controls.pdfs) writeFileSync(join(medchron, 'controls', p), '%PDF-1.4\n')
  }
  // Stubs. `aws` appends its argv to a log and answers `s3 ls` from a fixture
  // FILE (data, never interpolated into the stub's source). The vendoring
  // helper writes the VERSION.json the real one would and leaves a marker.
  const stubs = join(dir, 'stubs')
  mkdirSync(join(stubs, 'lib'), { recursive: true })
  writeFileSync(join(stubs, 'vault-has-icd'), scene.vaultHasIcd ? '1' : '0')
  writeFileSync(
    join(stubs, 'aws'),
    `#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$(dirname "$0")/aws.log"
if [ "$1" = s3 ] && [ "$2" = ls ]; then
  case "$3" in
    */icd/VERSION.json) [ "$(cat "$(dirname "$0")/vault-has-icd")" = 1 ] && echo "2026-09-04 10:00:00        512 VERSION.json"; exit 0 ;;
  esac
  exit 0
fi
exit 0
`
  )
  chmodSync(join(stubs, 'aws'), 0o755)
  writeFileSync(
    join(stubs, 'lib', 'medchron-vendor-icd.sh'),
    `#!/usr/bin/env bash
set -euo pipefail
mkdir -p "$1"
echo '{"stub": true}' > "$1/VERSION.json"
touch "$(dirname "$0")/../vendor-called"
`
  )
  chmodSync(join(stubs, 'lib', 'medchron-vendor-icd.sh'), 0o755)

  const harness = [
    'set -euo pipefail',
    'log() { echo "[provision/test] $*"; }',
    'die() { log "FATAL: $*"; exit 1; }',
    'SLUG=example-seat',
    'R2_BUCKET_CONFIG=example-config-bucket',
    'R2_ENDPOINT_URL=https://r2.invalid',
    'R2_ACCESS_KEY_ID=stub',
    'R2_SECRET_ACCESS_KEY=stub',
    `BIN_DIR=${JSON.stringify(stubs)}`,
    `MEDCHRON_FIRM_YAML=${JSON.stringify(join(medchron, 'firm.yaml'))}`,
    stageBlock(),
    'echo STAGE_BLOCK_COMPLETED',
  ].join('\n')
  let code = 0
  let out: string
  try {
    out = execFileSync('bash', ['-c', harness], {
      encoding: 'utf8',
      env: { PATH: [stubs, '/usr/bin', '/bin'].join(':'), HOME: dir },
      stdio: ['ignore', 'pipe', 'pipe'],
    })
  } catch (err) {
    const e = err as { stdout?: string; stderr?: string; status?: number }
    out = `${e.stdout ?? ''}${e.stderr ?? ''}`
    code = e.status ?? -1
  }
  const awsLog = join(stubs, 'aws.log')
  const awsCalls = existsSync(awsLog)
    ? readFileSync(awsLog, 'utf8').trim().split('\n').filter(Boolean)
    : []
  return { code, out, awsCalls, vendorCalled: existsSync(join(stubs, 'vendor-called')) }
}

const GOOD_CONTROLS = {
  json: [
    { pdf: 'controls/control-order.pdf', page: 1, label: 'ORDER' },
    { pdf: 'controls/control-index.pdf', page: 1, label: 'INDEX' },
  ],
  pdfs: ['control-order.pdf', 'control-index.pdf'],
}
const PREFIX = 's3://example-config-bucket/vaults/example-seat/medchron-controls'

describe('behavioural: the extracted block, driven', () => {
  it('authored controls + an empty vault: uploads the controls, vendors the ICD tables on the console, stages them', () => {
    const v = drive({ firmAuthored: true, controls: GOOD_CONTROLS, vaultHasIcd: false })
    expect(v.out).toContain('STAGE_BLOCK_COMPLETED')
    expect(v.code).toBe(0)
    expect(
      v.awsCalls.some((c) => c.includes(`s3 cp `) && c.includes(`controls ${PREFIX}/ --recursive`))
    ).toBe(true)
    expect(v.vendorCalled).toBe(true)
    expect(
      v.awsCalls.some((c) => c.includes(`s3 cp `) && c.includes(`icd ${PREFIX}/icd/ --recursive`))
    ).toBe(true)
    expect(v.out).toContain('R2 upload OK (medchron controls + ICD tables)')
  })

  it('a vault that already carries the ICD tables is not re-vendored', () => {
    const v = drive({ firmAuthored: true, controls: GOOD_CONTROLS, vaultHasIcd: true })
    expect(v.code).toBe(0)
    expect(v.vendorCalled).toBe(false)
    expect(v.awsCalls.some((c) => c.includes(`${PREFIX}/icd/ --recursive`))).toBe(false)
    expect(v.out).toContain('ICD tables already staged')
  })

  it('a firm config authored WITHOUT controls refuses the provision before any upload', () => {
    const v = drive({ firmAuthored: true, vaultHasIcd: false })
    expect(v.code).not.toBe(0)
    expect(v.out).toContain('FATAL: medchron firm config is authored for example-seat but')
    expect(v.out).not.toContain('STAGE_BLOCK_COMPLETED')
    expect(v.awsCalls).toEqual([])
    expect(v.vendorCalled).toBe(false)
  })

  it('a controls.json naming a page the authored set does not carry refuses before any upload', () => {
    const v = drive({
      firmAuthored: true,
      controls: { json: GOOD_CONTROLS.json, pdfs: ['control-order.pdf'] },
      vaultHasIcd: false,
    })
    expect(v.code).not.toBe(0)
    expect(v.out).toContain('FATAL: medchron controls.json names a page that is not in')
    expect(v.out).toContain('control-index.pdf')
    expect(v.awsCalls).toEqual([])
  })

  it('no firm config: nothing is staged and the block says so', () => {
    const v = drive({ firmAuthored: false, controls: GOOD_CONTROLS, vaultHasIcd: false })
    expect(v.code).toBe(0)
    expect(v.out).toContain('No medchron controls staged for example-seat')
    expect(v.awsCalls).toEqual([])
    expect(v.vendorCalled).toBe(false)
  })
})

/**
 * Step 2b validates firm.yaml against THIS CHECKOUT's runner schema before the
 * bytes leave for R2 (2026-09-09). The runner's key set is closed and its four
 * cost controls are required, so a firm.yaml the seat cannot load makes every
 * chronology job DEFER -- a silent stall rather than a failure. Validating at
 * provision time turns that into a refusal while a person is watching.
 *
 * Pinned statically: the block is inside the step-2b guard and strictly before
 * the upload, and it imports the runner's own config module rather than
 * re-implementing a schema that would then drift from it.
 */
describe('step 2b: the firm config is validated before it is uploaded', () => {
  const block = (() => {
    const start = src.indexOf('# >>> medchron-firm-validate')
    const end = src.indexOf('# <<< medchron-firm-validate')
    expect(start).toBeGreaterThan(-1)
    expect(end).toBeGreaterThan(start)
    return src.slice(start, end)
  })()

  it('runs before the firm config is copied to R2, and inside the authored guard', () => {
    const guard = src.indexOf('if [ -f "${MEDCHRON_FIRM_YAML}" ]; then')
    const validate = src.indexOf('# >>> medchron-firm-validate')
    const upload = src.indexOf(
      's3://${R2_BUCKET_CONFIG}/vaults/${SLUG}/medchron-firm.yaml"',
      validate
    )
    expect(guard).toBeGreaterThan(-1)
    expect(validate).toBeGreaterThan(guard)
    expect(upload).toBeGreaterThan(validate)
  })

  it("uses the runner's own config module, from the invoking checkout", () => {
    expect(block).toContain('operator/runners/medchron')
    expect(block).toContain('from medchron import config')
    expect(block).toContain('config.ConfigError')
  })

  it('a config that does not validate refuses the provision rather than uploading it', () => {
    expect(block).toContain('die ')
    expect(block).toContain('not uploading it')
  })

  /**
   * Live-caught 2026-09-09: the block ran under the bare system `python3`,
   * which has no PyYAML, so `import yaml` inside the runner's config module
   * died and the die line blamed the CONFIG. A validator that cannot run must
   * say so, not read as a wrong config. Two pins: the interpreter is the
   * script's own `uv run --with pyyaml` pattern (a bare `python3 -` fails
   * this), and "could not run" and "does not validate" are distinct exits.
   */
  it('runs under uv with pyyaml, never the bare system interpreter', () => {
    expect(block).toContain('uv run --quiet --with pyyaml python3 -')
    expect(block).not.toMatch(/medchron"\s+python3 - /)
  })

  it('tells a validator that could not run apart from a config that failed', () => {
    expect(block).toContain('could not run')
    expect(block).toContain('sys.exit(3)')
    expect(block).toContain('does not validate')
    expect(block).toContain('could not be validated')
  })
})
