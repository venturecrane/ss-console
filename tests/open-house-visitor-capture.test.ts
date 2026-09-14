/**
 * Open-house visitor capture (real estate POC, scott seat).
 *
 * Two things the skill's own prose promises that nothing else checks:
 *
 *   1. The cadence is a function of the visit date only. The skill stores the
 *      agent's rapport notes verbatim (a visitor's children, parents, health may
 *      appear there, because the agent chose to remember them) and promises
 *      never to schedule, rank, or filter on them. The "## Cadence" section is
 *      the contract; this pins that its text names the visit date and names
 *      none of the protected-class vocabulary. A future edit that makes the
 *      cadence "smarter" by reading the notes fails here.
 *   2. The record path is one absolute path, stated in the skill, so a record
 *      written on an inbound turn is found on a scheduled turn (the two run
 *      under different HERMES_HOME roots; a relative path is the known trap).
 *
 * And the seat binding that makes the act reachable (the /wired chain): the
 * scott seat enables the skill with webhook + scheduled initiation, routes the
 * AgentMail trigger to it, arms it on cron, and authors the internal-send
 * exposure the scheduled email to the agent needs. Parsed, never grepped.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { parse as parseYaml } from 'yaml'

const SKILL = resolve('operator/skills/open-house-visitor-capture/SKILL.md')
const SEAT = resolve('operator/customers/scott/customer.yaml')
const RECORD_DIR = '/opt/data/open-house/visitors/'

const skill = readFileSync(SKILL, 'utf8')

function section(md: string, heading: string): string {
  const start = md.indexOf(`\n## ${heading}`)
  expect(start, `section "${heading}" missing`).toBeGreaterThan(-1)
  const rest = md.slice(start + 1)
  const next = rest.indexOf('\n## ', 1)
  return next === -1 ? rest : rest.slice(0, next)
}

describe('open-house-visitor-capture: the skill contract', () => {
  it('cadence is a function of the visit date and reads nothing else', () => {
    const cadence = section(skill, 'Cadence').toLowerCase()
    expect(cadence).toContain('visit date')
    expect(cadence).toMatch(/day 2, day 7, day 30/)
    for (const word of [
      'child',
      'kid',
      'parent',
      'famil',
      'disab',
      'health',
      'age ',
      'religio',
      'national origin',
      'race',
      'pregnan',
    ]) {
      expect(cadence, `cadence text names "${word}"`).not.toContain(word)
    }
  })

  it('states one absolute record path and never a home-relative one', () => {
    expect(skill).toContain(RECORD_DIR)
    expect(skill).not.toMatch(/~\/\.hermes\/open-house|\$HERMES_HOME\/open-house/)
  })

  it('never names a visitor as a recipient', () => {
    const never = section(skill, 'What this skill never does')
    expect(never).toMatch(/never contacts a visitor/i)
  })

  it('carries no em dashes (house style, user-facing prose reaches the agent)', () => {
    expect(skill).not.toContain('—')
  })
})

describe('open-house-visitor-capture: the scott seat binds it (the /wired chain)', () => {
  const seat = parseYaml(readFileSync(SEAT, 'utf8')) as {
    personas: {
      slug: string
      entitlements: { exposure: Record<string, string> }
      skills: { name: string; enabled: boolean; initiation: Record<string, boolean> }[]
      cron: { skill: string; wake_policy: string; pre_run?: string }[]
    }[]
    webhook_triggers: { source: string; event_type: string; skill: string; persona: string }[]
    send_policy?: { reply?: { internal_exempt?: boolean }; held_release?: { enabled?: boolean } }
  }
  const persona = seat.personas.find((p) => p.slug === 'agent-crane')!

  it('one persona carries it; no second profile was opened for the POC', () => {
    expect(seat.personas).toHaveLength(1)
    expect(persona).toBeDefined()
  })

  it('the skill is enabled with webhook and scheduled initiation', () => {
    const bound = persona.skills.find((s) => s.name === 'open-house-visitor-capture')
    expect(bound?.enabled).toBe(true)
    expect(bound?.initiation.webhook).toBe(true)
    expect(bound?.initiation.scheduled).toBe(true)
  })

  it('the inbound-email trigger routes to it', () => {
    const trigger = seat.webhook_triggers.find(
      (t) => t.source === 'agentmail' && t.event_type === 'message.received'
    )
    expect(trigger?.skill).toBe('open-house-visitor-capture')
    expect(trigger?.persona).toBe('agent-crane')
  })

  it('cron arms the follow-up mode with wake_policy always and no pre_run', () => {
    const row = persona.cron.find((c) => c.skill === 'open-house-visitor-capture')
    expect(row?.wake_policy).toBe('always')
    expect(row?.pre_run).toBeUndefined()
  })

  it('the scheduled email to the rostered agent is authored autonomous, and outside sends are not', () => {
    expect(persona.entitlements.exposure.external_send_internal).toBe('autonomous')
    expect(persona.entitlements.exposure.external_send).not.toBe('autonomous')
  })

  it('rostered replies are exempt from the per-sender reply limit', () => {
    expect(seat.send_policy?.reply?.internal_exempt).toBe(true)
    expect(seat.send_policy?.held_release?.enabled).toBe(true)
  })
})
