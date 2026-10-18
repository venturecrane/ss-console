/**
 * Multi-person seat validator: `people:`, `spaces:`, and the cross-field rules
 * that make a seat's privacy by space hold together (ADR 0091).
 *
 * WHY THIS EXISTS. Authoring `people:` turns a seat into space mode. Every turn
 * then resolves to exactly one space (`person:<id>` or `shared`) from session
 * facts the model cannot write, and every read and write is filtered by it. The
 * overlay enforces that at runtime; this file is the pre-merge half, and its
 * rules are the ones a config could break without any runtime noticing:
 *
 *   1. `people` requires `spaces`, `memory.memory_enabled: false`,
 *      `memory.user_profile_enabled: false`, and a `space` on every cron entry.
 *      Native Hermes memory is one identity-blind store per profile, so a seat
 *      that left it on would hold both partners' words in one place the model
 *      reads. A cron run with no space would have no delivery target and no
 *      readable space.
 *   2. A cron `space` names a person id or `shared`. A space on a seat with no
 *      `people:` is rejected too: nothing could resolve it, and an authored key
 *      with no effect is the silent-drop class the block registry exists for.
 *   3. Each person's email is on `scope.inbound_allow_from`, or that person's
 *      mail would be refused at the door while the seat believes it serves them.
 *   4. A bound `telegram_id` is on both `telegram.allow_from` (DMs) and
 *      `telegram.group_allow_from` (the shared group). An empty id means the
 *      person has not bound Telegram yet (onboarding), and is allowed.
 *   5. The group answers only people (mirrors overlay #469's translate-time
 *      rules): every `telegram.group_allow_from` id is a bound person's
 *      `telegram_id`, and `telegram.group_chats` holds at most
 *      `spaces.shared_telegram_chat`. An unbound shared chat ('') is allowed;
 *      the overlay then delivers shared routines locally.
 *   6. No two cron entries share a (skill, space) pair: the pair is the
 *      routine's identity once space mode is on.
 *   (`people[].channel`, optional, is `email` or `telegram`: how the seat
 *   reaches that person on its own initiative. Email when absent.)
 *   7. No record store authors `readers`. On a space-mode seat a record's owner
 *      is a space (`p1`, `person:p1` or `shared`) and the fence reads by the
 *      turn's spaces, so `readers` would grant nothing; authoring it would
 *      read as an access grant the runtime does not honour.
 *   8. `scope.inbound_allow_from` is EXACTLY the people's emails: no SMD
 *      address, no other address, no domain. On such a seat a skill authored
 *      `recipients: roster` sends on a lane the broker opens to every rostered
 *      address, and the model picks the recipient; a Brokery rehearsal reached
 *      a real client that way (2026-10-16). The roster must be only the people
 *      the seat serves, so nobody else can be picked.
 *
 * `spaces` without `people` is rejected for the same reason as rule 2.
 *
 * Validate-only for the cross-field rules; `checkPeople` / `checkSpaces` return
 * the parsed blocks for the typed CustomerYaml.
 */

import type { SeatPerson, SeatSpaces, ValidationError } from './types'
import { isPlainObject } from './helpers'

const PERSON_KEYS = new Set(['id', 'name', 'telegram_id', 'email', 'channel'])
const SPACES_KEYS = new Set(['shared_telegram_chat'])
const PERSON_ID = /^[a-z0-9-]+$/
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

function err(path: string, message: string, errors: ValidationError[]): void {
  errors.push({ code: 'InvalidPeople', path, message })
}

/** A person id is a unique slug; records it in `seen` when it is. */
function checkPersonId(
  id: unknown,
  path: string,
  seen: Set<string>,
  errors: ValidationError[]
): boolean {
  if (typeof id !== 'string' || !PERSON_ID.test(id)) {
    err(`${path}.id`, 'people[].id must be a slug of [a-z0-9-]+', errors)
    return false
  }
  if (seen.has(id)) {
    err(`${path}.id`, `people[].id "${id}" is repeated; ids must be unique`, errors)
    return false
  }
  seen.add(id)
  return true
}

