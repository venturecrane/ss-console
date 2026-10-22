#!/usr/bin/env tsx
/**
 * Handbook review report: pages whose sources changed after their review, pages
 * past their review window, and pages never reviewed (scripts/lib/handbook-review.ts).
 *
 * Usage:
 *   npm run handbook:drift               # print the report
 *   npm run handbook:drift -- --strict   # exit 1 when any page has a finding
 *
 * Run weekly by .github/workflows/handbook-review.yml, which keeps one rolling
 * issue. Needs full git history (fetch-depth: 0) to find each page's review commit.
 */

import { execFileSync } from 'node:child_process'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { parse as parseYaml } from 'yaml'
import {
  hasFindings,
  renderReviewReport,
  reviewFindings,
  type ReviewPage,
} from './lib/handbook-review'

const HANDBOOK = 'docs/handbook'
const REPO_ROOTS =
  /^(src|docs|migrations|workers|operator|scripts|public|tests|\.github|\.claude)\//

function repoPathOf(href: unknown): string | null {
  if (typeof href !== 'string') return null
  const blob = href.match(
    /^https:\/\/github\.com\/venturecrane\/ss-console\/(?:blob|tree)\/[^/]+\/(.+)$/
  )
  if (blob) return blob[1].replace(/[#?].*$/, '')
  if (!/^[a-z]+:\/\//.test(href) && REPO_ROOTS.test(href)) return href.replace(/[#?].*$/, '')
  return null
}

function git(args: string[]): string {
  try {
    return execFileSync('git', args, { encoding: 'utf8' }).trim()
  } catch {
    return ''
  }
}

/**
 * The commit that last added or changed the page's `last_reviewed` line. None
 * means the review is not committed yet (a local run mid-edit), so it is as
 * fresh as the working tree and nothing has changed since.
 */
const reviewCommits = new Map<string, string>()
function reviewCommit(page: string): string {
  if (!reviewCommits.has(page))
    reviewCommits.set(
      page,
      git(['log', '-1', '--format=%H', '-G', '^last_reviewed:', '--', join(HANDBOOK, page)])
    )
  return reviewCommits.get(page)!
}

function changedSinceReview(page: string, source: string): string | null {
  const since = reviewCommit(page)
  if (!since) return null
  const out = git(['log', '-1', '--format=%cI', `${since}..HEAD`, '--', source])
  return out ? new Date(out).toISOString().slice(0, 10) : null
}

/**
 * Pages that are entirely generated and held equal to their source on every
 * build by tests/handbook-integrity.test.ts. A person reviewing them would be
 * checking the generator, which the test already does.
 */
const FULLY_GENERATED = new Set(['adr-index.md'])

const pages: ReviewPage[] = readdirSync(HANDBOOK)
  .filter((f) => f.endsWith('.md') && f !== 'README.md' && !FULLY_GENERATED.has(f))
  .map((file) => {
    const raw = readFileSync(join(HANDBOOK, file), 'utf8')
    const m = raw.match(/^---\n([\s\S]*?)\n---/)
    const fm = (m ? parseYaml(m[1]) : {}) as Record<string, unknown>
    const sources = Array.isArray(fm.sources) ? (fm.sources as { href?: unknown }[]) : []
    return {
      file,
      section: typeof fm.section === 'string' ? fm.section : 'unknown',
      lastReviewed: typeof fm.last_reviewed === 'string' ? fm.last_reviewed : undefined,
      reviewEvery: typeof fm.review_every === 'number' ? fm.review_every : undefined,
      sources: sources.map((s) => repoPathOf(s?.href)).filter((p): p is string => !!p),
    }
  })

const findings = reviewFindings(pages, changedSinceReview, new Date())
console.log(renderReviewReport(findings))
if (process.argv.includes('--strict') && hasFindings(findings)) process.exitCode = 1
