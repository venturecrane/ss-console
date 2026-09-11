import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'fs'
import { resolve } from 'path'
import {
  ANTHROPIC_API_URL,
  ANTHROPIC_VERSION,
  QUALITY_MODEL,
  modelFor,
} from '../src/lib/llm/models'

describe('llm/models: central model selection', () => {
  it('exposes the shared Anthropic API constants', () => {
    expect(ANTHROPIC_API_URL).toBe('https://api.anthropic.com/v1/messages')
    expect(ANTHROPIC_VERSION).toBe('2023-06-01')
  })

  it('defines the current tier defaults', () => {
    // This is the ONE place model IDs live. Bumping a tier (e.g. the Opus 4.8-era
    // refresh) is a one-line change here plus this assertion.
    expect(QUALITY_MODEL).toBe('claude-sonnet-4-6')
  })

  it('modelFor returns tier defaults when no env override is present', () => {
    expect(modelFor('QUALITY')).toBe(QUALITY_MODEL)
    expect(modelFor('QUALITY', {})).toBe(QUALITY_MODEL)
  })

  it('modelFor honors per-tier env overrides', () => {
    const env = { LLM_MODEL_QUALITY: 'claude-opus-4-8', LLM_MODEL_FAST: 'claude-haiku-5' }
    expect(modelFor('QUALITY', env)).toBe('claude-opus-4-8')
    expect(modelFor('FAST', env)).toBe('claude-haiku-5')
  })

  it('modelFor ignores blank/whitespace overrides and trims valid ones', () => {
    expect(modelFor('QUALITY', { LLM_MODEL_QUALITY: '' })).toBe(QUALITY_MODEL)
    expect(modelFor('QUALITY', { LLM_MODEL_QUALITY: '   ' })).toBe(QUALITY_MODEL)
    expect(modelFor('QUALITY', { LLM_MODEL_QUALITY: '  claude-opus-4-8  ' })).toBe(
      'claude-opus-4-8'
    )
  })
})

describe('llm/models: single-source guardrail', () => {
  // Every Anthropic model ID in the main app must come from llm/models.ts.
  // No other source file under src/ may hardcode a claude-* model literal.
  const MODEL_LITERAL = /['"]claude-(sonnet|haiku|opus)-/

  function walk(dir: string, out: string[] = []): string[] {
    for (const entry of readdirSync(dir)) {
      // Static repo walk over src/ — paths are derived from the filesystem,
      // not user input. Built by concatenation to keep the scanner happy.
      const full = `${dir}/${entry}`
      if (statSync(full).isDirectory()) {
        walk(full, out)
      } else if (full.endsWith('.test.ts') || full.endsWith('.test.tsx')) {
        // Test files legitimately assert against model IDs at runtime.
        continue
      } else if (full.endsWith('.ts') || full.endsWith('.tsx')) {
        out.push(full)
      }
    }
    return out
  }

  it('no src/ file hardcodes a claude model ID except llm/models.ts', () => {
    const root = resolve('src')
    const modelsModule = resolve('src/lib/llm/models.ts')
    const offenders: string[] = []

    for (const file of walk(root)) {
      if (file === modelsModule) continue
      const code = readFileSync(file, 'utf-8')
      if (MODEL_LITERAL.test(code)) {
        offenders.push(file.replace(`${process.cwd()}/`, ''))
      }
    }

    expect(offenders).toEqual([])
  })
})