/** `people[].channel`: absent means email (the default channel). Null when invalid. */
function checkChannel(
  raw: unknown,
  path: string,
  errors: ValidationError[]
): 'email' | 'telegram' | undefined | null {
  if (raw === undefined) return undefined
  if (raw === 'email' || raw === 'telegram') return raw
  err(
    `${path}.channel`,
    "people[].channel must be 'email' or 'telegram' (email when absent)",
    errors
  )
  return null
}

function checkPerson(
  raw: unknown,
  path: string,
  seen: Set<string>,
  errors: ValidationError[]
): SeatPerson | null {
  if (!isPlainObject(raw)) {
    err(path, 'each people entry must be an object', errors)
    return null
  }
  for (const key of Object.keys(raw)) {
    if (!PERSON_KEYS.has(key)) {
      err(`${path}.${key}`, `unknown key (known: ${[...PERSON_KEYS].join(', ')})`, errors)
    }
  }
  const { id, name, telegram_id: tg, email } = raw
  let ok = checkPersonId(id, path, seen, errors)
  if (typeof name !== 'string' || name.trim().length === 0) {
    err(`${path}.name`, 'people[].name must be a non-empty string', errors)
    ok = false
  }
  if (typeof tg !== 'string' || !(tg === '' || /^\d+$/.test(tg))) {
    err(
      `${path}.telegram_id`,
      "people[].telegram_id must be a numeric Telegram user id as a string, or '' until bound",
      errors
    )
    ok = false
  }
  if (typeof email !== 'string' || !EMAIL.test(email) || email !== email.toLowerCase()) {
    err(`${path}.email`, 'people[].email must be a lowercased email address', errors)
    ok = false
  }
  const channel = checkChannel(raw['channel'], path, errors)
  if (channel === null) ok = false
  if (!ok) return null
  return {
    id: id as string,
    name: name as string,
    telegram_id: tg as string,
    email: email as string,
    ...(channel ? { channel } : {}),
  }
}

/** Parse the optional `people:` block. Returns null when absent or invalid. */
export function checkPeople(
  root: Record<string, unknown>,
  errors: ValidationError[]
): SeatPerson[] | null {
  const raw = root['people']
  if (raw === undefined || raw === null) return null
  if (!Array.isArray(raw) || raw.length === 0) {
    err('people', 'people must be a non-empty list when present', errors)
    return null
  }
  const seen = new Set<string>()
  const out: SeatPerson[] = []
  raw.forEach((p, i) => {
    const person = checkPerson(p, `people[${i}]`, seen, errors)
    if (person !== null) out.push(person)
  })
  return out.length === raw.length ? out : null
}

/** Parse the optional `spaces:` block. Returns null when absent or invalid. */
export function checkSpaces(
  root: Record<string, unknown>,
  errors: ValidationError[]
): SeatSpaces | null {
  const raw = root['spaces']
  if (raw === undefined || raw === null) return null
  if (!isPlainObject(raw)) {
    err('spaces', 'spaces must be an object', errors)
    return null
  }
  for (const key of Object.keys(raw)) {
    if (!SPACES_KEYS.has(key)) {
      err(`spaces.${key}`, `unknown key (known: ${[...SPACES_KEYS].join(', ')})`, errors)
    }
  }
  const chat = raw['shared_telegram_chat']
  if (typeof chat !== 'string' || !(chat === '' || /^-\d+$/.test(chat))) {
    err(
      'spaces.shared_telegram_chat',
      "spaces.shared_telegram_chat must be a negative numeric group chat id as a string, or '' until created",
      errors
    )
    return null
  }
  return { shared_telegram_chat: chat }
}

