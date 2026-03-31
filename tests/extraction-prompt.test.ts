import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'
import {
  EXTRACTION_SYSTEM_PROMPT,
  buildExtractionUserPrompt,
  buildManualExtractionPrompt,
  validateExtraction,
} from '../src/portal/assessments/extraction-prompt.js'
import {
  PROBLEM_IDS,
  PROBLEM_LABELS,
  VERTICALS,
} from '../src/portal/assessments/extraction-schema.js'
import { SAMPLE_TRANSCRIPT, SAMPLE_EXTRACTION_OUTPUT } from './fixtures/sample-transcript.js'

// ---------------------------------------------------------------------------
// Schema constants
// ---------------------------------------------------------------------------

describe('extraction schema constants', () => {
  it('defines exactly 6 universal problem IDs', () => {
    expect(PROBLEM_IDS).toHaveLength(6)
  })

  it('every problem ID has a human-readable label', () => {
    for (const id of PROBLEM_IDS) {
      expect(PROBLEM_LABELS[id]).toBeDefined()
      expect(typeof PROBLEM_LABELS[id]).toBe('string')
      expect(PROBLEM_LABELS[id].length).toBeGreaterThan(0)
    }
  })

  it('problem IDs match the canonical 6', () => {
    expect(PROBLEM_IDS).toContain('owner_bottleneck')
    expect(PROBLEM_IDS).toContain('lead_leakage')
    expect(PROBLEM_IDS).toContain('financial_blindness')
    expect(PROBLEM_IDS).toContain('scheduling_chaos')
    expect(PROBLEM_IDS).toContain('manual_communication')
    expect(PROBLEM_IDS).toContain('team_invisibility')
  })

  it('defines verticals matching Decision #3', () => {
    expect(VERTICALS).toContain('home_services')
    expect(VERTICALS).toContain('professional_services')
  })
})

// ---------------------------------------------------------------------------
// Prompt construction
// ---------------------------------------------------------------------------

describe('extraction prompt construction', () => {
  it('system prompt references all 6 universal problems', () => {
    expect(EXTRACTION_SYSTEM_PROMPT).toContain('Owner bottleneck')
    expect(EXTRACTION_SYSTEM_PROMPT).toContain('Lead leakage')
    expect(EXTRACTION_SYSTEM_PROMPT).toContain('Financial blindness')
    expect(EXTRACTION_SYSTEM_PROMPT).toContain('Scheduling chaos')
    expect(EXTRACTION_SYSTEM_PROMPT).toContain('Manual communication')
    expect(EXTRACTION_SYSTEM_PROMPT).toContain('Team invisibility')
  })

  it('system prompt references disqualification criteria from Decision #4', () => {
    expect(EXTRACTION_SYSTEM_PROMPT).toContain('Hard disqualifiers')
    expect(EXTRACTION_SYSTEM_PROMPT).toContain('Soft disqualifiers')
    expect(EXTRACTION_SYSTEM_PROMPT).toContain('Not speaking to the owner')
    expect(EXTRACTION_SYSTEM_PROMPT).toMatch(/no tech baseline/i)
  })

  it('system prompt references champion identification from Decision #28', () => {
    expect(EXTRACTION_SYSTEM_PROMPT).toContain('champion')
  })

  it('system prompt references budget signal proxies from Decision #4', () => {
    expect(EXTRACTION_SYSTEM_PROMPT).toContain('Budget Signal Proxies')
    expect(EXTRACTION_SYSTEM_PROMPT).toContain('2+ employees on payroll')
    expect(EXTRACTION_SYSTEM_PROMPT).toContain('3+ years in business')
  })

  it('user prompt includes transcript', () => {
    const prompt = buildExtractionUserPrompt('Hello, this is a test transcript.')
    expect(prompt).toContain('Hello, this is a test transcript.')
  })

  it('user prompt includes full output schema reference', () => {
    const prompt = buildExtractionUserPrompt('test')
    expect(prompt).toContain('schema_version')
    expect(prompt).toContain('identified_problems')
    expect(prompt).toContain('disqualification_flags')
    expect(prompt).toContain('quote_drivers')
    expect(prompt).toContain('champion_candidate')
  })

  it('manual prompt combines system and user prompts', () => {
    const manual = buildManualExtractionPrompt('test transcript')
    expect(manual).toContain(EXTRACTION_SYSTEM_PROMPT)
    expect(manual).toContain('test transcript')
  })

  it('system prompt instructs JSON-only output', () => {
    expect(EXTRACTION_SYSTEM_PROMPT).toContain('Output ONLY valid JSON')
  })
})

