/**
 * Optional `telegram:` block validator (ADR 0033).
 *
 * Telegram is wired as a Hermes native polling channel: the bot token is a Fly
 * secret (TELEGRAM_BOT_TOKEN) that auto-enables the platform; this block authors
 * the ALLOWLIST (who may DM the bot) as reviewable config. The overlay
 * translate.py `_materialize_telegram_platform` renders it into the profile
 * config.yaml `telegram.allow_from`, which Hermes maps to TELEGRAM_ALLOWED_USERS.
 *
 * Fail-closed: when the block is enabled, the allowlist MUST be a non-empty list
 * of numeric Telegram user-id strings — the pinned Hermes ref allows ALL users
 * when the allowlist is empty (`telegram.py: if not allowed_csv: return True`),
 * so an empty allowlist is the fail-open trap this pre-merge check exists to
 * catch.
 *
 * `allowed_users` (DEPRECATED alias, phase 1 of 3). Seats were authored with
 * `telegram.allowed_users` while every reader only knew `allow_from`, so the
 * authored allowlist was silently ignored and the Fly secret was the only gate.
 * Phase 1 (this file + overlay translate.py) accepts BOTH: `allowed_users` is
 * unioned into `allow_from`, and on the seat an `allowed_users` block with no
 * explicit `enabled` counts as enabled only when TELEGRAM_BOT_TOKEN is present.
 * Phase 2 migrates authored configs to `enabled: true` + `allow_from`; phase 3
 * rejects `allowed_users`. Any OTHER unknown key is an error now, so the next
 * misspelled key cannot be silently ignored the same way.
 *
 * Group keys (ADR 0091, multi-person seats). `group_allow_from` (Telegram user
 * ids allowed to speak in a group), `group_chats` (allowed group chat ids, each
 * a negative numeric string) and `group_sessions_per_user` (false makes the
 * group one shared conversation) are passthroughs to the Hermes Telegram
 * adapter. The cross-field rules that tie them to `people:` live in
 * sections-people.ts.
 *
 * Validate-only (pushes errors); the value is materialized by the overlay, not
 * consumed from the parsed CustomerYaml object.
 */

import type { ValidationError } from './types'
import { isPlainObject } from './helpers'

const KNOWN_KEYS = new Set([
  'enabled',
  'allow_from',
  'allowed_users',
  'require_mention',
  'reactions',
  'group_allow_from',
  'group_chats',
  'group_sessions_per_user',
])

/** Validate one authored id list; returns the trimmed ids it holds. */
function checkIdList(
  raw: Record<string, unknown>,
  key: 'allow_from' | 'allowed_users' | 'group_allow_from',
  errors: ValidationError[]
): string[] {
  const list = raw[key]
  if (list === undefined || list === null) return []
  if (!Array.isArray(list)) {
    errors.push({
      code: 'TypeMismatch',
      path: `telegram.${key}`,
      message: `telegram.${key} must be a list`,
    })
    return []
  }
  const ids: string[] = []
  list.forEach((id, i) => {
    if (typeof id !== 'string' || !/^\d+$/.test(id.trim())) {
      errors.push({
        code: 'TypeMismatch',
        path: `telegram.${key}[${i}]`,
        message: `each ${key} entry must be a numeric Telegram user id as a string (e.g. "7367659986")`,
      })
    } else {
      ids.push(id.trim())
    }
  })
  return ids
}

/** Unknown keys are errors; the three flags must be booleans. */
function checkKeysAndFlags(raw: Record<string, unknown>, errors: ValidationError[]): void {
  for (const key of Object.keys(raw)) {
    if (!KNOWN_KEYS.has(key)) {
      errors.push({
        code: 'UnknownTelegramField',
        path: `telegram.${key}`,
        message:
          `telegram.${key}: unknown key (the overlay never reads it). ` +
          'Known keys: enabled, allow_from, require_mention, reactions, ' +
          'group_allow_from, group_chats, group_sessions_per_user.',
      })
    }
  }
  for (const key of [
    'enabled',
    'require_mention',
    'reactions',
    'group_sessions_per_user',
  ] as const) {
    if (raw[key] !== undefined && typeof raw[key] !== 'boolean') {
      errors.push({
        code: 'TypeMismatch',
        path: `telegram.${key}`,
        message: `telegram.${key} must be a boolean`,
      })
    }
  }
}

/** `group_chats`: a list of group chat ids, each a negative numeric string. */
function checkGroupChats(list: unknown, errors: ValidationError[]): void {
  if (list === undefined || list === null) return
  if (!Array.isArray(list)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'telegram.group_chats',
      message: 'telegram.group_chats must be a list',
    })
    return
  }
  list.forEach((id, i) => {
    if (typeof id !== 'string' || !/^-\d+$/.test(id.trim())) {
      errors.push({
        code: 'TypeMismatch',
        path: `telegram.group_chats[${i}]`,
        message:
          'each group_chats entry must be a Telegram group chat id as a negative numeric ' +
          'string (e.g. "-1001234567890")',
      })
    }
  })
}

export function checkTelegram(root: Record<string, unknown>, errors: ValidationError[]): void {
  const raw = root['telegram']
  if (raw === undefined || raw === null) return // optional block
  if (!isPlainObject(raw)) {
    errors.push({ code: 'TypeMismatch', path: 'telegram', message: 'telegram must be an object' })
    return
  }

  checkKeysAndFlags(raw, errors)
  const enabled = raw['enabled']

  const allowFrom = checkIdList(raw, 'allow_from', errors)
  const legacy = checkIdList(raw, 'allowed_users', errors)
  checkIdList(raw, 'group_allow_from', errors)
  checkGroupChats(raw['group_chats'], errors)
  const effective = new Set([...allowFrom, ...legacy])

  if (enabled === true && effective.size === 0) {
    errors.push({
      code: 'MissingField',
      path: 'telegram.allow_from',
      message:
        'telegram.allow_from is required and must be a non-empty list when telegram.enabled is true. ' +
        'An empty allowlist fails OPEN (the pinned Hermes ref answers any user). See ADR 0033.',
    })
  }
  // An authored `allowed_users` with no `enabled` turns the block on at boot
  // whenever the bot token is present, so it carries the same non-empty rule.
  if (
    enabled === undefined &&
    Array.isArray(raw['allowed_users']) &&
    (raw['allowed_users'] as unknown[]).length === 0
  ) {
    errors.push({
      code: 'MissingField',
      path: 'telegram.allowed_users',
      message:
        'telegram.allowed_users is empty. An empty allowlist fails OPEN; author ' +
        '`enabled: true` and a non-empty `allow_from` instead. See ADR 0033.',
    })
  }
}

/**
 * Non-fatal notes for authored `telegram:` keys that still work but are on the
 * way out. The validator has no warning channel, so the CLI wrapper prints these.
 */
export function telegramDeprecationNotes(root: unknown): string[] {
  if (!isPlainObject(root)) return []
  const raw = root['telegram']
  if (!isPlainObject(raw) || raw['allowed_users'] === undefined) return []
  return [
    'telegram.allowed_users is deprecated: it is read as an alias of telegram.allow_from ' +
      'and enables the block only when TELEGRAM_BOT_TOKEN is set on the seat. ' +
      'Author `enabled: true` and `allow_from` instead; a later release rejects allowed_users.',
  ]
}
