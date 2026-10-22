/**
 * Coverage for the git -> R2 seat-config RECONCILER (ss #2305).
 *
 * `scripts/ci-publish-customer-configs.sh` is a trigger: it publishes whatever
 * changed inside one push range. A range that is never processed — failed
 * deploy, skipped job, force-push, rewritten history — is never revisited,
 * because no later push's range contains it. That is the #2292 defect on the
 * other projection, and the worse one: a stale D1 row means the portal SHOWS
 * the wrong posture, a stale R2 object means the seat BOOTS FROM it.
 *
 * `scripts/ci-reconcile-r2-customer-configs.sh` closes it by comparing every
 * authored seat's object against what main carries. The D1 reconciler compares
 * a stamped `git_sha`; this one cannot — the R2 object is the authored file
 * verbatim, and stamping it would make R2 diverge from git (#1898) — so the
 * comparison is byte identity, and that is the whole check.
 *
 * The first describe block is the falsifier for the PR: it shows the EXISTING
 * publisher's work set is one push range and nothing else. If that ever starts
 * enumerating live objects, the reconciler's premise should be re-examined.
 *
 * No live R2. `aws` and `npx` are replaced by stubs on PATH, `aws` backed by a
 * directory standing in for the bucket, so detection, the republish and the
 * read-back proof all run against state the test itself can inspect and the run
 * itself mutates.
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

import { afterEach, describe, expect, it } from 'vitest'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const RECONCILER = join(REPO_ROOT, 'scripts', 'ci-reconcile-r2-customer-configs.sh')
const PUBLISHER = join(REPO_ROOT, 'scripts', 'ci-publish-customer-configs.sh')
const WORKFLOW = join(REPO_ROOT, '.github', 'workflows', 'customer-config-reconcile.yml')

/**
 * Stand-in for the aws CLI, backed by a directory so every byte comparison the
 * reconciler makes is a real one. Covers the three calls it issues:
 * head-object (the never-create gate), s3 cp both ways, and list-objects-v2.
 */