/** Every authored `personas[].cron[]` entry, with its path. */
function cronEntries(root: Record<string, unknown>): Array<[string, Record<string, unknown>]> {
  const out: Array<[string, Record<string, unknown>]> = []
  const personas = root['personas']
  if (!Array.isArray(personas)) return out
  personas.forEach((p, i) => {
    if (!isPlainObject(p) || !Array.isArray(p['cron'])) return
    ;(p['cron'] as unknown[]).forEach((c, j) => {
      if (isPlainObject(c)) out.push([`personas[${i}].cron[${j}]`, c])
    })
  })
  return out
}

function idList(block: unknown, key: string): string[] {
  if (!isPlainObject(block)) return []
  const list = block[key]
  return Array.isArray(list) ? list.filter((v): v is string => typeof v === 'string') : []
}

/** Rule 2 (and the space-mode half of rule 1): every cron `space` resolves. */
function checkCronSpaces(
  root: Record<string, unknown>,
  ids: Set<string> | null,
  errors: ValidationError[]
): void {
  for (const [path, cron] of cronEntries(root)) {
    const space = cron['space']
    if (space === undefined || space === null) {
      if (ids !== null) {
        errors.push({
          code: 'InvalidCronSpace',
          path: `${path}.space`,
          message:
            'cron.space is required on a seat that authors people: (person:<id> or shared). ' +
            'It is the run space and the delivery target. See ADR 0091.',
        })
      }
      continue
    }
    if (ids === null) {
      errors.push({
        code: 'InvalidCronSpace',
        path: `${path}.space`,
        message: 'cron.space is only meaningful on a seat that authors people: (ADR 0091)',
      })
      continue
    }
    const named =
      space === 'shared' ||
      (typeof space === 'string' && space.startsWith('person:') && ids.has(space.slice(7)))
    if (!named) {
      errors.push({
        code: 'InvalidCronSpace',
        path: `${path}.space`,
        message: `cron.space must be "shared" or "person:<id>" for a people[].id (known: ${[...ids].join(', ')})`,
      })
    }
  }
}

/** Rule 6: a (skill, space) pair names one routine. */
function checkDuplicateCronSpaces(root: Record<string, unknown>, errors: ValidationError[]): void {
  const seen = new Set<string>()
  for (const [path, cron] of cronEntries(root)) {
    const { skill, space } = cron
    // A non-string skill or space is already reported by its own check.
    if (typeof skill !== 'string' || typeof space !== 'string') continue
    const key = `${skill}\u0000${space}`
    if (seen.has(key)) {
      errors.push({
        code: 'InvalidCronSpace',
        path: `${path}.space`,
        message: `another cron entry already runs ${skill} in ${space}`,
      })
    }
    seen.add(key)
  }
}

/** Rule 5: the group fences admit only people, and only the shared chat. */
function checkGroupFence(
  root: Record<string, unknown>,
  people: SeatPerson[],
  errors: ValidationError[]
): void {
  const bound = new Set(people.map((p) => p.telegram_id).filter((t) => t !== ''))
  idList(root['telegram'], 'group_allow_from').forEach((id, i) => {
    if (!bound.has(id.trim())) {
      err(
        `telegram.group_allow_from[${i}]`,
        'on a seat that authors people:, every group_allow_from id must be a bound ' +
          "person's telegram_id (the group answers only people)",
        errors
      )
    }
  })
  const spaces = root['spaces']
  const shared = isPlainObject(spaces) ? spaces['shared_telegram_chat'] : undefined
  idList(root['telegram'], 'group_chats').forEach((chat, i) => {
    if (typeof shared !== 'string' || shared === '' || chat.trim() !== shared) {
      err(
        `telegram.group_chats[${i}]`,
        'on a seat that authors people:, telegram.group_chats may hold only ' +
          'spaces.shared_telegram_chat',
        errors
      )
    }
  })
}

/** Rule 7: `readers` grants nothing on a space-mode seat. */
function checkRecordStoreReaders(root: Record<string, unknown>, errors: ValidationError[]): void {
  const stores = root['record_stores']
  if (!Array.isArray(stores)) return
  stores.forEach((store, i) => {
    if (isPlainObject(store) && store['readers'] !== undefined) {
      err(
        `record_stores[${i}].readers`,
        'readers is not allowed on a seat that authors people: record owners are spaces ' +
          'and each turn reads only its own spaces, so readers grants nothing (ADR 0091)',
        errors
      )
    }
  })
}

