/**
 * The console half of the ss#2284 cross-language parity contract.
 *
 * `canonRosterAddress` decides whether two authored spellings are "the same
 * address" for the validator's collision rules (no address under two outbound
 * classes; no address in both the outbound roster and `inbound_allow_from`).
 * `_canonicalize_roster_entry` in `operator/adapter/recipient_classifier.py`
 * decides the same thing at classify time on the seat. When they disagree, a
 * config passes validation as two addresses and resolves at runtime to one — one
 * human holding two silent exposure classes.
 *
 * They DID disagree. Both real implementations driven on the same inputs:
 * NFD-`josé` canonicalized to a different string than NFC-`josé` (the validator
 * did a bare `trim().toLowerCase()`), and NBSP / ideographic-space / BOM inside
 * a local part were refused here and accepted there.
 *
 * This file and `operator/adapter/tests/test_roster_canon_parity.py` load the
 * SAME fixture. Neither language can move the rule alone.
 */
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { canonRosterAddress } from '../src/lib/operator/customer-yaml/sections-scope'

type Case = { name: string; input: string; expected: string | null }

/** One code point, spelled out — this file is full of characters that render
 *  as nothing, and an invisible literal in a test is a defect waiting to be
 *  'cleaned up' by an editor that trims whitespace. */
const CP = (n: number) => String.fromCodePoint(n)

const cases: Case[] = JSON.parse(
  readFileSync('operator/contracts/fixtures/roster-canon-cases.json', 'utf8')
).cases

describe('roster canonicalization (ss#2284 arbiter fixture)', () => {
  it('the fixture carries the cases that caught the divergence', () => {
    // A fixture that lost its adversarial rows would leave every assertion
    // below passing while measuring nothing (Law 12).
    expect(cases.length).toBeGreaterThanOrEqual(15)
    const inputs = cases.map((c) => c.input)
    expect(inputs.some((i) => i.includes(CP(0x0301)))).toBe(true) // combining acute (NFD)
    expect(inputs.some((i) => i.includes(CP(0x00a0)))).toBe(true) // NBSP
    expect(inputs.some((i) => i.includes(CP(0xfeff)))).toBe(true) // BOM
    expect(inputs.some((i) => i.includes(CP(0x0085)))).toBe(true) // NEL (C1 control)
    // The @domain grant is the only path the runtime's roster-entry check guards
    // alone: it returns before `_canonicalize_address` is ever reached. Without a
    // case here, reverting that check leaves all three suites green — which is
    // exactly what the first run of this fix's falsifier reported.
    expect(cases.some((c) => c.input.startsWith('@') && c.expected === null)).toBe(true)
    expect(cases.some((c) => c.expected !== null)).toBe(true)
  })

  for (const c of cases) {
    it(c.name, () => {
      expect(canonRosterAddress(c.input)).toBe(c.expected)
    })
  }

  it('NFD and NFC spellings of one address collide, so the validator can see them', () => {
    // The defect in one line: these are the same human, and the validator's
    // collision rules only fire if they canonicalize to the same string.
    const nfd = canonRosterAddress(`jose${CP(0x0301)}@firm.example`)
    const nfc = canonRosterAddress(`jos${CP(0x00e9)}@firm.example`)
    expect(nfd).not.toBeNull()
    expect(nfd).toBe(nfc)
  })
})
