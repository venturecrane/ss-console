/**
 * The demo seat (ADR 0107 purpose demo; runbook docs/handbook/demo-seat.md).
 *
 * The winning line: a sales representative emails SMD's demo Operator in front
 * of a prospect and every reply comes from the real product working for a
 * fictional firm. What can be held true in the repo, held here:
 *
 *  1. the two declarations agree: a seat's `seat.yaml` purpose is `demo` exactly
 *     when its `customer.yaml` authors `seat.kind: demo` (the seat reads only
 *     the second; enrollment and the rehearsal scope key on it);
 *  2. every demo act in the runbook has its skill bound, enabled and
 *     person-initiated, and the refusal-to-send act is real (an outside send is
 *     a draft for a person);
 *  3. nothing on the seat can spend money unasked: no skill that queues a job
 *     (the lane-proof gate's lane skills), and the one schedule calls no model;
 *  4. who the seat answers is SMD's administrators plus enrolled
 *     representatives, never a prospect, and never an administrator by code;
 *  5. it runs the production model pair and the firm's approved voice spec
 *     exists where the runbook says to install it from;
 *  6. nothing from a client: no placeholder awaiting a client's answer, no
 *     client name, and none of the client-mirror artifacts the seat used to
 *     carry.
 */

import { describe, expect, it } from 'vitest'
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { parse as parseYaml } from 'yaml'
import { validate, type CustomerYaml } from '../src/lib/operator/customer-yaml'
import { LANE_SKILL } from '../src/lib/operator/job-lanes.generated'

const CUSTOMERS = resolve('operator/customers')
const DEMO_DIR = resolve('operator/customers/pilot-smokeball')
const DEMO_YAML = resolve('operator/customers/pilot-smokeball/customer.yaml')
const CLIENT_YAML = resolve('operator/customers/ashton-price/customer.yaml')
const VOICE_SPEC = resolve(
  'operator/customers/pilot-smokeball/seed/voice/spec/work_product.voice.md'
)
const RUNBOOK = resolve('docs/handbook/demo-seat.md')

/** The skills each demo act in the runbook depends on. */
const DEMO_ACT_SKILLS = [
  'matter-inbox-router',
  'operator-introduce',
  'operator-help',
  'matter-status-responder',
  'deadline-and-sol-tracker',
  'matter-document-review',
  'meet-and-confer-drafter',
] as const

function validated(path: string): CustomerYaml {
  const result = validate(parseYaml(readFileSync(path, 'utf8')))
  if (!result.ok) throw new Error(`${path}: ${JSON.stringify(result.errors, null, 2)}`)
  return result.value
}

function raw(path: string): Record<string, unknown> {
  return parseYaml(readFileSync(path, 'utf8')) as Record<string, unknown>
}

/** The skills that author a job lane (operator/contracts/job-lanes.yaml), each of which can queue a paid job. */
function laneSkills(): string[] {
  return Object.values(LANE_SKILL)
}

const demo = validated(DEMO_YAML)
const operator = demo.personas.find((p) => p.slug === 'operator')!

describe('the demo seat declares itself the same way twice', () => {
  it('seat.yaml purpose demo <=> customer.yaml seat.kind demo, on every seat', () => {
    const seats = readdirSync(CUSTOMERS).filter(
      (d) => !d.startsWith('_') && existsSync(join(CUSTOMERS, d, 'seat.yaml'))
    )
    const demos: string[] = []
    for (const slug of seats) {
      const purpose = raw(join(CUSTOMERS, slug, 'seat.yaml'))['purpose']
      const seat = raw(join(CUSTOMERS, slug, 'customer.yaml'))['seat'] as Record<string, unknown>
      expect(
        purpose === 'demo',
        `${slug}: purpose ${String(purpose)} vs kind ${String(seat?.kind)}`
      ).toBe(seat?.kind === 'demo')
      if (purpose === 'demo') demos.push(slug)
    }
    expect(demos).toEqual(['pilot-smokeball'])
  })
})

