/**
 * operator/bin/lib/publish-firm-inputs.sh, DRIVEN, not read (2026-11-05).
 *
 * The one writer of a firm's lane inputs to R2: provisioning's stage fragments
 * and the engagements repo's merge workflow both run it. Each run here uses a
 * fake `aws` on PATH that records its argv and serves a tmp dir as the bucket,
 * and a fake runner checkout whose `jobs.<lane>.firm` validator refuses any
 * marker containing BAD and any pinned input missing from the staged tree.
 * The properties that matter, each with a run that fails if it broke:
 *
 *  - refused inputs publish nothing (no sync, no cp up);
 *  - a key outside vaults/<slug>/<lane>/[A-Za-z0-9._/-]+ refuses the whole
 *    publish, while a file the lane's filters drop is never judged;
 *  - drafting takes pinned inputs missing from the checkout out of R2 BEFORE
 *    the --delete sync (a publisher that skipped it would delete the firm's
 *    exemplars);
 *  - a read-back that differs from what was published exits 3;
 *  - a runner with no validator for the lane exits 4, never 1.
 *
 * Needs `uv` (the validator runs under `uv run --with pyyaml`), like
 * stage-web-connectors.test.ts; skipped without it.
 */
import { execFileSync, execSync, spawnSync } from 'node:child_process'
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
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterAll, describe, expect, it } from 'vitest'

const PUBLISHER = fileURLToPath(
  new URL('../operator/bin/lib/publish-firm-inputs.sh', import.meta.url)
)
const LIB = dirname(PUBLISHER)

function hasUv(): boolean {
  try {
    execSync('uv --version', { stdio: 'ignore' })
    return true
  } catch {
    return false
  }
}

const scratch: string[] = []
afterAll(() => scratch.forEach((d) => rmSync(d, { recursive: true, force: true })))

// The bucket is <root>/bucket/<key>. `s3 sync` copies every file (the real
// filters are aws's job; the publisher's own key list is what is under test).
const FAKE_AWS = `#!/usr/bin/env bash
echo "$*" >> "$FAKE_AWS_LOG"
b="$FAKE_BUCKET_DIR"
[ "\${FAKE_AWS_DOWN:-}" = "1" ] && exit 1
case "$1 $2" in
  "s3 cp")
    src="$3"; dst="$4"
    if [[ "$src" == s3://* ]]; then
      k="\${src#s3://*/}"; [ -f "$b/$k" ] || exit 1
      if [ "\${FAKE_AWS_CORRUPT:-}" = "1" ]; then echo corrupted > "$dst"; else cp "$b/$k" "$dst"; fi
    else
      k="\${dst#s3://*/}"; mkdir -p "$(dirname "$b/$k")"; cp "$src" "$b/$k"
    fi ;;
  "s3 sync")
    src="$3"; k="\${4#s3://*/}"; mkdir -p "$b/$k"; cp -R "$src". "$b/$k" ;;
  "s3api head-object") echo '"0123456789abcdef"' ;;
  *) exit 2 ;;
esac
`

// A validator in the runner's CLI shape: exit 0 OK, 1 problems.
const FAKE_FIRM = `import sys, pathlib, yaml
root = pathlib.Path(sys.argv[1])
marker = next(root.glob("*-firm.yaml"))
text = marker.read_text()
if "BAD" in text:
    print("problem: the marker says BAD")
    sys.exit(1)
for rel in ((yaml.safe_load(text) or {}).get("inputs") or {}):
    if not (root / rel).is_file():
        print(f"pinned input missing: {rel}")
        sys.exit(1)
print("ok")
`

interface Scene {
  root: string
  src: string
  runner: string
  bucket: string
  log: string
}

function scene(lane: string, files: Record<string, string>, validatorLanes = [lane]): Scene {
  const root = mkdtempSync(join(tmpdir(), 'publish-firm-inputs-'))
  scratch.push(root)
  const bin = join(root, 'bin')
  mkdirSync(bin)
  writeFileSync(join(bin, 'aws'), FAKE_AWS)
  chmodSync(join(bin, 'aws'), 0o755)
  const runner = join(root, 'runner')
  const pkg = join(runner, 'operator/runners/jobs/jobs')
  mkdirSync(pkg, { recursive: true })
  writeFileSync(join(pkg, '__init__.py'), '')
  for (const l of validatorLanes) {
    mkdirSync(join(pkg, l), { recursive: true })
    writeFileSync(join(pkg, l, '__init__.py'), '')
    writeFileSync(join(pkg, l, 'firm.py'), FAKE_FIRM)
  }
  const src = join(root, 'src', lane)
  for (const [rel, body] of Object.entries(files)) {
    mkdirSync(dirname(join(src, rel)), { recursive: true })
    writeFileSync(join(src, rel), body)
  }
  const bucket = join(root, 'bucket')
  mkdirSync(bucket)
  return { root, src, runner, bucket, log: join(root, 'aws.log') }
}

