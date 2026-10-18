/**
 * Multi-person seat validation (ADR 0090 `seat.role`, ADR 0091 `people:` /
 * `spaces:` / cron `space` / telegram group keys / memory switches).
 *
 * Baseline: the couples seat template with every bracketed placeholder filled.
 * That makes "the template validates" the happy path, and every rule below is a
 * single mutation of a real seat shape. Each rule carries a FALSIFIER: a case
 * that passes only because the rule exists, so deleting the rule fails a test.
 *
 * Existing seats: every shipped seat must still validate unchanged, and none of
 * them authors `people:` (so space mode cannot have switched on by accident).
 */

import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { parse as parseYaml } from 'yaml'
import { validate, type ValidationError } from '../src/lib/operator/customer-yaml'

const TEMPLATE = 'operator/customers/_couples-template/customer.yaml'

/** Every bracketed placeholder in the template, and a valid value for it. */
const FILL: Record<string, string> = {
  '[SLUG]': 'couple-test',
  '[COUPLE NAME]': 'Test Couple',
  '[CURRENT BLESSED FLEET PIN, e.g., v2026.5.7@<40-hex-sha>]':
    'v2026.5.7@a91a57fa5a13d516c38b07a141a9ce8a3daabeb0',
  '[PARTNER 1 NAME]': 'Alex Example',
  '[PARTNER 2 NAME]': 'Sam Example',
  '[PARTNER 1 EMAIL, lowercased]': 'alex@example.com',
  '[PARTNER 2 EMAIL, lowercased]': 'sam@example.com',
  '[AGENT NAME, lowercased]': 'juniper',
  '[AGENT NAME]': 'Juniper',
  '[PARTNER 1 CHECK-IN CRON, UTC]': '0 15 * * 1',
  '[PARTNER 2 CHECK-IN CRON, UTC]': '0 16 * * 1',
  '[PARTNER 1 GRATITUDE CRON, UTC]': '0 2 * * 4',
  '[PARTNER 2 GRATITUDE CRON, UTC]': '0 3 * * 4',
  '[DAILY REMINDER CRON, UTC]': '0 15 * * *',
  '[WEEKLY CHECK-IN AGENDA CRON, UTC]': '0 1 * * 1',
  '[SMD SENTINEL TELEGRAM ID until both partners are recorded]': '1000000001',
}

function templateText(): string {
  return readFileSync(resolve(TEMPLATE), 'utf8')
}

function filledText(): string {
  let text = templateText()
  for (const [k, v] of Object.entries(FILL)) text = text.split(k).join(v)
  return text
}

type Root = Record<string, any>

function seat(): Root {
  return parseYaml(filledText()) as Root
}

function errorsOf(root: unknown): ValidationError[] {
  const r = validate(root)
  return r.ok ? [] : r.errors
}

function codesAt(root: unknown): Array<[string, string]> {
  return errorsOf(root).map((e) => [e.code, e.path])
}

/** Record both partners' Telegram ids the way SMD authors them by PR. */
function bound(): Root {
  const s = seat()
  s.people[0].telegram_id = '111'
  s.people[1].telegram_id = '222'
  s.telegram.allow_from = ['111', '222']
  s.telegram.group_allow_from = ['111', '222']
  s.spaces.shared_telegram_chat = '-1001234567890'
  s.telegram.group_chats = ['-1001234567890']
  return s
}

describe('couples seat template', () => {
  it('fills every bracketed placeholder (a new one must be added to FILL)', () => {
    const placeholders = new Set(
      [...templateText().matchAll(/'(\[[^\]]+\][^']*)'/g)].flatMap((m) =>
        [...m[1].matchAll(/\[[^\]]+\]/g)].map((x) => x[0])
      )
    )
    for (const p of placeholders) {
      expect(
        Object.keys(FILL).some((k) => k.includes(p)),
        `unfilled placeholder ${p}`
      ).toBe(true)
    }
    expect(filledText()).not.toMatch(/\[[A-Z][^\]]*\]/)
  })

  it('validates once filled', () => {
    expect(errorsOf(seat())).toEqual([])
  })

  it('validates with both partners bound', () => {
    expect(errorsOf(bound())).toEqual([])
  })

  it('carries seat.role couples and the parsed people/spaces/cron space', () => {
    const r = validate(bound())
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.value.seat?.role).toBe('couples')
    expect(r.value.people?.map((p) => p.id)).toEqual(['p1', 'p2'])
    expect(r.value.spaces?.shared_telegram_chat).toBe('-1001234567890')
    expect(r.value.personas[0].cron.map((c) => c.space)).toEqual([
      'person:p1',
      'person:p2',
      'person:p1',
      'person:p2',
      'person:p1',
      'person:p2',
      'shared',
      'shared',
    ])
    expect(r.value.memory.memory_enabled).toBe(false)
    expect(r.value.memory.user_profile_enabled).toBe(false)
  })
})

