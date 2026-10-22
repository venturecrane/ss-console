/**
 * operator/bin/sync-customer-yaml.sh is the operator-run writer of the R2
 * object a seat boots from (vaults/<slug>/customer.yaml). It used to upload
 * after validation alone: no image guard, no never-create guard, and a looser
 * slug pattern than the CI writers. Whichever process writes that key decides
 * what the seat runs at its next restart (ADR 0093, the 2026-10-13 A&P
 * outage), so it now applies the same guards as the publisher and reconciler,
 * from the same shared implementation (scripts/lib/seat-image-guard.sh).
 *
 * No live R2: `aws` and `npx` are stubs on PATH, `aws` backed by a directory
 * standing in for the bucket. The script resolves its repo root from its own
 * location, so it runs from a fixture tree whose `scripts/` is the real one
 * (the real guard and the real checker run).
 */
import { execFileSync } from 'node:child_process'
import {
  chmodSync,
  copyFileSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterEach, describe, expect, it } from 'vitest'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const SYNC = join(REPO_ROOT, 'operator', 'bin', 'sync-customer-yaml.sh')

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
cfg=""
for a in "$@"; do cfg="$a"; done
case "\${cfg}" in
  *"\${FAKE_VALIDATOR_FAIL:-__none__}"*) echo "validation failed" >&2; exit 1 ;;