function publish(
  s: Scene,
  lane: string,
  env: Record<string, string> = {}
): { code: number; out: string; aws: string[] } {
  const r = spawnSync('bash', [PUBLISHER, 'acme', lane, s.src, '--runner-checkout', s.runner], {
    encoding: 'utf8',
    env: {
      ...process.env,
      PATH: `${join(s.root, 'bin')}:${process.env.PATH ?? ''}`,
      FAKE_AWS_LOG: s.log,
      FAKE_BUCKET_DIR: s.bucket,
      R2_ACCESS_KEY_ID: 'id',
      R2_SECRET_ACCESS_KEY: 'secret',
      R2_ENDPOINT_URL: 'https://r2.invalid',
      R2_BUCKET_CONFIG: 'cfg',
      ...env,
    },
  })
  const code = r.status ?? -1
  const out = `${r.stdout}${r.stderr}`
  const aws = existsSync(s.log)
    ? readFileSync(s.log, 'utf8').trim().split('\n').filter(Boolean)
    : []
  return { code, out, aws }
}

const writes = (aws: string[]): string[] =>
  aws.filter((l) => l.startsWith('s3 sync') || /^s3 cp \S+ s3:\/\//.test(l))

describe.skipIf(!hasUv())('publish-firm-inputs.sh', () => {
  it('publishes valid inputs with --delete and the lane filters, and reads the marker back', () => {
    const s = scene('demand', { 'demand-firm.yaml': 'inputs: {}\n', 'prompts/a.md': 'x\n' })
    const r = publish(s, 'demand')
    expect(r.code).toBe(0)
    expect(r.out).toContain('published acme/demand etag=01234567')
    const sync = r.aws.find((l) => l.startsWith('s3 sync'))
    expect(sync).toContain(
      's3://cfg/vaults/acme/demand/ --delete --exclude eval/* --exclude request-brief-*'
    )
    expect(
      r.aws.some((l) => l.startsWith('s3 cp s3://cfg/vaults/acme/demand/demand-firm.yaml'))
    ).toBe(true)
  })

  it('refused inputs publish nothing', () => {
    const s = scene('records', { 'records-firm.yaml': 'BAD: 1\n' })
    const r = publish(s, 'records')
    expect(r.code).toBe(1)
    expect(r.out).toContain('problem: the marker says BAD')
    expect(r.out).toContain('nothing was written')
    expect(writes(r.aws)).toEqual([])
  })

  it('with --validator-log the validator lines go to the file and only the first is echoed', () => {
    const s = scene('records', { 'records-firm.yaml': 'BAD: 1\n' })
    const vlog = join(s.root, 'validator.log')
    const out = (() => {
      try {
        execFileSync(
          'bash',
          [
            PUBLISHER,
            'acme',
            'records',
            s.src,
            '--runner-checkout',
            s.runner,
            '--validator-log',
            vlog,
          ],
          {
            encoding: 'utf8',
            stdio: ['ignore', 'pipe', 'pipe'],
            env: {
              ...process.env,
              PATH: `${join(s.root, 'bin')}:${process.env.PATH ?? ''}`,
              FAKE_AWS_LOG: s.log,
              FAKE_BUCKET_DIR: s.bucket,
              R2_ACCESS_KEY_ID: 'id',
              R2_SECRET_ACCESS_KEY: 'secret',
              R2_ENDPOINT_URL: 'https://r2.invalid',
            },
          }
        )
        return { code: 0, text: '' }
      } catch (e) {
        const err = e as { status: number; stderr: string }
        return { code: err.status, text: err.stderr }
      }
    })()
    expect(out.code).toBe(1)
    expect(readFileSync(vlog, 'utf8')).toContain('problem: the marker says BAD')
    expect(out.text).toContain('validator exit 1: problem: the marker says BAD')
  })

  it('a key outside the lane key space refuses the whole publish', () => {
    const s = scene('demand', { 'demand-firm.yaml': 'inputs: {}\n', 'stray name.yaml': 'x\n' })
    const r = publish(s, 'demand')
    expect(r.code).toBe(1)
    expect(r.out).toContain(
      'refusing to write R2 key outside vaults/acme/demand/: vaults/acme/demand/stray name.yaml'
    )
    expect(writes(r.aws)).toEqual([])
  })

  it('a file the lane filters drop is never judged as a key', () => {
    // litigation leaves out *.md, so a note with a space in its name is not uploaded and not refused.
    const s = scene('litigation', { 'litigation-firm.yaml': 'inputs: {}\n', 'a note.md': 'x\n' })
    const r = publish(s, 'litigation')
    expect(r.code).toBe(0)
    expect(r.out).toContain('publishing 1 file(s)')
  })

  it('drafting backfills pinned inputs from R2 before the --delete sync', () => {
    const s = scene('drafting', {
      'drafting-firm.yaml': 'inputs:\n  exemplars/brief-1.docx: abc\n',
    })
    mkdirSync(join(s.bucket, 'vaults/acme/drafting/exemplars'), { recursive: true })
    writeFileSync(join(s.bucket, 'vaults/acme/drafting/exemplars/brief-1.docx'), 'exemplar bytes')
    const r = publish(s, 'drafting')
    expect(r.code).toBe(0)
    const backfill = r.aws.findIndex((l) =>
      l.startsWith('s3 cp s3://cfg/vaults/acme/drafting/exemplars/brief-1.docx')
    )
    const sync = r.aws.findIndex((l) => l.startsWith('s3 sync'))
    expect(backfill).toBeGreaterThan(-1)
    expect(sync).toBeGreaterThan(backfill)
    expect(r.aws[sync]).toContain('--delete')
    // The exemplar survives the delete-sync: it was in the synced tree.
    expect(
      readFileSync(join(s.bucket, 'vaults/acme/drafting/exemplars/brief-1.docx'), 'utf8')
    ).toBe('exemplar bytes')
    expect(r.out).toContain('publishing 2 file(s)')
  })

  it('a pinned drafting input in neither the checkout nor R2 is refused, and nothing is written', () => {
    const s = scene('drafting', { 'drafting-firm.yaml': 'inputs:\n  exemplars/gone.docx: abc\n' })
    const r = publish(s, 'drafting')
    expect(r.code).toBe(1)
    expect(r.out).toContain('in neither the checkout nor R2')
    expect(writes(r.aws)).toEqual([])
  })

  it('a read-back that differs from what was published exits 3', () => {
    const s = scene('demand', { 'demand-firm.yaml': 'inputs: {}\n' })
    const r = publish(s, 'demand', { FAKE_AWS_CORRUPT: '1' })
    expect(r.code).toBe(3)
    expect(r.out).toContain('differs from what was published')
  })

  it('R2 unreachable exits 3 (after a valid judgement)', () => {
    const s = scene('records', { 'records-firm.yaml': 'inputs: {}\n' })
    const r = publish(s, 'records', { FAKE_AWS_DOWN: '1' })
    expect(r.code).toBe(3)
  })

  it('a runner with no validator for the lane exits 4 and writes nothing', () => {
    const s = scene('casefile', { 'casefile-firm.yaml': 'inputs: {}\n' }, ['demand'])
    const r = publish(s, 'casefile')
    expect(r.code).toBe(4)
    expect(r.out).toContain('has no casefile validator')
    expect(writes(r.aws)).toEqual([])
  })

  it('usage errors exit 2', () => {
    const s = scene('demand', { 'demand-firm.yaml': 'inputs: {}\n' })
    expect(publish({ ...s, src: join(s.root, 'nowhere') }, 'demand').code).toBe(2)
    expect(publish(s, 'not-a-lane').code).toBe(2)
  })
})

describe('provisioning and the merge workflow share the one publisher', () => {
  for (const lane of ['demand', 'drafting', 'litigation', 'casefile', 'records']) {
    it(`stage-${lane}-inputs.sh publishes through it, after its engagements-main gate`, () => {
      const body = readFileSync(join(LIB, `stage-${lane}-inputs.sh`), 'utf8')
      const gate = body.indexOf('SS_ALLOW_DIVERGENT_SOURCE')
      const call = body.indexOf(`lib/publish-firm-inputs.sh" "\${SLUG}" ${lane} `)
      expect(gate).toBeGreaterThan(-1)
      expect(call).toBeGreaterThan(gate)
      expect(body).not.toContain('aws s3 sync')
      expect(body).toContain('assert_lane_not_on_main')
    })
  }
})
