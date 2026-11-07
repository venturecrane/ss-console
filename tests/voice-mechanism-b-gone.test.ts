/**
 * Forbidden strings: the sample-transform voice mechanism's config keys and console
 * code cannot come back (ADR 0083 decision 6, "Mechanism B is removed"; CLAUDE.md
 * "Gone means gone").
 *
 * The keys are named in exactly one place in src/, the retired-fields list the
 * validator refuses them by (`RETIRED_TOP_LEVEL_FIELDS`). The D1 column
 * `voice_library_json` stays until a later migration drops it, and nothing writes
 * it; that name is allowed and the key itself is not. The Python twin is
 * operator/hermes-smd-overlay/tests/test_voice_mechanism_b_gone.py.
 */

import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { parse as parseYaml } from 'yaml'
import { validate } from '../src/lib/operator/customer-yaml/index.js'

const RETIRED = ['voice_library', 'voice_cohorts'] as const
const FORBIDDEN = [
  /\bvoice_library\b(?!_json)/,
  /\bvoice_cohorts\b/,
  /\bVoiceLibrary\b/,
  /\bVoiceCohorts\b/,
  /\bBASE_VOICE_COHORTS\b/,
  /\bresolveCohortVocabulary\b/,
  /\bcheckVoiceLibrary\b/,
  /\bcheckVoiceCohorts\b/,
  /sections-voice/,
]

function walk(dir: string, exts: string[]): string[] {
  const out: string[] = []
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) out.push(...walk(p, exts))
    else if (exts.some((e) => p.endsWith(e))) out.push(p)
  }
  return out
}

describe('the sample-transform voice mechanism is gone from the console', () => {
  const files = walk(resolve('src'), ['.ts', '.tsx', '.astro'])

  it('scans a real tree', () => {
    expect(files.length).toBeGreaterThan(100)
  })

  it('no source file names a retired key or the removed code, but the retired-fields list', () => {
    const hits: string[] = []
    for (const file of files) {
      readFileSync(file, 'utf8')
        .split('\n')
        .forEach((line, i) => {
          if (line.includes('RETIRED_TOP_LEVEL_FIELDS = [')) return
          for (const pattern of FORBIDDEN) {
            if (pattern.test(line)) hits.push(`${file}:${i + 1}: ${line.trim()}`)
          }
        })
    }
    expect(hits).toEqual([])
  })

  it('the check can fail: a planted line is caught', () => {
    expect(FORBIDDEN.some((p) => p.test("  const raw = root['voice_library']"))).toBe(true)
    expect(FORBIDDEN.some((p) => p.test('    voice_library_json: null,'))).toBe(false)
  })

  it('no seat, preset or template authors a retired key', () => {
    const yamls = [
      ...walk(resolve('operator/customers'), ['customer.yaml']),
      ...walk(resolve('operator/templates'), ['.yaml']),
    ]
    expect(yamls.length).toBeGreaterThan(7)
    for (const file of yamls) {
      const doc = parseYaml(readFileSync(file, 'utf8')) as Record<string, unknown> | null
      if (!doc || typeof doc !== 'object') continue
      for (const key of RETIRED) expect(key in doc, `${file} authors ${key}`).toBe(false)
    }
  })

  it.each(RETIRED)('the console validator refuses %s on a real seat', (key) => {
    const doc = parseYaml(
      readFileSync(resolve('operator/customers/ashton-price/customer.yaml'), 'utf8')
    ) as Record<string, unknown>
    const clean = validate(structuredClone(doc))
    expect(clean.ok, JSON.stringify(clean.ok ? [] : clean.errors.slice(0, 3))).toBe(true)
    const r = validate({ ...doc, [key]: { samples_path: 'r2://vaults/x/voice/' } })
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === key && e.code === 'BannedFieldName')).toBe(true)
  })
})
