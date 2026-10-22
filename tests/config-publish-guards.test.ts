/**
 * Guard coverage for the git -> R2 config publisher (ss #2082).
 *
 * `scripts/ci-publish-customer-configs.sh` writes the object a running Machine
 * boots from. Two of its behaviors are load-bearing enough that a regression
 * would be silent and expensive, so they are exercised here rather than trusted:
 *
 *  1. NEVER CREATE. A merge must not conjure config for a seat nobody
 *     provisioned, and "I could not tell whether an object exists" must not
 *     collapse into "there is none". Only a 404 is a skip.
 *  2. ONE KEY SPACE. The publisher writes `vaults/<slug>/customer.yaml` and
 *     nothing else. The `output-classes.json` key beside it belongs to the
 *     portal (ADR 0083).
 *
 * Plus the boot half: `operator/templates/entrypoint.sh` must validate the
 * fetched candidate BEFORE it becomes the live config.
 *
 * No live R2. The `aws` and `npx` the script invokes are replaced by stubs on
 * PATH, backed by a directory that stands in for the bucket, so the read-back
 * verification is a real byte comparison against what the script actually
 * uploaded.
 */
import { execFileSync } from 'node:child_process'
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterEach, describe, expect, it } from 'vitest'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const PUBLISHER = join(REPO_ROOT, 'scripts', 'ci-publish-customer-configs.sh')
const ENTRYPOINT = join(REPO_ROOT, 'operator', 'templates', 'entrypoint.sh')

/** Minimal well-formed config body. The stub validator decides pass/fail. */
function configBody(slug: string): string {
  return `customer_id: ${slug}\nvertical: law-firm\n`
}

const FAKE_AWS = `#!/usr/bin/env bash
# Stand-in for the aws CLI. Logs every invocation, and backs s3 cp with a
# directory so the publisher's read-back comparison is a real one.
echo "$*" >> "\${FAKE_AWS_LOG}"
BUCKET_DIR="\${FAKE_AWS_BUCKET_DIR}"

if [ "$1" = "s3api" ] && [ "$2" = "head-object" ]; then
  key=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --key) key="$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  if [ -n "\${FAKE_AWS_HEAD_ERROR:-}" ]; then
    echo "An error occurred (403) when calling the HeadObject operation: Forbidden" >&2
    exit 254
  fi
  if [ -f "\${BUCKET_DIR}/\${key}" ]; then exit 0; fi
  echo "An error occurred (404) when calling the HeadObject operation: Not Found" >&2
  exit 254
fi

if [ "$1" = "s3" ] && [ "$2" = "cp" ]; then
  src="$3"; dst="$4"
  case "\${dst}" in
    s3://*)
      key="\${dst#s3://}"; key="\${key#*/}"
      mkdir -p "$(dirname "\${BUCKET_DIR}/\${key}")"
      cp "\${src}" "\${BUCKET_DIR}/\${key}"
      exit 0
      ;;
    *)
      key="\${src#s3://}"; key="\${key#*/}"
      [ -f "\${BUCKET_DIR}/\${key}" ] || { echo "no such object" >&2; exit 254; }
      cp "\${BUCKET_DIR}/\${key}" "\${dst}"
      exit 0
      ;;
  esac
fi

echo "unexpected aws invocation: $*" >&2
exit 99
`

const FAKE_NPX = `#!/usr/bin/env bash
# Stand-in for the validator invocation. The real validator has its own suite;
# what matters here is that the publisher gates the upload on its exit code.
cfg=""
for a in "$@"; do cfg="$a"; done
echo "\${cfg}" >> "\${FAKE_NPX_LOG}"
case " \${FAKE_VALIDATOR_FAIL:-} " in
  *" \${cfg} "*) echo "validation failed" >&2; exit 1 ;;
esac
exit 0
`

interface Fixture {
  root: string
  binDir: string
  bucketDir: string
  awsLog: string
  npxLog: string
  before: string
  after: string
}

const fixtures: string[] = []