const FAKE_AWS = `#!/usr/bin/env bash
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

if [ "$1" = "s3api" ] && [ "$2" = "list-objects-v2" ]; then
  if [ -n "\${FAKE_AWS_LIST_ERROR:-}" ]; then
    echo "An error occurred (AccessDenied) when calling the ListObjectsV2 operation" >&2
    exit 254
  fi
  ( cd "\${BUCKET_DIR}" 2>/dev/null && find vaults -type f 2>/dev/null | tr '\\n' '\\t' )
  echo
  exit 0
fi

if [ "$1" = "s3" ] && [ "$2" = "cp" ]; then
  src="$3"; dst="$4"
  case "\${dst}" in
    s3://*)
      if [ -n "\${FAKE_AWS_UPLOAD_ERROR:-}" ]; then echo "upload exploded" >&2; exit 254; fi
      key="\${dst#s3://}"; key="\${key#*/}"
      mkdir -p "$(dirname "\${BUCKET_DIR}/\${key}")"
      # FAKE_AWS_UPLOAD_NOOP: the upload reports success but the object never
      # moves — the exact shape the read-back proof exists to catch.
      if [ -z "\${FAKE_AWS_UPLOAD_NOOP:-}" ]; then cp "\${src}" "\${BUCKET_DIR}/\${key}"; fi
      exit 0
      ;;
    *)
      key="\${src#s3://}"; key="\${key#*/}"
      if [ -n "\${FAKE_AWS_GET_ERROR:-}" ]; then echo "get exploded" >&2; exit 254; fi
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
# what matters here is that the reconciler gates the republish on its exit code.
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
  /** The commit that last touched ashton-price/customer.yaml, for report checks. */
  authoredSha: string
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
      // Fixture repos must not inherit husky's core.hooksPath.
      '-c',
      'core.hooksPath=/dev/null',
      ...args,
    ],
    {
      cwd: root,
      encoding: 'utf8',
      // Strip GIT_*: GIT_DIR / GIT_WORK_TREE / GIT_INDEX_FILE win over `cwd` and
      // git exports them to every hook, so under `git push` these fixtures would
      // otherwise operate on the parent repository instead of themselves.
      env: Object.fromEntries(
        Object.entries({ ...process.env, HUSKY: '0' }).filter(([k]) => !k.startsWith('GIT_'))
      ),
    }
  )
}

/** The authored body for a slug. Distinct per slug so byte identity is meaningful. */
function configBody(slug: string): string {
  return `customer_id: ${slug}\nvertical: law-firm\nformat_spec: expected\n`
}

function writeConfig(root: string, slug: string, body: string): void {
  mkdirSync(join(root, 'operator', 'customers', slug), { recursive: true })
  writeFileSync(join(root, 'operator', 'customers', slug, 'customer.yaml'), body)
}

/**
 * A throwaway repo carrying two live seats and both template dirs, with a real
 * two-commit history so `git log -1 -- <path>` has an answer to report.
 */
function makeFixture(): Fixture {
  const root = mkdtempSync(join(tmpdir(), 'r2-config-reconcile-'))
  fixtures.push(root)

  git(root, ['init', '-q', '-b', 'main'])
  writeFileSync(join(root, 'README.md'), 'fixture\n')
  writeConfig(root, 'ashton-price', 'customer_id: ashton-price\nformat_spec: none\n')
  writeConfig(root, 'pilot-law', configBody('pilot-law'))
  writeConfig(root, '_template', 'customer_id: REPLACE_ME\n')
  writeConfig(root, '_hosted-template', 'customer_id: REPLACE_ME\n')
  git(root, ['add', '-A'])
  git(root, ['commit', '-q', '-m', 'author configs'])

  // The change that a dropped push range would never have published.
  writeConfig(root, 'ashton-price', configBody('ashton-price'))
  git(root, ['add', '-A'])
  git(root, ['commit', '-q', '-m', 'raise ashton-price to format_spec: expected'])
  const authoredSha = git(root, ['rev-parse', '--short', 'HEAD']).trim()

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
    authoredSha,
  }
}

/** Put an object in the stand-in bucket, i.e. mark the seat provisioned. */
function seedObject(fx: Fixture, slug: string, body: string): void {
  const dest = join(fx.bucketDir, 'vaults', slug, 'customer.yaml')
  mkdirSync(join(dest, '..'), { recursive: true })
  writeFileSync(dest, body)
}

function readObject(fx: Fixture, slug: string): string | undefined {
  try {
    return readFileSync(join(fx.bucketDir, 'vaults', slug, 'customer.yaml'), 'utf8')
  } catch {
    return undefined
  }
}

interface RunResult {
  code: number
  out: string
  awsCalls: string[]
  npxCalls: string[]
}

function run(fx: Fixture, env: Record<string, string> = {}, root = fx.root): RunResult {
  // Strip GIT_* for the same reason as `git` above, and it matters more here:
  // the reconciler's enumeration is `git ls-tree` in the fixture, and an
  // inherited GIT_DIR would point it at the real repository.
  const inherited = Object.fromEntries(
    Object.entries(process.env).filter(([k]) => !k.startsWith('GIT_'))
  ) as Record<string, string>

  let outcome: { code: number; out: string }
  try {
    const stdout = execFileSync('bash', [RECONCILER], {
      cwd: root,
      encoding: 'utf8',
      env: {
        ...inherited,
        PATH: `${fx.binDir}:${process.env.PATH ?? ''}`,
        // Explicit creds so the script never reaches the Cloudflare derivation.
        R2_ACCESS_KEY_ID: 'test-key-id',
        R2_SECRET_ACCESS_KEY: 'test-secret',
        R2_ENDPOINT_URL: 'https://example.invalid',
        FAKE_AWS_LOG: fx.awsLog,
        FAKE_NPX_LOG: fx.npxLog,
        FAKE_AWS_BUCKET_DIR: fx.bucketDir,
        ...env,
      },
    })
    outcome = { code: 0, out: stdout }
  } catch (err) {
    const e = err as { status?: number; stdout?: Buffer | string; stderr?: Buffer | string }
    outcome = { code: e.status ?? 1, out: String(e.stdout ?? '') + String(e.stderr ?? '') }
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

/** Writes only. `s3 cp s3://... <local>` is a read, not a republish. */
const uploads = (calls: string[]): string[] =>
  calls.filter((c) => c.startsWith('s3 cp ') && !c.startsWith('s3 cp s3://'))

afterEach(() => {
  while (fixtures.length > 0) {
    const dir = fixtures.pop()
    if (dir) rmSync(dir, { recursive: true, force: true })
  }
})

