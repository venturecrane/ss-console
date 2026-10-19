/**
 * scripts/ci-publish-skills.sh: a merge publishes a CANDIDATE skills release
 * and changes nothing on any seat (fleet release, 2026-10-19).
 *
 * The aws CLI is a stub on PATH backed by a directory standing in for the
 * bucket, so the assertions are about real keys and real bytes:
 *   - the manifest lands at fleet/skills/<yyyymmddHHMM>-<sha8>.json and every
 *     object it names at fleet/skills/objects/<sha256>;
 *   - NOTHING is written under vaults/ (no seat pointer: only `fleet release
 *     --skills` points a seat at a version). FALSIFIER: make the script write a
 *     pointer and the vaults assertion goes red;
 *   - a push range that touches no skill publishes nothing.
 */
import { execFileSync } from 'node:child_process'
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterEach, describe, expect, it } from 'vitest'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const SCRIPT = join(REPO_ROOT, 'scripts', 'ci-publish-skills.sh')

const FAKE_AWS = `#!/usr/bin/env bash
echo "$*" >> "\${FAKE_AWS_LOG}"
B="\${FAKE_AWS_BUCKET_DIR}"
if [ "$1" = "s3" ] && [ "$2" = "ls" ]; then
  p="\${3#s3://}"; p="\${p#*/}"
  [ -d "$B/$p" ] || exit 1
  for f in "$B/$p"*; do [ -f "$f" ] && echo "2026-10-19 00:00:00 1 $(basename "$f")"; done
  exit 0
fi
if [ "$1" = "s3" ] && [ "$2" = "cp" ]; then
  src="$3"; dst="$4"
  case "$dst" in
    s3://*) k="\${dst#s3://}"; k="\${k#*/}"; mkdir -p "$(dirname "$B/$k")"; cp "$src" "$B/$k"; exit 0 ;;
    *) k="\${src#s3://}"; k="\${k#*/}"; [ -f "$B/$k" ] || exit 1; cp "$B/$k" "$dst"; exit 0 ;;
  esac
fi
echo "unexpected aws: $*" >&2; exit 99
`

const ACTION_CLASSES = `
import enum
class ActionClass(str, enum.Enum):
    READ = "read"
    INTERNAL_WRITE = "internal_write"
BANNED_TOOLS: frozenset[str] = frozenset({"terminal"})
_RAW_TOOL_ACTION_CLASS_MAP: dict = {"email_search": ActionClass.READ}
`

const dirs: string[] = []
afterEach(() => {
  while (dirs.length) rmSync(dirs.pop()!, { recursive: true, force: true })
})

function cleanEnv(): Record<string, string> {
  return Object.fromEntries(
    Object.entries(process.env).filter(
      ([k]) => !k.startsWith('GIT_') && !k.startsWith('R2_') && !k.startsWith('CLOUDFLARE_')
    )
  ) as Record<string, string>
}

function head(): string {
  return execFileSync('git', ['-C', REPO_ROOT, 'rev-parse', 'HEAD'], {
    env: cleanEnv(),
    encoding: 'utf8',
  }).trim()
}

function run(extra: Record<string, string>): { code: number; out: string; bucket: string } {
  const root = mkdtempSync(join(tmpdir(), 'skills-publish-'))
  dirs.push(root)
  const bin = join(root, 'bin')
  const bucket = join(root, 'bucket')
  mkdirSync(bin)
  mkdirSync(bucket)
  writeFileSync(join(bin, 'aws'), FAKE_AWS)
  chmodSync(join(bin, 'aws'), 0o755)
  writeFileSync(join(root, 'ac.py'), ACTION_CLASSES)
  const env = {
    ...cleanEnv(),
    PATH: `${bin}:${process.env.PATH ?? ''}`,
    FAKE_AWS_LOG: join(root, 'aws.log'),
    FAKE_AWS_BUCKET_DIR: bucket,
    R2_ACCESS_KEY_ID: 'k',
    R2_SECRET_ACCESS_KEY: 's',
    R2_ENDPOINT_URL: 'https://example.invalid',
    OVERLAY_ACTION_CLASSES_FILE: join(root, 'ac.py'),
    AFTER_SHA: head(),
    ...extra,
  }
  try {
    const out = execFileSync('bash', [SCRIPT], {
      env,
      encoding: 'utf8',
      cwd: REPO_ROOT,
      timeout: 120_000,
    })
    return { code: 0, out, bucket }
  } catch (err) {
    const e = err as { status?: number; stdout?: string; stderr?: string }
    return { code: e.status ?? 1, out: `${e.stdout ?? ''}${e.stderr ?? ''}`, bucket }
  }
}

describe('ci-publish-skills', () => {
  it('publishes the manifest and its objects, and writes no seat pointer', () => {
    const res = run({ FORCE: '1' })
    expect(res.code, res.out).toBe(0)
    const versions = readdirSync(join(res.bucket, 'fleet', 'skills')).filter((f) =>
      f.endsWith('.json')
    )
    expect(versions).toHaveLength(1)
    expect(versions[0]).toMatch(new RegExp(`^[0-9]{12}-${head().slice(0, 8)}\\.json$`))
    const manifest = JSON.parse(
      readFileSync(join(res.bucket, 'fleet', 'skills', versions[0]), 'utf8')
    ) as {
      files: Record<string, { sha256: string }>
      needs: Record<string, unknown>
      pins: Record<string, unknown>
    }

    expect(Object.keys(manifest.files).length).toBeGreaterThan(50)
    for (const entry of Object.values(manifest.files)) {
      expect(existsSync(join(res.bucket, 'fleet', 'skills', 'objects', entry.sha256))).toBe(true)
    }
    expect(manifest.needs['smd-note']).toBeDefined()
    expect(typeof manifest.pins['smd-note']).toBe('string')
    expect(existsSync(join(res.bucket, 'vaults'))).toBe(false)
    expect(res.out).toContain('candidate only')
  }, 90_000)

  it('a push range that touches no skill publishes nothing', () => {
    const sha = head()
    const res = run({ BEFORE_SHA: sha, AFTER_SHA: sha })
    expect(res.code).toBe(0)
    expect(res.out).toContain('no skills release to publish')
    expect(existsSync(join(res.bucket, 'fleet'))).toBe(false)
  }, 30_000)
})
