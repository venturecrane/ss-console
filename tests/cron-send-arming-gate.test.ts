/**
 * The arming gate: a cron-armed derived-outbound skill must carry an authored
 * render declaration.
 *
 * WHY (2026-08-24..31 outbound-quality review, 11 defects). Format drift,
 * unstable ACK codes, and recipient flapping are all the model re-composing a
 * routine email nobody declared a render mode for. The durable fix renders
 * templated bodies deterministically; this gate is what keeps the NEXT armed
 * routine from shipping undeclared. It runs in `npm run verify`, so the exact
 * PR that uncomments a seat's cron rows (ashton-price is sitting at `cron: []`
 * with 12 commented rows today) runs this join.
 *
 * The join itself lives ONCE in src/lib/operator/send-render.ts and is shared
 * with the provision-time backstop in scripts/validate-customer-yaml.ts.
 * Everything here is parsed -- customer.yaml through the canonical validate(),
 * the contracts through their parsers -- never grepped (config gates parse).
 *
 * `compositional` is a valid authored state, not a bypass: the gate refuses
 * the UNDECLARED. The falsifier at the bottom proves the gate can fire at all,
 * because a check that cannot fail measures nothing.
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'
import { parse as parseYaml } from 'yaml'

import { validate } from '../src/lib/operator/customer-yaml'
import {
  CHASE_SIGNATURE_SKILLS,
  armingViolations,
  customerNameFloorViolations,
  parseOutboundBindings,
  parseSendRender,
  templateHygieneViolations,
  type RenderDecl,
} from '../src/lib/operator/send-render'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const CUSTOMERS_DIR = join(REPO_ROOT, 'operator', 'customers')
const SEND_RENDER = join(REPO_ROOT, 'operator', 'contracts', 'send-render.yaml')
const OUTPUT_CLASSES = join(REPO_ROOT, 'operator', 'contracts', 'output-classes.yaml')

const templateExists = (path: string): boolean => existsSync(join(REPO_ROOT, path))

const renders = parseSendRender(readFileSync(SEND_RENDER, 'utf8'))
const outbound = parseOutboundBindings(readFileSync(OUTPUT_CLASSES, 'utf8'))

interface Seat {
  slug: string
  customerName: string
  firmLineAuthored: boolean
  cron: { skill: string }[]
}

/** Every authored seat's cron rows, through the CANONICAL validator. */
function authoredSeats(): Seat[] {
  const seats: Seat[] = []
  for (const entry of readdirSync(CUSTOMERS_DIR).sort()) {
    if (entry.startsWith('_') || entry.startsWith('.')) continue
    const path = join(CUSTOMERS_DIR, entry, 'customer.yaml')
    if (!existsSync(path)) continue
    const result = validate(parseYaml(readFileSync(path, 'utf8')))
    // A seat the canonical validator refuses cannot be evaluated, and
    // cannot-evaluate must never read as permitted.
    expect(
      result.ok,
      `${entry}/customer.yaml fails canonical validation; the arming gate cannot evaluate it` +
        (result.ok ? '' : `: ${JSON.stringify(result.errors.slice(0, 3))}`)
    ).toBe(true)
    if (!result.ok) continue
    seats.push({
      slug: entry,
      customerName: result.value.customer_name,
      firmLineAuthored: result.value.personas.some(
        (persona) => typeof persona.signature?.firm_line === 'string'
      ),
      cron: result.value.personas.flatMap((persona) =>
        persona.cron.map((row) => ({ skill: row.skill }))
      ),
    })
  }
  return seats
}

describe('cron-send arming gate', () => {
  const seats = authoredSeats()

  it('sees the authored fleet (the join is not vacuous)', () => {
    // pilot-smokeball's grid is the live proving ground; if it stops parsing
    // here the gate is measuring nothing.
    const pilot = seats.find((seat) => seat.slug === 'pilot-smokeball')
    expect(pilot).toBeDefined()
    expect(pilot!.cron.length).toBeGreaterThanOrEqual(6)
    expect(renders.size).toBeGreaterThanOrEqual(7)
  })

  it('every cron-armed derived-outbound skill is declared in send-render.yaml', () => {
    for (const seat of seats) {
      const violations = armingViolations({
        seat: seat.slug,
        cron: seat.cron,
        outbound,
        renders,
        templateExists,
      })
      expect(violations.map((v) => v.message)).toEqual([])
    }
  })

  it('every hash-verified declaration names a template that exists', () => {
    expect(templateHygieneViolations(renders, templateExists).map((v) => v.message)).toEqual([])
  })

  it('no chase-arming seat authors a floor-trigger customer_name', () => {
    // The signature block renders the name into every chase body; a trigger
    // word in it would have the ADR 0031 floor hold every autonomous chase as
    // a draft (PR #2651 review, finding 3).
    for (const seat of seats) {
      const violations = customerNameFloorViolations({
        seat: seat.slug,
        customerName: seat.customerName,
        cron: seat.cron,
        firmLineAuthored: seat.firmLineAuthored,
      })
      expect(violations.map((v) => v.message)).toEqual([])
    }
  })

  it('the chase-signature consuming set matches the shared voice derivation set', () => {
    // _shared-chase-voice.md's own header names the skills that derive from it.
    expect([...CHASE_SIGNATURE_SKILLS].sort()).toEqual([
      'client-verification-tracker',
      'discovery-response-tracker',
      'lien-ledger-tracker',
      'medical-records-chaser',
    ])
  })

  it('every declared skill is a real skill directory', () => {
    // A typo'd declaration would silently gate nothing.
    for (const skill of renders.keys()) {
      expect(
        existsSync(join(REPO_ROOT, 'operator', 'skills', skill, 'SKILL.md')),
        `send-render.yaml declares "${skill}" but no operator/skills/${skill}/SKILL.md exists`
      ).toBe(true)
    }
  })
})