describe('the gap this reconciler exists to close', () => {
  const publisher = readFileSync(PUBLISHER, 'utf8')

  it('derives the publisher work set from one push range and nothing else', () => {
    // The whole defect in one assertion. If a future edit gives the publisher a
    // second source of work, this breaks and the reconciler's premise should be
    // re-examined.
    const diffs = publisher.match(/^.*git diff --name-only.*$/gm) ?? []
    expect(diffs).toHaveLength(1)
    expect(publisher).toContain('BEFORE_SHA')
    expect(publisher).toContain('AFTER_SHA')
    // Nothing in it enumerates live objects, which is what a reconciler must do.
    expect(publisher).not.toContain('list-objects-v2')
  })

  it('puts the authored file in R2 verbatim, which is why byte identity is the only check', () => {
    // Why this could not share the D1 reconciler's code. The D1 row carries a
    // stamped git_sha; the object does not, because the publisher uploads the
    // file itself with nothing added (#1898 forbids R2 diverging from git). No
    // stamp means no provenance to compare, so bytes are the whole check.
    expect(publisher).toContain('r2 s3 cp "$cfg" "s3://${R2_BUCKET_CONFIG}/${key}"')
    expect(publisher).toContain('the R2 object is the authored file verbatim')
  })
})

describe('ci-reconcile-r2-customer-configs: drift detection and convergence', () => {
  it('detects and republishes an object whose bytes are not what main carries', () => {
    const fx = makeFixture()
    // The stale-object case: the seat is still booting the pre-change bytes,
    // because the push range that carried the change was never processed.
    seedObject(fx, 'ashton-price', 'customer_id: ashton-price\nformat_spec: none\n')
    seedObject(fx, 'pilot-law', configBody('pilot-law'))

    const res = run(fx)

    // Reported, not silently healed.
    expect(res.code).toBe(2)
    expect(res.out).toContain('ashton-price DRIFTED')
    expect(res.out).toContain('DRIFT  ashton-price:')
    // The age of the divergence — the reason full history is required.
    expect(res.out).toContain(fx.authoredSha)
    // Converged: the object now holds exactly what main carries.
    expect(readObject(fx, 'ashton-price')).toBe(configBody('ashton-price'))
    // And it republished ONLY the drifted slug.
    expect(uploads(res.awsCalls)).toHaveLength(1)
    expect(uploads(res.awsCalls)[0]).toContain(
      's3://smd-customer-config/vaults/ashton-price/customer.yaml'
    )
    expect(res.out).toContain('pilot-law: in sync')
  })

  it('detects a one-byte difference, not just a whole-file one', () => {
    // Byte identity is the entire check; a comparison that only noticed large
    // differences would miss the shapes that actually happen (a flipped enum,
    // a changed pin).
    const fx = makeFixture()
    seedObject(fx, 'ashton-price', configBody('ashton-price').replace('expected', 'expecteD'))
    seedObject(fx, 'pilot-law', configBody('pilot-law'))

    const res = run(fx)

    expect(res.code).toBe(2)
    expect(res.out).toContain('ashton-price DRIFTED')
    expect(readObject(fx, 'ashton-price')).toBe(configBody('ashton-price'))
  })

  it('exits clean and uploads nothing when every object matches main', () => {
    const fx = makeFixture()
    seedObject(fx, 'ashton-price', configBody('ashton-price'))
    seedObject(fx, 'pilot-law', configBody('pilot-law'))

    const res = run(fx)

    expect(res.code).toBe(0)
    expect(res.out).toContain('byte-identical to what HEAD carries')
    expect(uploads(res.awsCalls)).toHaveLength(0)
  })

  it('reports drift without writing anything under R2_RECONCILE_DRY_RUN', () => {
    const fx = makeFixture()
    seedObject(fx, 'ashton-price', 'customer_id: ashton-price\nformat_spec: none\n')
    seedObject(fx, 'pilot-law', configBody('pilot-law'))

    const res = run(fx, { R2_RECONCILE_DRY_RUN: '1' })

    expect(res.code).toBe(2)
    expect(res.out).toContain('would republish ashton-price')
    expect(uploads(res.awsCalls)).toHaveLength(0)
    expect(readObject(fx, 'ashton-price')).toBe('customer_id: ashton-price\nformat_spec: none\n')
  })

  it('validates before it republishes, and leaves the live object alone if that fails', () => {
    const fx = makeFixture()
    seedObject(fx, 'ashton-price', 'customer_id: ashton-price\nformat_spec: none\n')
    seedObject(fx, 'pilot-law', configBody('pilot-law'))

    const res = run(fx, {
      FAKE_VALIDATOR_FAIL: 'operator/customers/ashton-price/customer.yaml',
    })

    expect(res.code).toBe(1)
    expect(res.out).toContain('failed validation; not republishing')
    expect(uploads(res.awsCalls)).toHaveLength(0)
    // The object a live seat boots from is untouched.
    expect(readObject(fx, 'ashton-price')).toBe('customer_id: ashton-price\nformat_spec: none\n')
  })

  it('validates the config at its real path, so customer-local skills resolve', () => {
    // validate-customer-yaml.ts looks for a bound skill's body at
    // `<dirname of the yaml>/skills/<name>/SKILL.md`. Validating a temp copy
    // would fail every seat that binds one.
    const fx = makeFixture()
    seedObject(fx, 'ashton-price', 'customer_id: ashton-price\nformat_spec: none\n')
    seedObject(fx, 'pilot-law', configBody('pilot-law'))

    const res = run(fx)

    expect(res.npxCalls).toContain('operator/customers/ashton-price/customer.yaml')
  })
})