function git(root: string, args: string[]): string {
  return execFileSync(
    'git',
    [
      '-c',
      'user.email=t@example.com',
      '-c',
      'user.name=t',
      // Fixture repos must not inherit this repo's hooks: husky points
      // core.hooksPath at .husky/, and it is inherited, so a fixture commit
      // would run the real pre-commit hook against a temp dir with none of its
      // tooling installed.
      '-c',
      'core.hooksPath=/dev/null',
      ...args,
    ],
    {
      cwd: root,
      encoding: 'utf8',
      // The load-bearing half. `cwd` does NOT make a git invocation
      // self-contained: GIT_DIR, GIT_WORK_TREE and GIT_INDEX_FILE win over it,
      // and git EXPORTS them to every hook it runs. So under `git push` these
      // fixtures were committing into the parent repository's git dir rather
      // than their own, and all eight assertions failed on a truncated
      // "Command failed: git commit" that named neither the cause nor the repo.
      //
      // The tell was that they passed standalone and failed only inside a hook.
      // A test whose result depends on who invoked it is measuring its
      // environment rather than its subject; stripping GIT_* is what makes the
      // fixture actually throwaway.
      env: Object.fromEntries(
        Object.entries({ ...process.env, HUSKY: '0' }).filter(([k]) => !k.startsWith('GIT_'))
      ),
    }
  )
}

/**
 * A throwaway repo whose HEAD commit changes `customer.yaml` for each of
 * `slugs`, so the publisher's push-range diff has something to find.
 */
function makeFixture(slugs: string[], opts: { deleted?: string[] } = {}): Fixture {
  const root = mkdtempSync(join(tmpdir(), 'config-publish-'))
  fixtures.push(root)

  git(root, ['init', '-q'])
  // A baseline commit so BEFORE..AFTER is a real range. Pre-create any config
  // the test wants DELETED in the second commit.
  writeFileSync(join(root, 'README.md'), 'fixture\n')
  for (const slug of opts.deleted ?? []) {
    mkdirSync(join(root, 'operator', 'customers', slug), { recursive: true })
    writeFileSync(join(root, 'operator', 'customers', slug, 'customer.yaml'), configBody(slug))
  }
  git(root, ['add', '-A'])
  git(root, ['commit', '-q', '-m', 'baseline'])
  const before = git(root, ['rev-parse', 'HEAD']).trim()

  for (const slug of slugs) {
    mkdirSync(join(root, 'operator', 'customers', slug), { recursive: true })
    writeFileSync(join(root, 'operator', 'customers', slug, 'customer.yaml'), configBody(slug))
  }
  for (const slug of opts.deleted ?? []) {
    rmSync(join(root, 'operator', 'customers', slug, 'customer.yaml'))
  }
  git(root, ['add', '-A'])
  // --allow-empty: the no-op case below deliberately commits nothing.
  git(root, ['commit', '-q', '--allow-empty', '-m', 'change configs'])
  const after = git(root, ['rev-parse', 'HEAD']).trim()

  const binDir = join(root, '.fakebin')
  mkdirSync(binDir)
  writeFileSync(join(binDir, 'aws'), FAKE_AWS)
  writeFileSync(join(binDir, 'npx'), FAKE_NPX)
  chmodSync(join(binDir, 'aws'), 0o755)
  chmodSync(join(binDir, 'npx'), 0o755)

  const bucketDir = join(root, '.bucket')
  mkdirSync(bucketDir)

  return {
    root,
    binDir,
    bucketDir,
    awsLog: join(root, 'aws.log'),
    npxLog: join(root, 'npx.log'),
    before,
    after,
  }
}

/** Mark a key as already present in the stand-in bucket (i.e. provisioned). */
function seedObject(fx: Fixture, key: string, body: string): void {
  const dest = join(fx.bucketDir, key)
  mkdirSync(join(dest, '..'), { recursive: true })
  writeFileSync(dest, body)
}

