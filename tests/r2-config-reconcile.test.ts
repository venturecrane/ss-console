/**
 * Coverage for the git -> R2 seat-config RECONCILER (ss #2305), which is
 * READ-ONLY since Wave 2 W4.
 *
 * `scripts/ci-publish-customer-configs.sh` is the one writer of a seat's
 * customer.yaml, compiled spec and spec pointer; it converges every seat on
 * every push. `scripts/ci-reconcile-r2-customer-configs.sh` is the nightly
 * alarm: it compares every authored seat's R2 state against HEAD and reports
 * DRIFT, REFUSED (the image guard) and SPEC findings, and it never writes. A
 * second writer of a key either carries its own copy of every guard or undoes
 * the first writer's refusals, which is the #3397 defect.
 *
 * No live R2. `aws` and `npx` are stubs on PATH (tests/fixtures/fake-r2.ts),
 * `aws` backed by a directory and logging every call, so "never writes" is an
 * assertion over every call the run made.
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

import { afterEach, describe, expect, it, vi } from 'vitest'

import { git, installFakes, runScript, testSigningEnv, writes } from './fixtures/fake-r2'

// Each publisher or reconciler run spawns the spec compiler (tsx) once per
// seat, so a run takes seconds, not milliseconds, under a parallel suite.
vi.setConfig({ testTimeout: 60_000 })

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const RECONCILER = join(REPO_ROOT, 'scripts', 'ci-reconcile-r2-customer-configs.sh')
const PUBLISHER = join(REPO_ROOT, 'scripts', 'ci-publish-customer-configs.sh')
const INPUTS_LIB = join(REPO_ROOT, 'scripts', 'lib', 'seat-spec-inputs.sh')
const WORKFLOW = join(REPO_ROOT, '.github', 'workflows', 'customer-config-reconcile.yml')

interface Fixture {
  root: string
  binDir: string
  bucketDir: string
  awsLog: string
  npxLog: string
  /** The commit that last touched seat-alpha/customer.yaml, for report checks. */
  authoredSha: string
  head: string
  signing: Record<string, string>
}

const fixtures: string[] = []

function configBody(slug: string): string {
  return `customer_id: ${slug}\nvertical: law-firm\nformat_spec: expected\n`
}

function writeConfig(root: string, slug: string, body: string): void {
  mkdirSync(join(root, 'operator', 'customers', slug), { recursive: true })
  writeFileSync(join(root, 'operator', 'customers', slug, 'customer.yaml'), body)
}

/** Two live seats and both template dirs, with real history so `git log -1` answers. */
function makeFixture(): Fixture {
  const root = mkdtempSync(join(tmpdir(), 'r2-config-reconcile-'))
  fixtures.push(root)
  git(root, ['init', '-q', '-b', 'main'])
  writeFileSync(join(root, '.gitignore'), '.fakebin/\n.bucket/\n*.log\n*.pub\nkeys/\n')
  writeConfig(root, 'seat-alpha', 'customer_id: seat-alpha\nformat_spec: none\n')
  writeConfig(root, 'seat-beta', configBody('seat-beta'))
  writeConfig(root, '_template', 'customer_id: REPLACE_ME\n')
  writeConfig(root, '_hosted-template', 'customer_id: REPLACE_ME\n')
  git(root, ['add', '-A'])
  git(root, ['commit', '-q', '-m', 'author configs'])
  writeConfig(root, 'seat-alpha', configBody('seat-alpha'))
  git(root, ['add', '-A'])
  git(root, ['commit', '-q', '-m', 'raise seat-alpha to format_spec: expected'])
  const authoredSha = git(root, ['rev-parse', '--short', 'HEAD']).trim()
  const head = git(root, ['rev-parse', 'HEAD']).trim()
  const { binDir, bucketDir } = installFakes(root)
  return {
    root,
    binDir,
    bucketDir,
    awsLog: join(root, 'aws.log'),
    npxLog: join(root, 'npx.log'),
    authoredSha,
    head,
    signing: testSigningEnv(root),
  }
}

function seedObject(fx: Fixture, key: string, body: string): void {
  const dest = join(fx.bucketDir, key)
  mkdirSync(join(dest, '..'), { recursive: true })
  writeFileSync(dest, body)
}

const seedConfig = (fx: Fixture, slug: string, body: string): void =>
  seedObject(fx, `vaults/${slug}/customer.yaml`, body)