// ---------------------------------------------------------------------------
// Sample transcript
// ---------------------------------------------------------------------------

describe('sample transcript', () => {
  it('has realistic speaker separation', () => {
    expect(SAMPLE_TRANSCRIPT).toContain('[Speaker 1')
    expect(SAMPLE_TRANSCRIPT).toContain('[Speaker 2')
  })

  it('mentions problems that map to the 6 universal problems', () => {
    // Scheduling chaos signals
    expect(SAMPLE_TRANSCRIPT).toContain('double-booking')

    // Lead leakage signals
    expect(SAMPLE_TRANSCRIPT).toContain('sticky note')

    // Owner bottleneck signals
    expect(SAMPLE_TRANSCRIPT).toMatch(/can't take a day off/i)
  })

  it('contains a champion candidate mention', () => {
    expect(SAMPLE_TRANSCRIPT).toContain('Derek')
  })

  it('contains tool mentions for extraction', () => {
    expect(SAMPLE_TRANSCRIPT).toContain('Google Calendar')
    expect(SAMPLE_TRANSCRIPT).toContain('QuickBooks Online')
  })

  it('contains ROI anchor math verbalized by the owner', () => {
    // Owner computes their own loss numbers — Decision #15
    expect(SAMPLE_TRANSCRIPT).toMatch(/\$1,500/)
    expect(SAMPLE_TRANSCRIPT).toMatch(/\$2,000/)
  })
})

// ---------------------------------------------------------------------------
// Validation function
// ---------------------------------------------------------------------------

describe('validateExtraction', () => {
  it('validates sample extraction output successfully', () => {
    const result = validateExtraction(SAMPLE_EXTRACTION_OUTPUT)
    expect(result.errors).toEqual([])
    expect(result.valid).toBe(true)
  })

  it('rejects null input', () => {
    const result = validateExtraction(null)
    expect(result.valid).toBe(false)
    expect(result.errors).toContain('Root must be a non-null object')
  })

  it('rejects non-object input', () => {
    const result = validateExtraction('not an object')
    expect(result.valid).toBe(false)
  })

  it('rejects wrong schema version', () => {
    const data = { ...SAMPLE_EXTRACTION_OUTPUT, schema_version: '2.0' }
    const result = validateExtraction(data)
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.includes('schema_version'))).toBe(true)
  })

  it('rejects missing business_name', () => {
    const data = { ...SAMPLE_EXTRACTION_OUTPUT, business_name: '' }
    const result = validateExtraction(data)
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.includes('business_name'))).toBe(true)
  })

  it('rejects invalid vertical', () => {
    const data = { ...SAMPLE_EXTRACTION_OUTPUT, vertical: 'invalid_vertical' }
    const result = validateExtraction(data)
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.includes('vertical'))).toBe(true)
  })

  it('rejects identified_problems with invalid problem_id', () => {
    const data = {
      ...SAMPLE_EXTRACTION_OUTPUT,
      identified_problems: [
        {
          problem_id: 'not_a_real_problem',
          severity: 'high',
          summary: 'Test',
          owner_quotes: ['quote'],
          underlying_cause: 'cause',
        },
      ],
    }
    const result = validateExtraction(data)
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.includes('problem_id'))).toBe(true)
  })

  it('rejects identified_problems with empty owner_quotes', () => {
    const data = {
      ...SAMPLE_EXTRACTION_OUTPUT,
      identified_problems: [
        {
          problem_id: 'owner_bottleneck',
          severity: 'high',
          summary: 'Test',
          owner_quotes: [],
          underlying_cause: 'cause',
        },
      ],
    }
    const result = validateExtraction(data)
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.includes('owner_quotes'))).toBe(true)
  })

  it('rejects invalid current_tools status', () => {
    const data = {
      ...SAMPLE_EXTRACTION_OUTPUT,
      current_tools: [
        {
          name: 'Test Tool',
          purpose: 'Testing',
          status: 'broken',
        },
      ],
    }
    const result = validateExtraction(data)
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.includes('current_tools'))).toBe(true)
  })

  it('rejects missing disqualification_flags structure', () => {
    const data = { ...SAMPLE_EXTRACTION_OUTPUT, disqualification_flags: null }
    const result = validateExtraction(data)
    expect(result.valid).toBe(false)
  })

  it('rejects invalid quote_drivers.estimated_complexity', () => {
    const qd = SAMPLE_EXTRACTION_OUTPUT.quote_drivers as Record<string, unknown>
    const data = {
      ...SAMPLE_EXTRACTION_OUTPUT,
      quote_drivers: {
        ...qd,
        estimated_complexity: 'extreme',
      },
    }
    const result = validateExtraction(data)
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.includes('estimated_complexity'))).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// File existence and hygiene
// ---------------------------------------------------------------------------