interface RunResult {
  code: number
  stdout: string
  stderr: string
  awsCalls: string[]
  npxCalls: string[]
}

function runPublisher(fx: Fixture, env: Record<string, string> = {}): RunResult {
  // Strip GIT_* for the same reason the `git` helper above does, and it matters
  // more here: the publisher's first act is a `git diff` of the push range, so
  // an inherited GIT_DIR points it at the parent repository and it reports the
  // real repo's changed files instead of the fixture's. `cwd` does not override
  // those variables, and git exports them to every hook, which is why this only
  // failed under `git push`.
  const inherited = Object.fromEntries(
    Object.entries(process.env).filter(([k]) => !k.startsWith('GIT_'))
  ) as Record<string, string>
  const childEnv = {
    ...inherited,
    PATH: `${fx.binDir}:${process.env.PATH ?? ''}`,
    BEFORE_SHA: fx.before,
    AFTER_SHA: fx.after,
    // Explicit creds so the script never reaches the Cloudflare derivation path.
    R2_ACCESS_KEY_ID: 'test-key-id',
    R2_SECRET_ACCESS_KEY: 'test-secret',
    R2_ENDPOINT_URL: 'https://example.invalid',
    FAKE_AWS_LOG: fx.awsLog,
    FAKE_NPX_LOG: fx.npxLog,
    FAKE_AWS_BUCKET_DIR: fx.bucketDir,
    ...env,
  }

  let outcome: { code: number; stdout: string; stderr: string }
  try {
    const stdout = execFileSync('bash', [PUBLISHER], {
      cwd: fx.root,
      encoding: 'utf8',
      env: childEnv,
    })
    outcome = { code: 0, stdout, stderr: '' }
  } catch (err) {
    const e = err as { status?: number; stdout?: Buffer | string; stderr?: Buffer | string }
    outcome = {
      code: e.status ?? 1,
      stdout: String(e.stdout ?? ''),
      stderr: String(e.stderr ?? ''),
    }
  }

  const readLog = (path: string): string[] => {
    try {
      return readFileSync(path, 'utf8').split('\n').filter(Boolean)
    } catch {
      return []
    }
  }
  return { ...outcome, awsCalls: readLog(fx.awsLog), npxCalls: readLog(fx.npxLog) }
}

/** Writes only. `s3 cp s3://... <local>` is the publisher's read-back verify. */
const uploads = (calls: string[]): string[] =>
  calls.filter((c) => c.startsWith('s3 cp ') && !c.startsWith('s3 cp s3://'))

afterEach(() => {
  while (fixtures.length > 0) {
    const dir = fixtures.pop()
    if (dir) rmSync(dir, { recursive: true, force: true })
  }
})

describe('ci-publish-customer-configs: never-create guard', () => {
  it('publishes a changed config when the slug already has an object', () => {
    const fx = makeFixture(['ashton-price'])
    seedObject(fx, 'vaults/ashton-price/customer.yaml', 'stale: true\n')

    const res = runPublisher(fx)

    expect(res.code).toBe(0)
    expect(uploads(res.awsCalls)).toHaveLength(1)
    expect(uploads(res.awsCalls)[0]).toContain(
      's3://smd-customer-config/vaults/ashton-price/customer.yaml'
    )
    // The read-back comparison passed against real bytes, not a stubbed OK.
    expect(readFileSync(join(fx.bucketDir, 'vaults/ashton-price/customer.yaml'), 'utf8')).toBe(
      configBody('ashton-price')
    )
    expect(res.stdout).toContain('ashton-price published')
  })

  it('never creates an object for a slug that has none', () => {
    const fx = makeFixture(['never-provisioned'])

    const res = runPublisher(fx)

    expect(uploads(res.awsCalls)).toHaveLength(0)
    expect(res.stdout).toContain('first publish is Captain-gated')
    // A skipped unprovisioned slug is not a pipeline failure.
    expect(res.code).toBe(0)
  })

  it('refuses to guess when the existence check fails for any reason but 404', () => {
    const fx = makeFixture(['ashton-price'])
    seedObject(fx, 'vaults/ashton-price/customer.yaml', 'stale: true\n')

    const res = runPublisher(fx, { FAKE_AWS_HEAD_ERROR: '1' })

    expect(uploads(res.awsCalls)).toHaveLength(0)
    expect(res.code).not.toBe(0)
    expect(res.stdout + res.stderr).toContain('refusing to guess whether the seat is provisioned')
  })

  it('does not delete the object a live Machine boots from when the file is removed', () => {
    const fx = makeFixture([], { deleted: ['retired-seat'] })
    seedObject(fx, 'vaults/retired-seat/customer.yaml', 'live: true\n')

    const res = runPublisher(fx)

    expect(res.code).toBe(0)
    expect(res.awsCalls).toHaveLength(0)
    expect(res.stdout).toContain('customer retirement is manual')
    expect(readFileSync(join(fx.bucketDir, 'vaults/retired-seat/customer.yaml'), 'utf8')).toBe(
      'live: true\n'
    )
  })
})

