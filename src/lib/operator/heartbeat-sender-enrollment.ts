/**
 * The `sender_enrollment` heartbeat field (migration 0151): a guide seat's
 * invite-code enrollment (ADR 0106) as the overlay reads it from the seat's
 * audit ledger (overlay shared/heartbeat.py read_sender_enrollment). Its own
 * module because heartbeat-parsers.ts sits at the file-size ceiling.
 *
 * Three facts and no more: how many people are enrolled, how many attempts in
 * the trailing day failed (the broker's FAILURES set), and whether the seat is
 * locked (the broker's seat-wide limit of failures in the trailing hour). The
 * pager (workers/fleet-alerts/src/enrollment-guessing.ts) acts on them, so an
 * object carrying any other key, or a count that is not a non-negative
 * integer, is refused whole.
 */

import { isRecord } from '../api/helpers'

const KEYS = new Set(['enrolled', 'failed_24h', 'locked'])

/** The bound a seat could plausibly report; a larger number is not a count. */
const MAX_COUNT = 1_000_000

function count(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= MAX_COUNT
    ? value
    : null
}

/**
 * `{"enrolled": 3, "failed_24h": 0, "locked": false}` as canonical JSON text
 * (keys in that order), or NULL. Validated whole: the pager reads it, so a
 * partial object stores NULL (hold) rather than a number nobody measured.
 */
export function parseSenderEnrollment(value: unknown): string | null {
  if (!isRecord(value)) return null
  const keys = Object.keys(value)
  if (keys.length !== KEYS.size || keys.some((k) => !KEYS.has(k))) return null
  const enrolled = count(value.enrolled)
  const failed = count(value.failed_24h)
  if (enrolled === null || failed === null || typeof value.locked !== 'boolean') return null
  return JSON.stringify({ enrolled, failed_24h: failed, locked: value.locked })
}
