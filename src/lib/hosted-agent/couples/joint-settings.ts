/**
 * The joint section of couples onboarding (ADR 0090 decision 4, role.yaml
 * `onboarding.joint`): what the couple decides together. Pure parsing and
 * validation; persistence lives in src/lib/db/couples-onboarding.ts.
 *
 * Every field maps to one place in the generated customer.yaml:
 *
 *   timezone            the clock every schedule below is expressed in; the
 *                       generator converts to UTC cron
 *   checkin_topics      couples-checkin skill settings (the topics it asks about)
 *   checkin.<p>         couples-checkin routine schedule for that partner
 *   gratitude.<p>       gratitude-prompts routine schedule for that partner
 *   daily_reminder_time dates-to-remember (both partners) and travel (shared)
 *   weekly_checkin      weekly-checkin-agenda (shared)
 *   quiet_hours         couples-checkin skill settings (each partner's default)
 *   preferred_channel   couples-checkin skill settings (email by default)
 *
 * A partner's cadence is part of the JOINT section on purpose: the Captain's
 * decision on #3264 is that a change to check-ins or to the other partner's
 * settings needs both partners' yes.
 */

export const PERSON_KEYS = ['p1', 'p2'] as const
export type PersonKey = (typeof PERSON_KEYS)[number]

export const WEEKDAYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'] as const
export type Weekday = (typeof WEEKDAYS)[number]

export interface Cadence {
  days: Weekday[]
  /** Local time of day, HH:MM (24h). */
  time: string
}

export interface JointSettings {
  timezone: string
  checkin_topics: string[]
  checkin: Record<PersonKey, Cadence>
  gratitude: Record<PersonKey, Cadence>
  daily_reminder_time: string
  weekly_checkin: { day: Weekday; time: string }
  quiet_hours: { start: string; end: string }
  /**
   * How each partner prefers the agent to reach them. Email is the default:
   * every partner has an approved address from onboarding, and Telegram is
   * optional and may be connected later (Captain, 2026-10-17).
   */
  preferred_channel: Record<PersonKey, Channel>
}

export const CHANNELS = ['email', 'telegram'] as const
export type Channel = (typeof CHANNELS)[number]

function parseChannel(raw: unknown): Channel {
  return raw === 'telegram' ? 'telegram' : 'email'
}

/** Missing or unknown values fall back to email, the default channel. */
function parseChannels(raw: unknown): Record<PersonKey, Channel> {
  const get = (k: string): unknown =>
    typeof raw === 'object' && raw !== null ? Reflect.get(raw, k) : undefined
  return { p1: parseChannel(get('p1')), p2: parseChannel(get('p2')) }
}

const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/
const MAX_TOPICS = 12
const MAX_TOPIC_CHARS = 80

export function isValidTimezone(tz: string): boolean {
  if (tz.length === 0 || tz.length > 64) return false
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: tz })
    return true
  } catch {
    return false
  }
}

function isWeekday(value: unknown): value is Weekday {
  return typeof value === 'string' && (WEEKDAYS as readonly string[]).includes(value)
}

function isTime(value: unknown): value is string {
  return typeof value === 'string' && TIME_RE.test(value)
}

function parseTopics(raw: unknown): string[] | null {
  if (!Array.isArray(raw)) return null
  const topics: string[] = []
  for (const t of raw) {
    if (typeof t !== 'string') return null
    const topic = t.trim()
    if (topic.length === 0) continue
    // A topic is stored in a `key: value` frontmatter line and a `;`-joined
    // setting, so neither separator nor a newline may appear inside one.
    if (topic.length > MAX_TOPIC_CHARS || /[;\n\r:]/.test(topic)) return null
    if (!topics.includes(topic)) topics.push(topic)
  }
  return topics.length > 0 && topics.length <= MAX_TOPICS ? topics : null
}

