#!/usr/bin/env tsx
/**
 * Project a customer.yaml into the portal `customer_configs` D1 row (ADR 0012).
 *
 * This is the repeatable, git-sourced projection the portal read replica is
 * built from — NOT a hand-seed (hand-edited customer_configs rows are forbidden
 * by ADR 0012). It reuses the canonical mapper at
 * src/lib/portal/customer-config-projection.ts (the future CI pipeline must
 * import the same mapper), validates with the canonical schema validator, and
 * emits an idempotent .sql file applied via `wrangler d1 execute --file`.
 *
 * Usage:
 *   npx tsx scripts/project-customer-config.ts <slug> <entity_id> \
 *     [--org-id=<org>] [--actor=<email>] [--out=<path>] [--apply-local]
 *
 * It NEVER writes to remote D1. For the gated prod apply, run by hand after a
 * local dry-run:
 *   npx wrangler d1 execute ss-console-db --remote --file=<out>
 *
 * Exit codes:
 *   0 — SQL emitted (and applied to local D1 when --apply-local)
 *   1 — schema validation errors
 *   2 — file not readable / not parseable YAML
 *   3 — git provenance guard failed (dirty tree or file never committed)
 */

import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parse as parseYaml } from 'yaml'

import { validate } from '../src/lib/operator/customer-yaml'
import { validateRoutineGrid, type RoutineGrid } from '../src/lib/operator/routine-grid'
import {
  buildProjectionSql,
  projectCustomerYamlToConfigRow,
} from '../src/lib/portal/customer-config-projection'
import { scheduleChangedAt, type Revision } from '../src/lib/portal/schedule-change'
import { ORG_ID } from '../src/lib/constants'

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')

function fail(code: number, message: string): never {
  process.stderr.write(`${message}\n`)
  process.exit(code)
}

interface Args {
  slug: string
  entityId: string
  orgId: string
  actor: string
  syncedBy: 'manual' | 'ci'
  out: string
  applyLocal: boolean
}

function parseArgs(argv: string[]): Args {
  const positional: string[] = []
  const flags = new Map<string, string>()
  let applyLocal = false
  for (const arg of argv) {
    if (arg === '--apply-local') applyLocal = true
    else if (arg.startsWith('--')) {
      const [k, v] = arg.slice(2).split('=')
      flags.set(k, v ?? '')
    } else positional.push(arg)
  }
  const [slug, entityId] = positional
  if (!slug || !entityId) {
    fail(
      2,
      'Usage: project-customer-config.ts <slug> <entity_id> [--org-id=] [--actor=] [--synced-by=manual|ci] [--out=] [--apply-local]'
    )
  }
  return {
    slug,
    entityId,
    orgId: flags.get('org-id') || ORG_ID,
    actor: flags.get('actor') || 'scott@smd.services',
    syncedBy: flags.get('synced-by') === 'ci' ? 'ci' : 'manual',
    out: flags.get('out') || join(REPO_ROOT, 'scripts/.generated', `project-${slug}.sql`),
    applyLocal,
  }
}

/**
 * ADR 0012 provenance guard: the bytes we project must be the committed bytes.
 * A dirty worktree (uncommitted edits) or an untracked file would serialize
 * content that no commit names — silent provenance corruption. Hard-fail.
 */
function assertCommittedClean(relPath: string): void {
  const dirty = execFileSync('git', ['status', '--porcelain', '--', relPath], {
    cwd: REPO_ROOT,
    encoding: 'utf-8',
  }).trim()
  if (dirty) {
    fail(
      3,
      `Refusing to project: ${relPath} has uncommitted changes. Commit it first so the projected bytes are the committed bytes.`
    )
  }
}

/**
 * Resolve the customer.yaml commit SHA that stamps the row's git_sha. Guards
 * clean-tree first (via assertCommittedClean).
 *
 * Note the row's git_sha is ALWAYS customer.yaml's SHA, even when only the
 * routine-grid.yaml changed. The grid is a sibling artifact projected into the
 * same row, but customer.yaml stays the row's provenance anchor: it is the
 * config's identity and the file the CI landback check pins against (it reads
 * the stamped SHA back out of the generated SQL, not by recomputing per file).
 * The grid's own committed state is protected separately by assertCommittedClean
 * on its path, so a grid-only change still projects only committed bytes.
 */
function resolveGitSha(relPath: string): string {
  assertCommittedClean(relPath)
  const sha = execFileSync('git', ['log', '-1', '--format=%H', '--', relPath], {
    cwd: REPO_ROOT,
    encoding: 'utf-8',
  }).trim()
  if (!sha) fail(3, `Refusing to project: ${relPath} has no commit history.`)
  return sha
}