describe('ci-reconcile-r2-customer-configs: never creates, never deletes', () => {
  it('never creates an object for a seat that has none', () => {
    // First publish binds a config to a Machine, a volume and a secret set, and
    // stays Captain-gated.
    const fx = makeFixture()
    seedObject(fx, 'pilot-law', configBody('pilot-law'))

    const res = run(fx)

    expect(res.code).toBe(0)
    expect(uploads(res.awsCalls)).toHaveLength(0)
    expect(res.out).toContain('first publish is Captain-gated')
    expect(readObject(fx, 'ashton-price')).toBeUndefined()
  })

  it('warns but never deletes an object whose customer.yaml is gone from main', () => {
    const fx = makeFixture()
    seedObject(fx, 'ashton-price', configBody('ashton-price'))
    seedObject(fx, 'pilot-law', configBody('pilot-law'))
    seedObject(fx, 'retired-seat', 'live: true\n')

    const res = run(fx)

    expect(res.code).toBe(0)
    expect(res.out).toContain('seat retirement is manual')
    expect(res.out).toContain('UNAUTHORED  retired-seat')
    // Still there. There is no delete call anywhere in the script.
    expect(readObject(fx, 'retired-seat')).toBe('live: true\n')
    expect(readFileSync(RECONCILER, 'utf8')).not.toContain('rm-object')
    expect(readFileSync(RECONCILER, 'utf8')).not.toContain('delete-object')
  })

  it('never touches a template dir', () => {
    // The templates carry deliberate placeholder values that fail validation,
    // and no seat boots from them.
    const fx = makeFixture()
    seedObject(fx, 'ashton-price', configBody('ashton-price'))
    seedObject(fx, 'pilot-law', configBody('pilot-law'))

    const res = run(fx)

    expect(res.code).toBe(0)
    expect(res.out).toContain('Skipping template dir: _template')
    expect(res.out).toContain('Skipping template dir: _hosted-template')
    for (const call of res.awsCalls) {
      expect(call).not.toContain('_template')
      expect(call).not.toContain('_hosted-template')
    }
  })

  it('writes only vaults/<slug>/customer.yaml, never a neighbouring key', () => {
    const fx = makeFixture()
    seedObject(fx, 'ashton-price', 'customer_id: ashton-price\nformat_spec: none\n')
    seedObject(fx, 'pilot-law', configBody('pilot-law'))

    const res = run(fx)

    expect(res.code).toBe(2)
    for (const call of res.awsCalls) {
      expect(call).not.toContain('output-classes')
      // The image guard READS the seat's image manifest and skills pointer;
      // every other object reference, and every write, is the customer.yaml key.
      if (call.includes('s3://') && !call.startsWith('s3 cp s3://')) {
        expect(call).toMatch(/s3:\/\/smd-customer-config\/vaults\/[a-z0-9-]+\/customer\.yaml/)
      }
    }
    expect(uploads(res.awsCalls).length).toBeGreaterThan(0)
    for (const call of uploads(res.awsCalls)) {
      expect(call).toMatch(/ s3:\/\/smd-customer-config\/vaults\/[a-z0-9-]+\/customer\.yaml/)
    }
  })

  it('refuses a slug that could name any other object', () => {
    // `.` is the character that separates customer.yaml from
    // output-classes.json; the canonical slug charset cannot carry one.
    const fx = makeFixture()
    writeConfig(fx.root, 'bad.slug', configBody('bad.slug'))
    git(fx.root, ['add', '-A'])
    git(fx.root, ['commit', '-q', '-m', 'author a hostile slug'])
    seedObject(fx, 'ashton-price', configBody('ashton-price'))
    seedObject(fx, 'pilot-law', configBody('pilot-law'))

    const res = run(fx)

    expect(res.code).toBe(1)
    expect(res.out).toContain('refusing to reconcile suspicious slug')
    expect(uploads(res.awsCalls)).toHaveLength(0)
  })
})

