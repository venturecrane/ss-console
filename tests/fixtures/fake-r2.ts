/**
 * A stand-in R2 for the config publisher and reconciler tests: an `aws` stub on
 * PATH backed by a directory, which logs every invocation so a test can prove
 * which calls wrote, plus an `npx` stub for the validator, and a throwaway
 * Ed25519 keypair for signing. No live R2, no real key.
 */
import { execFileSync } from 'node:child_process'
import { generateKeyPairSync } from 'node:crypto'
import { chmodSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

export const FAKE_AWS = `#!/usr/bin/env bash
echo "$*" >> "\${FAKE_AWS_LOG}"
BUCKET_DIR="\${FAKE_AWS_BUCKET_DIR}"
if [ "$1" = "s3api" ] && [ "$2" = "head-object" ]; then
  key=""
  while [ $# -gt 0 ]; do
    case "$1" in --key) key="$2"; shift 2 ;; *) shift ;; esac
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
  if [ -n "\${FAKE_AWS_LIST_ERROR:-}" ]; then echo "AccessDenied" >&2; exit 254; fi
  ( cd "\${BUCKET_DIR}" 2>/dev/null && find vaults -type f 2>/dev/null | tr '\\n' '\\t' )
  echo
  exit 0
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

export const FAKE_NPX = `#!/usr/bin/env bash
cfg=""
for a in "$@"; do cfg="$a"; done
echo "\${cfg}" >> "\${FAKE_NPX_LOG}"
case " \${FAKE_VALIDATOR_FAIL:-} " in
  *" \${cfg} "*) echo "validation failed" >&2; exit 1 ;;
esac
exit 0
`

/** git in a fixture repo, isolated from the parent repo's hooks and GIT_* env. */
export function git(root: string, args: string[]): string {
  return execFileSync(
    'git',
    [
      '-c',
      'user.email=t@example.com',
      '-c',
      'user.name=t',
      '-c',
      'core.hooksPath=/dev/null',
      ...args,
    ],
    {
      cwd: root,
      encoding: 'utf8',
      env: Object.fromEntries(
        Object.entries({ ...process.env, HUSKY: '0' }).filter(([k]) => !k.startsWith('GIT_'))
      ),
    }
  )
}

/** Install the aws and npx stubs into <root>/.fakebin and make the bucket dir. */
export function installFakes(root: string): { binDir: string; bucketDir: string } {
  const binDir = join(root, '.fakebin')
  mkdirSync(binDir, { recursive: true })
  writeFileSync(join(binDir, 'aws'), FAKE_AWS)
  writeFileSync(join(binDir, 'npx'), FAKE_NPX)
  chmodSync(join(binDir, 'aws'), 0o755)
  chmodSync(join(binDir, 'npx'), 0o755)
  const bucketDir = join(root, '.bucket')
  mkdirSync(bucketDir, { recursive: true })
  return { binDir, bucketDir }
}

/** A throwaway signing keypair; the public half is written to <root>/test-seat-spec.pub. */
export function testSigningEnv(root: string): Record<string, string> {
  const { privateKey, publicKey } = generateKeyPairSync('ed25519')
  const pub = join(root, 'test-seat-spec.pub')
  writeFileSync(pub, publicKey.export({ format: 'pem', type: 'spki' }))
  return {
    SEAT_SPEC_SIGNING_KEY: String(privateKey.export({ format: 'pem', type: 'pkcs8' })),
    SEAT_SPEC_PUBKEY: pub,
  }
}

export interface ScriptRun {
  code: number
  out: string
  awsCalls: string[]
  npxCalls: string[]
}

/** Run a bash script in a fixture with the stubs first on PATH. */
export function runScript(
  script: string,
  root: string,
  logs: { awsLog: string; npxLog: string; bucketDir: string; binDir: string },
  env: Record<string, string> = {}
): ScriptRun {
  const inherited = Object.fromEntries(
    Object.entries(process.env).filter(
      ([k]) => !k.startsWith('GIT_') && !k.startsWith('SEAT_SPEC_')
    )
  ) as Record<string, string>
  let outcome: { code: number; out: string }
  try {
    const stdout = execFileSync('bash', [script], {
      cwd: root,
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'pipe'],
      env: {
        ...inherited,
        PATH: `${logs.binDir}:${process.env.PATH ?? ''}`,
        R2_ACCESS_KEY_ID: 'test-key-id',
        R2_SECRET_ACCESS_KEY: 'test-secret',
        R2_ENDPOINT_URL: 'https://example.invalid',
        FAKE_AWS_LOG: logs.awsLog,
        FAKE_NPX_LOG: logs.npxLog,
        FAKE_AWS_BUCKET_DIR: logs.bucketDir,
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
  return { ...outcome, awsCalls: readLog(logs.awsLog), npxCalls: readLog(logs.npxLog) }
}

/** Writes only: `s3 cp <local> s3://...`. A `s3 cp s3://... <local>` is a read. */
export const uploads = (calls: string[]): string[] =>
  calls.filter((c) => c.startsWith('s3 cp ') && !c.startsWith('s3 cp s3://'))

/** Every aws call that could change the bucket. */
export const writes = (calls: string[]): string[] =>
  calls.filter(
    (c) =>
      uploads([c]).length > 0 ||
      /^s3 (rm|mv|sync) /.test(c) ||
      /^s3api (put-object|delete-object|delete-objects|copy-object) /.test(c)
  )
