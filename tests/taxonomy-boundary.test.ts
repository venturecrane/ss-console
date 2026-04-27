/**
 * Taxonomy boundary test — enforces ADR 0001 (two-layer taxonomy model).
 *
 * The lead-gen subsystem uses the 5-category *observation* taxonomy
 * (process_design, tool_systems, data_visibility, customer_pipeline,
 * team_operations). The marketing subsystem uses the 6-category *delivery*
 * taxonomy (process design, custom internal tools, systems integration,
 * operational visibility, vendor/platform selection, AI & automation).
 *
 * The two layers are deliberately distinct. This test asserts the lead-gen
 * prompt files do not drift into the marketing delivery taxonomy by name.
 *
 * @see docs/adr/0001-taxonomy-two-layer-model.md
 * @see https://github.com/venturecrane/ss-console/issues/591
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'
import { PROBLEM_IDS } from '../src/lead-gen/schemas/lead-scoring-schema.js'

// Inline string literals — same pattern used by other tests in this repo
// (see tests/claude-extract.test.ts) — keeps the path argument to resolve()
// a literal so it cannot be construed as user input.
function readPromptFiles(): { path: string; content: string }[] {
  return [
    {
      path: 'src/lead-gen/prompts/job-qualification-prompt.ts',
      content: readFileSync(resolve('src/lead-gen/prompts/job-qualification-prompt.ts'), 'utf-8'),
    },
    {
      path: 'src/lead-gen/prompts/new-business-prompt.ts',
      content: readFileSync(resolve('src/lead-gen/prompts/new-business-prompt.ts'), 'utf-8'),
    },
    {
      path: 'src/lead-gen/prompts/partner-nurture-prompt.ts',
      content: readFileSync(resolve('src/lead-gen/prompts/partner-nurture-prompt.ts'), 'utf-8'),
    },
    {
      path: 'src/lead-gen/prompts/review-scoring-prompt.ts',
      content: readFileSync(resolve('src/lead-gen/prompts/review-scoring-prompt.ts'), 'utf-8'),
    },
  ]
}

describe('lead-gen prompts — observation taxonomy boundary', () => {
  it('does not reference marketing delivery taxonomy IDs (snake_case)', () => {
    const files = readPromptFiles()
    expect(files.length).toBeGreaterThan(0)

    // These snake_case identifiers are the marketing 6-cat labels normalized.
    // None of them should ever appear in lead-gen prompts — outreach speaks
    // observation, not delivery.
    const forbiddenIds = [
      'custom_internal_tools',
      'systems_integration',
      'operational_visibility',
      'vendor_platform_selection',
      'ai_automation',
    ]

    for (const { path, content } of files) {
      for (const id of forbiddenIds) {
        expect(
          content,
          `${path} references marketing delivery ID "${id}" — forbidden by ADR 0001`
        ).not.toContain(id)
      }
    }
  })

  it('does not contain marketing delivery taxonomy phrases verbatim', () => {
    const files = readPromptFiles()
    // These phrases are doctrinal marketing-side labels. They should never appear
    // in lead-gen prompts — outreach speaks observation, not delivery.
    const forbidden = [
      'Custom internal tools',
      'Vendor/platform selection',
      'Systems integration',
      'Operational visibility',
    ]
    for (const { path, content } of files) {
      for (const phrase of forbidden) {
        expect(
          content,
          `${path} contains marketing delivery phrase "${phrase}" — forbidden by ADR 0001`
        ).not.toContain(phrase)
      }
    }
  })

  it('canonical observation taxonomy has exactly 5 IDs', () => {
    expect(PROBLEM_IDS).toHaveLength(5)
    expect([...PROBLEM_IDS].sort()).toEqual(
      [
        'customer_pipeline',
        'data_visibility',
        'process_design',
        'team_operations',
        'tool_systems',
      ].sort()
    )
  })

  it('prompts collectively reference all 5 observation IDs', () => {
    const files = readPromptFiles()
    const allContent = files.map((f) => f.content).join('\n')
    for (const id of PROBLEM_IDS) {
      expect(allContent, `No prompt mentions observation ID "${id}"`).toContain(id)
    }
  })
})