describe('ci-publish-customer-configs: one key space', () => {
  it('writes only vaults/<slug>/customer.yaml, never a neighbouring key', () => {
    const fx = makeFixture(['ashton-price', 'pilot-law'])
    seedObject(fx, 'vaults/ashton-price/customer.yaml', 'a\n')
    seedObject(fx, 'vaults/pilot-law/customer.yaml', 'b\n')

    const res = runPublisher(fx)

    expect(res.code).toBe(0)
    for (const call of res.awsCalls) {
      expect(call).not.toContain('output-classes')
      // The image guard READS the seat's image-manifest.json; every other
      // object reference, and every write, is the customer.yaml key.
      if (call.includes('s3://') && !call.startsWith('s3 cp s3://')) {
        expect(call).toMatch(/s3:\/\/smd-customer-config\/vaults\/[a-z0-9-]+\/customer\.yaml/)
      }
    }
    for (const call of uploads(res.awsCalls)) {
      expect(call).toMatch(/ s3:\/\/smd-customer-config\/vaults\/[a-z0-9-]+\/customer\.yaml/)
    }
  })

  it('constructs the key from a constant basename and a charset-bounded slug', () => {
    const source = readFileSync(PUBLISHER, 'utf8')
    // One assignment, one literal. If a future edit made the object name an
    // input, these two assertions are what fail.
    const basenameAssignments = source.match(/^R2_CONFIG_BASENAME=.*$/gm) ?? []
    expect(basenameAssignments).toEqual(['R2_CONFIG_BASENAME="customer.yaml"'])
    const keyAssignments = source.match(/^[ \t]*key=.*$/gm) ?? []
    expect(keyAssignments).toEqual(['  key="vaults/${slug}/${R2_CONFIG_BASENAME}"'])
    // The slug segment carries the canonical pattern (#2285), not a looser
    // one. tests/customer-slug-pattern.test.ts holds all four guards to the
    // same shape; this assertion pins the key guard's copy of it.
    expect(source).toContain('^vaults/[a-z0-9][a-z0-9-]{0,38}[a-z0-9]/customer\\.yaml$')
  })

  it('refuses a slug that could name any other object', () => {
    // `.` is the character that separates `customer.yaml` from
    // `output-classes.json`; the slug charset cannot carry one.
    const fx = makeFixture(['bad.slug'])
    seedObject(fx, 'vaults/bad.slug/customer.yaml', 'x\n')

    const res = runPublisher(fx)

    expect(res.code).not.toBe(0)
    expect(res.awsCalls).toHaveLength(0)
    expect(res.stdout + res.stderr).toContain('Refusing to publish suspicious slug')
  })
})