describe('extraction prompt file hygiene', () => {
  const promptPath = resolve('src/portal/assessments/extraction-prompt.ts')
  const schemaPath = resolve('src/portal/assessments/extraction-schema.ts')
  const samplePath = resolve('tests/fixtures/sample-transcript.ts')

  it('extraction-prompt.ts exists', () => {
    const content = readFileSync(promptPath, 'utf-8')
    expect(content.length).toBeGreaterThan(0)
  })

  it('extraction-schema.ts exists', () => {
    const content = readFileSync(schemaPath, 'utf-8')
    expect(content.length).toBeGreaterThan(0)
  })

  it('sample-transcript.ts exists', () => {
    const content = readFileSync(samplePath, 'utf-8')
    expect(content.length).toBeGreaterThan(0)
  })

  it('no dollar amounts in prompt template files (per content policy)', () => {
    const promptContent = readFileSync(promptPath, 'utf-8')
    const schemaContent = readFileSync(schemaPath, 'utf-8')
    // The prompt and schema should not contain dollar amounts — those are
    // in the decision stack and CLAUDE.md for internal reference only.
    // Dollar pattern: $ followed by digits, but not inside JSDoc or type annotations
    const dollarPattern = /\$\d[\d,]+/
    // Filter out lines that are clearly code/type/doc patterns
    for (const content of [promptContent, schemaContent]) {
      const lines = content.split('\n')
      for (const line of lines) {
        // Skip comment lines describing pricing (e.g., $150/hr in Decision Stack references)
        if (line.trim().startsWith('*') || line.trim().startsWith('//')) continue
        // Skip lines that are clearly template examples with dollar sign as variable prefix
        if (line.includes('${')) continue
        expect(line, `Dollar amount found: ${line.trim()}`).not.toMatch(dollarPattern)
      }
    }
  })
})

// ---------------------------------------------------------------------------
// Schema-to-D1 alignment
// ---------------------------------------------------------------------------

describe('schema alignment with D1 assessments table', () => {
  it('sample output has fields matching D1 columns', () => {
    // The assessments table has these extraction-relevant columns:
    // - extraction TEXT (the full JSON — this is the AssessmentExtraction)
    // - problems TEXT (JSON array of identified problem IDs)
    // - disqualifiers TEXT (JSON of disqualification flags)
    // - champion_name TEXT
    // - champion_role TEXT

    // Verify the sample can produce the denormalized fields
    const extraction = SAMPLE_EXTRACTION_OUTPUT
    const problems = (extraction.identified_problems as Array<{ problem_id: string }>).map(
      (p) => p.problem_id
    )
    expect(problems).toEqual(['scheduling_chaos', 'lead_leakage', 'owner_bottleneck'])

    const champion = extraction.champion_candidate as { name: string; role: string }
    expect(champion.name).toBe('Derek')
    expect(champion.role).toBe('Senior crew lead')

    expect(extraction.disqualification_flags).toBeDefined()
  })

  it('problem IDs in sample match PROBLEM_IDS constants', () => {
    const problems = (
      SAMPLE_EXTRACTION_OUTPUT.identified_problems as Array<{ problem_id: string }>
    ).map((p) => p.problem_id)
    for (const pid of problems) {
      expect(PROBLEM_IDS as readonly string[]).toContain(pid)
    }
  })

  it('vertical in sample matches VERTICALS constants', () => {
    expect(VERTICALS as readonly string[]).toContain(SAMPLE_EXTRACTION_OUTPUT.vertical)
  })
})
