#!/usr/bin/env tsx
/**
 * Compile, sign and verify a seat spec (Wave 2, W4). The only producer of
 * fleet/specs/<slug>/<sha256>.json and vaults/<slug>/spec-pointer.json; the R2
 * reads and writes are the callers' (scripts/ci-publish-customer-configs.sh,
 * the one writer, and scripts/ci-reconcile-r2-customer-configs.sh, which only
 * reads). The shape and the refusal rules are in scripts/lib/seat-spec.ts and
 * scripts/lib/seat-spec-compile.ts.
 *
 * Usage:
 *   compile-seat-spec.ts lanes --slug <slug>
 *       prints "<lane> <vault key>" for every lane's firm-input manifest, so the
 *       shell fetches exactly the objects the spec pins.
 *   compile-seat-spec.ts compile --slug <slug> --out <dir> [--repo <dir>]
 *       [--config <customer.yaml>] [--compiled-from <sha>]
 *       [--image-manifest <f>] [--image-capabilities <f>] [--image-lane-inputs <f>]
 *       [--skills-pointer <f>] [--skills-manifest <f>] [--published <f>]
 *       [--lane-pin <lane>=<f> ...] [--current-spec <f>] [--sign] [--provisioning]
 *       writes <dir>/spec.json (the canonical bytes) and, with --sign and
 *       SEAT_SPEC_SIGNING_KEY set, <dir>/pointer.json. A missing input file is
 *       "not available"; an unparseable one is "unreadable". --provisioning
 *       records the requirements without checking them: provisioning ships
 *       the image in the same run, so there is no running image to check.
 *   compile-seat-spec.ts verify --pointer <f> --spec <f> [--pubkey <f>]
 *
 * Exit codes (compile): 0 compiled; 10 unchanged (equal to --current-spec but
 * for compiled_from, so republishing would change nothing on the seat);
 * 1 refused for the image (nothing may publish); 3 refused for a lane (the
 * spec may not publish, the customer.yaml may); 2 usage or I/O error.
 * Exit codes (verify): 0 verifies, 1 does not, 2 usage.
 *
 * The signing key is read from the environment and never printed. Firm inputs
 * are hashed, never copied: only their digests reach the spec.
 */

import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parseArgs } from 'node:util'

import { LANE_INPUTS, readLaneSchemaDigests, sha256Hex } from './lib/seat-spec'
import {
  compileSpec,
  pointerProblem,
  sameSpec,
  signedPointer,
  specBytes,
  specKey,
  type CompileInputs,
  type MaybeJson,
} from './lib/seat-spec-compile'

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
// The public key a signing key must match and a pointer must verify against.
// SEAT_SPEC_PUBKEY names another file only for the tests' throwaway keys; a
// pointer signed by any key but the committed one is refused on every seat.
const PUBKEY =
  process.env.SEAT_SPEC_PUBKEY || join(REPO_ROOT, 'operator', 'templates', 'seat-spec.pub')
const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$/
const SHA_RE = /^[0-9a-f]{40}$/

function die(code: number, message: string): never {
  process.stderr.write(`${message}\n`)
  process.exit(code)
}

function readJson(path: string | undefined): MaybeJson {
  if (!path || !existsSync(path)) return undefined
  try {
    return JSON.parse(readFileSync(path, 'utf8')) as unknown
  } catch (err) {
    return err instanceof Error ? err : new Error(String(err))
  }
}

function readOptional(path: string | undefined): Buffer | undefined {
  return path && existsSync(path) ? readFileSync(path) : undefined
}

function lanePins(values: string[]): Record<string, Buffer> {
  const out: Record<string, Buffer> = {}
  for (const v of values) {
    const [lane, path] = v.split('=', 2)
    if (!lane || !path || !(lane in LANE_INPUTS))
      die(2, `--lane-pin ${v}: expected <lane>=<file> for a known lane`)
    const bytes = readOptional(path)
    if (bytes) out[lane] = bytes
  }
  return out
}

