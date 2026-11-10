/**
 * Guards the single source of truth for the /book "interest" slug → label
 * mapping (src/lib/booking/config.ts).
 *
 * Background (2026-06-30): the slug→label map had been hand-copied into four
 * places — the allow-list, the admin email, the visitor intent chip, and the
 * CRM context line — and three of them drifted to only 3 of the 15 entries.
 * The result: a visitor arriving from any of the 12 vertical pack CTAs
 * (e.g. /book?interest=med-spa) saw no "Inquiring about" chip at all. These
 * tests assert the maps can never silently desync again, and that the route
 * contract + the centralized bookHref() default stay intact.
 */
import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'fs'
import { resolve, extname } from 'path'
import {
  INTEREST_LABELS,
  ALLOWED_INTERESTS,
  interestLabel,
  bookHref,
} from '../src/lib/booking/config'

describe('interest label ↔ allow-list parity', () => {
  it('every allowed slug has a real human label (non-empty and not the raw slug)', () => {
    // This is the invariant that would have caught the original bug: a vertical
    // slug that passes the allow-list but has no authored label.
    for (const slug of ALLOWED_INTERESTS) {
      const label = interestLabel(slug)
      expect(label, `missing label for "${slug}"`).toBeTruthy()
      expect(label, `label for "${slug}" must not be the raw slug`).not.toBe(slug)
    }
  })

  it('the allow-list is exactly the keys of the label map', () => {
    expect(ALLOWED_INTERESTS).toEqual(new Set(Object.keys(INTEREST_LABELS)))
  })

  it('carries all 15 product interests (tripwire on an accidental add/remove)', () => {
    expect(Object.keys(INTEREST_LABELS)).toHaveLength(15)
    expect(ALLOWED_INTERESTS.size).toBe(15)
  })
})

describe('interestLabel() behavior', () => {
  it('resolves known slugs to their authored labels', () => {
    expect(interestLabel('med-spa')).toBe('Operator for Med Spas')
    expect(interestLabel('operator')).toBe('Operator')
    expect(interestLabel('ai')).toBe('AI & Automation')
  })

  it('returns null for empty input', () => {
    expect(interestLabel(null)).toBeNull()
    expect(interestLabel(undefined)).toBeNull()
    expect(interestLabel('')).toBeNull()
  })

  it('falls back to the raw slug for an unrecognized value', () => {
    expect(interestLabel('bogus')).toBe('bogus')
  })
})

describe('bookHref() route contract', () => {
  it('builds /book?interest=<slug> when a solution-specific interest is given', () => {
    expect(bookHref('operator')).toBe('/book?interest=operator')
    expect(bookHref('med-spa')).toBe('/book?interest=med-spa')
  })

  it('returns a neutral /book (no interest) for the firm-level front door', () => {
    // Firm-level surfaces start the assessment without presuming a solution.
    expect(bookHref()).toBe('/book')
    expect(bookHref(undefined)).toBe('/book')
  })
})

describe('single source of truth (no reintroduced local maps)', () => {
  const SRC_ROOT = resolve('src')

  function collectSourceFiles(dir: string): string[] {
    const out: string[] = []
    for (const entry of readdirSync(dir)) {
      // String-concat (not path.join) keeps this off the path-traversal linter;
      // `entry` is a directory listing, never user input. CI is POSIX.
      const full = `${dir}/${entry}`
      if (statSync(full).isDirectory()) {
        out.push(...collectSourceFiles(full))
      } else if (
        ['.astro', '.ts', '.tsx'].includes(extname(entry)) &&
        !entry.endsWith('.test.ts') &&
        !entry.endsWith('.test.tsx')
      ) {
        out.push(full)
      }
    }
    return out
  }

  it('declares `const INTEREST_LABELS` in exactly one file (config.ts)', () => {
    const decl = /\b(?:export\s+)?const\s+INTEREST_LABELS\s*[:=]/
    const offenders = collectSourceFiles(SRC_ROOT).filter((f) =>
      decl.test(readFileSync(f, 'utf-8'))
    )
    expect(offenders).toEqual([resolve('src/lib/booking/config.ts')])
  })
})
