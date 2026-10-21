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
 * issue. Needs full git history (fetch-depth: 0) to date each source's last commit.
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

function lastCommitDay(path: string): string | null {
  try {
    const out = execFileSync('git', ['log', '-1', '--format=%cI', '--', path], {
      encoding: 'utf8',
    }).trim()
    return out ? new Date(out).toISOString().slice(0, 10) : null
  } catch {
    return null
  }
}

const pages: ReviewPage[] = readdirSync(HANDBOOK)
  .filter((f) => f.endsWith('.md') && f !== 'README.md')
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

const findings = reviewFindings(pages, lastCommitDay, new Date())
console.log(renderReviewReport(findings))
if (process.argv.includes('--strict') && hasFindings(findings)) process.exitCode = 1
