/**
 * Venture Handbook sections: the single definition every consumer reads.
 *
 * The handbook is graded against one reader: a new administrator who runs the
 * business and is not an engineer. The first three groups are written for that
 * reader; the Engineering Appendix is reached by link. Audience is a property of
 * the section, so a page cannot claim an audience its group contradicts.
 *
 * Consumers: src/content.config.ts (schema enum), the playbook sidebar and
 * landing page, tests/handbook-integrity.test.ts, and the ADR-index generator.
 */

export const HANDBOOK_SECTION_KEYS = ['running', 'business', 'product', 'engineering'] as const

export type HandbookSectionKey = (typeof HANDBOOK_SECTION_KEYS)[number]

export type HandbookAudience = 'admin' | 'engineer'

export interface HandbookSection {
  key: HandbookSectionKey
  label: string
  blurb: string
  audience: HandbookAudience
}

export const HANDBOOK_SECTIONS: readonly HandbookSection[] = [
  {
    key: 'running',
    label: 'Running SMD',
    blurb: 'What arrives, what we owe, who decides, and what to do when something breaks.',
    audience: 'admin',
  },
  {
    key: 'business',
    label: 'The Business',
    blurb: 'What SMD is, what it sells, how it talks, and what has been decided.',
    audience: 'admin',
  },
  {
    key: 'product',
    label: 'The Product',
    blurb: 'The consoles and packs clients and staff actually use.',
    audience: 'admin',
  },
  {
    key: 'engineering',
    label: 'Engineering Appendix',
    blurb:
      'Reached by link; not required reading. During continuity an administrator also needs Secrets & Access and Deployment & Release.',
    audience: 'engineer',
  },
]

export const HANDBOOK_OWNERS = ['captain', 'administrator', 'engineering'] as const

export type HandbookOwner = (typeof HANDBOOK_OWNERS)[number]

export const OWNER_LABELS: Record<HandbookOwner, string> = {
  captain: 'Captain',
  administrator: 'Administrator',
  engineering: 'Engineering',
}