describe('ci-publish-customer-configs: validation and template dirs', () => {
  it('skips scaffold dirs and tombstones before the validator runs', () => {
    // The templates carry deliberate placeholder values and fail validation;
    // reaching the validator at all would fail the whole job. A decommissioned
    // seat's tombstone is a record, never a seat (scripts/lib/seat-dir.sh).
    const fx = makeFixture(['_template', '_hosted-template', 'gone.decommissioned.2026-10-01'])

    const res = runPublisher(fx)

    expect(res.code).toBe(0)
    expect(res.awsCalls).toHaveLength(0)
    expect(res.npxCalls).toHaveLength(0)
    expect(res.stdout).toContain('Skipping scaffold dir: _template')
    expect(res.stdout).toContain('Skipping scaffold dir: _hosted-template')
    expect(res.stdout).toContain('Skipping tombstone dir: gone.decommissioned.2026-10-01')
  })

  it('does not publish a config that fails validation', () => {
    const fx = makeFixture(['ashton-price'])
    seedObject(fx, 'vaults/ashton-price/customer.yaml', 'good: true\n')

    const res = runPublisher(fx, {
      FAKE_VALIDATOR_FAIL: 'operator/customers/ashton-price/customer.yaml',
    })

    expect(res.code).not.toBe(0)
    expect(uploads(res.awsCalls)).toHaveLength(0)
    // The object a live seat boots from is untouched.
    expect(readFileSync(join(fx.bucketDir, 'vaults/ashton-price/customer.yaml'), 'utf8')).toBe(
      'good: true\n'
    )
  })

  it('publishes nothing when the push range touched no customer.yaml', () => {
    const fx = makeFixture([])

    const res = runPublisher(fx)

    expect(res.code).toBe(0)
    expect(res.awsCalls).toHaveLength(0)
    expect(res.stdout).toContain('nothing to publish')
  })
})