function parseCadence(raw: unknown): Cadence | null {
  if (typeof raw !== 'object' || raw === null) return null
  const days: unknown = Reflect.get(raw, 'days')
  const time: unknown = Reflect.get(raw, 'time')
  if (!Array.isArray(days) || days.length === 0 || !days.every(isWeekday)) return null
  if (!isTime(time)) return null
  // Keep calendar order and drop repeats so the cron is stable.
  const ordered = WEEKDAYS.filter((d) => days.includes(d))
  return { days: ordered, time }
}

function parsePerPerson(raw: unknown): Record<PersonKey, Cadence> | null {
  if (typeof raw !== 'object' || raw === null) return null
  const p1 = parseCadence(Reflect.get(raw, 'p1'))
  const p2 = parseCadence(Reflect.get(raw, 'p2'))
  return p1 && p2 ? { p1, p2 } : null
}

/**
 * Parse an untrusted value (a stored JSON blob or a form-built object) into
 * JointSettings. Null when any field is missing or invalid: the joint section
 * is all or nothing, so a half-filled one can never be confirmed.
 */
export function parseJointSettings(raw: unknown): JointSettings | null {
  if (typeof raw !== 'object' || raw === null) return null
  const get = (k: string): unknown => Reflect.get(raw, k)
  const timezone = get('timezone')
  if (typeof timezone !== 'string' || !isValidTimezone(timezone)) return null
  const checkin_topics = parseTopics(get('checkin_topics'))
  const checkin = parsePerPerson(get('checkin'))
  const gratitude = parsePerPerson(get('gratitude'))
  const daily = get('daily_reminder_time')
  const weekly_checkin = parseWeekly(get('weekly_checkin'))
  const quiet_hours = parseQuiet(get('quiet_hours'))
  if (!checkin_topics || !checkin || !gratitude || !isTime(daily)) return null
  if (!weekly_checkin || !quiet_hours) return null
  return {
    timezone,
    checkin_topics,
    checkin,
    gratitude,
    daily_reminder_time: daily,
    weekly_checkin,
    quiet_hours,
    preferred_channel: parseChannels(get('preferred_channel')),
  }
}

function parseWeekly(raw: unknown): JointSettings['weekly_checkin'] | null {
  if (typeof raw !== 'object' || raw === null) return null
  const day: unknown = Reflect.get(raw, 'day')
  const time: unknown = Reflect.get(raw, 'time')
  return isWeekday(day) && isTime(time) ? { day, time } : null
}

function parseQuiet(raw: unknown): JointSettings['quiet_hours'] | null {
  if (typeof raw !== 'object' || raw === null) return null
  const start: unknown = Reflect.get(raw, 'start')
  const end: unknown = Reflect.get(raw, 'end')
  return isTime(start) && isTime(end) && start !== end ? { start, end } : null
}

function formDays(form: FormData, name: string): string[] {
  return form.getAll(name).filter((v): v is string => typeof v === 'string')
}

function formText(form: FormData, name: string): string {
  const v = form.get(name)
  return typeof v === 'string' ? v.trim() : ''
}

/** Build the candidate object from the portal's joint form, then parse it. */
export function jointSettingsFromForm(form: FormData): JointSettings | null {
  const cadence = (prefix: string, p: PersonKey) => ({
    days: formDays(form, `${prefix}_${p}_days`),
    time: formText(form, `${prefix}_${p}_time`),
  })
  return parseJointSettings({
    timezone: formText(form, 'timezone'),
    checkin_topics: formText(form, 'checkin_topics').split(/\r?\n/),
    checkin: { p1: cadence('checkin', 'p1'), p2: cadence('checkin', 'p2') },
    gratitude: { p1: cadence('gratitude', 'p1'), p2: cadence('gratitude', 'p2') },
    daily_reminder_time: formText(form, 'daily_reminder_time'),
    weekly_checkin: {
      day: formText(form, 'weekly_checkin_day'),
      time: formText(form, 'weekly_checkin_time'),
    },
    quiet_hours: { start: formText(form, 'quiet_start'), end: formText(form, 'quiet_end') },
    preferred_channel: {
      p1: formText(form, 'channel_p1'),
      p2: formText(form, 'channel_p2'),
    },
  })
}
