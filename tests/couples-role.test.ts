/**
 * The couples role package (ADR 0090) stays in step with its seat template.
 *
 * operator/roles/couples/role.yaml is the manifest a couple's seat is copied
 * from. Three things could drift silently, and each is pinned here:
 *
 *   1. A skill or slot in the manifest that the template does not carry (or
 *      the reverse), so a seat copied from the template is not the role it claims to be.
 *      Routines themselves are set by the couple in conversation (#3264), so
 *      the template authors no cron at all.
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
import { CONVERSATION_SCHEDULED_SKILLS } from '../src/lib/operator/customer-yaml/sections-people'

const ROLE_DIR = 'operator/roles/couples'

interface RoleSkill {
  name: string
  built: boolean
  settings?: Record<string, unknown>
}
interface RoleSlot {
  skill: string
  space: string
}

const role = parseYaml(readFileSync(resolve(ROLE_DIR, 'role.yaml'), 'utf8')) as {
  role: string
  persona: string
  seat_template: string
  skills: RoleSkill[]
  slots: RoleSlot[]
  required_connections: unknown[]
  privacy: { model: string }
  record_stores: string[]
}
const template = parseYaml(readFileSync(resolve(role.seat_template), 'utf8')) as {
  seat: { role: string; product: string }
  personas: Array<{
    skills: Array<{
      name: string
      enabled: boolean
      settings?: Record<string, unknown>
      initiation?: { scheduled?: boolean }
    }>
    cron: Array<{ skill: string; space: string }>
  }>
  record_stores: Array<{ name: string; path: string; owner_field?: string; readers?: unknown }>
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

  it('the template skill settings are the role skill settings', () => {
    for (const skill of role.skills) {
      const t = template.personas[0].skills.find((s) => s.name === skill.name)
      expect(t?.settings ?? {}, skill.name).toEqual(skill.settings ?? {})
    }
  })

  it('every slot skill is scheduled and rides the roster lane', () => {
    for (const slot of role.slots) {
      const t = template.personas[0].skills.find((s) => s.name === slot.skill)
      expect(t?.settings?.recipients, slot.skill).toBe('roster')
      expect(t?.initiation?.scheduled, slot.skill).toBe(true)
      expect(['person', 'shared']).toContain(slot.space)
    }
  })

  it('the template authors no cron: the couple sets every schedule in conversation', () => {
    expect(template.personas[0].cron).toEqual([])
  })

  it('the slots are exactly the scheduled skills, and match the overlay set', () => {
    // A scheduled skill with no slot would never run; the overlay's
    // shared/couples_settings.py KINDS must carry the same five.
    const scheduled = template.personas[0].skills
      .filter((s) => s.initiation?.scheduled)
      .map((s) => s.name)
      .sort()
    expect(role.slots.map((s) => s.skill).sort()).toEqual(scheduled)
    expect([...CONVERSATION_SCHEDULED_SKILLS].sort()).toEqual(scheduled)
    expect(Object.fromEntries(role.slots.map((s) => [s.skill, s.space]))).toEqual({
      'couples-checkin': 'person',
      'gratitude-prompts': 'person',
      'dates-to-remember': 'person',
      travel: 'shared',
      'weekly-checkin-agenda': 'shared',
    })
  })

  it('the calendar skill is limited to the roster', () => {
    const cal = template.personas[0].skills.find((s) => s.name === 'calendar')
    expect(cal?.settings?.recipients).toBe('roster')
  })

  it('the template authors exactly the role record stores, each private per space', () => {
    expect(template.record_stores.map((s) => s.name).sort()).toEqual([...role.record_stores].sort())
    for (const store of template.record_stores) {
      // owner_field makes the store private per space (a person id or shared);
      // without it every record is shared and a gift list could not stay private.
      expect(store.owner_field, store.name).toBe('owner')
      expect(store.readers, store.name).toBeUndefined()
    }
  })

  it('every store a built skill names is authored on the template', () => {
    const authored = new Set(template.record_stores.map((s) => s.name))
    for (const skill of role.skills.filter((s) => s.built)) {
      const body = readFileSync(resolve('operator/skills', skill.name, 'SKILL.md'), 'utf8')
      const m = body.match(/^\s+stores: \[([^\]]*)\]$/m)
      for (const name of (m?.[1] ?? '')
        .split(',')
        .map((x) => x.trim())
        .filter(Boolean)) {
        expect(authored.has(name), `${skill.name} names store ${name}`).toBe(true)
      }
    }
  })

  it('no couples skill offers a tool this persona does not have', () => {
    // ADR 0090 R2: no terminal, code, file or delegation tools on this persona.
    const banned =
      /\b(write_file|read_file|search_files|patch|terminal|execute_code|delegate_task|workspace_drive_upload)\(?/
    for (const skill of role.skills.filter(
      (s) => s.built && s.name !== 'workspace' && s.name !== 'inbox-triage'
    )) {
      const body = readFileSync(resolve('operator/skills', skill.name, 'SKILL.md'), 'utf8')
      const used = [...body.matchAll(/`([a-z_]+)`/g)].map((x) => x[1]).filter((t) => banned.test(t))
      expect(used, skill.name).toEqual([])
    }
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
    ['either partner acts alone (Captain, 2026-10-19)', /Either partner can do anything alone/],
    ['nothing waits for the other partner', /Nothing waits for the other partner's\s+agreement/],
    ['a private item stays private', /keep it in their private space/i],
    ['gives support resources', /give them support\s+resources/i],
    ['names the 988 Suicide & Crisis Lifeline', /988 Suicide & Crisis Lifeline/],
    ['does not track the other partner', /You do not track the other partner/],
  ])('%s', (_label, pattern) => {
    expect(persona).toMatch(pattern)
  })

  it('never asks a partner to confirm with "share it" or wait for an agree', () => {
    expect(persona).not.toMatch(/share it|reply `?agree/i)
  })

  it('carries no em dash', () => {
    expect(persona).not.toContain('—')
    expect(readFileSync(resolve(ROLE_DIR, 'role.yaml'), 'utf8')).not.toContain('—')
  })
})
