/**
 * Chase-voice signature conventions (outbound-quality track).
 *
 * The 2026-08 review found an unattributed vendor draft: a chase left the seat
 * with no indication of whose office it came from. The fix is an AUTHORED
 * signature block specified once in the pack's shared chase voice
 * (_shared-chase-voice.md "Salutation and signature") and inherited by the
 * four chase skills, with the firm name sourced from `customer_name` (or the
 * persona `signature:` schema key) and NEVER hardcoded -- a committed firm
 * display name in a skill body is Pattern A with a client's name on it.
 */
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import { FLOOR_TRIGGER_WORDS, findFloorTrigger } from '../src/lib/operator/floor-triggers'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const SHARED = join(
  REPO_ROOT,
  'operator',
  'verticals',
  'law-firm',
  'addons',
  'pi',
  'references',
  '_shared-chase-voice.md'
)
const SKILLS_DIR = join(REPO_ROOT, 'operator', 'skills')

/** The shared-voice derivation set (_shared-chase-voice.md's own header). */
const INHERITING_SKILLS = [
  'client-verification-tracker',
  'medical-records-chaser',
  'lien-ledger-tracker',
  'discovery-response-tracker',
]

/**
 * Firm display names authored in customer.yaml. A voice file carrying one as a
 * literal would survive the engagement it belongs to (the client-identity gate
 * already bans the domain; this bans the display name).
 */
const FIRM_NAME_LITERALS = ['Ashton & Price', 'Ashton and Price', 'Pilot Law']

describe('shared chase voice: salutation and signature', () => {
  const shared = readFileSync(SHARED, 'utf8')

  it('the shared file specifies the section', () => {
    expect(shared).toContain('## Salutation and signature')
    // The degradation ladder's three rungs, in the authored order.
    expect(shared).toContain('Named contact')
    expect(shared).toContain('Role-addressed')
    expect(shared).toContain('No salutation')
    // The firm line is sourced, never composed.
    expect(shared).toContain('customer_name')
    expect(shared).toContain('signature:')
  })

  it('the rendered block degrades to authored data, never invention', () => {
    expect(shared).toContain('Never a guessed name')
    expect(shared).toMatch(/signature\.firm_line \| customer_name/)
  })

  it('every inheriting voice file references the shared section', () => {
    for (const skill of INHERITING_SKILLS) {
      const path = join(SKILLS_DIR, skill, 'references', 'voice.md')
      expect(existsSync(path), `${skill} has no references/voice.md`).toBe(true)
      const voice = readFileSync(path, 'utf8')
      expect(voice, `${skill}/references/voice.md must derive from the shared file`).toContain(
        '_shared-chase-voice.md'
      )
      expect(
        voice,
        `${skill}/references/voice.md must reference the shared Salutation and signature section`
      ).toContain('Salutation and signature')
    }
  })
})

describe('no voice file hardcodes a firm display name', () => {
  const voiceFiles: string[] = [SHARED]
  for (const skill of readdirSync(SKILLS_DIR)) {
    const candidate = join(SKILLS_DIR, skill, 'references', 'voice.md')
    if (existsSync(candidate)) voiceFiles.push(candidate)
  }

  it('scans a non-trivial set (the check is not vacuous)', () => {
    expect(voiceFiles.length).toBeGreaterThan(10)
  })

  it.each(voiceFiles.map((path) => [path.slice(REPO_ROOT.length), path]))(
    '%s carries no firm-name literal',
    (_label, path) => {
      const content = readFileSync(path, 'utf8')
      for (const literal of FIRM_NAME_LITERALS) {
        expect(content, `${path} hardcodes "${literal}"`).not.toContain(literal)
      }
    }
  )
})

describe('floor-trigger vocabulary stays in step with the shared table', () => {
  // The authoring gate (src/lib/operator/floor-triggers.ts) refuses these
  // words in signature text and chase-arming customer_names. Its source of
  // truth is the substitution table in _shared-chase-voice.md ("Floor-clean by
  // construction"), which mirrors the overlay's content_floor.py categories
  // (hermes-smd-overlay; ADR 0031). The overlay file lives in the other repo,
  // so THIS is the drift guard: a word added to the floor updates the table,
  // the constant, and the overlay pattern in the same change -- and this test
  // is what makes a one-sided edit a red PR.
  const shared = readFileSync(SHARED, 'utf8')

  /** Single-word entries of the table's "Do not write" column. */
  const tableWords = (): string[] => {
    const words: string[] = []
    for (const line of shared.split('\n')) {
      if (!line.startsWith('| ')) continue
      const firstCell = line.split('|')[1]?.trim() ?? ''
      if (firstCell === '' || firstCell.startsWith('Do not write') || firstCell.startsWith('---')) {
        continue
      }
      for (const token of firstCell.split('/')) {
        const word = token.trim().toLowerCase()
        if (word !== '' && !word.includes(' ')) words.push(word)
      }
    }
    return words
  }

  it('every enforced trigger word appears in the table', () => {
    const table = tableWords()
    expect(table.length).toBeGreaterThanOrEqual(10) // the parse found the table
    for (const word of FLOOR_TRIGGER_WORDS) {
      expect(table, `"${word}" is enforced but absent from the shared table`).toContain(word)
    }
  })

  it('every single-word table entry is enforced', () => {
    for (const word of tableWords()) {
      expect(
        [...FLOOR_TRIGGER_WORDS],
        `the shared table bans "${word}" but floor-triggers.ts does not enforce it`
      ).toContain(word)
    }
  })

  it('the matcher refuses words, not substrings, and catches plurals', () => {
    expect(findFloorTrigger('Example, Attorneys at Law')).toBe('attorney')
    expect(findFloorTrigger('Please sign here')).toBe('sign')
    expect(findFloorTrigger('Signal Hill LLP')).toBeNull()
    expect(findFloorTrigger('Coffee & Co')).toBeNull()
    expect(findFloorTrigger('Thank you.')).toBeNull()
  })
})