describe('ci-publish-customer-configs: image guard (2026-10-13 A&P outage)', () => {
  // The incident's shape: a config that adds an hourly cron whose pre_run.py
  // ships only in a newer image than the one the seat runs.
  const withCron = (slug: string): string =>
    [
      `customer_id: ${slug}`,
      'vertical: law-firm',
      'personas:',
      '  - slug: operator',
      '    skills:',
      '      - name: inbox-triage',
      '        version: pending',
      '        enabled: true',
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
  const withoutCron = (slug: string): string =>
    [
      `customer_id: ${slug}`,
      'vertical: law-firm',
      'personas:',
      '  - slug: operator',
      '    skills:',
      '      - name: inbox-triage',
      '        version: pending',
      '        enabled: true',
      '',
    ].join('\n')
  const manifest = (skills: Record<string, string[]>): string =>
    JSON.stringify({
      schema_version: 1,
      built_from: '7c75042c',
      skills: Object.fromEntries(
        Object.entries(skills).map(([name, files]) => [name, { pin: 'abc123', files }])
      ),
    })

  function fixtureWith(slug: string, body: string): Fixture {
    const fx = makeFixture([slug])
    writeFileSync(join(fx.root, 'operator', 'customers', slug, 'customer.yaml'), body)
    git(fx.root, ['add', '-A'])
    git(fx.root, ['commit', '-q', '-m', 'config names a new cron'])
    fx.after = git(fx.root, ['rev-parse', 'HEAD']).trim()
    return fx
  }

  it('refuses a config naming a pre_run the running image lacks, leaving the object untouched', () => {
    const fx = fixtureWith('ashton-price', withCron('ashton-price'))
    seedObject(fx, 'vaults/ashton-price/customer.yaml', withoutCron('ashton-price'))
    // The older image: connector-auth-check is not in it at all.
    seedObject(
      fx,
      'vaults/ashton-price/image-manifest.json',
      manifest({ 'inbox-triage': ['SKILL.md'] })
    )

    const res = runPublisher(fx)

    expect(res.code).not.toBe(0)
    expect(uploads(res.awsCalls)).toHaveLength(0)
    expect(res.stdout + res.stderr).toContain('pre_run:connector-auth-check/pre_run.py')
    expect(res.stdout + res.stderr).toContain('built from 7c75042c')
    expect(readFileSync(join(fx.bucketDir, 'vaults/ashton-price/customer.yaml'), 'utf8')).toBe(
      withoutCron('ashton-price')
    )
  })

  it('refuses when the skill is in the image but its pre_run is not', () => {
    const fx = fixtureWith('ashton-price', withCron('ashton-price'))
    seedObject(fx, 'vaults/ashton-price/customer.yaml', withoutCron('ashton-price'))
    seedObject(
      fx,
      'vaults/ashton-price/image-manifest.json',
      manifest({ 'inbox-triage': ['SKILL.md'], 'connector-auth-check': ['SKILL.md'] })
    )

    const res = runPublisher(fx)

    expect(res.code).not.toBe(0)
    expect(uploads(res.awsCalls)).toHaveLength(0)
    expect(res.stdout + res.stderr).toContain('pre_run:connector-auth-check/pre_run.py')
  })

  it('publishes the same config once the running image carries the pre_run', () => {
    const fx = fixtureWith('ashton-price', withCron('ashton-price'))
    seedObject(fx, 'vaults/ashton-price/customer.yaml', withoutCron('ashton-price'))
    seedObject(
      fx,
      'vaults/ashton-price/image-manifest.json',
      manifest({
        'inbox-triage': ['SKILL.md'],
        'connector-auth-check': ['SKILL.md', 'pre_run.py'],
      })
    )

    const res = runPublisher(fx)

    expect(res.code).toBe(0)
    expect(uploads(res.awsCalls)).toHaveLength(1)
    expect(res.stdout).toContain('ashton-price published')
  })

  it('cannot determine the image (no manifest) and the config adds a requirement: refuses', () => {
    const fx = fixtureWith('ashton-price', withCron('ashton-price'))
    seedObject(fx, 'vaults/ashton-price/customer.yaml', withoutCron('ashton-price'))

    const res = runPublisher(fx)

    expect(res.code).not.toBe(0)
    expect(uploads(res.awsCalls)).toHaveLength(0)
    expect(res.stdout + res.stderr).toContain('cannot determine')
  })

  it('cannot determine the image (unreadable manifest): refuses, never passes', () => {
    const fx = fixtureWith('ashton-price', withCron('ashton-price'))
    seedObject(fx, 'vaults/ashton-price/customer.yaml', withoutCron('ashton-price'))
    seedObject(fx, 'vaults/ashton-price/image-manifest.json', '{not json')

    const res = runPublisher(fx)

    expect(res.code).not.toBe(0)
    expect(uploads(res.awsCalls)).toHaveLength(0)
    expect(res.stdout + res.stderr).toContain('unreadable')
  })

  it('no manifest, but the config adds nothing over the object the seat boots from: publishes', () => {
    // A removal (the fix-forward for a skew) must not wait on a rebuild.
    const fx = fixtureWith('ashton-price', withoutCron('ashton-price'))
    seedObject(fx, 'vaults/ashton-price/customer.yaml', withCron('ashton-price'))

    const res = runPublisher(fx)

    expect(res.code).toBe(0)
    expect(uploads(res.awsCalls)).toHaveLength(1)
    expect(res.stdout).toContain('adds no skill or pre_run')
  })

  // FLEET RELEASE (2026-10-19): skills reach a seat as their own release, so a
  // seat runs its image's manifest PLUS the skills release its pointer names.
  const skillsRelease = (version: string, files: Record<string, string[]>): string =>
    JSON.stringify({
      version,
      source_sha: 'c'.repeat(40),
      files: Object.fromEntries(
        Object.entries(files).flatMap(([skill, fs]) =>
          fs.map((f) => [`${skill}/${f}`, { sha256: 'd'.repeat(64), size: 1 }])
        )
      ),
      retired: [],
      needs: {},
      pins: {},
    })
  const pointTo = (fx: Fixture, slug: string, version: string): void =>
    seedObject(
      fx,
      `vaults/${slug}/skills-pointer.json`,
      JSON.stringify({
        version,
        manifest_key: `fleet/skills/${version}.json`,
        manifest_sha256: '0'.repeat(64),
      })
    )

  it("a pre_run the image lacks but the seat's skills release carries: publishes", () => {
    const fx = fixtureWith('ashton-price', withCron('ashton-price'))
    seedObject(fx, 'vaults/ashton-price/customer.yaml', withoutCron('ashton-price'))
    seedObject(
      fx,
      'vaults/ashton-price/image-manifest.json',
      manifest({ 'inbox-triage': ['SKILL.md'] })
    )
    seedObject(
      fx,
      'fleet/skills/202610191200-abcdef12.json',
      skillsRelease('202610191200-abcdef12', { 'connector-auth-check': ['SKILL.md', 'pre_run.py'] })
    )
    pointTo(fx, 'ashton-price', '202610191200-abcdef12')

    const res = runPublisher(fx)

    expect(res.code).toBe(0)
    expect(uploads(res.awsCalls)).toHaveLength(1)
    expect(res.stdout).toContain('skills release 202610191200-abcdef12')
  })

  it('the skills release the pointer names lacks it too: refuses', () => {
    const fx = fixtureWith('ashton-price', withCron('ashton-price'))
    seedObject(fx, 'vaults/ashton-price/customer.yaml', withoutCron('ashton-price'))
    seedObject(
      fx,
      'vaults/ashton-price/image-manifest.json',
      manifest({ 'inbox-triage': ['SKILL.md'] })
    )
    seedObject(
      fx,
      'fleet/skills/202610191200-abcdef12.json',
      skillsRelease('202610191200-abcdef12', { 'connector-auth-check': ['SKILL.md'] })
    )
    pointTo(fx, 'ashton-price', '202610191200-abcdef12')

    const res = runPublisher(fx)

    expect(res.code).not.toBe(0)
    expect(uploads(res.awsCalls)).toHaveLength(0)
    expect(res.stdout + res.stderr).toContain('pre_run:connector-auth-check/pre_run.py')
  })

  it('a pointer outside the skills key space is ignored, never followed', () => {
    const fx = fixtureWith('ashton-price', withCron('ashton-price'))
    seedObject(fx, 'vaults/ashton-price/customer.yaml', withoutCron('ashton-price'))
    seedObject(
      fx,
      'vaults/ashton-price/image-manifest.json',
      manifest({ 'inbox-triage': ['SKILL.md'] })
    )
    seedObject(
      fx,
      'vaults/ashton-price/skills-pointer.json',
      JSON.stringify({ version: 'x', manifest_key: 'vaults/ashton-price/customer.yaml' })
    )

    const res = runPublisher(fx)

    expect(res.code).not.toBe(0)
    expect(uploads(res.awsCalls)).toHaveLength(0)
  })
})

describe('entrypoint boot fetch validates before adopting', () => {
  const source = readFileSync(ENTRYPOINT, 'utf8')

  it('validates the fetched candidate before it replaces the live config', () => {
    const validateIdx = source.indexOf('validate_candidate_config "${LIVE_CUSTOMER_YAML}.r2.tmp"')
    const moveIdx = source.indexOf('mv -f "${LIVE_CUSTOMER_YAML}.r2.tmp" "${LIVE_CUSTOMER_YAML}"')
    expect(validateIdx).toBeGreaterThan(-1)
    expect(moveIdx).toBeGreaterThan(-1)
    expect(validateIdx).toBeLessThan(moveIdx)
  })

  it('uses the same on-box validator the live applier uses', () => {
    expect(source).toContain('from bootstrap.validate import validate_customer_yaml')
  })

  it('keeps the existing config when the candidate is refused', () => {
    expect(source).toContain('keeping the existing root-owned customer.yaml')
  })

  it('treats an unimportable validator as a refusal, not a skip', () => {
    expect(source).toContain('validator unimportable')
    expect(source).toContain('refusing to adopt it')
  })
})
