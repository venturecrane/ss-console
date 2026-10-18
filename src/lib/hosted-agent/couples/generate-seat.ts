/**
 * The couples seat generator: onboarding intake -> customer.yaml (ADR 0090
 * decision 4, the ADR 0067 "guided generator from intake fields" seam, built
 * once for the role).
 *
 * Input is what onboarding recorded and both partners confirmed; output is the
 * seat's customer.yaml, made from operator/customers/_couples-template/ and
 * nothing else. Every value in the output is either the template's, an
 * authored onboarding answer, or a mechanical conversion of one (local time
 * plus timezone to a UTC cron). Nothing is invented: a field onboarding did not
 * produce stays at its template default (an unbound Telegram id stays '', an
 * unrecorded group stays '').
 *
 * The output is validated by the canonical validator before it is returned, and
 * it reaches a seat only through a reviewed PR (scripts/couples-seat.ts never
 * merges): a merged customer.yaml publishes to R2 within a minute.
 *
 * TELEGRAM is optional (Captain, 2026-10-17: email is the default channel and
 * Telegram may be connected later). With no bot recorded and nobody bound the
 * block is `enabled: false` and holds only the sentinel.
 *
 * When on (ADR 0091 s5, R4): a bound partner's id goes on people[].telegram_id,
 * telegram.allow_from and telegram.group_allow_from. Until BOTH are bound the
 * SMD sentinel id also stays on allow_from (never on group_allow_from), so the
 * boot guard against an empty allowlist holds while no partner is admitted
 * that onboarding has not bound.
 */

import { parseDocument, isMap, isScalar, isSeq, type Document } from 'yaml'
import { validate, type ValidationError } from '../../operator/customer-yaml'
import { WEEKDAYS, type Cadence, type JointSettings, type PersonKey } from './joint-settings'

/**
 * The id that holds the Telegram allowlist non-empty before both partners
 * bind. 777000 is Telegram's own service-notifications account: it is not a
 * person and never messages a bot, so it admits no one.
 */
export const COUPLES_SENTINEL_TELEGRAM_ID = '777000'

export interface SeatPersonInput {
  key: PersonKey
  name: string
  email: string
  telegramId: string | null
}

export interface CouplesSeatInput {
  slug: string
  coupleName: string
  agentName: string
  hermesRef: string
  people: [SeatPersonInput, SeatPersonInput]
  joint: JointSettings
  jointVersion: number
  sharedTelegramChat: string | null
  /**
   * The seat's bot, once SMD made one in BotFather. Telegram is optional
   * (email is the default channel): with no bot and nobody bound, the seat's
   * Telegram block is authored `enabled: false`.
   */
  telegramBotUsername: string | null
  /** Provenance for the generated header. */
  intakeId: string
  /** The instant whose UTC offset converts local times (DST is not followed). */
  now: Date
}

export type GenerateResult =
  { ok: true; yaml: string; warnings: string[] } | { ok: false; errors: string[] }

/** Minutes east of UTC for `tz` at `at` (Phoenix: -420). */
export function utcOffsetMinutes(tz: string, at: Date): number {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: tz,
    hourCycle: 'h23',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).formatToParts(at)
  const n = (t: string): number => Number(parts.find((p) => p.type === t)?.value ?? '0')
  const asUtc = Date.UTC(n('year'), n('month') - 1, n('day'), n('hour'), n('minute'))
  const atMinute = Math.floor(at.getTime() / 60000) * 60000
  return Math.round((asUtc - atMinute) / 60000)
}

/**
 * A local weekly cadence as a UTC cron. Days shift with the time when the
 * conversion crosses midnight, so "Monday 18:30 in Phoenix" becomes
 * "Tuesday 01:30 UTC".
 */
export function cadenceToUtcCron(cadence: Cadence, offsetMinutes: number): string {
  const [h, m] = cadence.time.split(':').map(Number) as [number, number]
  let minutes = h * 60 + m - offsetMinutes
  let shift = 0
  if (minutes < 0) {
    minutes += 1440
    shift = -1
  } else if (minutes >= 1440) {
    minutes -= 1440
    shift = 1
  }
  const days = cadence.days.map((d) => (WEEKDAYS.indexOf(d) + shift + 7) % 7).sort((a, b) => a - b)
  return `${minutes % 60} ${Math.floor(minutes / 60)} * * ${days.join(',')}`
}

