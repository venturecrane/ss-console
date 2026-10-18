#!/usr/bin/env tsx
/**
 * Generate a couples seat's customer.yaml from its onboarding intake, and
 * optionally open it as a PR for review (ADR 0090 decision 4).
 *
 * Usage:
 *   npx tsx scripts/couples-seat.ts --intake <intake-id> [--local] [--hermes-ref <ref>]
 *                                   [--out <path>] [--open-pr]
 *   npx tsx scripts/couples-seat.ts --export <rows.json> ...   (offline: the four
 *                                   result sets of EXPORT_QUERIES as JSON)
 *
 * --intake reads D1 with `wrangler d1 execute ss-console-db --remote` (or
 * --local). The generated file is validated by the canonical validator before
 * it is written. It is written to operator/customers/<slug>/customer.yaml
 * unless --out names another path.
 *
 * --open-pr branches from origin/main, commits ONLY that file, pushes, and
 * opens a PR. It never merges and never enables auto-merge: a merged
 * customer.yaml publishes to R2 within a minute and the seat reads it on its
 * next boot, so a human reviews every one.
 *
 * hermes_ref: the pin every live seat agrees on (read from
 * operator/customers/<seat>/customer.yaml). When the live seats disagree the
 * script refuses and asks for --hermes-ref.
 *
 * Prints counts and paths only. Partner names and addresses go into the file,
 * not the terminal.
 */

import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parse as parseYaml } from 'yaml'
import { generateCouplesSeat } from '../src/lib/hosted-agent/couples/generate-seat'
import {
  EXPORT_QUERIES,
  INTAKE_ID_RE,
  parseSeatExport,
  type SeatExportRows,
} from '../src/lib/hosted-agent/couples/seat-export'

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const TEMPLATE = join(REPO_ROOT, 'operator/customers/_couples-template/customer.yaml')

interface Args {
  intake: string | null
  exportFile: string | null
  local: boolean
  hermesRef: string | null
  out: string | null
  openPr: boolean
}

function fail(message: string): never {
  process.stderr.write(`couples-seat: ${message}\n`)
  process.exit(1)
}

function parseArgs(argv: string[]): Args {
  const args: Args = {
    intake: null,
    exportFile: null,
    local: false,
    hermesRef: null,
    out: null,
    openPr: false,
  }
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    const next = (): string => argv[++i] ?? fail(`${a} needs a value`)
    if (a === '--intake') args.intake = next()
    else if (a === '--export') args.exportFile = next()
    else if (a === '--local') args.local = true
    else if (a === '--hermes-ref') args.hermesRef = next()
    else if (a === '--out') args.out = next()
    else if (a === '--open-pr') args.openPr = true
    else fail(`unknown argument ${a}`)
  }
  if (!args.intake === !args.exportFile) fail('pass exactly one of --intake or --export')
  if (args.intake && !INTAKE_ID_RE.test(args.intake)) fail('--intake must be an intake id (uuid)')
  return args
}

function d1(sql: string, local: boolean): unknown[] {
  const out = execFileSync(
    'npx',
    [
      'wrangler',
      'd1',
      'execute',
      'ss-console-db',
      local ? '--local' : '--remote',
      '--json',
      '--command',
      sql,
    ],
    { cwd: REPO_ROOT, encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 }
  )
  const parsed: unknown = JSON.parse(out)
  const first: unknown = Array.isArray(parsed) ? parsed[0] : null
  const results: unknown = first && typeof first === 'object' ? Reflect.get(first, 'results') : null
  return Array.isArray(results) ? results : []
}

function rowsFromD1(intake: string, local: boolean): SeatExportRows {
  return {
    instance: d1(EXPORT_QUERIES.instance(intake), local),
    people: d1(EXPORT_QUERIES.people(intake), local),
    joint: d1(EXPORT_QUERIES.joint(intake), local),
    confirmations: d1(EXPORT_QUERIES.confirmations(intake), local),
  }
}