function headSha(repo: string): string {
  return execFileSync('git', ['-C', repo, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim()
}

function inputsFrom(v: Record<string, string | string[] | boolean | undefined>): CompileInputs {
  const slug = String(v.slug ?? '')
  if (!SLUG_RE.test(slug)) die(2, `--slug ${slug}: not a seat slug`)
  const repo = resolve(String(v.repo ?? REPO_ROOT))
  const cfg = String(v.config ?? join(repo, 'operator', 'customers', slug, 'customer.yaml'))
  if (!existsSync(cfg)) die(2, `no customer.yaml at ${cfg}`)
  const compiledFrom = String(v['compiled-from'] ?? headSha(repo))
  if (!SHA_RE.test(compiledFrom)) die(2, `--compiled-from ${compiledFrom}: not a 40-hex commit`)
  const str = (k: string): string | undefined => {
    const value = v[k]
    return typeof value === 'string' ? value : undefined
  }
  return {
    slug,
    compiledFrom,
    customerYaml: readFileSync(cfg),
    laneProofs: readOptional(join(repo, 'operator', 'customers', slug, 'lane-proofs.yaml')) ?? null,
    lanePins: lanePins((v['lane-pin'] as string[] | undefined) ?? []),
    laneSchemaDigests: readLaneSchemaDigests(repo),
    imageManifest: readJson(str('image-manifest')),
    imageCapabilities: readJson(str('image-capabilities')),
    imageLaneInputs: readJson(str('image-lane-inputs')),
    skillsPointer: readJson(str('skills-pointer')),
    skillsManifest: readJson(str('skills-manifest')),
    published: readOptional(str('published')),
  }
}

function report(lines: string[]): void {
  for (const line of lines) process.stderr.write(`${line}\n`)
}

const str = { type: 'string' } as const
const COMPILE_OPTIONS = {
  slug: str,
  out: str,
  repo: str,
  config: str,
  'compiled-from': str,
  'image-manifest': str,
  'image-capabilities': str,
  'image-lane-inputs': str,
  'skills-pointer': str,
  'skills-manifest': str,
  published: str,
  'current-spec': str,
  'lane-pin': { type: 'string', multiple: true },
  sign: { type: 'boolean' },
  provisioning: { type: 'boolean' },
} as const

function compile(argv: string[]): number {
  const { values } = parseArgs({ args: argv, options: COMPILE_OPTIONS })
  const out = String(values.out ?? '')
  if (!out) die(2, '--out is required')
  const result = compileSpec({ ...inputsFrom(values), unchecked: values.provisioning === true })
  for (const note of result.notes) process.stdout.write(`${note}\n`)
  if (result.imageRefusals.length > 0) {
    report(result.imageRefusals)
    return 1
  }
  if (result.laneRefusals.length > 0) {
    report([
      'the spec is refused (the customer.yaml is not):',
      ...result.laneRefusals.map((l) => `  ${l}`),
    ])
    return 3
  }
  const bytes = specBytes(result.spec)
  const sha = sha256Hex(bytes)
  process.stdout.write(`spec_sha256=${sha}\nspec_key=${specKey(String(result.spec.slug), sha)}\n`)
  const current = readJson(
    typeof values['current-spec'] === 'string' ? values['current-spec'] : undefined
  )
  if (sameSpec(current, result.spec)) {
    process.stdout.write('status=unchanged\n')
    return 10
  }
  mkdirSync(out, { recursive: true })
  writeFileSync(join(out, 'spec.json'), bytes)
  const key = process.env.SEAT_SPEC_SIGNING_KEY
  if (values.sign && key) {
    const pointer = signedPointer(result.spec, key, readFileSync(PUBKEY, 'utf8'))
    writeFileSync(join(out, 'pointer.json'), `${JSON.stringify(pointer, null, 1)}\n`)
    process.stdout.write(`status=signed key_id=${String(pointer.key_id)}\n`)
  } else {
    process.stdout.write(
      `status=unsigned${values.sign ? ' (SEAT_SPEC_SIGNING_KEY is not set)' : ''}\n`
    )
  }
  return 0
}

function verifyCmd(argv: string[]): number {
  const { values } = parseArgs({
    args: argv,
    options: { pointer: { type: 'string' }, spec: { type: 'string' }, pubkey: { type: 'string' } },
  })
  if (!values.pointer || !values.spec) die(2, 'verify needs --pointer and --spec')
  const pointer = readJson(values.pointer)
  const bytes = readOptional(values.spec)
  if (!bytes) die(1, `no spec at ${values.spec}`)
  const problem = pointerProblem(pointer, bytes, readFileSync(values.pubkey ?? PUBKEY, 'utf8'))
  if (problem) die(1, problem)
  process.stdout.write('verified\n')
  return 0
}

function main(argv: string[]): number {
  const [cmd, ...rest] = argv
  if (cmd === 'compile') return compile(rest)
  if (cmd === 'verify') return verifyCmd(rest)
  if (cmd === 'lanes') {
    const { values } = parseArgs({ args: rest, options: { slug: { type: 'string' } } })
    const slug = String(values.slug ?? '')
    if (!SLUG_RE.test(slug)) die(2, `--slug ${slug}: not a seat slug`)
    for (const [lane, { manifest }] of Object.entries(LANE_INPUTS)) {
      process.stdout.write(`${lane} vaults/${slug}/${manifest}\n`)
    }
    return 0
  }
  die(2, 'usage: compile-seat-spec.ts lanes|compile|verify ... (see the header)')
}

// An exception is an error (exit 2), never a refusal: exit 1 means "the image
// cannot run this config" to every caller, and a crash must not read as that.
let code: number
try {
  code = main(process.argv.slice(2))
} catch (err) {
  process.stderr.write(`compile-seat-spec: ${err instanceof Error ? err.message : String(err)}\n`)
  code = 2
}
process.exit(code)