function readObject(fx: Fixture, key: string): string | undefined {
  try {
    return readFileSync(join(fx.bucketDir, key), 'utf8')
  } catch {
    return undefined
  }
}

function run(fx: Fixture, env: Record<string, string> = {}) {
  return runScript(RECONCILER, fx.root, fx, {
    SEAT_SPEC_PUBKEY: fx.signing.SEAT_SPEC_PUBKEY,
    ...env,
  })
}

/** Publish every provisioned seat with the test key, then forget the publisher's calls. */
function publishSigned(fx: Fixture): void {
  const res = runScript(PUBLISHER, fx.root, fx, {
    ...fx.signing,
    BEFORE_SHA: fx.head,
    AFTER_SHA: fx.head,
  })
  expect(res.code, res.out).toBe(0)
  rmSync(fx.awsLog, { force: true })
  rmSync(fx.npxLog, { force: true })
}

/** Both seats provisioned with stale bytes, then converged by the signed publisher. */
function convergedFixture(): Fixture {
  const fx = makeFixture()
  seedConfig(fx, 'seat-alpha', 'old\n')
  seedConfig(fx, 'seat-beta', 'old\n')
  publishSigned(fx)
  return fx
}

afterEach(() => {
  while (fixtures.length > 0) {
    const dir = fixtures.pop()
    if (dir) rmSync(dir, { recursive: true, force: true })
  }
})

