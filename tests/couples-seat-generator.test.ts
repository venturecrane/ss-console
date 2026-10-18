/**
 * The couples seat generator (ADR 0090 decision 4): onboarding rows ->
 * customer.yaml, validated by the canonical validator.
 *
 * The pipeline test runs the export queries against a real migrated D1 that
 * went through onboarding the way partners do, so the generator's SQL, its
 * parser and the schema cannot drift apart unnoticed.
 */

import { describe, it, expect, beforeAll } from 'vitest'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { parse as parseYaml } from 'yaml'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import type { D1Database } from '@cloudflare/workers-types'
import { validate } from '../src/lib/operator/customer-yaml'
import {
  COUPLES_SENTINEL_TELEGRAM_ID,
  observesDst,
  cadenceToUtcCron,
  generateCouplesSeat,
  personaSlug,
  utcOffsetMinutes,
  type CouplesSeatInput,
} from '../src/lib/hosted-agent/couples/generate-seat'
import {
  EXPORT_QUERIES,
  parseSeatExport,
  type SeatExportRows,
} from '../src/lib/hosted-agent/couples/seat-export'
import {
  createCouplesInstance,
  listPeople,
  recordPersonIntake,
} from '../src/lib/db/couples-onboarding'
import { confirmJointSettings, saveJointSettings } from '../src/lib/db/couples-joint'
import { issueLinkToken, redeemLinkToken } from '../src/lib/db/couples-link-tokens'
import { JOINT } from './fixtures/couples'

const TEMPLATE = readFileSync(resolve('operator/customers/_couples-template/customer.yaml'), 'utf8')
const NOW = new Date('2026-10-17T12:00:00Z')
const REF = 'v2026.9.14@345cd2b057a452236de401d3534b8502a7465e8d'

function input(over: Partial<CouplesSeatInput> = {}): CouplesSeatInput {
  return {
    slug: 'couple-test',
    coupleName: 'Alex and Sam',
    agentName: 'Juniper',
    hermesRef: REF,
    people: [
      { key: 'p1', name: 'Alex Example', email: 'alex@example.com', telegramId: null },
      { key: 'p2', name: 'Sam Example', email: 'sam@example.com', telegramId: null },
    ],
    joint: JOINT,
    jointVersion: 3,
    sharedTelegramChat: null,
    telegramBotUsername: null,
    intakeId: '00000000-0000-4000-8000-000000000000',
    now: NOW,
    ...over,
  }
}

type Root = Record<string, any>

function generated(over: Partial<CouplesSeatInput> = {}): { yaml: string; root: Root } {
  const r = generateCouplesSeat(TEMPLATE, input(over))
  if (!r.ok) throw new Error(r.errors.join('\n'))
  return { yaml: r.yaml, root: parseYaml(r.yaml) as Root }
}

describe('UTC schedule conversion', () => {
  it('reads the offset from the timezone (Phoenix has no DST)', () => {
    expect(utcOffsetMinutes('America/Phoenix', NOW)).toBe(-420)
    expect(utcOffsetMinutes('Asia/Kolkata', NOW)).toBe(330)
  })

  it('shifts the day forward when the UTC time crosses midnight', () => {
    // Monday and Thursday 18:30 in Phoenix are Tuesday and Friday 01:30 UTC.
    expect(cadenceToUtcCron({ days: ['mon', 'thu'], time: '18:30' }, -420)).toBe('30 1 * * 2,5')
  })

  it('shifts the day back, and wraps Sunday to Saturday', () => {
    // Sunday 05:00 in Tokyo (+9) is Saturday 20:00 UTC.
    expect(cadenceToUtcCron({ days: ['sun'], time: '05:00' }, 540)).toBe('0 20 * * 6')
  })

  it('keeps a same-day time on its day', () => {
    expect(cadenceToUtcCron({ days: ['wed'], time: '08:15' }, 330)).toBe('45 2 * * 3')
  })

  it('makes a persona slug from the agent name', () => {
    expect(personaSlug("Juni O'Neil")).toBe('juni-o-neil')
  })
})

describe('daylight saving (Hermes cron has no per-job timezone at the pinned ref)', () => {
  it('knows which zones observe DST', () => {
    expect(observesDst('America/Phoenix', NOW)).toBe(false)
    expect(observesDst('America/Denver', NOW)).toBe(true)
    expect(observesDst('Europe/London', NOW)).toBe(true)
    expect(observesDst('Asia/Kolkata', NOW)).toBe(false)
  })

  it('FALSIFIER: a DST zone produces a warning and a header note; Phoenix does not', () => {
    const denver = generateCouplesSeat(
      TEMPLATE,
      input({ joint: { ...JOINT, timezone: 'America/Denver' } })
    )
    expect(denver.ok && denver.warnings.some((w) => w.includes('observes daylight saving'))).toBe(
      true
    )
    expect(denver.ok && denver.yaml).toContain('America/Denver observes daylight saving time')
    const phoenix = generateCouplesSeat(TEMPLATE, input())
    expect(phoenix.ok && phoenix.warnings.some((w) => w.includes('daylight saving'))).toBe(false)
    expect(phoenix.ok && phoenix.yaml).toContain(
      'America/Phoenix does not observe daylight saving time'
    )
  })
})