esac
exit 0
`

const withCron = [
  'customer_id: ashton-price',
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

interface Fixture {
  root: string
  bucket: string
  awsLog: string
  bin: string
}

const fixtures: string[] = []
afterEach(() => {
  while (fixtures.length > 0) rmSync(fixtures.pop() as string, { recursive: true, force: true })
})

function makeFixture(slug: string, config: string): Fixture {
  const root = mkdtempSync(join(tmpdir(), 'sync-customer-yaml-'))
  fixtures.push(root)
  mkdirSync(join(root, 'operator', 'bin'), { recursive: true })
  copyFileSync(SYNC, join(root, 'operator', 'bin', 'sync-customer-yaml.sh'))
  // The real guard, the real checker, the real creds helper.
  symlinkSync(join(REPO_ROOT, 'scripts'), join(root, 'scripts'))
  mkdirSync(join(root, 'operator', 'customers', slug), { recursive: true })
  writeFileSync(join(root, 'operator', 'customers', slug, 'customer.yaml'), config)
  const bin = join(root, '.bin')
  mkdirSync(bin)
  writeFileSync(join(bin, 'aws'), FAKE_AWS)
  writeFileSync(join(bin, 'npx'), FAKE_NPX)
  chmodSync(join(bin, 'aws'), 0o755)
  chmodSync(join(bin, 'npx'), 0o755)
  const bucket = join(root, '.bucket')
  mkdirSync(bucket)
  return { root, bucket, awsLog: join(root, 'aws.log'), bin }
}

function seed(fx: Fixture, key: string, body: string): void {
  mkdirSync(join(fx.bucket, key, '..'), { recursive: true })
  writeFileSync(join(fx.bucket, key), body)
}

function object(fx: Fixture, key: string): string | undefined {
  try {
    return readFileSync(join(fx.bucket, key), 'utf8')
  } catch {
    return undefined
  }
}

function readCalls(path: string): string[] {
  try {
    return readFileSync(path, 'utf8').split('\n').filter(Boolean)
  } catch {
    return []
  }
}

function sync(fx: Fixture, slug: string, env: Record<string, string> = {}) {
  let outcome: { code: number; out: string }
  try {
    const stdout = execFileSync(
      'bash',
      [join(fx.root, 'operator', 'bin', 'sync-customer-yaml.sh'), slug],
      {
        cwd: fx.root,
        encoding: 'utf8',
        stdio: ['ignore', 'pipe', 'pipe'],
        env: {
          ...process.env,
          PATH: `${fx.bin}:${process.env.PATH ?? ''}`,
          R2_ACCESS_KEY_ID: 'test-key-id',
          R2_SECRET_ACCESS_KEY: 'test-secret',
          R2_ENDPOINT_URL: 'https://example.invalid',
          FAKE_AWS_LOG: fx.awsLog,
          FAKE_AWS_BUCKET_DIR: fx.bucket,
          ...env,
        },
      }
    )
    outcome = { code: 0, out: stdout }
  } catch (err) {
    const e = err as { status?: number; stdout?: string; stderr?: string }
    outcome = { code: e.status ?? 1, out: String(e.stdout ?? '') + String(e.stderr ?? '') }
  }
  const { code, out } = outcome
  const calls = readCalls(fx.awsLog)
  const uploads = calls.filter((c) => c.startsWith('s3 cp ') && !c.startsWith('s3 cp s3://'))
  return { code, out, uploads }
}

const KEY = 'vaults/ashton-price/customer.yaml'

describe('sync-customer-yaml.sh applies the guards every R2 config writer applies', () => {
  it('uploads and reads back when the running image carries what the config names', () => {
    const fx = makeFixture('ashton-price', withCron)
    seed(fx, KEY, withoutCron)
    seed(
      fx,
      'vaults/ashton-price/image-manifest.json',
      manifest({ 'inbox-triage': ['SKILL.md'], 'connector-auth-check': ['SKILL.md', 'pre_run.py'] })
    )

    const res = sync(fx, 'ashton-price')

    expect(res.code).toBe(0)
    expect(res.out).toContain('read back byte-identical')
    expect(res.uploads).toHaveLength(1)
    expect(object(fx, KEY)).toBe(withCron)
  })

  it('IMAGE GUARD: refuses a config naming a pre_run the running image lacks', () => {
    const fx = makeFixture('ashton-price', withCron)
    seed(fx, KEY, withoutCron)
    seed(fx, 'vaults/ashton-price/image-manifest.json', manifest({ 'inbox-triage': ['SKILL.md'] }))

    const res = sync(fx, 'ashton-price')

    expect(res.code).not.toBe(0)
    expect(res.out).toContain('pre_run:connector-auth-check/pre_run.py')
    expect(res.out).toContain("the seat's running image cannot run this config")
    expect(res.uploads).toHaveLength(0)
    expect(object(fx, KEY)).toBe(withoutCron)
  })

  it('NEVER CREATE: refuses a seat with no object, pointing at provisioning', () => {
    const fx = makeFixture('ashton-price', withoutCron)

    const res = sync(fx, 'ashton-price')

    expect(res.code).not.toBe(0)
    expect(res.out).toContain('provision-customer.sh')
    expect(res.uploads).toHaveLength(0)
    expect(object(fx, KEY)).toBeUndefined()
  })

  it('NEVER CREATE: a non-404 existence failure refuses rather than guesses', () => {
    const fx = makeFixture('ashton-price', withoutCron)
    seed(fx, KEY, withoutCron)

    const res = sync(fx, 'ashton-price', { FAKE_AWS_HEAD_ERROR: '1' })

    expect(res.code).not.toBe(0)
    expect(res.out).toContain('refusing to guess whether the seat is provisioned')
    expect(res.uploads).toHaveLength(0)
  })

  it('refuses a config that fails validation before touching R2', () => {
    const fx = makeFixture('ashton-price', withoutCron)
    seed(fx, KEY, 'old\n')

    const res = sync(fx, 'ashton-price', { FAKE_VALIDATOR_FAIL: 'ashton-price/customer.yaml' })

    expect(res.code).not.toBe(0)
    expect(res.out).toContain('validation failed')
    expect(res.uploads).toHaveLength(0)
    expect(object(fx, KEY)).toBe('old\n')
  })

  it.each([
    ['a trailing dash', 'ashton-'],
    ['a dot, which could name a neighbouring key', 'bad.slug'],
    ['an underscore template dir', '_template'],
    ['more than 40 characters', `a${'b'.repeat(40)}`],
  ])('uses the canonical slug pattern: rejects %s', (_label, slug) => {
    const fx = makeFixture('ashton-price', withoutCron)

    const res = sync(fx, slug)

    expect(res.code).not.toBe(0)
    expect(res.out).toContain('invalid slug')
    expect(res.uploads).toHaveLength(0)
  })
})