/**
 * When the seat's schedule last changed (src/lib/portal/schedule-change.ts):
 * every committed revision of customer.yaml, newest first, reduced to the
 * commit time of the newest schedule change. Needs full history (the deploy
 * and reconcile checkouts use fetch-depth: 0); a shallow clone sees only its
 * oldest reachable revision and so answers no earlier than the truth, which
 * suspends grading rather than inventing misses. Returned in UTC.
 */
function resolveScheduleChangedAt(relPath: string): string | null {
  const log = execFileSync('git', ['log', '--format=%H %cI', '--', relPath], {
    cwd: REPO_ROOT,
    encoding: 'utf-8',
  }).trim()
  const revisions: Revision[] = []
  for (const line of log ? log.split('\n') : []) {
    const [sha, at] = line.split(' ')
    let text: string
    try {
      text = execFileSync('git', ['show', `${sha}:${relPath}`], {
        cwd: REPO_ROOT,
        encoding: 'utf-8',
        stdio: ['ignore', 'pipe', 'ignore'],
      })
    } catch {
      continue // the commit deleted the file at this path
    }
    revisions.push({ committedAt: new Date(at).toISOString(), text })
  }
  return scheduleChangedAt(revisions)
}

/**
 * Load + validate the seat's routine-grid.yaml when it exists next to
 * customer.yaml (ADR 0075). Absent → null (the seat has no grid). Present →
 * clean-tree guarded, parsed, and HARD-VALIDATED: CI must never project a
 * malformed grid (exit 1), so an invalid grid fails the projection outright
 * rather than degrading — the read side's fail-soft null is a runtime safety
 * net, not a license to project garbage.
 */
function loadRoutineGrid(slug: string): RoutineGrid | null {
  const relPath = `operator/customers/${slug}/routine-grid.yaml`
  const absPath = join(REPO_ROOT, relPath)
  if (!existsSync(absPath)) return null
  assertCommittedClean(relPath)
  let parsed: unknown
  try {
    parsed = parseYaml(readFileSync(absPath, 'utf-8'))
  } catch (err) {
    fail(2, `Could not parse ${relPath}: ${err instanceof Error ? err.message : String(err)}`)
  }
  const result = validateRoutineGrid(parsed)
  if (!result.ok) {
    const lines = result.errors.map((e) => `  [${e.code}] ${e.path}: ${e.message}`).join('\n')
    fail(1, `${relPath} failed validation:\n${lines}`)
  }
  return result.value
}

function main(): void {
  const args = parseArgs(process.argv.slice(2))
  const relPath = `operator/customers/${args.slug}/customer.yaml`
  const absPath = join(REPO_ROOT, relPath)
  if (!existsSync(absPath)) fail(2, `Not found: ${relPath}`)

  const gitSha = resolveGitSha(relPath)

  let parsed: unknown
  try {
    parsed = parseYaml(readFileSync(absPath, 'utf-8'))
  } catch (err) {
    fail(2, `Could not parse YAML: ${err instanceof Error ? err.message : String(err)}`)
  }

  const result = validate(parsed)
  if (!result.ok) {
    const lines = result.errors.map((e) => `  [${e.code}] ${e.path}: ${e.message}`).join('\n')
    fail(1, `customer.yaml failed validation:\n${lines}`)
  }

  const routineGrid = loadRoutineGrid(args.slug)

  const row = projectCustomerYamlToConfigRow(
    result.value,
    {
      entityId: args.entityId,
      orgId: args.orgId,
      gitSha,
      syncedAt: new Date().toISOString(),
      scheduleChangedAt: resolveScheduleChangedAt(relPath),
    },
    routineGrid
  )

  const sql = buildProjectionSql(row, args.actor, args.syncedBy)
  mkdirSync(dirname(args.out), { recursive: true })
  writeFileSync(args.out, sql, 'utf-8')

  process.stdout.write(`Wrote projection SQL → ${args.out}\n`)
  process.stdout.write(
    `  customer_slug=${row.customer_slug} entity_id=${row.entity_id} git_sha=${gitSha}\n`
  )
  process.stdout.write(`  schedule_changed_at=${row.schedule_changed_at ?? 'unknown'}\n`)
  process.stdout.write(
    `  routine_grid: ${routineGrid ? `${routineGrid.rows.length} rows` : 'none'}\n\n`
  )

  if (args.applyLocal) {
    process.stdout.write('Applying to LOCAL D1 (dry-run)...\n')
    execFileSync(
      'npx',
      ['wrangler', 'd1', 'execute', 'ss-console-db', '--local', `--file=${args.out}`],
      {
        cwd: REPO_ROOT,
        stdio: 'inherit',
      }
    )
  } else {
    process.stdout.write('Next:\n')
    process.stdout.write(
      `  Dry-run (local):  npx wrangler d1 execute ss-console-db --local  --file=${args.out}\n`
    )
    process.stdout.write(
      `  Apply (PROD):     npx wrangler d1 execute ss-console-db --remote --file=${args.out}\n`
    )
  }
}

main()