describe('seat.role', () => {
  it('FALSIFIER: an unknown role is rejected', () => {
    const s = seat()
    s.seat.role = 'roommates'
    expect(codesAt(s)).toEqual([['EnumViolation', 'seat.role']])
  })

  it('absent role validates and stays absent', () => {
    const s = seat()
    delete s.seat.role
    const r = validate(s)
    expect(r.ok && r.value.seat).toEqual({ kind: 'customer', product: 'hosted-agent' })
  })
})

describe('rule 1: people requires spaces, both memory switches false, a space on every cron', () => {
  it('FALSIFIER: people without spaces is rejected', () => {
    const s = seat()
    delete s.spaces
    expect(codesAt(s)).toEqual([['InvalidPeople', 'spaces']])
  })

  it('FALSIFIER: memory_enabled true is rejected', () => {
    const s = seat()
    s.memory.memory_enabled = true
    expect(codesAt(s)).toEqual([['InvalidPeople', 'memory.memory_enabled']])
  })

  it('FALSIFIER: memory_enabled absent is rejected (must be authored false)', () => {
    const s = seat()
    delete s.memory.memory_enabled
    expect(codesAt(s)).toEqual([['InvalidPeople', 'memory.memory_enabled']])
  })

  it('FALSIFIER: user_profile_enabled not false is rejected', () => {
    const s = seat()
    delete s.memory.user_profile_enabled
    expect(codesAt(s)).toEqual([['InvalidPeople', 'memory.user_profile_enabled']])
  })

  it('FALSIFIER: a cron entry with no space is rejected', () => {
    const s = seat()
    delete s.personas[0].cron[1].space
    expect(codesAt(s)).toEqual([['InvalidCronSpace', 'personas[0].cron[1].space']])
  })
})

describe('rule 2: a cron space names a person id or shared', () => {
  it('shared is accepted', () => {
    const s = seat()
    s.personas[0].cron[0].space = 'shared'
    expect(errorsOf(s)).toEqual([])
  })

  it('FALSIFIER: a person id not on people is rejected', () => {
    const s = seat()
    s.personas[0].cron[0].space = 'person:p3'
    expect(codesAt(s)).toEqual([['InvalidCronSpace', 'personas[0].cron[0].space']])
  })

  it('FALSIFIER: a bare id without the person: prefix is rejected', () => {
    const s = seat()
    s.personas[0].cron[0].space = 'p1'
    expect(codesAt(s)).toEqual([['InvalidCronSpace', 'personas[0].cron[0].space']])
  })

  it('a non-string space is a type error', () => {
    const s = seat()
    s.personas[0].cron[0].space = 7
    expect(codesAt(s).map((c) => c[0])).toContain('TypeMismatch')
  })
})

describe('rule 8: scope.inbound_allow_from is exactly the people emails', () => {
  it('FALSIFIER: an SMD address on the roster is rejected', () => {
    const s = seat()
    s.scope.inbound_allow_from = ['alex@example.com', 'sam@example.com', 'team@smd.services']
    expect(codesAt(s)).toEqual([['InvalidPeople', 'scope.inbound_allow_from[2]']])
  })

  it('FALSIFIER: a domain wildcard on the roster is rejected', () => {
    const s = seat()
    s.scope.inbound_allow_from = ['alex@example.com', 'sam@example.com', '@example.com']
    expect(codesAt(s)).toEqual([['InvalidPeople', 'scope.inbound_allow_from[2]']])
  })

  it('the template roster is exactly the two partners', () => {
    const s = seat()
    expect([...s.scope.inbound_allow_from].sort()).toEqual(
      s.people.map((p: { email: string }) => p.email).sort()
    )
  })
})

describe('rule 3: each person email is on scope.inbound_allow_from', () => {
  it('FALSIFIER: an email missing from the allowlist is rejected', () => {
    const s = seat()
    s.scope.inbound_allow_from = ['alex@example.com']
    expect(codesAt(s)).toEqual([['InvalidPeople', 'people[1].email']])
  })

  it('the allowlist comparison ignores the allowlist entry case', () => {
    const s = seat()
    s.scope.inbound_allow_from = ['Alex@Example.com', 'sam@example.com']
    expect(errorsOf(s)).toEqual([])
  })

  it('a person email must itself be lowercased', () => {
    const s = seat()
    s.people[0].email = 'Alex@example.com'
    expect(codesAt(s)).toContainEqual(['InvalidPeople', 'people[0].email'])
  })
})

