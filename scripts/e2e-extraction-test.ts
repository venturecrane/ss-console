#!/usr/bin/env npx tsx
/**
 * E2E Assessment Extraction Test
 *
 * Runs sample transcripts through the Claude extraction pipeline and
 * validates the structured output against the schema.
 *
 * Usage:
 *   ANTHROPIC_API_KEY=sk-ant-... npx tsx scripts/e2e-extraction-test.ts
 *   ANTHROPIC_API_KEY=sk-ant-... npx tsx scripts/e2e-extraction-test.ts --cached
 *
 * Flags:
 *   --cached  Use cached extraction results from .cache/extractions/ instead
 *             of calling the API. On first run without --cached, results are
 *             written to the cache automatically.
 *
 * Cost: ~$0.04 per run (2 Anthropic API calls at Sonnet pricing)
 */

import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs'
import { resolve } from 'node:path'

// Import extraction utilities
import { extractAssessment } from '../src/lib/claude/extract'
import { validateExtraction } from '../src/portal/assessments/extraction-prompt'

// Import transcript fixtures
import { PLUMBING_TRANSCRIPT } from '../tests/fixtures/transcript-plumbing-qualify'
import { ACCOUNTING_TRANSCRIPT } from '../tests/fixtures/transcript-accounting-disqualify'

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

const CACHE_DIR = resolve(import.meta.dirname ?? '.', '../.cache/extractions')
const USE_CACHE = process.argv.includes('--cached')

interface TestCase {
  name: string
  transcript: string
  cacheFile: string
  assertions: (extraction: Record<string, unknown>) => string[]
}

const TEST_CASES: TestCase[] = [
  {
    name: 'Transcript A — Plumbing (Qualifying)',
    transcript: PLUMBING_TRANSCRIPT,
    cacheFile: 'plumbing-qualify.json',
    assertions: (ext) => {
      const errors: string[] = []
      const flags = ext.disqualification_flags as Record<string, Record<string, boolean>>
      const qd = ext.quote_drivers as Record<string, unknown>

      // No hard disqualifiers
      if (flags?.hard) {
        for (const [key, val] of Object.entries(flags.hard)) {
          if (val === true) errors.push(`Hard disqualifier ${key} should be false`)
        }
      }

      // No soft disqualifiers
      if (flags?.soft) {
        for (const [key, val] of Object.entries(flags.soft)) {
          if (val === true) errors.push(`Soft disqualifier ${key} should be false`)
        }
      }

      // Medium complexity
      if (qd?.estimated_complexity !== 'medium') {
        errors.push(`Expected medium complexity, got "${String(qd?.estimated_complexity)}"`)
      }

      // Should recommend 2-3 problems
      const rp = qd?.recommended_problems as string[] | undefined
      if (!rp || rp.length < 2 || rp.length > 3) {
        errors.push(`Expected 2-3 recommended problems, got ${rp?.length ?? 0}`)
      }

      // Should include scheduling_chaos and lead_leakage
      if (rp && !rp.includes('scheduling_chaos')) {
        errors.push('Missing scheduling_chaos in recommended_problems')
      }
      if (rp && !rp.includes('lead_leakage')) {
        errors.push('Missing lead_leakage in recommended_problems')
      }

      // Should have a champion
      if (!ext.champion_candidate) {
        errors.push('Expected a champion candidate to be identified')
      }

      // Should have ROI anchors
      const roiAnchors = qd?.roi_anchors as string[] | undefined
      if (!roiAnchors || roiAnchors.length === 0) {
        errors.push('Expected at least one ROI anchor')
      }

      return errors
    },
  },
  {
    name: 'Transcript B — Accounting (Soft Disqualifier)',
    transcript: ACCOUNTING_TRANSCRIPT,
    cacheFile: 'accounting-disqualify.json',
    assertions: (ext) => {
      const errors: string[] = []
      const flags = ext.disqualification_flags as Record<string, Record<string, boolean>>
      const qd = ext.quote_drivers as Record<string, unknown>

      // No hard disqualifiers
      if (flags?.hard) {
        for (const [key, val] of Object.entries(flags.hard)) {
          if (val === true) errors.push(`Hard disqualifier ${key} should be false`)
        }
      }

      // Soft disqualifiers: books_behind and no_champion should be true
      if (!flags?.soft?.books_behind) {
        errors.push('Soft disqualifier books_behind should be true')
      }
      if (!flags?.soft?.no_champion) {
        errors.push('Soft disqualifier no_champion should be true')
      }

      // no_willingness_to_change should be true (owner explicitly resists)
      if (!flags?.soft?.no_willingness_to_change) {
        errors.push('Soft disqualifier no_willingness_to_change should be true')
      }

      // Medium or high complexity — both are defensible. The prompt defines
      // complexity by estimated hours, not adoption risk, so Claude may rate
      // the scope as medium even when adoption risk is high.
      const complexity = qd?.estimated_complexity
      if (complexity !== 'medium' && complexity !== 'high') {
        errors.push(`Expected medium or high complexity, got "${String(complexity)}"`)
      }

      // Should not have a champion
      if (ext.champion_candidate !== null) {
        errors.push('Expected champion_candidate to be null')
      }

      return errors
    },
  },
]

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function loadCache(filename: string): Record<string, unknown> | null {
  const path = resolve(CACHE_DIR, filename)
  if (!existsSync(path)) return null
  try {
    return JSON.parse(readFileSync(path, 'utf-8')) as Record<string, unknown>
  } catch {
    return null
  }
}