describe('ci-reconcile-r2-customer-configs: cannot-evaluate is never reported as clean', () => {
  it('refuses to guess when the existence check fails for any reason but 404', () => {
    const fx = makeFixture()
    seedObject(fx, 'ashton-price', 'customer_id: ashton-price\nformat_spec: none\n')

    const res = run(fx, { FAKE_AWS_HEAD_ERROR: '1' })

    expect(res.code).toBe(1)
    expect(res.out).toContain('refusing to guess whether the seat is provisioned')
    expect(uploads(res.awsCalls)).toHaveLength(0)
  })

  it('fails rather than reports clean when the object cannot be downloaded', () => {
    // The whole point of the control: an object we could not read is an object
    // we cannot call in sync.
    const fx = makeFixture()
    seedObject(fx, 'ashton-price', configBody('ashton-price'))
    seedObject(fx, 'pilot-law', configBody('pilot-law'))

    const res = run(fx, { FAKE_AWS_GET_ERROR: '1' })

    expect(res.code).toBe(1)
    expect(res.out).toContain('could not be downloaded; cannot compare')
    expect(res.out).not.toContain('in sync')
  })

  it('refuses to reconcile from a shallow clone', () => {
    // Byte comparison would survive depth-1, but the reported AGE would not:
    // `git log -1` names the newest commit in the slice. A confidently wrong
    // "stale since today" is worse than no answer.
    const fx = makeFixture()
    seedObject(fx, 'ashton-price', configBody('ashton-price'))
    const shallowRoot = mkdtempSync(join(tmpdir(), 'r2-config-reconcile-shallow-'))
    fixtures.push(shallowRoot)
    execFileSync('git', ['clone', '-q', '--depth', '1', `file://${fx.root}`, shallowRoot], {
      env: Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('GIT_'))),
    })

    const res = run(fx, {}, shallowRoot)

    expect(res.code).toBe(1)
    expect(res.out).toContain('shallow clone')
  })

  it('refuses a dirty operator/customers rather than converge R2 onto it', () => {
    // A modified customer.yaml is not what main authored. Comparing against it
    // and "converging" would publish an unmerged edit to a live seat.
    const fx = makeFixture()
    seedObject(fx, 'ashton-price', configBody('ashton-price'))
    seedObject(fx, 'pilot-law', configBody('pilot-law'))
    writeConfig(fx.root, 'ashton-price', 'customer_id: ashton-price\nsomeone_edited: true\n')

    const res = run(fx)

    expect(res.code).toBe(1)
    expect(res.out).toContain('is not clean at HEAD')
    expect(uploads(res.awsCalls)).toHaveLength(0)
    expect(readObject(fx, 'ashton-price')).toBe(configBody('ashton-price'))
  })

  it('fails when the upload reports success but the object never moved', () => {
    const fx = makeFixture()
    seedObject(fx, 'ashton-price', 'customer_id: ashton-price\nformat_spec: none\n')
    seedObject(fx, 'pilot-law', configBody('pilot-law'))

    const res = run(fx, { FAKE_AWS_UPLOAD_NOOP: '1' })

    expect(res.code).toBe(1)
    expect(res.out).toContain('republish did not land')
  })

  it('fails when the upload itself errors', () => {
    const fx = makeFixture()
    seedObject(fx, 'ashton-price', 'customer_id: ashton-price\nformat_spec: none\n')
    seedObject(fx, 'pilot-law', configBody('pilot-law'))

    const res = run(fx, { FAKE_AWS_UPLOAD_ERROR: '1' })

    expect(res.code).toBe(1)
    expect(res.out).toContain('R2 upload failed')
  })

  it('keeps reporting drift when the warn-only bucket listing cannot run', () => {
    // Deliberate asymmetry, and the one read here that is NOT a hard fail: the
    // listing feeds a warning that never alerts, so failing the run on it would
    // take the drift check — the part that does alert — down with it.
    const fx = makeFixture()
    seedObject(fx, 'ashton-price', 'customer_id: ashton-price\nformat_spec: none\n')
    seedObject(fx, 'pilot-law', configBody('pilot-law'))

    const res = run(fx, { FAKE_AWS_LIST_ERROR: '1' })

    expect(res.code).toBe(2)
    expect(res.out).toContain('the unauthored-object check did not run')
    expect(res.out).toContain('ashton-price DRIFTED')
  })
})