describe('rule 4: a bound telegram_id is on allow_from and group_allow_from', () => {
  it("an unbound person ('') needs no telegram entry", () => {
    expect(errorsOf(seat())).toEqual([])
  })

  it('FALSIFIER: a bound id missing from allow_from is rejected', () => {
    const s = bound()
    s.telegram.allow_from = ['111']
    expect(codesAt(s)).toEqual([['InvalidPeople', 'people[1].telegram_id']])
  })

  it('FALSIFIER: a bound id missing from group_allow_from is rejected', () => {
    const s = bound()
    s.telegram.group_allow_from = ['222']
    expect(codesAt(s)).toEqual([['InvalidPeople', 'people[0].telegram_id']])
  })

  it('a bound id with no telegram block at all is rejected on both lists', () => {
    const s = bound()
    delete s.telegram
    expect(codesAt(s)).toEqual([
      ['InvalidPeople', 'people[0].telegram_id'],
      ['InvalidPeople', 'people[0].telegram_id'],
      ['InvalidPeople', 'people[1].telegram_id'],
      ['InvalidPeople', 'people[1].telegram_id'],
    ])
  })
})

describe('people and spaces shape', () => {
  it('duplicate person ids are rejected', () => {
    const s = seat()
    s.people[1].id = 'p1'
    expect(codesAt(s)).toContainEqual(['InvalidPeople', 'people[1].id'])
  })

  it('a non-slug id, a non-numeric telegram id and an unknown key are rejected', () => {
    const s = seat()
    s.people[0].id = 'P 1'
    s.people[1].telegram_id = '@sam'
    s.people[1].phone = '555'
    const got = codesAt(s)
    expect(got).toContainEqual(['InvalidPeople', 'people[0].id'])
    expect(got).toContainEqual(['InvalidPeople', 'people[1].telegram_id'])
    expect(got).toContainEqual(['InvalidPeople', 'people[1].phone'])
  })

  it('an empty people list is rejected', () => {
    const s = seat()
    s.people = []
    expect(codesAt(s)).toContainEqual(['InvalidPeople', 'people'])
  })

  it('shared_telegram_chat must be negative numeric or empty', () => {
    const s = seat()
    s.spaces.shared_telegram_chat = '1001234567890'
    expect(codesAt(s)).toEqual([['InvalidPeople', 'spaces.shared_telegram_chat']])
  })

  it('FALSIFIER: spaces without people is rejected', () => {
    const s = seat()
    delete s.people
    for (const c of s.personas[0].cron) delete c.space
    expect(codesAt(s)).toEqual([['InvalidPeople', 'spaces']])
  })

  it('FALSIFIER: a cron space without people is rejected', () => {
    const s = seat()
    delete s.people
    delete s.spaces
    for (const c of s.personas[0].cron.slice(1)) delete c.space
    expect(codesAt(s)).toEqual([['InvalidCronSpace', 'personas[0].cron[0].space']])
  })
})

describe('telegram group keys', () => {
  it('FALSIFIER: group_chats entries must be negative numeric strings', () => {
    const s = bound()
    s.telegram.group_chats = ['1001234567890']
    expect(codesAt(s)).toContainEqual(['TypeMismatch', 'telegram.group_chats[0]'])
  })

  it('group_allow_from entries must be numeric user ids', () => {
    const s = bound()
    s.telegram.group_allow_from = ['111', '222', '@x']
    expect(codesAt(s)).toContainEqual(['TypeMismatch', 'telegram.group_allow_from[2]'])
  })

  it('group_sessions_per_user must be a boolean', () => {
    const s = bound()
    s.telegram.group_sessions_per_user = 'no'
    expect(codesAt(s)).toEqual([['TypeMismatch', 'telegram.group_sessions_per_user']])
  })
})

