/**
 * `npm run deadcode` reports zero dependency findings.
 *
 * The dependency categories are warn-tier in knip.jsonc, so a false finding
 * never fails the gate; it only prints, every run, until readers learn to skip
 * the list. That is what happened to `@astrojs/cloudflare` and
 * `@astrojs/sitemap`: reported unused from 2026-09-09, "fixed" 09-10 by a
 * config change that did not hold, and still printing on 09-25 (review,
 * Dependencies 2). This runs the real command and pins the list at empty, so a
 * genuinely unused dependency surfaces here as a failure rather than as one
 * more line in a list nobody reads.
 */
import { describe, expect, it } from 'vitest'
import { execFileSync } from 'child_process'

describe('knip dependency findings', () => {
  it('npm run deadcode names no unused, unlisted or unresolved dependency', () => {
    const out = execFileSync('npx', ['knip', '--production', '--reporter', 'json'], {
      encoding: 'utf8',
      maxBuffer: 32 * 1024 * 1024,
      timeout: 120_000,
    })
    const report = JSON.parse(out) as {
      issues: {
        file: string
        dependencies?: { name: string }[]
        devDependencies?: { name: string }[]
        unlisted?: { name: string }[]
        unresolved?: { name: string }[]
      }[]
    }
    const findings = report.issues.flatMap((issue) =>
      (['dependencies', 'devDependencies', 'unlisted', 'unresolved'] as const).flatMap((kind) =>
        (issue[kind] ?? []).map((d) => `${kind}: ${d.name} (${issue.file})`)
      )
    )
    expect(findings).toEqual([])
  }, 150_000)
})
