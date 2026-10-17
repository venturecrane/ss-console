/**
 * The couples role package (ADR 0090) stays in step with its seat template.
 *
 * operator/roles/couples/role.yaml is the manifest a couple's seat is generated
 * from. Three things could drift silently, and each is pinned here:
 *
 *   1. A skill or routine in the manifest that the template does not carry (or
 *      the reverse), so a generated seat is not the role it claims to be.
 *   2. A `built:` flag that lies. An enabled skill with no body fails the seat's
 *      boot, so "built: true" with no SKILL.md, or "built: false" for a skill
 *      that now exists, would mislead whoever provisions the next couple.
 *   3. The persona's safety rules losing one of the authored commitments
 *      (#3264 Captain decision 5): no relaying, crisis resources incl. 988, no
 *      monitoring, no sides, support not counseling.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync, existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { parse as parseYaml } from 'yaml'

const ROLE_DIR = 'operator/roles/couples'

interface RoleSkill {
  name: string
  built: boolean
}
interface RoleRoutine {
  skill: string
  space: string
}

const role = parseYaml(readFileSync(resolve(ROLE_DIR, 'role.yaml'), 'utf8')) as {
  role: string
  persona: string
  seat_template: string
  skills: RoleSkill[]
  routines: RoleRoutine[]
  required_connections: unknown[]
  privacy: { model: string }
}
const template = parseYaml(readFileSync(resolve(role.seat_template), 'utf8')) as {
  seat: { role: string; product: string }
  personas: Array<{
    skills: Array<{ name: string; enabled: boolean; settings?: Record<string, unknown> }>
    cron: Array<{ skill: string; space: string }>
  }>
}

describe('couples role package', () => {
  it('names itself, its persona and a template that selects it', () => {
    expect(role.role).toBe('couples')
    expect(existsSync(resolve(ROLE_DIR, role.persona))).toBe(true)
    expect(template.seat.role).toBe('couples')
    expect(template.seat.product).toBe('hosted-agent')
    expect(role.privacy.model).toBe('spaces')
    expect(role.required_connections).toEqual([])
  })

  it('the template carries exactly the role skills, all enabled', () => {
    const persona = template.personas[0]
    expect(persona.skills.map((s) => s.name).sort()).toEqual(role.skills.map((s) => s.name).sort())
    expect(persona.skills.every((s) => s.enabled)).toBe(true)
  })

  it('the calendar skill is limited to the roster', () => {
    const cal = template.personas[0].skills.find((s) => s.name === 'calendar')
    expect(cal?.settings?.recipients).toBe('roster')
  })

  it('the template routines are the role routines, each with its space', () => {
    const got = template.personas[0].cron.map((c) => `${c.skill}@${c.space}`).sort()
    const want = role.routines.map((r) => `${r.skill}@${r.space}`).sort()
    expect(got).toEqual(want)
  })

  for (const skill of role.skills) {
    it(`built flag for ${skill.name} matches operator/skills/`, () => {
      expect(existsSync(resolve('operator/skills', skill.name, 'SKILL.md'))).toBe(skill.built)
    })
  }
})

describe('couples persona safety rules', () => {
  const persona = readFileSync(resolve(ROLE_DIR, role.persona), 'utf8')

  it.each([
    ['never relays private words', /Never relay a partner's private words/],
    ['crisis disclosure gets support resources', /crisis disclosure gets support resources/i],
    ['names the 988 Suicide & Crisis Lifeline', /988 Suicide & Crisis Lifeline/],
    ['refuses monitoring', /Refuse to monitor the other partner/],
    ['never takes sides', /Never take sides/],
    ['support, not counseling', /not a counselor/],
  ])('%s', (_label, pattern) => {
    expect(persona).toMatch(pattern)
  })

  it('carries no em dash', () => {
    expect(persona).not.toContain('—')
    expect(readFileSync(resolve(ROLE_DIR, 'role.yaml'), 'utf8')).not.toContain('—')
  })
})
