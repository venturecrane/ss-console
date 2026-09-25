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

type Flags = Record<string, Record<string, boolean>> | undefined

/** Keys in a flag group whose value is exactly true, in object order. */
function trueKeys(group: Record<string, boolean> | undefined): string[] {
  return Object.entries(group ?? {})
    .filter(([, val]) => val === true)
    .map(([key]) => key)
}

/** Every flag in the group must be off. */
function expectNoneTrue(group: Record<string, boolean> | undefined, label: string): string[] {
  return trueKeys(group).map((key) => `${label} disqualifier ${key} should be false`)
}

/** Each named soft flag must be on. */
function expectSoftTrue(flags: Flags, names: readonly string[]): string[] {
  return names
    .filter((name) => !flags?.soft?.[name])
    .map((name) => `Soft disqualifier ${name} should be true`)
}

/** The plumbing case's quote-driver expectations. */
function plumbingQuoteErrors(qd: Record<string, unknown> | undefined): string[] {
  const errors: string[] = []
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
  for (const problem of ['scheduling_chaos', 'lead_leakage']) {
    if (rp && !rp.includes(problem)) errors.push(`Missing ${problem} in recommended_problems`)
  }
  return errors
}

const TEST_CASES: TestCase[] = [
  {
    name: 'Transcript A — Plumbing (Qualifying)',
    transcript: PLUMBING_TRANSCRIPT,
    cacheFile: 'plumbing-qualify.json',
    assertions: (ext) => {
      const flags = ext.disqualification_flags as Flags
      const qd = ext.quote_drivers as Record<string, unknown> | undefined
      // No hard or soft disqualifiers
      const errors = [
        ...expectNoneTrue(flags?.hard, 'Hard'),
        ...expectNoneTrue(flags?.soft, 'Soft'),
        ...plumbingQuoteErrors(qd),
      ]

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
      const flags = ext.disqualification_flags as Flags
      const qd = ext.quote_drivers as Record<string, unknown> | undefined
      // No hard disqualifiers. books_behind and no_champion should be true, and
      // no_willingness_to_change too (owner explicitly resists).
      const errors = [
        ...expectNoneTrue(flags?.hard, 'Hard'),
        ...expectSoftTrue(flags, ['books_behind', 'no_champion', 'no_willingness_to_change']),
      ]

      // Medium or high complexity are both defensible. The prompt defines
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
    const parsed: unknown = JSON.parse(readFileSync(path, 'utf-8'))
    return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)
      ? { ...parsed }
      : null
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
  // Strings print bare; everything else (numbers, booleans, null, arrays,
  // objects) prints as JSON, and undefined as "undefined".
  const text = typeof value === 'string' ? value : String(JSON.stringify(value))
  console.log(`${pad}${label}: ${text}`)
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

/** The extraction for one case, from cache or the API. Null means the case failed to load. */
async function loadExtraction(
  testCase: TestCase,
  apiKey: string | undefined
): Promise<Record<string, unknown> | null> {
  if (USE_CACHE) {
    const cached = loadCache(testCase.cacheFile)
    if (!cached) {
      console.error(`  Cache miss: ${testCase.cacheFile} not found. Run without --cached first.`)
      return null
    }
    console.log('  Source: cache\n')
    return cached
  }

  console.log('  Calling Claude API...')
  const start = Date.now()
  let extraction: Record<string, unknown>
  try {
    extraction = (await extractAssessment(apiKey!, testCase.transcript)) as unknown as Record<
      string,
      unknown
    >
  } catch (err) {
    console.error(`  API Error: ${(err as Error).message}`)
    return null
  }
  const elapsed = ((Date.now() - start) / 1000).toFixed(1)
  console.log(`  Completed in ${elapsed}s\n`)

  // Cache the result
  saveCache(testCase.cacheFile, extraction)
  return extraction
}

/** Schema validation. Returns whether it passed. */
function reportValidation(extraction: Record<string, unknown>): boolean {
  const validation = validateExtraction(extraction)
  if (validation.valid) {
    console.log('  Schema validation: PASSED')
    return true
  }
  console.log('  Schema validation: FAILED')
  for (const err of validation.errors) {
    console.log(`    - ${err}`)
  }
  return false
}

function printKeyFields(extraction: Record<string, unknown>): void {
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
}

function printQuoteDrivers(extraction: Record<string, unknown>): void {
  const qd = extraction.quote_drivers as Record<string, unknown> | undefined
  if (!qd) return
  console.log('\n  Quote drivers:')
  printResult('Recommended problems', qd.recommended_problems)
  printResult('Estimated complexity', qd.estimated_complexity)
  printResult('Upward pressures', (qd.upward_pressures as string[])?.length ?? 0)
  printResult('Downward pressures', (qd.downward_pressures as string[])?.length ?? 0)
  printResult('ROI anchors', (qd.roi_anchors as string[])?.length ?? 0)
}

function printFlagsAndChampion(extraction: Record<string, unknown>): void {
  const flags = extraction.disqualification_flags as Flags
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
}

/** The case's own assertions. Returns whether they all passed. */
function reportAssertions(testCase: TestCase, extraction: Record<string, unknown>): boolean {
  console.log('\n  Assertions:')
  const assertionErrors = testCase.assertions(extraction)
  if (assertionErrors.length === 0) {
    console.log('    All assertions PASSED')
    return true
  }
  for (const err of assertionErrors) {
    console.log(`    FAILED: ${err}`)
  }
  return false
}

/** Run one case end to end. Returns whether it passed. */
async function runCase(testCase: TestCase, apiKey: string | undefined): Promise<boolean> {
  console.log(`--- ${testCase.name} ---\n`)
  const extraction = await loadExtraction(testCase, apiKey)
  if (!extraction) return false

  const schemaOk = reportValidation(extraction)
  printKeyFields(extraction)
  printQuoteDrivers(extraction)
  printFlagsAndChampion(extraction)
  const assertionsOk = reportAssertions(testCase, extraction)
  console.log('')
  return schemaOk && assertionsOk
}

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
    if (!(await runCase(testCase, apiKey))) allPassed = false
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
