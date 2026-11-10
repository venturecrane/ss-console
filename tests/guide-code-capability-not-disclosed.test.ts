/**
 * The guide seat never tells a non-administrator that it can generate an
 * invite code (owner's rule, 2026-11-10; ADR 0106).
 *
 * Who is an administrator is decided in code, by the overlay tool
 * `invite_code_issue`, never by the model. So the guard checks every text a
 * non-administrator can be shown, or that the model answers them from:
 *
 *   1. The tool's reply to anyone it has not confirmed as an administrator
 *      (`DECLINE` in the enrollment plugin) is exactly the access sentence,
 *      and that sentence names no capability and nothing about a role. The
 *      older refusal wordings (the "issued only to administrators" decline and
 *      the chat refusal) are gone from the plugin.
 *   2. The product-guide skill tells the model to send that same sentence for
 *      any other question about who makes codes, and carries none of the old
 *      wording.
 *   3. No document in the product library or the pitch-practice references,
 *      and no row of the library index, says that the guide, the seat or an
 *      administrator makes, issues or gets codes. The administrator how-to
 *      lives in the handbook (connectors-channels.md), which no seat reads.
 *
 * The last describe shows each check fails on the wording it replaced.
 * `GUARD_ROOT` points the checks at another tree (used once to show the guard
 * failing on origin/main before the fix).
 */

import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'

const ROOT = resolve(process.env.GUARD_ROOT ?? '.')
const PLUGIN = join(ROOT, 'operator/hermes-smd-overlay/plugins/hermes-smd-enrollment/__init__.py')
const SKILL = join(ROOT, 'operator/skills/product-guide/SKILL.md')
const LIBRARY = join(ROOT, 'operator/skills/product-guide/references')
const PRACTICE = join(ROOT, 'operator/skills/pitch-practice')

const ACCESS_SENTENCE = 'Access to the guide comes from SMD Services, at team@smd.services.'

/** Words a reply to a non-administrator may never carry. */
const CAPABILITY = /\bcodes?\b|\bissu|\badministrator|\badmin\b|\brefus|\binvite|\brole\b/gi

/** Old wordings that told a non-administrator the seat issues codes. */
const OLD_WORDINGS = [
  /issued only to the guide seat's administrators/i,
  /codes are\s+issued only/i,
  /an administrator asks from their own/i,
  /CHAT_REFUSAL/,
]

/**
 * A sentence in the library that discloses the capability: it is about codes
 * and says an administrator gets or issues them, or that the guide or seat
 * makes or issues them, or quotes the administrator's request phrase.
 */
function disclosures(text: string): string[] {
  const out: string[] = []
  for (const raw of text.split(/(?<=[.!?|])\s+|\n+/)) {
    const s = raw.trim()
    // An invite code, not program code ("fixed by code" is not about one).
    if (!/\binvite codes?\b|\bcodes\b|\b(a|the|your|one) code\b/i.test(s)) continue
    if (
      /\badministrators?\b|\badmins?\b/i.test(s) ||
      /\bissu(e|es|ed|ing)\b/i.test(s) ||
      /I need a code/i.test(s) ||
      /\b(guide|seat)\b[^.]*\b(makes?|creates?|generates?|mints?)\b/i.test(s)
    ) {
      out.push(s)
    }
  }
  return out
}

function declineLiteral(py: string): string | null {
  const m = py.match(/^DECLINE\s*=\s*(\(([\s\S]*?)\)|"([^"]*)")\s*$/m)
  if (!m) return null
  const body = m[2] ?? `"${m[3]}"`
  return [...body.matchAll(/"([^"]*)"/g)].map((x) => x[1]).join('')
}

function mdFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory()
      ? mdFiles(join(dir, e.name))
      : /\.(md|ya?ml)$/.test(e.name)
        ? [join(dir, e.name)]
        : []
  )
}

describe('the guide seat does not disclose that it can issue codes', () => {
  it("the tool's reply to a non-administrator is the access sentence, naming no capability", () => {
    const py = readFileSync(PLUGIN, 'utf8')
    const decline = declineLiteral(py)
    expect(decline).toBe(ACCESS_SENTENCE)
    expect(decline?.match(CAPABILITY) ?? []).toEqual([])
    expect(OLD_WORDINGS.filter((re) => re.test(py)).map(String)).toEqual([])
  })

  it('the skill sends the same sentence for any other question about who makes codes', () => {
    const skill = readFileSync(SKILL, 'utf8').replace(/\s+/g, ' ')
    expect(skill).toContain(`"${ACCESS_SENTENCE.replace(/\s+/g, ' ')}"`)
    expect(OLD_WORDINGS.filter((re) => re.test(skill)).map(String)).toEqual([])
  })

  it('no library document, index row or practice reference says who makes or issues codes', () => {
    const found: string[] = []
    for (const f of [...mdFiles(LIBRARY), ...mdFiles(PRACTICE)]) {
      for (const s of disclosures(readFileSync(f, 'utf8'))) found.push(`${f}: ${s}`)
    }
    expect(found).toEqual([])
  })
})

describe('each check fails on the wording it replaced', () => {
  it('the capability words catch the old decline and the old chat refusal', () => {
    const oldDecline =
      "Invite codes are issued only to the guide seat's administrators at SMD Services. " +
      'If someone needs access, ask SMD Services at team@smd.services.'
    expect(oldDecline.match(CAPABILITY)?.length).toBeGreaterThan(0)
    expect(OLD_WORDINGS.some((re) => re.test(oldDecline))).toBe(true)
  })

  it('the library check catches the old connecting section and index row', () => {
    expect(
      disclosures(
        "This section is for the guide's administrators at SMD Services. Codes are issued only to them."
      ).length
    ).toBeGreaterThan(0)
    expect(
      disclosures(
        '| Starting to use the guide with an invite code, and how an administrator gets a code for someone |'
      ).length
    ).toBeGreaterThan(0)
    expect(disclosures('1. Get an invite code from SMD Services.')).toEqual([])
  })

  it('the DECLINE reader reads both a one-line and a parenthesised literal', () => {
    expect(declineLiteral(`DECLINE = "${ACCESS_SENTENCE}"\n`)).toBe(ACCESS_SENTENCE)
    expect(declineLiteral('DECLINE = (\n    "a "\n    "b."\n)\n')).toBe('a b.')
  })
})
