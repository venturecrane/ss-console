/**
 * Behavioural tests for the assessment extraction client
 * (src/lib/claude/extract.ts). The Anthropic Messages API is the only fake:
 * global fetch is stubbed, so these assert the request the client sends and
 * what it does with each shape of reply.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'
import {
  ExtractionApiError,
  ExtractionValidationError,
  extractAssessment,
} from '../src/lib/claude/extract'
import { ANTHROPIC_API_URL, ANTHROPIC_VERSION, QUALITY_MODEL } from '../src/lib/llm/models'
import {
  EXTRACTION_SYSTEM_PROMPT,
  buildExtractionUserPrompt,
} from '../src/portal/assessments/extraction-prompt'
import { SAMPLE_EXTRACTION_OUTPUT, SAMPLE_TRANSCRIPT } from './fixtures/sample-transcript'

function reply(text: string, init: ResponseInit = { status: 200 }): Response {
  return new Response(JSON.stringify({ content: [{ type: 'text', text }] }), init)
}

function stubFetch(response: Response) {
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => response)
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('extractAssessment', () => {
  it('posts the transcript to the Messages API with the key, version, and quality model', async () => {
    const fetchMock = stubFetch(reply(JSON.stringify(SAMPLE_EXTRACTION_OUTPUT)))
    await extractAssessment('test-key', SAMPLE_TRANSCRIPT)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe(ANTHROPIC_API_URL)
    expect(init?.method).toBe('POST')
    expect(init?.headers).toEqual({
      'x-api-key': 'test-key',
      'anthropic-version': ANTHROPIC_VERSION,
      'content-type': 'application/json',
    })
    const body = JSON.parse(String(init?.body))
    expect(body.model).toBe(QUALITY_MODEL)
    expect(body.max_tokens).toBe(4096)
    expect(body.system).toBe(EXTRACTION_SYSTEM_PROMPT)
    expect(body.messages).toEqual([
      { role: 'user', content: buildExtractionUserPrompt(SAMPLE_TRANSCRIPT) },
    ])
  })

  it('returns the validated extraction', async () => {
    stubFetch(reply(JSON.stringify(SAMPLE_EXTRACTION_OUTPUT)))
    expect(await extractAssessment('k', SAMPLE_TRANSCRIPT)).toEqual(SAMPLE_EXTRACTION_OUTPUT)
  })

  it('strips a code fence around the JSON', async () => {
    stubFetch(reply('```json\n' + JSON.stringify(SAMPLE_EXTRACTION_OUTPUT) + '\n```'))
    expect(await extractAssessment('k', SAMPLE_TRANSCRIPT)).toEqual(SAMPLE_EXTRACTION_OUTPUT)
  })

  it('throws ExtractionApiError with the status on a non-OK response', async () => {
    stubFetch(new Response('overloaded', { status: 529, statusText: 'Overloaded' }))
    const error = await extractAssessment('k', SAMPLE_TRANSCRIPT).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ExtractionApiError)
    expect((error as ExtractionApiError).statusCode).toBe(529)
    expect((error as ExtractionApiError).responseBody).toBe('overloaded')
  })

  it('throws ExtractionApiError when the reply has no text block', async () => {
    stubFetch(new Response(JSON.stringify({ content: [{ type: 'tool_use' }] }), { status: 200 }))
    await expect(extractAssessment('k', SAMPLE_TRANSCRIPT)).rejects.toBeInstanceOf(
      ExtractionApiError
    )
  })

  it('throws ExtractionApiError when the text is not JSON', async () => {
    stubFetch(reply('not json'))
    await expect(extractAssessment('k', SAMPLE_TRANSCRIPT)).rejects.toBeInstanceOf(
      ExtractionApiError
    )
  })

  it('throws ExtractionValidationError when the JSON fails the schema', async () => {
    stubFetch(reply(JSON.stringify({ business_name: 'Only a name' })))
    const error = await extractAssessment('k', SAMPLE_TRANSCRIPT).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ExtractionValidationError)
    expect((error as ExtractionValidationError).validationErrors.length).toBeGreaterThan(0)
  })
})

describe('claude extraction: API route key handling', () => {
  it('API route reads ANTHROPIC_API_KEY from env and refuses without it', () => {
    const code = readFileSync(resolve('src/pages/api/admin/assessments/[id].ts'), 'utf-8')
    expect(code).toContain('env.ANTHROPIC_API_KEY')
    expect(code).toContain('no_api_key')
  })
})