describe('every demo act can happen on this seat', () => {
  it('each act skill is bound, enabled and person-initiated', () => {
    for (const name of DEMO_ACT_SKILLS) {
      const skill = operator.skills.find((s) => s.name === name)
      expect(skill, `${name} is not bound`).toBeDefined()
      expect(skill!.enabled, `${name} is not enabled`).toBe(true)
      expect(skill!.initiation.manual, `${name} cannot be asked for`).toBe(true)
      expect(existsSync(resolve('operator/skills', name, 'SKILL.md')), `${name} has no body`).toBe(
        true
      )
    }
  })

  it('the refusal to send is real: an outside send is a draft, and nothing can raise it', () => {
    expect(operator.entitlements.exposure['external_send']).toBe('draft_for_review')
    expect(operator.entitlements.exposure_ceiling?.['external_send']).toBe('draft_for_review')
    // The firm's own people (the representative) get real replies.
    expect(operator.entitlements.exposure['external_send_internal']).toBe('autonomous')
  })

  it('a preference given once holds per person: native memory is off, so no meeting leaks into another', () => {
    const memory = raw(DEMO_YAML)['memory'] as Record<string, unknown>
    expect(memory['memory_enabled']).toBe(false)
    expect(memory['user_profile_enabled']).toBe(false)
  })

  it('the runbook names every act skill', () => {
    const runbook = readFileSync(RUNBOOK, 'utf8')
    for (const name of DEMO_ACT_SKILLS) expect(runbook, name).toContain(name)
  })
})

describe('nothing on the demo seat spends money unasked', () => {
  it('binds no skill that queues a job', () => {
    const bound = new Set(operator.skills.map((s) => s.name))
    for (const lane of laneSkills())
      expect(bound.has(lane), `${lane} queues a paid job`).toBe(false)
  })

  it('its one schedule is the turn-free auth keepalive', () => {
    expect(operator.cron.map((c) => c.skill)).toEqual(['connector-auth-check'])
    const scheduled = operator.skills.filter((s) => s.initiation.scheduled).map((s) => s.name)
    expect(scheduled).toEqual(['connector-auth-check'])
  })

  it('no Smokeball event wakes it', () => {
    expect(demo.webhook_triggers.map((t) => `${t.source}:${t.skill}`)).toEqual([
      'agentmail:matter-inbox-router',
    ])
  })

  it('needs no lane proof (an empty lane-proofs.yaml pages SMD for nothing it does)', () => {
    const proofs = raw(join(DEMO_DIR, 'lane-proofs.yaml'))
    expect(proofs['lanes']).toEqual({})
  })
})

describe('who the demo seat answers', () => {
  it('SMD administrators, written here; representatives arrive by invite code', () => {
    expect(raw(DEMO_YAML)['sender_enrollment']).toEqual({ enabled: true })
    expect(demo.scope.admins.length).toBeGreaterThan(0)
    for (const admin of demo.scope.admins) expect(admin.endsWith('@smd.services'), admin).toBe(true)
    // The authored roster is the administrators and nobody else: a prospect is
    // never listed, and no domain grant lets one in.
    expect([...demo.scope.inbound_allow_from].sort()).toEqual([...demo.scope.admins].sort())
  })
})

describe('the product as a firm gets it', () => {
  it('runs the client seat model pair', () => {
    const client = validated(CLIENT_YAML)
    expect(demo.model).toBe(client.model)
    expect(demo.escalation_model).toBe(client.escalation_model)
  })

  it("declares the fictional firm's voice and the spec to install exists", () => {
    const classes = raw(DEMO_YAML)['output_classes'] as Record<string, Record<string, string>>
    expect(classes['work_product']['voice_spec']).toBe('expected')
    expect(classes['outbound_external']['voice_spec']).toBe('expected')
    expect(existsSync(VOICE_SPEC)).toBe(true)
  })
})

describe('nothing from a client', () => {
  it('carries no placeholder, no client name and no client-mirror artifact', () => {
    const text = readFileSync(DEMO_YAML, 'utf8')
    expect(text).not.toContain('PLACEHOLDER')
    for (const word of [/christa/i, /ashton/i, /\bA&P\b/, /\bChris\b/])
      expect(text).not.toMatch(word)
    for (const file of ['commitments.json', 'routine-grid.yaml', 'initiation-card.yaml'])
      expect(existsSync(join(DEMO_DIR, file)), file).toBe(false)
  })
})
