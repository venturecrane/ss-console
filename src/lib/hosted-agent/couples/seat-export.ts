/**
 * The onboarding rows a couples seat is generated from, read back from D1 and
 * parsed (never cast) into the generator's input. Used by
 * scripts/couples-seat.ts; the SQL lives here so the script and its test read
 * the same columns.
 *
 * Refuses (returns `errors`) unless onboarding is complete: a customer slug and
 * agent name, two partners who both consented, and a joint section whose
 * CURRENT version both partners confirmed. Telegram binding and the shared
 * group are not required: the generator writes what is bound and keeps the
 * sentinel for the rest.
 */

import { parseJointSettings, type JointSettings } from './joint-settings'
import type { SeatPersonInput } from './generate-seat'

export const EXPORT_QUERIES = {
  instance: (id: string) =>
    `SELECT i.id, i.customer_slug, i.agent_name, i.shared_telegram_chat, i.telegram_bot_username,
            e.name AS couple_name
       FROM hosted_agent_intake i JOIN entities e ON e.id = i.entity_id
      WHERE i.id = '${id}' AND i.role = 'couples'`,
  people: (id: string) =>
    `SELECT id, person_key, name, email, consent_at, telegram_id
       FROM hosted_agent_people WHERE intake_id = '${id}' ORDER BY person_key`,
  joint: (id: string) =>
    `SELECT version, settings_json FROM hosted_agent_joint_settings WHERE intake_id = '${id}'`,
  confirmations: (id: string) =>
    `SELECT c.person_id FROM hosted_agent_joint_confirmations c
       JOIN hosted_agent_joint_settings s ON s.intake_id = c.intake_id AND s.version = c.version
      WHERE c.intake_id = '${id}'`,
}

/** An intake id is a UUID; anything else never reaches a query string. */
export const INTAKE_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/

export interface SeatExportRows {
  instance: unknown[]
  people: unknown[]
  joint: unknown[]
  confirmations: unknown[]
}

export interface ParsedSeatExport {
  intakeId: string
  slug: string
  coupleName: string
  agentName: string
  sharedTelegramChat: string | null
  telegramBotUsername: string | null
  people: [SeatPersonInput, SeatPersonInput]
  joint: JointSettings
  jointVersion: number
}

function str(row: unknown, key: string): string | null {
  if (typeof row !== 'object' || row === null) return null
  const v: unknown = Reflect.get(row, key)
  return typeof v === 'string' && v.length > 0 ? v : null
}

function num(row: unknown, key: string): number | null {
  if (typeof row !== 'object' || row === null) return null
  const v: unknown = Reflect.get(row, key)
  return typeof v === 'number' && Number.isInteger(v) ? v : null
}

interface PersonRow {
  id: string
  person: SeatPersonInput
  consented: boolean
}

function parsePerson(row: unknown): PersonRow | null {
  const id = str(row, 'id')
  const key = str(row, 'person_key')
  const name = str(row, 'name')
  const email = str(row, 'email')
  if (!id || (key !== 'p1' && key !== 'p2') || !name || !email) return null
  const tg = str(row, 'telegram_id')
  return {
    id,
    person: { key, name, email, telegramId: tg && /^\d+$/.test(tg) ? tg : null },
    consented: str(row, 'consent_at') !== null,
  }
}

function parseJointRow(row: unknown): { version: number; settings: JointSettings } | null {
  const version = num(row, 'version')
  const json = str(row, 'settings_json')
  if (version === null || json === null) return null
  try {
    const settings = parseJointSettings(JSON.parse(json))
    return settings ? { version, settings } : null
  } catch {
    return null
  }
}

function checkPeople(raw: unknown[], errors: string[]): PersonRow[] {
  const people = raw.map(parsePerson)
  if (people.length !== 2 || people.some((p) => p === null)) {
    errors.push('Expected two well-formed partner rows.')
  }
  const valid = people.filter((p): p is PersonRow => p !== null)
  for (const p of valid) if (!p.consented) errors.push(`${p.person.key} has not consented.`)
  return valid
}

function checkJoint(
  rows: SeatExportRows,
  people: PersonRow[],
  errors: string[]
): { version: number; settings: JointSettings } | null {
  const joint = parseJointRow(rows.joint[0])
  if (!joint) {
    errors.push('No valid joint settings saved.')
    return null
  }
  const confirmed = new Set(rows.confirmations.map((c) => str(c, 'person_id')))
  if (!people.every((p) => confirmed.has(p.id))) {
    errors.push('Joint settings are not confirmed by both partners.')
  }
  return joint
}

export function parseSeatExport(
  rows: SeatExportRows
): { ok: true; value: ParsedSeatExport } | { ok: false; errors: string[] } {
  const errors: string[] = []
  const inst = rows.instance[0]
  const intakeId = str(inst, 'id')
  const slug = str(inst, 'customer_slug')
  const agentName = str(inst, 'agent_name')
  const coupleName = str(inst, 'couple_name')
  if (!intakeId) return { ok: false, errors: ['No couples instance with that id.'] }
  if (!slug) errors.push('No customer slug assigned.')
  if (!agentName) errors.push('No agent name.')
  const valid = checkPeople(rows.people, errors)
  const joint = checkJoint(rows, valid, errors)
  if (errors.length > 0 || !joint || !slug || !agentName || valid.length !== 2) {
    return { ok: false, errors }
  }
  return {
    ok: true,
    value: {
      intakeId,
      slug,
      coupleName: coupleName ?? slug,
      agentName,
      sharedTelegramChat: str(inst, 'shared_telegram_chat'),
      telegramBotUsername: str(inst, 'telegram_bot_username'),
      people: [valid[0].person, valid[1].person],
      joint: joint.settings,
      jointVersion: joint.version,
    },
  }
}