describe('the join itself (synthetic seats)', () => {
  const syntheticOutbound = new Map<string, string>([
    ['chaser', 'derived'],
    ['digest', 'none'],
  ])
  const declaredCompositional = new Map<string, RenderDecl>([
    ['chaser', { skill: 'chaser', render: 'compositional', template: null }],
  ])

  it('an armed derived-outbound skill with no declaration is refused', () => {
    const violations = armingViolations({
      seat: 'x',
      cron: [{ skill: 'chaser' }],
      outbound: syntheticOutbound,
      renders: new Map(),
      templateExists: () => true,
    })
    expect(violations.map((v) => v.code)).toEqual(['undeclared-render'])
  })

  it('declared compositional is a valid authored state, not a bypass', () => {
    const violations = armingViolations({
      seat: 'x',
      cron: [{ skill: 'chaser' }],
      outbound: syntheticOutbound,
      renders: declaredCompositional,
      templateExists: () => false,
    })
    expect(violations).toEqual([])
  })

  it('an outbound: none skill needs no declaration', () => {
    const violations = armingViolations({
      seat: 'x',
      cron: [{ skill: 'digest' }],
      outbound: syntheticOutbound,
      renders: new Map(),
      templateExists: () => true,
    })
    expect(violations).toEqual([])
  })

  it('a templated declaration whose artifact is missing is refused', () => {
    const templated = new Map<string, RenderDecl>([
      ['chaser', { skill: 'chaser', render: 'templated', template: 'operator/x/template.md' }],
    ])
    const violations = armingViolations({
      seat: 'x',
      cron: [{ skill: 'chaser' }],
      outbound: syntheticOutbound,
      renders: templated,
      templateExists: () => false,
    })
    expect(violations.map((v) => v.code)).toEqual(['missing-template'])
  })

  it('a cron skill absent from output-classes cannot be evaluated and is refused', () => {
    const violations = armingViolations({
      seat: 'x',
      cron: [{ skill: 'mystery' }],
      outbound: syntheticOutbound,
      renders: new Map(),
      templateExists: () => true,
    })
    expect(violations.map((v) => v.code)).toEqual(['unknown-outbound'])
  })

  it('a malformed contract throws rather than gating against an empty set', () => {
    expect(() => parseSendRender('skills: []')).toThrow()
    expect(() => parseSendRender('skills:\n  x:\n    render: freestyle\n')).toThrow()
    expect(() => parseSendRender('skills:\n  x:\n    render: templated\n')).toThrow()
    expect(() => parseOutboundBindings('nothing: here')).toThrow()
  })
})

describe('falsifier: the gate can fire on the real fleet', () => {
  it('removing one live declaration produces the violation', () => {
    // Prove the join is wired to the real data: drop deadline-miss-escalator
    // from a COPY of the committed contract and the pilot seat must redden.
    const regressed = new Map(renders)
    expect(regressed.delete('deadline-miss-escalator')).toBe(true)
    const seats = authoredSeats()
    const pilot = seats.find((seat) => seat.slug === 'pilot-smokeball')!
    const violations = armingViolations({
      seat: pilot.slug,
      cron: pilot.cron,
      outbound,
      renders: regressed,
      templateExists,
    })
    expect(violations.map((v) => v.code)).toEqual(['undeclared-render'])
    expect(violations[0].skill).toBe('deadline-miss-escalator')
  })
})

describe('the customer_name floor gate (synthetic seats)', () => {
  const armedChase = [{ skill: 'medical-records-chaser' }]

  it('refuses a trigger word in the name of a chase-arming seat, naming the word', () => {
    const violations = customerNameFloorViolations({
      seat: 'x',
      customerName: 'Example, Attorneys at Law',
      cron: armedChase,
    })
    expect(violations.map((v) => v.code)).toEqual(['floor-trigger-name'])
    // Corrective: the message names the offending word and the way out.
    expect(violations[0].message).toContain('"attorney"')
    expect(violations[0].message).toContain('signature.firm_line')
  })

  it('a seat arming no chase-signature skill is untouched', () => {
    const violations = customerNameFloorViolations({
      seat: 'x',
      customerName: 'Example, Attorneys at Law',
      cron: [{ skill: 'deadline-miss-escalator' }],
    })
    expect(violations).toEqual([])
  })

  it('a floor-clean authored firm_line override suppresses the name gate', () => {
    // The override is what renders; the display name never reaches a chase
    // body. The override itself is floor-checked by the schema validator.
    const violations = customerNameFloorViolations({
      seat: 'x',
      customerName: 'Example, Attorneys at Law',
      cron: armedChase,
      firmLineAuthored: true,
    })
    expect(violations).toEqual([])
  })

  it('word boundaries hold: substrings and clean names pass', () => {
    for (const name of ['Signal Hill LLP', 'Coffee & Co', 'Ashton & Price LLP', 'Pilot Law']) {
      expect(
        customerNameFloorViolations({ seat: 'x', customerName: name, cron: armedChase })
      ).toEqual([])
    }
  })

  it('the plural form is refused too', () => {
    // The overlay's \battorney\b would miss "Attorneys", but an authoring gate
    // that lets the plural through teaches authors the word is fine.
    const violations = customerNameFloorViolations({
      seat: 'x',
      customerName: 'Example Attorneys',
      cron: armedChase,
    })
    expect(violations.map((v) => v.code)).toEqual(['floor-trigger-name'])
  })
})
