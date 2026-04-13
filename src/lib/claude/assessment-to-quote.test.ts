import { describe, it, expect, vi, beforeEach } from 'vitest'
import { generateQuoteLineItems, type AssessmentExtraction } from './assessment-to-quote.js'

const mockExtraction: AssessmentExtraction = {
  problems: ['process_design', 'customer_pipeline', 'tool_systems'],
  disqualified: false,
  duration_minutes: 45,
  notes: 'Owner handles all scheduling personally. No CRM. Team of 12.',
}

const mockLineItems = [
  {
    problem: 'Process Design',
    description: 'Document 5 core operational workflows as step-by-step SOPs',
    estimated_hours: 12,
  },
  {
    problem: 'Customer Pipeline',
    description: 'Configure CRM with pipeline stages and automated follow-up sequences',
    estimated_hours: 10,
  },
  {
    problem: 'Tools & Systems',
    description: 'Build customer notification templates and automate appointment reminders',
    estimated_hours: 8,
  },
]

function mockClaudeResponse(lineItems: unknown[]) {
  return {
    ok: true,
    status: 200,
    json: async () => ({
      content: [{ type: 'text', text: JSON.stringify(lineItems) }],
    }),
    text: async () =>
      JSON.stringify({ content: [{ type: 'text', text: JSON.stringify(lineItems) }] }),
  } as unknown as Response
}

function mockErrorResponse(status: number) {
  return {
    ok: false,
    status,
    text: async () => 'API error',
  } as unknown as Response
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('generateQuoteLineItems', () => {
  it('returns well-formed line items from Claude response', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(mockClaudeResponse(mockLineItems))

    const result = await generateQuoteLineItems(mockExtraction, 'Some entity context', 'test-key')

    expect(result).toHaveLength(3)
    for (const item of result) {
      expect(item).toHaveProperty('problem')
      expect(item).toHaveProperty('description')
      expect(item).toHaveProperty('estimated_hours')
      expect(typeof item.problem).toBe('string')
      expect(typeof item.description).toBe('string')
      expect(typeof item.estimated_hours).toBe('number')
    }
    expect(result[0].problem).toBe('Process Design')
    expect(result[1].estimated_hours).toBe(10)
  })

  it('returns empty array when API returns an error', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(mockErrorResponse(500))

    const result = await generateQuoteLineItems(mockExtraction, 'context', 'test-key')

    expect(result).toEqual([])
  })

  it('returns empty array when no API key is provided', async () => {
    const result = await generateQuoteLineItems(mockExtraction, 'context')

    expect(result).toEqual([])
  })

  it('returns empty array when fetch throws', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(new Error('Network error'))

    const result = await generateQuoteLineItems(mockExtraction, 'context', 'test-key')

    expect(result).toEqual([])
  })

  it('returns empty array when Claude returns empty content', async () => {
    const emptyResponse = {
      ok: true,
      json: async () => ({ content: [] }),
    } as unknown as Response
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(emptyResponse)

    const result = await generateQuoteLineItems(mockExtraction, 'context', 'test-key')

    expect(result).toEqual([])
  })

  it('strips markdown fences from Claude response', async () => {
    const fencedResponse = {
      ok: true,
      json: async () => ({
        content: [
          {
            type: 'text',
            text: '```json\n' + JSON.stringify(mockLineItems) + '\n```',
          },
        ],
      }),
    } as unknown as Response
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(fencedResponse)

    const result = await generateQuoteLineItems(mockExtraction, 'context', 'test-key')

    expect(result).toHaveLength(3)
  })

  it('filters out malformed items from the response', async () => {
    const mixedItems = [
      ...mockLineItems,
      { problem: 'Missing Hours', description: 'No hours field' },
      { bad: 'item' },
    ]
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(mockClaudeResponse(mixedItems))

    const result = await generateQuoteLineItems(mockExtraction, 'context', 'test-key')

    expect(result).toHaveLength(3)
  })

  it('sends correct headers and body to Claude API', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(mockClaudeResponse(mockLineItems))

    await generateQuoteLineItems(mockExtraction, 'entity context here', 'sk-test-123')

    expect(fetchSpy).toHaveBeenCalledOnce()
    const [url, opts] = fetchSpy.mock.calls[0]
    expect(url).toBe('https://api.anthropic.com/v1/messages')
    const headers = (opts as RequestInit).headers as Record<string, string>
    expect(headers['x-api-key']).toBe('sk-test-123')
    expect(headers['anthropic-version']).toBe('2023-06-01')

    const body = JSON.parse((opts as RequestInit).body as string)
    expect(body.model).toBe('claude-sonnet-4-20250514')
    expect(body.messages[0].content).toContain('process_design')
    expect(body.messages[0].content).toContain('entity context here')
  })
})