/**
 * ADR 0093 ("config ahead of image"), the 2026-10-13 A&P outage, on the writer
 * that reopened it. The publisher refuses a config the seat's running image
 * cannot run and leaves the old object in place. Before this guard, the nightly
 * reconciler then saw byte drift and republished the refused config, and the
 * seat adopted it at its next restart. These cases run the REAL checker
 * (scripts/check-config-against-image.mjs, via scripts/lib/seat-image-guard.sh)
 * against fake manifests in the stand-in bucket.
 */
describe('ci-reconcile-r2-customer-configs: image guard (ADR 0093)', () => {
  // The incident's shape: HEAD adds an hourly cron whose pre_run.py ships only
  // in a newer image than the one the seat runs.
  const withCron = [
    'customer_id: ashton-price',
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
  const withoutCron = [
    'customer_id: ashton-price',
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

  /** HEAD authors `head`; R2 still holds `live`; the seat's image carries `image`. */
  function scenario(head: string, live: string, image?: string): Fixture {
    const fx = makeFixture()
    writeConfig(fx.root, 'ashton-price', head)
    git(fx.root, ['add', '-A'])
    git(fx.root, ['commit', '-q', '-m', 'ashton-price config change'])
    seedObject(fx, 'ashton-price', live)
    seedObject(fx, 'pilot-law', configBody('pilot-law'))
    if (image !== undefined) {
      const dest = join(fx.bucketDir, 'vaults', 'ashton-price', 'image-manifest.json')
      writeFileSync(dest, image)
    }
    return fx
  }

  it('refuses to republish a config naming a pre_run the running image lacks', () => {
    const fx = scenario(withCron, withoutCron, manifest({ 'inbox-triage': ['SKILL.md'] }))

    const res = run(fx)

    // A finding (the workflow reports it), not a control failure.
    expect(res.code).toBe(2)
    expect(res.out).toContain('ashton-price: NOT republishing')
    expect(res.out).toContain('REFUSED  ashton-price:')
    expect(res.out).toContain('pre_run:connector-auth-check/pre_run.py')
    expect(res.out).toContain('built from 7c75042c')
    expect(res.out).toContain('refused (image): 1')
    // Distinct from ordinary drift: it was not counted as healed.
    expect(res.out).toContain('drifted:         0')
    expect(res.out).not.toContain('  DRIFT  ashton-price')
    // The publish did not happen, and the object the seat boots from is intact.
    expect(uploads(res.awsCalls)).toHaveLength(0)
    expect(readObject(fx, 'ashton-price')).toBe(withoutCron)
  })

  it('refuses when no manifest is readable and HEAD adds a requirement', () => {
    // "Cannot determine" never passes on its own.
    const fx = scenario(withCron, withoutCron)

    const res = run(fx)

    expect(res.code).toBe(2)
    expect(res.out).toContain('REFUSED  ashton-price:')
    expect(res.out).toContain('cannot determine')
    expect(uploads(res.awsCalls)).toHaveLength(0)
    expect(readObject(fx, 'ashton-price')).toBe(withoutCron)
  })

  it('reports the refusal in a dry run too, instead of "would republish"', () => {
    const fx = scenario(withCron, withoutCron, manifest({ 'inbox-triage': ['SKILL.md'] }))

    const res = run(fx, { R2_RECONCILE_DRY_RUN: '1' })

    expect(res.code).toBe(2)
    expect(res.out).toContain('REFUSED  ashton-price:')
    expect(res.out).not.toContain('would republish ashton-price')
    expect(uploads(res.awsCalls)).toHaveLength(0)
  })

  it('still republishes genuine drift once the running image carries what HEAD names', () => {
    const fx = scenario(
      withCron,
      withoutCron,
      manifest({ 'inbox-triage': ['SKILL.md'], 'connector-auth-check': ['SKILL.md', 'pre_run.py'] })
    )

    const res = run(fx)

    expect(res.code).toBe(2)
    expect(res.out).toContain('DRIFT  ashton-price:')
    expect(res.out).toContain('refused (image): 0')
    expect(uploads(res.awsCalls)).toHaveLength(1)
    expect(readObject(fx, 'ashton-price')).toBe(withCron)
  })

  it('republishes a removal without a manifest: it cannot make the seat worse', () => {
    // The fix-forward for a skew must never wait on a rebuild.
    const fx = scenario(withoutCron, withCron)

    const res = run(fx)

    expect(res.code).toBe(2)
    expect(res.out).toContain('DRIFT  ashton-price:')
    expect(uploads(res.awsCalls)).toHaveLength(1)
    expect(readObject(fx, 'ashton-price')).toBe(withoutCron)
  })

  it('a refused seat does not stop the other seats from converging', () => {
    const fx = scenario(withCron, withoutCron, manifest({ 'inbox-triage': ['SKILL.md'] }))
    seedObject(fx, 'pilot-law', 'customer_id: pilot-law\nstale: true\n')

    const res = run(fx)

    expect(res.code).toBe(2)
    expect(res.out).toContain('REFUSED  ashton-price:')
    expect(res.out).toContain('DRIFT  pilot-law:')
    expect(uploads(res.awsCalls)).toHaveLength(1)
    expect(uploads(res.awsCalls)[0]).toContain('vaults/pilot-law/customer.yaml')
    expect(readObject(fx, 'ashton-price')).toBe(withoutCron)
    expect(readObject(fx, 'pilot-law')).toBe(configBody('pilot-law'))
  })
})

describe('one image guard for every writer of vaults/<slug>/customer.yaml', () => {
  // A guard on one writer of a key is a guard on none of them: the reconciler
  // proved it. Every script that writes this object must source the one shared
  // implementation and call it before its upload.
  const writers = [
    PUBLISHER,
    RECONCILER,
    join(REPO_ROOT, 'operator', 'bin', 'sync-customer-yaml.sh'),
  ]

  for (const path of writers) {
    it(`${path.slice(REPO_ROOT.length)} sources the shared guard and runs it before uploading`, () => {
      const src = readFileSync(path, 'utf8')
      expect(src).toMatch(/source "\$\{[A-Z_]+\}\/(scripts\/)?lib\/seat-image-guard\.sh"/)
      const guard = src.indexOf('seat_image_guard "')
      const upload = src.search(/r2 s3 cp "\$\{?(cfg|CUSTOMER_YAML)\}?" "s3:\/\//)
      expect(guard).toBeGreaterThan(-1)
      expect(upload).toBeGreaterThan(guard)
      // No private copy of the checker invocation left behind.
      expect(src).not.toContain('check-config-against-image.mjs"')
    })
  }
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
      `#!/usr/bin/env bash\necho "REPORT: ashton-price DRIFTED"\nexit ${code}\n`
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
    expect(res.stdout).toContain('REPORT: ashton-price DRIFTED')
    // The issue-opening step is gated on exactly this.
    expect(res.outputs).toContain('status=2')
    expect(res.failed).toBe(false)
  })

  it('still fails the run when the reconciler could not evaluate (exit 1)', () => {
    const res = runStep(stepBody(), 1)
    expect(res.failed).toBe(true)
    // Even a HOLD must not be silent — a control that cannot evaluate says so.
    expect(res.stdout).toContain('REPORT: ashton-price DRIFTED')
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
    expect(res.stdout).not.toContain('REPORT: ashton-price DRIFTED')
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
    '  REFUSED  ashton-price: R2 aaa != git bbb (authored x); the image guard refused:',
    '             pre_run:connector-auth-check/pre_run.py',
    '',
  ].join('\n')
  const DRIFT_REPORT = 'drifted:         1\n  DRIFT  pilot-law: R2 aaa != git bbb (authored x)\n'

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