function saveCache(filename: string, data: Record<string, unknown>): void {
  mkdirSync(CACHE_DIR, { recursive: true })
  writeFileSync(resolve(CACHE_DIR, filename), JSON.stringify(data, null, 2))
}

function printResult(label: string, value: unknown, indent = 2): void {
  const pad = ' '.repeat(indent)
  console.log(
    `${pad}${label}: ${typeof value === 'object' ? JSON.stringify(value) : String(value)}`
  )
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main(): Promise<void> {
  const apiKey = process.env.ANTHROPIC_API_KEY

  if (!USE_CACHE && !apiKey) {
    console.error('Error: ANTHROPIC_API_KEY is required (or use --cached)')
    process.exit(1)
  }

  console.log('=== E2E Assessment Extraction Test ===\n')
  console.log(`Mode: ${USE_CACHE ? 'cached' : 'live API'}`)

  if (!USE_CACHE) {
    console.log(`Calling Anthropic API (${TEST_CASES.length} requests, ~$0.04 estimated cost)...\n`)
  }

  let allPassed = true

  for (const testCase of TEST_CASES) {
    console.log(`--- ${testCase.name} ---\n`)

    let extraction: Record<string, unknown>

    if (USE_CACHE) {
      const cached = loadCache(testCase.cacheFile)
      if (!cached) {
        console.error(`  Cache miss: ${testCase.cacheFile} not found. Run without --cached first.`)
        allPassed = false
        continue
      }
      extraction = cached
      console.log('  Source: cache\n')
    } else {
      console.log('  Calling Claude API...')
      const start = Date.now()
      try {
        extraction = (await extractAssessment(apiKey!, testCase.transcript)) as unknown as Record<
          string,
          unknown
        >
      } catch (err) {
        console.error(`  API Error: ${(err as Error).message}`)
        allPassed = false
        continue
      }
      const elapsed = ((Date.now() - start) / 1000).toFixed(1)
      console.log(`  Completed in ${elapsed}s\n`)

      // Cache the result
      saveCache(testCase.cacheFile, extraction)
    }

    // Schema validation
    const validation = validateExtraction(extraction)
    if (!validation.valid) {
      console.log('  Schema validation: FAILED')
      for (const err of validation.errors) {
        console.log(`    - ${err}`)
      }
      allPassed = false
    } else {
      console.log('  Schema validation: PASSED')
    }

    // Key fields
    console.log('\n  Key fields:')
    printResult('Business name', extraction.business_name)
    printResult('Vertical', extraction.vertical)
    printResult('Employee count', extraction.employee_count)
    printResult('Years in business', extraction.years_in_business)
    printResult('Geography', extraction.geography)

    const problems = extraction.identified_problems as Array<Record<string, unknown>> | undefined
    if (problems) {
      console.log(`\n  Identified problems (${problems.length}):`)
      for (const p of problems) {
        console.log(`    - ${String(p.problem_id)} [${String(p.severity)}]`)
      }
    }

    const qd = extraction.quote_drivers as Record<string, unknown> | undefined
    if (qd) {
      console.log('\n  Quote drivers:')
      printResult('Recommended problems', qd.recommended_problems)
      printResult('Estimated complexity', qd.estimated_complexity)
      printResult('Upward pressures', (qd.upward_pressures as string[])?.length ?? 0)
      printResult('Downward pressures', (qd.downward_pressures as string[])?.length ?? 0)
      printResult('ROI anchors', (qd.roi_anchors as string[])?.length ?? 0)
    }

    const flags = extraction.disqualification_flags as
      | Record<string, Record<string, boolean>>
      | undefined
    if (flags) {
      console.log('\n  Disqualification flags:')
      const hardTriggered = Object.entries(flags.hard || {})
        .filter(([, v]) => v)
        .map(([k]) => k)
      const softTriggered = Object.entries(flags.soft || {})
        .filter(([, v]) => v)
        .map(([k]) => k)
      printResult('Hard', hardTriggered.length > 0 ? hardTriggered : 'none')
      printResult('Soft', softTriggered.length > 0 ? softTriggered : 'none')
    }

    const champion = extraction.champion_candidate as Record<string, unknown> | null
    console.log(
      `\n  Champion: ${champion ? `${String(champion.name)} (${String(champion.confidence)})` : 'none identified'}`
    )

    // Assertion checks
    console.log('\n  Assertions:')
    const assertionErrors = testCase.assertions(extraction)
    if (assertionErrors.length === 0) {
      console.log('    All assertions PASSED')
    } else {
      for (const err of assertionErrors) {
        console.log(`    FAILED: ${err}`)
      }
      allPassed = false
    }

    console.log('')
  }

  // Final summary
  console.log('=== Summary ===')
  if (allPassed) {
    console.log('All tests PASSED')
    process.exit(0)
  } else {
    console.log('Some tests FAILED')
    process.exit(1)
  }
}

main().catch((err) => {
  console.error('Fatal error:', err)
  process.exit(1)
})
