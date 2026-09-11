/**
 * Setup-turn reply rules drift gate (ss welcome rehearsal, 2026-08-21).
 *
 * The four skills a "initialize yourself and set up for our firm" request runs
 * are the conductor (`operator-self-initiation`) plus the three acts its
 * `self_initiation.sequence` names on both seats: `operator-self-test`,
 * `voice-establishment`, `document-library-establishment`. During the welcome
 * rehearsal on the Ashton & Price seat, two things went wrong in prose that no
 * test covered:
 *
 *   1. After the outbound citation gate refused its own draft several times,
 *      the Operator's reply to the firm's administrator explained itself in
 *      machinery vocabulary: "the citation gate", "gate logs", what a person
 *      should go look at. The firm is a law firm, not our on-call. A reply that
 *      narrates our internal refusals is a reply that should have been shorter.
 *
 *   2. The document-library proposal nominated a LIVE CLIENT MATTER (the most
 *      documented one it found in the survey) as the home for the firm's
 *      template folder. The old prose invited exactly that: it said to propose
 *      "a matter you name from the survey", and offered a heuristic ("an
 *      administrative or internal matter is a better home") that a model
 *      satisfies by guessing from a matter's name or type. A matter called
 *      "Office Depot" is a client's case until the firm says otherwise.
 *
 * This gate pins the corrected prose. It is a string gate on skill bodies
 * because skill bodies ARE the runtime for these turns: the conductor reads
 * `/app/skills/<slug>/SKILL.md` at turn time and the model acts on what it
 * says. There is no code path to unit-test between the sentence and the act.
 *
 * The third rule pinned here is the caption trap that started (1): the
 * outbound citation filter reads "Name v. Name" as a court-case caption, so a
 * reply comparing two capitalized things with "vs" / "v." / "versus" refuses
 * itself. The skills now say "compared with". This file asserts the skills do
 * not themselves model the banned form in their instruction prose, since a
 * skill body is the model's nearest example of how to write.
 */

import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync, readdirSync } from 'fs'
import { resolve } from 'path'

// Literal path constants (never interpolated) so the paths are auditable.
const SKILL_PATHS = {
  'operator-self-initiation': resolve('operator/skills/operator-self-initiation/SKILL.md'),
  'operator-self-test': resolve('operator/skills/operator-self-test/SKILL.md'),
  'voice-establishment': resolve('operator/skills/voice-establishment/SKILL.md'),
  'document-library-establishment': resolve(
    'operator/skills/document-library-establishment/SKILL.md'
  ),
} as const

type SkillName = keyof typeof SKILL_PATHS

const SKILLS = Object.keys(SKILL_PATHS) as SkillName[]

function body(skill: SkillName): string {
  return readFileSync(SKILL_PATHS[skill], 'utf-8')
}

/**
 * The body with every whitespace run collapsed to one space.
 *
 * Every phrase pinned below is matched against THIS, not the raw body: these
 * files hard-wrap prose near 95 columns, so any pinned sentence can be split
 * across a line at any time by an unrelated edit that reflows a paragraph. A
 * raw `toContain` would then pass a `not.toContain` guard for the wrong
 * reason, which is exactly how the pre-fix body scanned clean on two of these
 * assertions.
 */
function flat(skill: SkillName): string {
  return body(skill).replace(/\s+/g, ' ')
}

/**
 * Strip everything that is not instruction prose: fenced code blocks, inline
 * backtick spans, and quoted spans.
 *
 * Quoted spans are excluded because the rules themselves have to NAME the
 * banned separators to ban them ("never 'vs', 'vs.', 'v.', or 'versus'"), and
 * a gate that fires on its own rule statement is a gate nobody can satisfy.
 * Replacement is a single space, not the empty string, so stripping a span
 * cannot glue two words into a false match.
 */