export function dailyToUtcCron(time: string, offsetMinutes: number): string {
  return cadenceToUtcCron({ days: [...WEEKDAYS], time }, offsetMinutes).replace(
    / \* \* [0-9,]+$/,
    ' * * *'
  )
}

/** A persona slug from the agent's name: lowercase, hyphens for anything else. */
export function personaSlug(agentName: string): string {
  return agentName
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 32)
}

function substitutions(input: CouplesSeatInput, offset: number): Record<string, string> {
  const [p1, p2] = input.people
  const daily = dailyToUtcCron(input.joint.daily_reminder_time, offset)
  const weekly = cadenceToUtcCron(
    { days: [input.joint.weekly_checkin.day], time: input.joint.weekly_checkin.time },
    offset
  )
  return {
    '[SLUG]': input.slug,
    '[COUPLE NAME from intake]': input.coupleName,
    '[CURRENT BLESSED FLEET PIN, e.g., v2026.5.7@<40-hex-sha>]': input.hermesRef,
    '[PARTNER 1 NAME]': p1.name,
    '[PARTNER 2 NAME]': p2.name,
    '[PARTNER 1 EMAIL, lowercased]': p1.email.toLowerCase(),
    '[PARTNER 2 EMAIL, lowercased]': p2.email.toLowerCase(),
    '[AGENT NAME, lowercased]': personaSlug(input.agentName),
    '[AGENT NAME from intake]': input.agentName,
    '[PARTNER 1 CHECK-IN CRON, UTC]': cadenceToUtcCron(input.joint.checkin.p1, offset),
    '[PARTNER 2 CHECK-IN CRON, UTC]': cadenceToUtcCron(input.joint.checkin.p2, offset),
    '[PARTNER 1 GRATITUDE CRON, UTC]': cadenceToUtcCron(input.joint.gratitude.p1, offset),
    '[PARTNER 2 GRATITUDE CRON, UTC]': cadenceToUtcCron(input.joint.gratitude.p2, offset),
    '[DAILY REMINDER CRON, UTC]': daily,
    '[WEEKLY CHECK-IN AGENDA CRON, UTC]': weekly,
    '[SMD SENTINEL TELEGRAM ID until both partners bind]': COUPLES_SENTINEL_TELEGRAM_ID,
  }
}

/** Replace the template's how-to-use header with a provenance header. */
function header(input: CouplesSeatInput): string {
  return [
    '# COUPLES seat (ADR 0090 role `couples`, ADR 0091 spaces).',
    '#',
    `# GENERATED by scripts/couples-seat.ts from couples onboarding intake ${input.intakeId}`,
    `# (joint settings version ${input.jointVersion}, confirmed by both partners), from`,
    '# operator/customers/_couples-template/customer.yaml. Change it by changing the',
    '# onboarding answers and regenerating, not by hand: a hand edit is overwritten.',
    '#',
    `# Schedules are UTC, converted from ${input.joint.timezone} at UTC offset`,
    `# ${utcOffsetMinutes(input.joint.timezone, input.now)} minutes on ${input.now.toISOString().slice(0, 10)}.`,
    '# A timezone with daylight saving time shifts them by an hour for part of the',
    '# year; regenerate after a change if the couple wants the local time kept.',
    '',
  ].join('\n')
}

function stripTemplateHeader(text: string): string {
  const start = text.indexOf('# COUPLES seat template')
  const end = text.indexOf('# ---- IDENTITY ----')
  if (start === -1 || end === -1 || end < start) return text
  return text.slice(0, start) + text.slice(end)
}

function boundIds(input: CouplesSeatInput): string[] {
  return input.people.map((p) => p.telegramId ?? '').filter((id) => /^\d+$/.test(id))
}

/** Telegram is on only when there is a bot to restrict or someone bound to it. */
export function telegramEnabled(input: CouplesSeatInput): boolean {
  return input.telegramBotUsername !== null || boundIds(input).length > 0
}

