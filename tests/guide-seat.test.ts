/**
 * The guide seat is authored so that only its two skills can run, only listed
 * people reach it, and its chat channel cannot fail open (ADR 0105).
 *
 * Deterministic, no model calls: these are the $0 rehearsal assets for the
 * seat's configuration, its email routing and its chat allowlist. The answers
 * themselves are graded against operator/skills/product-guide/rehearsal/
 * questions.yaml by the paid check, which needs the Captain's approval.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { parse as parseYaml } from 'yaml'
import { validate } from '../src/lib/operator/customer-yaml'

const SEAT = resolve('operator/customers/smd-guide/customer.yaml')
const CAPTAIN_MARK = resolve('operator/customers/captain-mark/customer.yaml')

type Doc = Record<string, any>

const load = (p: string): Doc => parseYaml(readFileSync(p, 'utf8')) as Doc
const clone = (d: Doc): Doc => JSON.parse(JSON.stringify(d)) as Doc
const codes = (d: Doc) =>
  validate(d).ok ? [] : (validate(d) as { errors: { code: string; path: string }[] }).errors

describe('smd-guide seat config (ADR 0105)', () => {
  const seat = load(SEAT)

  it('validates', () => {
    expect(codes(seat)).toEqual([])
  })

  it('is SMD-internal, the operator product, the guide role', () => {
    expect(seat.seat).toEqual({ kind: 'internal', product: 'operator', role: 'guide' })
  })

  it('enables exactly the role package skills', () => {
    const role = load(resolve('operator/roles/guide/role.yaml'))
    const enabled = seat.personas
      .flatMap((p: Doc) => p.skills)
      .filter((s: Doc) => s.enabled)
      .map((s: Doc) => s.name)
      .sort()
    expect(enabled).toEqual(role.skills.map((s: Doc) => s.name).sort())
    expect(enabled).toEqual(['pitch-practice', 'product-guide', 'rep-training'])
  })

  it('routes every inbound email to product-guide, which hands practice to pitch-practice', () => {
    expect(seat.webhook_triggers).toEqual([
      {
        source: 'agentmail',
        event_type: 'message.received',
        skill: 'product-guide',
        persona: 'guide',
      },
    ])
    const router = readFileSync(resolve('operator/skills/product-guide/SKILL.md'), 'utf8')
    expect(router).toContain('/app/skills/pitch-practice/SKILL.md')
  })

  it('authors one send class only: the reply to a rostered sender', () => {
    // internal_write is needed twice (ADR 0106): the reply path's own
    // create_draft is INTERNAL_WRITE (shared/action_classes.py, run through the
    // trust gate by hermes-smd-reply/text_delivery.py), so without it every
    // email reply on this seat was refused (a correction to ADR 0105's config);
    // and the administrator's invite_code_issue tool. No other class is authored.
    for (const p of seat.personas) {
      expect(p.entitlements.exposure).toEqual({
        external_send_internal: 'autonomous',
        internal_write: 'autonomous',
      })
    }
  })

  it('switches invite-code enrollment on (ADR 0106)', () => {
    expect((seat as unknown as { sender_enrollment?: unknown }).sender_enrollment).toEqual({
      enabled: true,
      daily_limit: 60,
    })
  })

  it('answers only listed people, has no outside source, and keeps no memory', () => {
    expect(seat.scope.inbound_allow_from).toEqual(['scott@smd.services'])
    expect(Object.keys(seat.connectors)).toEqual(['Email'])
    // Every store is walled per person (issue #3946): one representative's
    // records are never another's source, and none is open to the model's
    // write unless the procedure for it ships.
    const stores = (seat.record_stores ?? []) as Doc[]
    expect(stores.map((s) => s.name)).toEqual(['rep-progress'])
    for (const s of stores) expect(s.partition).toBe('sender')
    expect(stores[0].managed).toBe(true)
    expect((seat.memory as Doc).peer_memory_enabled).toBe(false)
    expect((seat.escalation as Doc).request_card_text).toBe(false)
    expect(seat.memory.memory_enabled).toBe(false)
    expect(seat.memory.user_profile_enabled).toBe(false)
  })
})

describe('smd-guide chat channel (ADR 0105, ADR 0033)', () => {
  const seat = load(SEAT)

  it('is off for first boot, with an allowlist of exactly the ids already bound to a named person', () => {
    // Off until a bot exists and its token is vaulted (Captain, 2026-11-09): a
    // seat with chat enabled and no token does not boot. The allowlist stays
    // authored so turning it on is a one-line reviewed change.
    expect(seat.telegram.enabled).toBe(false)
    // Every id on the guide's chat allowlist must be one the repo already binds
    // to a person: the Captain's id is people p2 on captain-mark. A new id (a
    // representative's) arrives with its own reviewed PR, never invented here.
    const bound = new Set(
      (load(CAPTAIN_MARK).people as Doc[])
        .filter((p) => p.name === 'Scott Durgan')
        .map((p) => p.telegram_id)
    )
    expect(seat.telegram.allow_from.length).toBeGreaterThan(0)
    for (const id of seat.telegram.allow_from) expect(bound.has(id)).toBe(true)
  })

  it('cannot fail open: the validator refuses the same block with an empty or missing allowlist', () => {
    for (const allow of [[], undefined]) {
      const d = clone(seat)
      d.telegram.enabled = true
      if (allow === undefined) delete d.telegram.allow_from
      else d.telegram.allow_from = allow
      expect(codes(d).some((e) => e.path === 'telegram.allow_from')).toBe(true)
    }
  })

  it('a disabled block with no allowlist validates (the only safe empty form)', () => {
    const d = clone(seat)
    d.telegram = { enabled: false }
    expect(codes(d)).toEqual([])
  })
})