/** Rule 1: the blocks and memory switches a space-mode seat must author. */
function checkSpaceModePrerequisites(
  root: Record<string, unknown>,
  errors: ValidationError[]
): void {
  const spaces = root['spaces']
  if (spaces === undefined || spaces === null) {
    err('spaces', 'spaces is required on a seat that authors people: (ADR 0091)', errors)
  }
  const memory = isPlainObject(root['memory']) ? root['memory'] : {}
  for (const key of ['memory_enabled', 'user_profile_enabled'] as const) {
    if (memory[key] !== false) {
      err(
        `memory.${key}`,
        `memory.${key} must be false on a seat that authors people: native Hermes memory is ` +
          'one identity-blind store per profile (ADR 0091 enforcement point 1)',
        errors
      )
    }
  }
}

/** Rules 3 and 4: each person is reachable on the channels the seat lists. */
function checkPersonChannels(
  root: Record<string, unknown>,
  people: SeatPerson[],
  errors: ValidationError[]
): void {
  const inbound = new Set(idList(root['scope'], 'inbound_allow_from').map((a) => a.toLowerCase()))
  const dm = new Set(idList(root['telegram'], 'allow_from').map((a) => a.trim()))
  const group = new Set(idList(root['telegram'], 'group_allow_from').map((a) => a.trim()))
  const emails = new Set(people.map((p) => p.email))
  idList(root['scope'], 'inbound_allow_from').forEach((a, i) => {
    if (!emails.has(a.toLowerCase())) {
      err(
        `scope.inbound_allow_from[${i}]`,
        `${a} is not one of the people: on a seat that authors people:, ` +
          'scope.inbound_allow_from must be exactly their emails, because a roster-lane ' +
          'send may go to any rostered address',
        errors
      )
    }
  })
  people.forEach((p, i) => {
    if (!inbound.has(p.email)) {
      err(
        `people[${i}].email`,
        `${p.email} is not on scope.inbound_allow_from, so this person's mail would be refused`,
        errors
      )
    }
    if (p.telegram_id === '') return
    if (!dm.has(p.telegram_id)) {
      err(`people[${i}].telegram_id`, 'a bound telegram_id must be on telegram.allow_from', errors)
    }
    if (!group.has(p.telegram_id)) {
      err(
        `people[${i}].telegram_id`,
        'a bound telegram_id must be on telegram.group_allow_from',
        errors
      )
    }
  })
}

/**
 * The cross-field rules of ADR 0091. Runs on every seat: a seat without
 * `people:` only has to not author `spaces:` or a cron `space`.
 */
export function checkSpaceMode(
  root: Record<string, unknown>,
  people: SeatPerson[] | null,
  errors: ValidationError[]
): void {
  const authored = root['people'] !== undefined && root['people'] !== null
  if (!authored) {
    if (root['spaces'] !== undefined && root['spaces'] !== null) {
      err('spaces', 'spaces is only meaningful on a seat that authors people: (ADR 0091)', errors)
    }
    checkCronSpaces(root, null, errors)
    return
  }
  checkSpaceModePrerequisites(root, errors)
  // Ids come from the raw block so one malformed person does not turn every
  // cron space naming a well-formed person into a second, misleading error.
  const rawPeople = Array.isArray(root['people']) ? (root['people'] as unknown[]) : []
  const ids = new Set(
    rawPeople
      .map((p) => (isPlainObject(p) ? p['id'] : undefined))
      .filter((id): id is string => typeof id === 'string')
  )
  checkCronSpaces(root, ids, errors)
  checkDuplicateCronSpaces(root, errors)
  checkRecordStoreReaders(root, errors)
  if (people !== null) {
    checkPersonChannels(root, people, errors)
    checkGroupFence(root, people, errors)
  }
}