describe('generated customer.yaml', () => {
  it("FALSIFIER: each partner's chosen channel is authored on people[]", () => {
    const { root } = generated()
    expect(validate(root).ok).toBe(true)
    expect(root.people.map((p: Root) => p.channel)).toEqual(['email', 'telegram'])
    const emailOnly = generated({
      joint: { ...JOINT, preferred_channel: { p1: 'email', p2: 'email' } },
    }).root
    expect(emailOnly.people.map((p: Root) => p.channel)).toEqual(['email', 'email'])
  })

  it('validates, with every template placeholder filled', () => {
    const { yaml, root } = generated()
    expect(validate(root).ok).toBe(true)
    expect(yaml).not.toMatch(/'\[[A-Z]/)
    expect(root.customer_id).toBe('couple-test')
    expect(root.seat).toEqual({ kind: 'customer', product: 'hosted-agent', role: 'couples' })
    expect(root.hermes_ref).toBe(REF)
  })

  it('writes the confirmed schedules as UTC cron, per partner', () => {
    const { root } = generated()
    const cron = root.personas[0].cron as Array<{ skill: string; space: string; schedule: string }>
    const at = (skill: string, space: string) =>
      cron.find((c) => c.skill === skill && c.space === space)?.schedule
    expect(at('couples-checkin', 'person:p1')).toBe('30 1 * * 2,5')
    expect(at('couples-checkin', 'person:p2')).toBe('0 2 * * 3')
    expect(at('gratitude-prompts', 'person:p2')).toBe('15 3 * * 4')
    expect(at('dates-to-remember', 'person:p1')).toBe('0 15 * * *')
    expect(at('weekly-checkin-agenda', 'shared')).toBe('0 2 * * 1')
  })

  it('authors the joint answers the couples skills read on couples-checkin', () => {
    const { root } = generated()
    const skill = root.personas[0].skills.find((s: Root) => s.name === 'couples-checkin')
    expect(skill.settings).toEqual({
      // FALSIFIER: the template's send lane survives the onboarding settings.
      recipients: 'roster',
      timezone: 'America/Phoenix',
      topics: 'how the week felt; money; time together',
      quiet_hours: '21:00-08:00',
      onboarding_version: 3,
    })
  })

  it('Telegram fully absent (no bot, nobody connected): disabled, sentinel only, valid', () => {
    const { root } = generated()
    expect(validate(root).ok).toBe(true)
    expect(root.telegram.enabled).toBe(false)
    expect(root.telegram.allow_from).toEqual([COUPLES_SENTINEL_TELEGRAM_ID])
    expect(root.telegram.group_allow_from).toEqual([])
    expect(root.telegram.group_chats).toEqual([])
    expect(root.spaces.shared_telegram_chat).toBe('')
  })

  it('the template itself validates with Telegram fully absent (disabled)', () => {
    const r = generateCouplesSeat(TEMPLATE, input())
    expect(r.ok && r.warnings).toEqual([
      'Telegram is off (optional): no bot recorded and nobody connected. Email works.',
    ])
  })

  it('before anyone binds: sentinel alone on allow_from, nobody in the group', () => {
    const { root } = generated({ telegramBotUsername: 'juniper_couple_bot' })
    expect(root.telegram.enabled).toBe(true)
    expect(root.telegram.allow_from).toEqual([COUPLES_SENTINEL_TELEGRAM_ID])
    expect(root.telegram.group_allow_from).toEqual([])
    expect(root.people.map((p: Root) => p.telegram_id)).toEqual(['', ''])
  })

  it('FALSIFIER: with one partner bound the sentinel stays, and never enters the group fence', () => {
    const { root } = generated({
      people: [
        { key: 'p1', name: 'Alex', email: 'alex@example.com', telegramId: '111' },
        { key: 'p2', name: 'Sam', email: 'sam@example.com', telegramId: null },
      ],
    })
    expect(validate(root).ok).toBe(true)
    expect(root.telegram.allow_from).toEqual(['111', COUPLES_SENTINEL_TELEGRAM_ID])
    expect(root.telegram.group_allow_from).toEqual(['111'])
  })

  it('FALSIFIER: with both bound and the group recorded, the sentinel is gone', () => {
    const { root } = generated({
      people: [
        { key: 'p1', name: 'Alex', email: 'alex@example.com', telegramId: '111' },
        { key: 'p2', name: 'Sam', email: 'sam@example.com', telegramId: '222' },
      ],
      sharedTelegramChat: '-1001234567890',
    })
    expect(validate(root).ok).toBe(true)
    expect(root.telegram.allow_from).toEqual(['111', '222'])
    expect(root.telegram.group_allow_from).toEqual(['111', '222'])
    expect(root.telegram.group_chats).toEqual(['-1001234567890'])
    expect(root.spaces.shared_telegram_chat).toBe('-1001234567890')
  })

  it('FALSIFIER: every id and time is a quoted string (the overlay parses YAML 1.1)', () => {
    const { yaml } = generated({
      people: [
        { key: 'p1', name: 'Alex', email: 'alex@example.com', telegramId: '111' },
        { key: 'p2', name: 'Sam', email: 'sam@example.com', telegramId: '222' },
      ],
      sharedTelegramChat: '-1001234567890',
    })
    expect(yaml).toContain("telegram_id: '111'")
    expect(yaml).toContain("shared_telegram_chat: '-1001234567890'")
    expect(yaml).toMatch(/allow_from:\n\s+- '111'\n\s+- '222'/)
    expect(yaml).toContain("quiet_hours: '21:00-08:00'")
    expect(yaml).not.toMatch(/^\s+- \d+$/m)
  })

  it('FALSIFIER: output that would not validate is refused, not returned', () => {
    const r = generateCouplesSeat(TEMPLATE, input({ slug: 'Not A Slug!' }))
    expect(r.ok).toBe(false)
  })

  it('passes the standalone validator script (the provisioning gate)', () => {
    const dir = mkdtempSync(join(tmpdir(), 'couples-seat-'))
    const file = join(dir, 'customer.yaml')
    writeFileSync(file, generated().yaml)
    const out = execFileSync('npx', ['tsx', 'scripts/validate-customer-yaml.ts', file], {
      encoding: 'utf8',
    })
    expect(out).toMatch(/ OK$/m)
  }, 60_000)
})

describe('export -> generator pipeline (real schema)', () => {
  let db: D1Database
  let intakeId: string

  async function rows(): Promise<SeatExportRows> {
    const all = async (sql: string) => (await db.prepare(sql).all()).results ?? []
    return {
      instance: await all(EXPORT_QUERIES.instance(intakeId)),
      people: await all(EXPORT_QUERIES.people(intakeId)),
      joint: await all(EXPORT_QUERIES.joint(intakeId)),
      confirmations: await all(EXPORT_QUERIES.confirmations(intakeId)),
    }
  }

  beforeAll(async () => {
    db = createTestD1()
    await runMigrations(db, {
      files: discoverNumericMigrations(resolve(process.cwd(), 'migrations')),
    })
    intakeId = await createCouplesInstance(db, {
      coupleName: 'Alex and Sam',
      agentName: 'Juniper',
      partners: [
        { name: 'Alex Example', email: 'alex@example.com' },
        { name: 'Sam Example', email: 'sam@example.com' },
      ],
    })
    await db
      .prepare(`UPDATE hosted_agent_intake SET customer_slug = 'couple-test' WHERE id = ?`)
      .bind(intakeId)
      .run()
  })

  it('FALSIFIER: refuses before consent and joint confirmation', async () => {
    const parsed = parseSeatExport(await rows())
    expect(parsed.ok).toBe(false)
    if (!parsed.ok) {
      expect(parsed.errors).toEqual(
        expect.arrayContaining(['p1 has not consented.', 'No valid joint settings saved.'])
      )
    }
  })

  it('FALSIFIER: refuses with one confirmation of the joint section', async () => {
    const [p1, p2] = await listPeople(db, intakeId)
    await recordPersonIntake(db, p1.id, { name: 'Alex Example', consent: true })
    await recordPersonIntake(db, p2.id, { name: 'Sam Example', consent: true })
    await saveJointSettings(db, intakeId, p1.id, JOINT)
    const parsed = parseSeatExport(await rows())
    expect(parsed.ok).toBe(false)
    if (!parsed.ok)
      expect(parsed.errors).toEqual(['Joint settings are not confirmed by both partners.'])
  })

  it('generates a valid seat once both confirmed, carrying the bound Telegram id', async () => {
    const [p1, p2] = await listPeople(db, intakeId)
    await confirmJointSettings(db, intakeId, p2.id, 1)
    const { token } = await issueLinkToken(db, p1.id)
    await redeemLinkToken(db, { token, seatSlug: 'couple-test', telegramUserId: '111' })

    const parsed = parseSeatExport(await rows())
    if (!parsed.ok) throw new Error(parsed.errors.join('; '))
    expect(parsed.value.telegramBotUsername).toBeNull()
    const r = generateCouplesSeat(TEMPLATE, { ...parsed.value, hermesRef: REF, now: NOW })
    if (!r.ok) throw new Error(r.errors.join('; '))
    const root = parseYaml(r.yaml) as Root
    expect(validate(root).ok).toBe(true)
    expect(root.people[0]).toMatchObject({
      id: 'p1',
      telegram_id: '111',
      email: 'alex@example.com',
    })
    expect(root.telegram.allow_from).toEqual(['111', COUPLES_SENTINEL_TELEGRAM_ID])
    expect(root.telegram.enabled).toBe(true)
    expect(r.warnings).toEqual([
      '1 partner(s) have not linked Telegram: the sentinel id stays on allow_from.',
      'No shared group recorded: group_chats is empty.',
    ])
  })
})