function setTelegram(doc: Document, input: CouplesSeatInput): void {
  input.people.forEach((p, i) => doc.setIn(['people', i, 'telegram_id'], p.telegramId ?? ''))
  doc.setIn(['telegram', 'enabled'], telegramEnabled(input))
  const bound = boundIds(input)
  const allow = bound.length === 2 ? bound : [...bound, COUPLES_SENTINEL_TELEGRAM_ID]
  doc.setIn(['telegram', 'allow_from'], doc.createNode(allow))
  doc.setIn(['telegram', 'group_allow_from'], doc.createNode(bound))
  const chat = input.sharedTelegramChat ?? ''
  doc.setIn(['spaces', 'shared_telegram_chat'], chat)
  doc.setIn(['telegram', 'group_chats'], doc.createNode(chat ? [chat] : []))
  for (const path of [
    ['telegram', 'allow_from'],
    ['telegram', 'group_allow_from'],
    ['telegram', 'group_chats'],
  ]) {
    const node = doc.getIn(path, true)
    if (!isSeq(node)) continue
    // Block style like the template; an empty list stays `[]` on its key line.
    node.flow = node.items.length === 0
    for (const item of node.items) if (isScalar(item)) item.type = 'QUOTE_SINGLE'
  }
}

/**
 * The joint answers the couples skills read, authored on the couples-checkin
 * skill (role.yaml: checkin_topics and quiet_hours map to couples-checkin).
 * The overlay seeds the shared `joint-settings.md` record and each partner's
 * default quiet hours from these at boot, and never overwrites a record the
 * couple changed together afterwards.
 */
function setCheckinSettings(doc: Document, input: CouplesSeatInput): void {
  const skills = doc.getIn(['personas', 0, 'skills'], true)
  if (!isSeq(skills)) return
  for (const item of skills.items) {
    if (!isMap(item) || item.get('name') !== 'couples-checkin') continue
    const settings = doc.createNode({
      timezone: input.joint.timezone,
      topics: input.joint.checkin_topics.join('; '),
      quiet_hours: `${input.joint.quiet_hours.start}-${input.joint.quiet_hours.end}`,
      preferred_channels: `p1=${input.joint.preferred_channel.p1}; p2=${input.joint.preferred_channel.p2}`,
      onboarding_version: input.jointVersion,
    })
    // Quote every string: the overlay reads this file with a YAML 1.1 parser,
    // where an unquoted `21:00`-shaped value can become a base-60 number.
    if (isMap(settings)) {
      for (const pair of settings.items) {
        if (isScalar(pair.value) && typeof pair.value.value === 'string') {
          pair.value.type = 'QUOTE_SINGLE'
        }
      }
    }
    item.set('settings', settings)
  }
}

function formatErrors(errors: ValidationError[]): string[] {
  return errors.map((e) => `${e.code} at ${e.path}: ${e.message}`)
}

export function generateCouplesSeat(templateText: string, input: CouplesSeatInput): GenerateResult {
  const offset = utcOffsetMinutes(input.joint.timezone, input.now)
  let text = stripTemplateHeader(templateText)
  for (const [k, v] of Object.entries(substitutions(input, offset))) text = text.split(k).join(v)
  const leftover = text.match(/'\[[A-Z][^\]]*\]'/g)
  if (leftover) return { ok: false, errors: [`template placeholder not filled: ${leftover[0]}`] }

  const doc = parseDocument(text)
  if (doc.errors.length > 0) return { ok: false, errors: doc.errors.map((e) => e.message) }
  setTelegram(doc, input)
  setCheckinSettings(doc, input)

  const yaml = header(input) + doc.toString({ lineWidth: 0 })
  const result = validate(parseDocument(yaml).toJS())
  if (!result.ok) return { ok: false, errors: formatErrors(result.errors) }

  const warnings: string[] = []
  const bound = boundIds(input).length
  if (!telegramEnabled(input)) {
    warnings.push('Telegram is off (optional): no bot recorded and nobody connected. Email works.')
  } else if (bound < 2) {
    warnings.push(
      `${2 - bound} partner(s) have not linked Telegram: the sentinel id stays on allow_from.`
    )
  }
  if (telegramEnabled(input) && !input.sharedTelegramChat) {
    warnings.push('No shared group recorded: group_chats is empty.')
  }
  return { ok: true, yaml, warnings }
}
