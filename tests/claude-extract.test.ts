import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

describe('claude extraction: client module', () => {
  const source = () => readFileSync(resolve('src/lib/claude/extract.ts'), 'utf-8')

  it('extract.ts exists', () => {
    expect(existsSync(resolve('src/lib/claude/extract.ts'))).toBe(true)
  })

  it('exports extractAssessment function', () => {
    expect(source()).toContain('export async function extractAssessment')
  })

  it('exports ExtractionApiError class', () => {
    expect(source()).toContain('export class ExtractionApiError')
  })

  it('exports ExtractionValidationError class', () => {
    expect(source()).toContain('export class ExtractionValidationError')
  })

  it('uses raw fetch to Anthropic API (no SDK)', () => {
    const code = source()
    expect(code).toContain('https://api.anthropic.com/v1/messages')
    expect(code).toContain('fetch(')
    // Should not import the Anthropic SDK
    expect(code).not.toContain('@anthropic-ai/sdk')
    expect(code).not.toContain("from 'anthropic'")
  })

  it('includes x-api-key header', () => {
    expect(source()).toContain("'x-api-key': apiKey")
  })

  it('includes anthropic-version header', () => {
    const code = source()
    expect(code).toContain("'anthropic-version': ANTHROPIC_VERSION")
    expect(code).toContain("ANTHROPIC_VERSION = '2023-06-01'")
  })

  it('includes content-type header', () => {
    expect(source()).toContain("'content-type': 'application/json'")
  })

  it('uses the correct model', () => {
    expect(source()).toContain('claude-sonnet-4-20250514')
  })

  it('sets max_tokens to 4096', () => {
    expect(source()).toContain('4096')
    expect(source()).toContain('max_tokens: MAX_TOKENS')
  })

  it('imports and uses EXTRACTION_SYSTEM_PROMPT', () => {
    const code = source()
    expect(code).toContain('EXTRACTION_SYSTEM_PROMPT')
    expect(code).toContain('system: EXTRACTION_SYSTEM_PROMPT')
  })

  it('imports and uses buildExtractionUserPrompt', () => {
    const code = source()
    expect(code).toContain('buildExtractionUserPrompt')
    expect(code).toContain('buildExtractionUserPrompt(transcript)')
  })

  it('calls validateExtraction on the response', () => {
    const code = source()
    expect(code).toContain('validateExtraction')
    expect(code).toContain('validateExtraction(parsed)')
  })

  it('throws ExtractionValidationError on invalid extraction output', () => {
    const code = source()
    expect(code).toContain('!validation.valid')
    expect(code).toContain('new ExtractionValidationError')
  })

  it('throws ExtractionApiError on non-OK response', () => {
    const code = source()
    expect(code).toContain('!response.ok')
    expect(code).toContain('new ExtractionApiError')
  })

  it('handles code fences in Claude response', () => {
    const code = source()
    expect(code).toContain('startsWith')
    expect(code).toContain('```')
  })

  it('parses text content block from Messages API response', () => {
    const code = source()
    expect(code).toContain('contentBlocks')
    expect(code).toContain("block.type === 'text'")
  })
})

describe('claude extraction: API route integration', () => {
  const source = () => readFileSync(resolve('src/pages/api/admin/assessments/[id].ts'), 'utf-8')

  it('API route imports extractAssessment', () => {
    expect(source()).toContain("import { extractAssessment } from '../../../../lib/claude/extract'")
  })

  it('API route imports getTranscript from R2', () => {
    expect(source()).toContain('getTranscript')
  })

  it('API route handles action === extract', () => {
    const code = source()
    expect(code).toContain("action === 'extract'")
  })

  it('API route checks transcript_path exists before extraction', () => {
    const code = source()
    expect(code).toContain('!existing.transcript_path')
    expect(code).toContain('no_transcript')
  })

  it('API route checks ANTHROPIC_API_KEY from env', () => {
    const code = source()
    expect(code).toContain('env.ANTHROPIC_API_KEY')
    expect(code).toContain('no_api_key')
  })

  it('API route fetches transcript text from R2', () => {
    const code = source()
    expect(code).toContain('getTranscript(env.STORAGE, existing.transcript_path)')
    expect(code).toContain('transcriptObject.text()')
  })

  it('API route calls extractAssessment with apiKey and transcript', () => {
    expect(source()).toContain('extractAssessment(apiKey, transcriptText)')
  })

  it('API route updates assessment with extraction result', () => {
    const code = source()
    expect(code).toContain('JSON.stringify(result)')
    expect(code).toContain('extraction: JSON.stringify(result)')
  })

  it('API route redirects with extracted=1 on success', () => {
    expect(source()).toContain('?extracted=1')
  })

  it('API route redirects with error on extraction failure', () => {
    expect(source()).toContain('extraction_failed')
  })

  it('API route logs extraction errors', () => {
    expect(source()).toContain('Extraction error')
  })
})