function rowsFromFile(path: string): SeatExportRows {
  const raw: unknown = JSON.parse(readFileSync(path, 'utf8'))
  const list = (k: string): unknown[] => {
    const v: unknown = raw && typeof raw === 'object' ? Reflect.get(raw, k) : null
    return Array.isArray(v) ? v : []
  }
  return {
    instance: list('instance'),
    people: list('people'),
    joint: list('joint'),
    confirmations: list('confirmations'),
  }
}

/** The hermes_ref every live seat authors, or null when they disagree. */
function fleetHermesRef(): string | null {
  const dir = join(REPO_ROOT, 'operator/customers')
  const refs = new Set<string>()
  for (const seat of readdirSync(dir)) {
    if (seat.startsWith('_')) continue
    const file = join(dir, seat, 'customer.yaml')
    if (!existsSync(file)) continue
    const doc: unknown = parseYaml(readFileSync(file, 'utf8'))
    const ref: unknown = doc && typeof doc === 'object' ? Reflect.get(doc, 'hermes_ref') : null
    if (typeof ref === 'string') refs.add(ref)
  }
  return refs.size === 1 ? [...refs][0] : null
}

function git(args: string[]): string {
  return execFileSync('git', args, { cwd: REPO_ROOT, encoding: 'utf8' }).trim()
}

function branchName(slug: string, version: number): string {
  return `seat/${slug}-onboarding-v${version}`
}

/** Branch from a fresh origin/main BEFORE the file is written, so a regenerated
 * seat never rides on whatever the current checkout holds. */
function startBranch(branch: string): void {
  git(['fetch', 'origin', 'main'])
  git(['switch', '-c', branch, 'origin/main'])
}

function openPr(relPath: string, slug: string, version: number, warnings: string[]): void {
  const branch = branchName(slug, version)
  git(['add', '--', relPath])
  git([
    'commit',
    '-m',
    `feat(seat): ${slug} customer.yaml from couples onboarding (joint v${version})`,
  ])
  git(['push', '-u', 'origin', branch])
  const body = [
    `Generated by \`scripts/couples-seat.ts\` from couples onboarding (joint settings version ${version}).`,
    '',
    '**Review before merging. Do not auto-merge.** A merged customer.yaml publishes to R2 within a minute, and the seat reads it on its next boot.',
    '',
    ...(warnings.length > 0 ? ['Still pending:', ...warnings.map((w) => `- ${w}`), ''] : []),
    'Check: every partner email and Telegram id matches what each partner bound in onboarding; the schedules match the joint settings both partners confirmed.',
  ].join('\n')
  execFileSync(
    'gh',
    [
      'pr',
      'create',
      '--base',
      'main',
      '--head',
      branch,
      '--title',
      `feat(seat): ${slug} from couples onboarding`,
      '--body',
      body,
    ],
    { cwd: REPO_ROOT, stdio: 'inherit' }
  )
}

function main(): void {
  const args = parseArgs(process.argv.slice(2))
  const rows = args.intake ? rowsFromD1(args.intake, args.local) : rowsFromFile(args.exportFile!)
  const parsed = parseSeatExport(rows)
  if (!parsed.ok) fail(`onboarding is not complete:\n  - ${parsed.errors.join('\n  - ')}`)
  const hermesRef = args.hermesRef ?? fleetHermesRef()
  if (!hermesRef) fail('live seats disagree on hermes_ref; pass --hermes-ref <ref>')

  const result = generateCouplesSeat(readFileSync(TEMPLATE, 'utf8'), {
    ...parsed.value,
    hermesRef,
    now: new Date(),
  })
  if (!result.ok) fail(`generated config does not validate:\n  - ${result.errors.join('\n  - ')}`)

  const relPath = args.out ?? `operator/customers/${parsed.value.slug}/customer.yaml`
  if (args.openPr) startBranch(branchName(parsed.value.slug, parsed.value.jointVersion))
  const absPath = resolve(REPO_ROOT, relPath)
  mkdirSync(dirname(absPath), { recursive: true })
  writeFileSync(absPath, result.yaml)
  process.stdout.write(`wrote ${relPath} (valid)\n`)
  for (const w of result.warnings) process.stdout.write(`pending: ${w}\n`)
  if (args.openPr) openPr(relPath, parsed.value.slug, parsed.value.jointVersion, result.warnings)
}

main()