describe('the reconciler is read-only against R2', () => {
  it('contains no write verb, in itself or in the input library it sources', () => {
    for (const path of [RECONCILER, INPUTS_LIB]) {
      const code = readFileSync(path, 'utf8')
        .split('\n')
        .filter((l) => !/^\s*#/.test(l))
      // Every `s3 cp` must read FROM s3:// into a local file.
      for (const line of code.filter((l) => /\bs3 cp\b/.test(l))) {
        expect(line, path).toMatch(/s3 cp "s3:\/\//)
      }
      expect(code.join('\n'), path).not.toMatch(
        /put-object|delete-object|copy-object|\bs3 rm\b|\bs3 mv\b|\bs3 sync\b/
      )
    }
  })

  it('the static check above can fail: the publisher, which writes, trips it', () => {
    const code = readFileSync(PUBLISHER, 'utf8').split('\n')
    const cps = code.filter((l) => !/^\s*#/.test(l) && /\bs3 cp\b/.test(l))
    expect(cps.some((l) => !/s3 cp "s3:\/\//.test(l))).toBe(true)
  })

  it('reports drift and writes nothing: the object stays as the seat boots it', () => {
    const fx = convergedFixture()
    seedConfig(fx, 'seat-alpha', 'customer_id: seat-alpha\nformat_spec: none\n')

    const res = run(fx)

    expect(res.code, res.out).toBe(2)
    expect(res.out).toContain('seat-alpha DRIFTED')
    expect(res.out).toContain('DRIFT  seat-alpha:')
    expect(res.out).toContain(fx.authoredSha)
    expect(res.out).toContain('the next push to main republishes it')
    expect(writes(res.awsCalls)).toEqual([])
    expect(readObject(fx, 'vaults/seat-alpha/customer.yaml')).toBe(
      'customer_id: seat-alpha\nformat_spec: none\n'
    )
    expect(res.out).toContain('seat-beta: customer.yaml in sync')
  })

  it('detects a one-byte difference, not just a whole-file one', () => {
    const fx = convergedFixture()
    seedConfig(fx, 'seat-alpha', configBody('seat-alpha').replace('expected', 'expecteD'))
    const res = run(fx)
    expect(res.code).toBe(2)
    expect(res.out).toContain('seat-alpha DRIFTED')
    expect(writes(res.awsCalls)).toEqual([])
  })

  it('exits clean when every seat holds HEAD bytes and a signed spec that verifies', () => {
    const fx = convergedFixture()

    const res = run(fx)

    expect(res.code, res.out).toBe(0)
    expect(res.out).toContain('seat-alpha: signed spec current and verified')
    expect(res.out).toContain('reconcile clean')
    expect(writes(res.awsCalls)).toEqual([])
  })

  it('a dry run is the same read-only run', () => {
    const fx = convergedFixture()
    seedConfig(fx, 'seat-alpha', 'customer_id: seat-alpha\nformat_spec: none\n')
    const res = run(fx, { R2_RECONCILE_DRY_RUN: '1' })
    expect(res.code).toBe(2)
    expect(writes(res.awsCalls)).toEqual([])
  })
})

describe('the reconciler judges the signed spec', () => {
  it('a seat with no published spec is a SPEC finding', () => {
    const fx = makeFixture()
    seedConfig(fx, 'seat-alpha', configBody('seat-alpha'))
    seedConfig(fx, 'seat-beta', configBody('seat-beta'))

    const res = run(fx)

    expect(res.code).toBe(2)
    expect(res.out).toContain(
      'SPEC  seat-alpha: the published spec differs from what HEAD compiles'
    )
    expect(writes(res.awsCalls)).toEqual([])
  })

  it('a pointer whose signature was tampered with does not verify', () => {
    const fx = convergedFixture()
    const key = 'vaults/seat-alpha/spec-pointer.json'
    const pointer = JSON.parse(readObject(fx, key) ?? '{}') as Record<string, string>
    const sig = Buffer.from(pointer.signature, 'base64')
    sig[0] ^= 0xff
    seedObject(fx, key, JSON.stringify({ ...pointer, signature: sig.toString('base64') }))

    const res = run(fx)

    expect(res.code).toBe(2)
    expect(res.out).toContain('SPEC  seat-alpha: the published spec does not verify')
    expect(res.out).toContain('signature does not verify')
    expect(res.out).toContain('seat-beta: signed spec current and verified')
  })

  it('a spec signed by a key other than the committed one is reported, not trusted', () => {
    const fx = convergedFixture()
    const keys = join(fx.root, 'keys')
    mkdirSync(keys)
    const other = testSigningEnv(keys)
    const res = run(fx, { SEAT_SPEC_PUBKEY: other.SEAT_SPEC_PUBKEY })
    expect(res.code).toBe(2)
    expect(res.out).toContain('not the committed key')
  })

  it('a spec stale against HEAD is a SPEC finding even when the yaml bytes match', () => {
    const fx = convergedFixture()
    writeConfig(fx.root, 'seat-alpha', configBody('seat-alpha') + 'notes: changed\n')
    git(fx.root, ['commit', '-qam', 'change seat-alpha'])
    seedConfig(fx, 'seat-alpha', configBody('seat-alpha') + 'notes: changed\n')

    const res = run(fx)

    expect(res.code).toBe(2)
    expect(res.out).toContain('seat-alpha: customer.yaml in sync')
    expect(res.out).toContain('SPEC  seat-alpha: the published spec differs')
  })
})

describe('ci-reconcile-r2-customer-configs: never creates, never deletes', () => {
  it('never creates an object for a seat that has none', () => {
    const fx = makeFixture()
    seedConfig(fx, 'seat-beta', 'old\n')
    publishSigned(fx)
    const res = run(fx)
    expect(res.code, res.out).toBe(0)
    expect(writes(res.awsCalls)).toEqual([])
    expect(res.out).toContain('first publish is Captain-gated')
    expect(readObject(fx, 'vaults/seat-alpha/customer.yaml')).toBeUndefined()
  })

  it('warns but never deletes an object whose customer.yaml is gone from main', () => {
    const fx = convergedFixture()
    seedConfig(fx, 'retired-seat', 'live: true\n')
    const res = run(fx)
    expect(res.code).toBe(0)
    expect(res.out).toContain('retired-seat has an object')
    expect(res.out).toContain('UNAUTHORED  retired-seat')
    expect(readObject(fx, 'vaults/retired-seat/customer.yaml')).toBe('live: true\n')
    expect(writes(res.awsCalls)).toEqual([])
  })

  it('never touches a template dir', () => {
    const fx = convergedFixture()
    const res = run(fx)
    expect(res.out).toContain('Skipping scaffold dir: _template')
    expect(res.awsCalls.some((c) => c.includes('_template'))).toBe(false)
  })

  it('refuses a slug that could name any other object', () => {
    const fx = makeFixture()
    writeConfig(fx.root, 'bad.slug', 'x\n')
    git(fx.root, ['add', '-A'])
    git(fx.root, ['commit', '-q', '-m', 'hostile slug'])
    const res = run(fx)
    expect(res.code).toBe(1)
    expect(res.out).toContain('refusing to reconcile suspicious slug: bad.slug')
  })
})

describe('ci-reconcile-r2-customer-configs: cannot-evaluate is never reported as clean', () => {
  it('refuses to guess when the existence check fails for any reason but 404', () => {
    const fx = makeFixture()
    seedConfig(fx, 'seat-alpha', configBody('seat-alpha'))
    const res = run(fx, { FAKE_AWS_HEAD_ERROR: '1' })
    expect(res.code).toBe(1)
    expect(res.out).toContain('refusing to guess whether the seat is provisioned')
  })

  it('fails rather than reports clean when the object cannot be downloaded', () => {
    const fx = makeFixture()
    seedConfig(fx, 'seat-alpha', configBody('seat-alpha'))
    seedConfig(fx, 'seat-beta', configBody('seat-beta'))
    const res = run(fx, { FAKE_AWS_GET_ERROR: '1' })
    expect(res.code).toBe(1)
    expect(res.out).toContain('could not be read; cannot compare')
  })

  it('refuses to reconcile from a shallow clone', () => {
    const fx = makeFixture()
    const shallow = mkdtempSync(join(tmpdir(), 'r2-shallow-'))
    fixtures.push(shallow)
    execFileSync('git', ['clone', '-q', '--depth', '1', `file://${fx.root}`, shallow], {
      env: Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('GIT_'))),
    })
    const res = runScript(RECONCILER, shallow, fx)
    expect(res.code).toBe(1)
    expect(res.out).toContain('shallow clone')
  })

  it('refuses a dirty operator/customers rather than compare against it', () => {
    const fx = makeFixture()
    writeConfig(fx.root, 'seat-beta', 'locally edited\n')
    const res = run(fx)
    expect(res.code).toBe(1)
    expect(res.out).toContain('is not clean at HEAD')
  })

  it('keeps reporting drift when the warn-only bucket listing cannot run', () => {
    const fx = convergedFixture()
    seedConfig(fx, 'seat-alpha', 'customer_id: seat-alpha\nformat_spec: none\n')
    const res = run(fx, { FAKE_AWS_LIST_ERROR: '1' })
    expect(res.code).toBe(2)
    expect(res.out).toContain('the unauthored-object check did not run')
    expect(res.out).toContain('DRIFT  seat-alpha:')
  })
})

describe('ci-reconcile-r2-customer-configs: the image guard is reported, not acted on', () => {
  const withCron = [
    'customer_id: seat-alpha',
    'vertical: law-firm',
    'personas:',
    '  - slug: operator',
    '    skills:',
    '      - name: connector-auth-check',
    '        version: pending',
    '        enabled: true',
    '    cron:',
    '      - skill: connector-auth-check',
    "        schedule: '13 * * * *'",
    '        pre_run: pre_run.py',
    '        wake_policy: pre_run_decides',
    '',
  ].join('\n')

  it('reports REFUSED for a config naming a pre_run the running image lacks', () => {
    const fx = makeFixture()
    writeConfig(fx.root, 'seat-alpha', withCron)
    git(fx.root, ['commit', '-qam', 'enable a cron'])
    seedConfig(fx, 'seat-alpha', configBody('seat-alpha'))
    seedConfig(fx, 'seat-beta', configBody('seat-beta'))
    seedObject(
      fx,
      'vaults/seat-alpha/image-manifest.json',
      JSON.stringify({ schema_version: 1, built_from: '7c75042c', skills: {} })
    )

    const res = run(fx)

    expect(res.code).toBe(2)
    expect(res.out).toContain('REFUSED  seat-alpha')
    expect(res.out).toContain('pre_run:connector-auth-check/pre_run.py')
    expect(writes(res.awsCalls)).toEqual([])
    expect(readObject(fx, 'vaults/seat-alpha/customer.yaml')).toBe(configBody('seat-alpha'))
  })
})

describe('the R2 reconciler is actually scheduled', () => {
  const source = readFileSync(WORKFLOW, 'utf8')

  it('rides the same schedule and dispatch as its D1 sibling', () => {
    expect(existsSync(WORKFLOW)).toBe(true)
    expect(source).toContain('reconcile-r2:')
    expect(source).toContain('schedule:')
    expect(source).toMatch(/cron:/)
    expect(source).toContain('workflow_dispatch:')
  })

  it('runs the R2 reconciler and gates it on this suite', () => {
    expect(source).toContain('bash scripts/ci-reconcile-r2-customer-configs.sh')
    expect(source).toContain('npx vitest run tests/r2-config-reconcile.test.ts')
  })

  it('opens an issue on findings instead of only annotating the log', () => {
    const r2Job = source.slice(source.indexOf('reconcile-r2:'))
    expect(r2Job).toContain('gh issue create')
    expect(r2Job).toContain("== '2'")
  })

  it('checks out full history, which the reconciler requires', () => {
    const r2Job = source.slice(source.indexOf('reconcile-r2:'))
    expect(r2Job).toContain('fetch-depth: 0')
  })
})

/**
 * ss#2307, applied to the sibling before it can repeat. The exit-code contract
 * is correct and covered by every test above; the WORKFLOW STEP consuming it is
 * where the D1 job threw that contract away. GitHub runs `run:` bodies under
 * `bash -e`, so exit 2 — the findings code, the only non-zero path that is not
 * an error — aborts the step before `cat` and before `status` reaches
 * GITHUB_OUTPUT, leaving the issue-opening step unreachable.
 *
 * These tests execute the REAL step body extracted from the YAML, under the
 * shell GitHub uses, against a stub that exits 2. Asserting on the body's text
 * would only restate the fix; running it is what can fail.
 */
describe('the R2 reconcile step reports what the reconciler found (ss#2307)', () => {
  let dir: string | undefined

  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true })
    dir = undefined
  })

  /** The `run:` body of the R2 reconcile step, dedented, exactly as GitHub runs it. */
  const stepBody = (): string => {
    const source = readFileSync(WORKFLOW, 'utf8')
    const start = source.indexOf('      - name: Reconcile every seat config against main')
    expect(start).toBeGreaterThan(-1)
    const body = source.slice(source.indexOf('run: |', start) + 'run: |\n'.length)
    const lines: string[] = []
    for (const line of body.split('\n')) {
      if (line.trim() !== '' && !line.startsWith('          ')) break
      lines.push(line.slice(10))
    }
    return lines.join('\n')
  }

  const runStep = (body: string, code: number) => {
    dir = mkdtempSync(join(tmpdir(), 'r2-step-2307-'))
    mkdirSync(join(dir, 'scripts'), { recursive: true })
    writeFileSync(
      join(dir, 'scripts', 'ci-reconcile-r2-customer-configs.sh'),
      `#!/usr/bin/env bash\necho "REPORT: seat-alpha DRIFTED"\nexit ${code}\n`
    )
    const outputs = join(dir, 'github_output')
    writeFileSync(outputs, '')
    writeFileSync(join(dir, 'step.sh'), body)
    let stdout: string
    let failed = false
    try {
      stdout = execFileSync('bash', ['-e', join(dir, 'step.sh')], {
        cwd: dir,
        encoding: 'utf-8',
        env: { ...process.env, GITHUB_OUTPUT: outputs },
      })
    } catch (err) {
      failed = true
      stdout = String((err as { stdout?: string }).stdout ?? '')
    }
    return { stdout, failed, outputs: readFileSync(outputs, 'utf-8') }
  }

  it('prints the report and records status=2 when the reconciler finds drift', () => {
    const res = runStep(stepBody(), 2)
    // Without this the finding exists only in an exit code nobody can read.
    expect(res.stdout).toContain('REPORT: seat-alpha DRIFTED')
    // The issue-opening step is gated on exactly this.
    expect(res.outputs).toContain('status=2')
    expect(res.failed).toBe(false)
  })

  it('still fails the run when the reconciler could not evaluate (exit 1)', () => {
    const res = runStep(stepBody(), 1)
    expect(res.failed).toBe(true)
    // Even a HOLD must not be silent — a control that cannot evaluate says so.
    expect(res.stdout).toContain('REPORT: seat-alpha DRIFTED')
  })

  it('passes the report through untouched on a clean run', () => {
    const res = runStep(stepBody(), 0)
    expect(res.failed).toBe(false)
    expect(res.outputs).toContain('status=0')
  })

  /**
   * The falsifier. Reintroduce the pre-fix body and confirm this harness
   * reports the defect — otherwise the three tests above are green for a reason
   * nobody has established.
   */
  it('detects the regression if the `|| STATUS=$?` guard is removed', () => {
    const regressed = stepBody().replace(/ \|\| STATUS=\$\?/, '\n          STATUS=$?')
    expect(regressed).not.toEqual(stepBody())
    const res = runStep(regressed, 2)
    expect(res.stdout).not.toContain('REPORT: seat-alpha DRIFTED')
    expect(res.outputs).not.toContain('status=2')
  })
})