describe('claude extraction: assessment detail page', () => {
  const source = () =>
    readFileSync(resolve('src/pages/admin/clients/[id]/assessments/[assessmentId].astro'), 'utf-8')

  it('page shows Extract with Claude button', () => {
    const code = source()
    expect(code).toContain('Extract with Claude')
    expect(code).toContain('extract-form')
  })

  it('extract form posts action=extract to assessment API', () => {
    const code = source()
    expect(code).toContain('name="action" value="extract"')
    expect(code).toContain('/api/admin/assessments/${assessment.id}')
  })

  it('extract button only visible when transcript exists and extraction is empty', () => {
    const code = source()
    expect(code).toContain('showExtractButton')
    expect(code).toContain('!!assessment.transcript_path && !assessment.extraction')
  })

  it('extract button disabled when API key not configured', () => {
    const code = source()
    expect(code).toContain('hasApiKey')
    expect(code).toContain('API key not configured')
    expect(code).toContain('disabled')
  })

  it('page handles extracted=1 query param for success message', () => {
    const code = source()
    expect(code).toContain("get('extracted')")
    expect(code).toContain('Extraction completed successfully')
  })

  it('page parses extraction JSON for structured display', () => {
    const code = source()
    expect(code).toContain('extractionData')
    expect(code).toContain('JSON.parse(assessment.extraction)')
  })

  it('page displays extraction result section when data exists', () => {
    const code = source()
    expect(code).toContain('extraction-result')
    expect(code).toContain('Extraction Results')
  })

  it('extraction result displays problems with severity badges', () => {
    const code = source()
    expect(code).toContain('identified_problems.map')
    expect(code).toContain('problem.severity')
    expect(code).toContain('problem.summary')
  })

  it('extraction result displays champion candidate', () => {
    const code = source()
    expect(code).toContain('champion_candidate')
    expect(code).toContain('Champion Candidate')
  })

  it('extraction result displays disqualification flags', () => {
    const code = source()
    expect(code).toContain('disqualification_flags')
    expect(code).toContain('Disqualification Flags')
  })

  it('extraction result displays owner quotes', () => {
    const code = source()
    expect(code).toContain('owner_quotes')
  })

  it('extraction result displays executive summary', () => {
    const code = source()
    expect(code).toContain('executive_summary')
    expect(code).toContain('Executive Summary')
  })

  it('page includes error messages for extraction failures', () => {
    const code = source()
    expect(code).toContain('no_transcript')
    expect(code).toContain('no_api_key')
    expect(code).toContain('extraction_failed')
    expect(code).toContain('transcript_missing')
  })
})

describe('claude extraction: env type declaration', () => {
  it('env.d.ts includes ANTHROPIC_API_KEY', () => {
    const code = readFileSync(resolve('src/env.d.ts'), 'utf-8')
    expect(code).toContain('ANTHROPIC_API_KEY')
  })

  it('ANTHROPIC_API_KEY is optional', () => {
    const code = readFileSync(resolve('src/env.d.ts'), 'utf-8')
    expect(code).toContain('ANTHROPIC_API_KEY?: string')
  })
})

describe('claude extraction: prompt and schema imports', () => {
  it('extraction-prompt.ts exists', () => {
    expect(existsSync(resolve('src/portal/assessments/extraction-prompt.ts'))).toBe(true)
  })

  it('extraction-schema.ts exists', () => {
    expect(existsSync(resolve('src/portal/assessments/extraction-schema.ts'))).toBe(true)
  })

  it('extraction-prompt exports validateExtraction', () => {
    const code = readFileSync(resolve('src/portal/assessments/extraction-prompt.ts'), 'utf-8')
    expect(code).toContain('export function validateExtraction')
  })

  it('extraction-schema exports AssessmentExtraction interface', () => {
    const code = readFileSync(resolve('src/portal/assessments/extraction-schema.ts'), 'utf-8')
    expect(code).toContain('export interface AssessmentExtraction')
  })

  it('extract.ts correctly imports from extraction-prompt', () => {
    const code = readFileSync(resolve('src/lib/claude/extract.ts'), 'utf-8')
    expect(code).toContain("from '../../portal/assessments/extraction-prompt'")
  })

  it('extract.ts imports type from extraction-schema', () => {
    const code = readFileSync(resolve('src/lib/claude/extract.ts'), 'utf-8')
    expect(code).toContain("from '../../portal/assessments/extraction-schema'")
  })

  it('extract.ts imports validateExtraction from extraction-prompt', () => {
    const code = readFileSync(resolve('src/lib/claude/extract.ts'), 'utf-8')
    expect(code).toContain('validateExtraction')
    expect(code).toContain("from '../../portal/assessments/extraction-prompt'")
  })
})