describe('rule 5: the group answers only people (overlay #469 parity)', () => {
  it('FALSIFIER: group_chats without group_allow_from is rejected (any seat)', () => {
    const s = bound()
    s.telegram.group_allow_from = []
    s.people[0].telegram_id = ''
    s.people[1].telegram_id = ''
    s.telegram.allow_from = ['1000000001']
    expect(codesAt(s)).toEqual([['MissingField', 'telegram.group_allow_from']])
  })

  it('FALSIFIER: a group_allow_from id that is not a bound person is rejected', () => {
    const s = bound()
    s.telegram.group_allow_from = ['111', '222', '999']
    expect(codesAt(s)).toEqual([['InvalidPeople', 'telegram.group_allow_from[2]']])
  })

  it('FALSIFIER: the SMD sentinel may not sit on group_allow_from', () => {
    const s = seat()
    s.telegram.group_allow_from = ['1000000001']
    expect(codesAt(s)).toEqual([['InvalidPeople', 'telegram.group_allow_from[0]']])
  })

  it('FALSIFIER: group_chats holding a chat other than the shared one is rejected', () => {
    const s = bound()
    s.telegram.group_chats = ['-1001234567890', '-1009999999999']
    expect(codesAt(s)).toEqual([['InvalidPeople', 'telegram.group_chats[1]']])
  })

  it('FALSIFIER: group_chats set while the shared chat is unbound is rejected', () => {
    const s = bound()
    s.spaces.shared_telegram_chat = ''
    expect(codesAt(s)).toEqual([['InvalidPeople', 'telegram.group_chats[0]']])
  })

  it('an unbound shared chat with shared routines is allowed (delivers locally)', () => {
    const s = seat()
    s.personas[0].cron[0].space = 'shared'
    expect(s.spaces.shared_telegram_chat).toBe('')
    expect(errorsOf(s)).toEqual([])
  })
})

describe('rule 6: no two cron entries share a (skill, space) pair', () => {
  it('FALSIFIER: a repeated pair is rejected', () => {
    const s = seat()
    s.personas[0].cron[1].space = 'person:p1'
    expect(codesAt(s)).toEqual([['InvalidCronSpace', 'personas[0].cron[1].space']])
  })

  it('the same skill in two spaces is fine', () => {
    expect(errorsOf(seat())).toEqual([])
  })
})

describe('rule 7: record_stores readers is rejected on a space-mode seat', () => {
  it('FALSIFIER: a store with readers is rejected', () => {
    const s = seat()
    s.record_stores = [
      {
        name: 'lists',
        path: '/opt/data/couple/lists',
        owner_field: 'owner',
        readers: ['alex@example.com'],
      },
    ]
    expect(codesAt(s)).toEqual([['InvalidPeople', 'record_stores[0].readers']])
  })

  it('a store with an owner field and no readers is fine', () => {
    const s = seat()
    s.record_stores = [{ name: 'lists', path: '/opt/data/couple/lists', owner_field: 'owner' }]
    expect(errorsOf(s)).toEqual([])
  })
})

describe('memory switches on a single-person seat', () => {
  it('are optional, and must be booleans when authored', () => {
    const s = seat()
    delete s.people
    delete s.spaces
    for (const c of s.personas[0].cron) delete c.space
    expect(errorsOf(s)).toEqual([])
    s.memory.memory_enabled = 'off'
    expect(codesAt(s)).toEqual([['TypeMismatch', 'memory.memory_enabled']])
  })
})

describe('existing seats are unchanged', () => {
  const CUSTOMERS = resolve('operator/customers')
  const seats = readdirSync(CUSTOMERS).filter(
    (d) =>
      !d.startsWith('_') &&
      !d.includes('.decommissioned.') &&
      existsSync(resolve(CUSTOMERS, d, 'customer.yaml'))
  )

  it('covers the five live seats', () => {
    for (const s of ['scott', 'brokery', 'ashton-price', 'pilot-smokeball', 'smd-staging']) {
      expect(seats).toContain(s)
    }
  })

  for (const slug of seats) {
    it(`${slug} validates and authors no space-mode keys`, () => {
      const text = readFileSync(resolve(CUSTOMERS, slug, 'customer.yaml'), 'utf8')
      const root = parseYaml(text) as Root
      expect(errorsOf(root)).toEqual([])
      // A couples seat is space-mode by design (ADR 0091); it only has to validate.
      if (root.seat?.role === 'couples') return
      expect(root.people).toBeUndefined()
      expect(root.spaces).toBeUndefined()
      expect(root.seat?.role).toBeUndefined()
    })
  }
})

describe('people[].channel (how the seat reaches a person; email by default)', () => {
  it('accepts email and telegram, and absence', () => {
    const s = bound()
    s.people[0].channel = 'telegram'
    delete s.people[1].channel
    expect(errorsOf(s)).toEqual([])
  })

  it('FALSIFIER: any other value is rejected', () => {
    const s = bound()
    s.people[0].channel = 'sms'
    expect(codesAt(s)).toContainEqual(['InvalidPeople', 'people[0].channel'])
  })
})