function proseOnly(text: string): string {
  return text
    .replace(/^```[\s\S]*?^```/gm, ' ') // fenced code blocks
    .replace(/`[^`\n]*`/g, ' ') // inline code spans
    .replace(/"[^"\n]*"/g, ' ') // double-quoted spans
    .replace(/[''][^''\n]*['']/g, ' ') // curly single-quoted spans
    .replace(/'[^'\n]*'/g, ' ') // straight single-quoted spans
}

/**
 * A capitalized token, a case separator, another capitalized token: the shape
 * the outbound citation filter reads as a court-case caption.
 */
const CAPTION_SHAPE = /\b[A-Z][\w'.-]+ (vs\.?|v\.|versus) [A-Z]/g

describe('setup-turn reply rules are pinned in skill prose', () => {
  describe('document-library-establishment never offers a client file as the shelf', () => {
    it('carries the hard floor sentence', () => {
      expect(
        flat('document-library-establishment'),
        'the storage-location section must state the floor in these words: a client matter ' +
          'is never nominated as the library home, however well documented it is'
      ).toContain('Never nominate a client matter')
    })

    it('no longer tells the Operator to name the matter itself from the survey', () => {
      expect(
        flat('document-library-establishment'),
        "the survey is a list of the firm's matters, most of them clients' cases. Naming " +
          'one from it is guessing. The skill must ask the firm for an internal matter ' +
          'and stop.'
      ).not.toContain('a matter you name from the survey')
    })

    it('no longer offers the guess-from-the-name heuristic', () => {
      expect(
        flat('document-library-establishment'),
        'a matter typed "Internal Affairs" is a client\'s case until the firm says otherwise'
      ).not.toContain('an administrative or internal matter is a better home')
    })

    // ss-console#2536. The old branch ended the conversation: it told the admin
    // the Operator cannot create a matter and to go make one by hand. It can
    // now offer to create ONE matter, the firm's authored internal one, and the
    // admin's own words are the authority. Two strings are pinned because both
    // are load-bearing at runtime: the phrase the admin is asked to reply with
    // (it is what the confirming matcher binds on), and the convention number
    // the library and the self-test fall back to.
    it('offers to create the authored internal matter and names the confirming words', () => {
      expect(
        flat('document-library-establishment'),
        'the storage-location section must ask the admin for these exact words, because ' +
          'that is the phrase the readback tells them to reply with'
      ).toContain('yes, create it')
    })

    it('names the convention number the library falls back to', () => {
      expect(
        flat('document-library-establishment'),
        'the skill must name OPS-OPERATOR-LIBRARY, the number the Operator library uses ' +
          'when the firm authored none, so the skill and the resolver agree on one key'
      ).toContain('OPS-OPERATOR-LIBRARY')
    })

    it('still refuses to create anything on the proposal turn', () => {
      expect(
        flat('document-library-establishment'),
        'the offer is a proposal like any other: the turn that makes it creates nothing'
      ).toContain('Nothing is created on this turn')
    })
  })

  describe('operator-self-initiation status board speaks to the firm', () => {
    it('forbids naming the machinery in the reply', () => {
      expect(
        flat('operator-self-initiation'),
        'the status board must not mention gates, filters, refusals of its own drafts, or logs'
      ).toContain('never mentions gates')
    })

    it('caps redrafts at one', () => {
      expect(
        flat('operator-self-initiation'),
        "a refused board is redrafted once against the refusal's stated kind, then sent. " +
          "The rehearsal's machinery leak came out of a retry loop."
      ).toContain('One considered redraft')
    })
  })

  describe('voice-establishment carries the comparison wording', () => {
    it('names the safe form', () => {
      expect(
        flat('voice-establishment'),
        '"compared with" is the form that does not trip the outbound caption filter'
      ).toContain('compared with')
    })
  })

  describe('no skill models a caption-shaped comparison in its own prose', () => {
    for (const skill of SKILLS) {
      it(`${skill} instruction prose has no "Name v. Name" form`, () => {
        const hits = [...proseOnly(body(skill)).matchAll(CAPTION_SHAPE)].map((m) => m[0])
        expect(
          hits,
          `${SKILL_PATHS[skill]} models the shape the outbound citation filter reads as a ` +
            `court-case caption. Rewrite as "compared with" or restructure the sentence. ` +
            `Hits: ${JSON.stringify(hits)}`
        ).toEqual([])
      })
    }
  })

  describe('no fenced report template carries a tier-1 fabrication marker', () => {
    // The fixed-shape boards inside fenced blocks are what the model reproduces
    // verbatim on the reply. On 2026-08-21 the self-initiation board header
    // ("OPERATOR SELF-INITIATION | [seat] | [date]", then written with dashes)
    // cost two refused drafts per turn on the A&P seat (tier1_marker em-dash)
    // before the model substituted a pipe. Every operator skill is checked, not
    // just the four setup ones: the introduce skill's rule template had the
    // same dash.
    const allSkills = readdirSync(resolve(__dirname, '..', 'operator', 'skills'), {
      withFileTypes: true,
    })
      .filter((d) => d.isDirectory())
      .map((d) => resolve(__dirname, '..', 'operator', 'skills', d.name, 'SKILL.md'))
      .filter((f) => existsSync(f))
    for (const file of allSkills) {
      it(`${file.split('/operator/')[1]} fenced blocks have no em dash`, () => {
        const fenced: string[] = []
        let inFence = false
        for (const line of readFileSync(file, 'utf-8').split('\n')) {
          if (line.trim().startsWith('```')) {
            inFence = !inFence
            continue
          }
          if (inFence && line.includes('\u2014')) fenced.push(line)
        }
        expect(
          fenced,
          `${file}: a fenced template line carries an em dash; the model copies it and the ` +
            `tier-1 fabrication marker refuses the draft. Use " | " or a comma.`
        ).toEqual([])
      })
    }
  })
})
