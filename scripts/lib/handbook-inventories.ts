/**
 * Facts of the tree that handbook pages must keep naming.
 *
 * The 2026-10-21 assessment found the pages that drifted were the ones listing
 * what exists: the runbook table named 6 of 23 runbooks, the workers list missed
 * the pager, and about 30 tables were absent from the data model. Each list here
 * is read from the tree; tests/handbook-integrity.test.ts fails when a page that
 * claims to cover one stops naming every member. The pages keep their own
 * grouping and prose; only completeness is enforced. Scheduled jobs are rendered
 * in full because no page groups them by hand.
 */

import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { execFileSync } from 'node:child_process'

/** Every runbook under docs/runbooks/, excluding READMEs and incident notes. */
function runbookPaths(root: string): string[] {
  const out: string[] = []
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const p = join(dir, name)
      if (statSync(p).isDirectory()) {
        if (name !== 'incidents') walk(p)
      } else if (name.endsWith('.md') && name !== 'README.md') {
        out.push(relative(join(root, 'docs', 'runbooks'), p))
      }
    }
  }
  walk(join(root, 'docs', 'runbooks'))
  return out.sort()
}

/** Worker directories under workers/ that hold tracked files (deployed code, not leftovers). */
function trackedWorkers(root: string): string[] {
  const out = execFileSync('git', ['ls-files', 'workers'], { cwd: root, encoding: 'utf8' })
  return [
    ...new Set(
      out
        .split('\n')
        .filter(Boolean)
        .map((p) => p.split('/')[1])
    ),
  ].sort()
}

/** Tables created by migrations/ and not later dropped. */
function liveTables(root: string): string[] {
  const dir = join(root, 'migrations')
  const tables = new Set<string>()
  for (const f of readdirSync(dir)
    .filter((x) => x.endsWith('.sql'))
    .sort()) {
    const sql = readFileSync(join(dir, f), 'utf8').replace(/--.*$/gm, '')
    for (const m of sql.matchAll(/create\s+table\s+(?:if\s+not\s+exists\s+)?["`]?(\w+)/gi))
      tables.add(m[1].toLowerCase())
    for (const m of sql.matchAll(/drop\s+table\s+(?:if\s+exists\s+)?["`]?(\w+)/gi))
      tables.delete(m[1].toLowerCase())
    for (const m of sql.matchAll(/alter\s+table\s+["`]?(\w+)["`]?\s+rename\s+to\s+["`]?(\w+)/gi)) {
      tables.delete(m[1].toLowerCase())
      tables.add(m[2].toLowerCase())
    }
  }
  // SQLite's copy-and-swap migrations create `<name>_new` and rename it back;
  // anything still suffixed is an intermediate the rename above did not catch.
  return [...tables].filter((t) => !/_(new|old|tmp|backup)$/.test(t)).sort()
}

export interface ScheduledJob {
  where: string
  name: string
  schedule: string
  /** The clock the cron expression is read in. */
  timezone: string
}

/** Every scheduled job: GitHub workflows, Worker crons, and the ops machine's crontab. */
export function scheduledJobs(root: string): ScheduledJob[] {
  const jobs: ScheduledJob[] = []
  const wf = join(root, '.github', 'workflows')
  for (const f of readdirSync(wf)
    .filter((x) => /\.ya?ml$/.test(x))
    .sort()) {
    const text = readFileSync(join(wf, f), 'utf8')
    for (const m of text.matchAll(/^\s*-\s*cron:\s*['"]([^'"]+)['"]/gm))
      jobs.push({
        where: 'GitHub Actions',
        name: `.github/workflows/${f}`,
        schedule: m[1],
        timezone: 'UTC',
      })
  }
  for (const w of trackedWorkers(root)) {
    const toml = join(root, 'workers', w, 'wrangler.toml')
    if (!existsSync(toml)) continue
    const crons = readFileSync(toml, 'utf8').match(/^crons\s*=\s*\[([^\]]*)\]/m)
    for (const c of crons ? [...crons[1].matchAll(/["']([^"']+)["']/g)] : [])
      jobs.push({
        where: 'Cloudflare Worker',
        name: `workers/${w}`,
        schedule: c[1],
        timezone: 'UTC',
      })
  }
  const crontab = join(root, 'operator', 'ops-machine', 'crontab')
  if (existsSync(crontab)) {
    const text = readFileSync(crontab, 'utf8')
    // cron reads the schedule in the crontab's own TZ= setting, not UTC.
    const timezone = text.match(/^\s*TZ=(\S+)/m)?.[1] ?? 'UTC'
    for (const line of text.split('\n')) {
      const m = line.match(/^\s*([^#\s]\S*\s+\S+\s+\S+\s+\S+\s+\S+)\s+(.+)$/)
      if (m && !/^[A-Z_]+=/.test(line.trim())) {
        const cmd = m[2].trim().replace(/\s+/g, ' ')
        jobs.push({
          where: 'Ops machine (smd-ops)',
          name: cmd.length > 70 ? `${cmd.slice(0, 67)}...` : cmd,
          schedule: m[1],
          timezone,
        })
      }
    }
  }
  return jobs
}

export const JOBS_BEGIN =
  '<!-- BEGIN GENERATED: scheduled jobs. Regenerate with `npm run handbook:inventories -- --write`; tests/handbook-integrity.test.ts fails while it is stale. -->'
export const JOBS_END = '<!-- END GENERATED: scheduled jobs -->'

export function renderScheduledJobs(jobs: ScheduledJob[]): string {
  const lines = [
    JOBS_BEGIN,
    '',
    '| Runs on | Job | Schedule (cron) | Time zone |',
    '|---|---|---|---|',
  ]
  for (const j of jobs)
    lines.push(
      `| ${j.where} | \`${j.name.replace(/\|/g, '\\|')}\` | \`${j.schedule}\` | ${j.timezone} |`
    )
  lines.push('', JOBS_END)
  return lines.join('\n')
}

export function committedJobsBlock(page: string): string | null {
  const s = page.indexOf(JOBS_BEGIN)
  const e = page.indexOf(JOBS_END)
  return s === -1 || e === -1 || e < s ? null : page.slice(s, e + JOBS_END.length)
}

export interface Omission {
  page: string
  what: string
  missing: string[]
}

/**
 * For each page that claims to list what exists, the members it does not name.
 * `readPage` is injectable so the check can be shown to fail.
 */
export function inventoryOmissions(
  root: string,
  readPage: (file: string) => string = (f) =>
    readFileSync(join(root, 'docs', 'handbook', f), 'utf8')
): Omission[] {
  const runbooks = readPage('disaster-recovery.md')
  const repoMap = readPage('repository-map.md')
  const dataModel = readPage('data-model.md')
  return [
    {
      page: 'disaster-recovery.md',
      what: 'runbook',
      missing: runbookPaths(root).filter((r) => !runbooks.includes(r.split('/').pop()!)),
    },
    {
      page: 'repository-map.md',
      what: 'deployed worker',
      missing: trackedWorkers(root).filter((w) => !repoMap.includes(w)),
    },
    {
      page: 'data-model.md',
      what: 'live table',
      missing: liveTables(root).filter((t) => !new RegExp(`\\b${t}\\b`).test(dataModel)),
    },
  ]
}