/**
 * The REFUSED finding must reach a human distinctly from ordinary drift. DRIFT
 * heals itself and gets a dated issue; REFUSED persists until the seat is
 * reprovisioned, so it gets ONE standing issue the nightly run comments on.
 * Runs the real step body from the YAML against a `gh` stub.
 */
describe('the R2 issue step separates REFUSED from DRIFT', () => {
  let dir: string | undefined

  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true })
    dir = undefined
  })

  const issueStepBody = (): string => {
    const source = readFileSync(WORKFLOW, 'utf8')
    const start = source.indexOf(
      '      - name: Open an issue when a seat config drifted in R2 or the image guard held it back'
    )
    expect(start).toBeGreaterThan(-1)
    const body = source.slice(source.indexOf('run: |', start) + 'run: |\n'.length)
    const lines: string[] = []
    for (const line of body.split('\n')) {
      if (line.trim() !== '' && !line.startsWith('          ')) break
      lines.push(line.slice(10))
    }
    return lines.join('\n')
  }

  const runIssueStep = (report: string, existing = '') => {
    dir = mkdtempSync(join(tmpdir(), 'r2-issue-step-'))
    const bin = join(dir, 'bin')
    mkdirSync(bin)
    const log = join(dir, 'gh.log')
    writeFileSync(
      join(bin, 'gh'),
      [
        '#!/usr/bin/env bash',
        `echo "$*" >> "${log}"`,
        'if [ "$1 $2" = "issue list" ]; then printf "%s" "${FAKE_EXISTING:-}"; fi',
        'exit 0',
        '',
      ].join('\n')
    )
    chmodSync(join(bin, 'gh'), 0o755)
    writeFileSync(join(dir, 'reconcile-r2.txt'), report)
    writeFileSync(join(dir, 'step.sh'), issueStepBody())
    execFileSync('bash', ['-e', join(dir, 'step.sh')], {
      cwd: dir,
      encoding: 'utf8',
      env: {
        ...process.env,
        PATH: `${bin}:${process.env.PATH ?? ''}`,
        REPO: 'venturecrane/ss-console',
        RUN_URL: 'https://example.invalid/run',
        FAKE_EXISTING: existing,
      },
    })
    const calls = existsSync(log) ? readFileSync(log, 'utf8').split('\n').filter(Boolean) : []
    const refusedPath = join(dir, 'refused.md')
    const refused = existsSync(refusedPath) ? readFileSync(refusedPath, 'utf8') : ''
    return { calls, refused }
  }

  const REFUSED_REPORT = [
    'refused (image): 1',
    '  REFUSED  seat-alpha: R2 aaa != git bbb (authored x); the image guard refused:',
    '             pre_run:connector-auth-check/pre_run.py',
    '',
  ].join('\n')
  const DRIFT_REPORT = 'drifted:         1\n  DRIFT  seat-beta: R2 aaa != git bbb (authored x)\n'

  it('opens the standing refused-by-guard issue, not a dated drift issue', () => {
    const res = runIssueStep(REFUSED_REPORT)
    const creates = res.calls.filter((c) => c.startsWith('issue create'))
    expect(creates).toHaveLength(1)
    expect(creates[0]).toContain('R2 seat config refused by the image guard')
    expect(res.refused).toContain('pre_run:connector-auth-check/pre_run.py')
    expect(res.refused).toContain('reprovision the seat')
  })

  it('comments on the standing issue instead of opening a new one each night', () => {
    const res = runIssueStep(REFUSED_REPORT, '4242')
    expect(res.calls.filter((c) => c.startsWith('issue create'))).toHaveLength(0)
    expect(res.calls.some((c) => c.startsWith('issue comment 4242'))).toBe(true)
  })

  it('keeps ordinary drift on its dated issue', () => {
    const res = runIssueStep(DRIFT_REPORT)
    const creates = res.calls.filter((c) => c.startsWith('issue create'))
    expect(creates).toHaveLength(1)
    expect(creates[0]).toContain('R2 seat config drift')
    expect(res.calls.some((c) => c.startsWith('issue list'))).toBe(false)
  })

  it('reports both when one run finds both', () => {
    const res = runIssueStep(DRIFT_REPORT + REFUSED_REPORT)
    expect(res.calls.filter((c) => c.startsWith('issue create'))).toHaveLength(2)
  })
})
