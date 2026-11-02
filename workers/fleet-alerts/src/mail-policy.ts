/**
 * Who gets emailed (Captain, 2026-11-02: "mostly noise", and "email only when a
 * client is affected or you have to do something").
 *
 * In two weeks team@ received 488 ops emails, about 33 a day, most of them a
 * machine reporting on itself to whoever was debugging it. Two rules now keep
 * that out of the mail. The alert still happens: its row is written exactly as
 * before, and operator/bin/work-health.py lists open alerts, Sentry rows and
 * shortfalls for the triage agent. Only the email stops.
 *
 *  1. A seat whose customer.yaml says it is not a customer (proving, preprod,
 *     internal, sandbox) is never emailed about. Unknown kind emails: a seat goes
 *     quiet only when its own config says it may, and a failed read emails.
 *  2. A machine condition, on any seat, is for the triage agent. The list names
 *     what is quiet, so a NEW condition emails until someone classifies it: a
 *     noisy new alert is a smaller mistake than a silent one.
 *
 * What still emails on a customer seat: the seat is down or has stopped itself,
 * the Operator is not answering, a connection or sign-in is failing or about to
 * die, scheduled work is not firing, a send was refused, and audit tampering.
 */

import {
  conditionPayload,
  SPEC_CONTROL_BROKEN_PREFIX,
  WEBHOOK_SURFACE_MISSING_PREFIX,
} from './conditions'

const QUIET_CONDITIONS: ReadonlySet<string> = new Set([
  'gateway_restarted',
  'gateway_supervisor_inert',
  'gateway_loop_unprovable',
  'connector_check_error',
  'spec_control_unprovable',
  'webhook_surface_unprovable',
  'rollout_failed',
  'image_drift',
  'image_unknown',
  'config_drift',
  'skills_drift',
  'selftest_failing',
  'spec_unverified',
])

const QUIET_SOURCES: ReadonlySet<string> = new Set(['sentry'])

export function conditionIsQuiet(condition: string): boolean {
  return (
    QUIET_CONDITIONS.has(condition) ||
    conditionPayload(condition, SPEC_CONTROL_BROKEN_PREFIX) !== null ||
    conditionPayload(condition, WEBHOOK_SURFACE_MISSING_PREFIX) !== null
  )
}

export function sinkSourceIsQuiet(source: string): boolean {
  return QUIET_SOURCES.has(source)
}

/** `seat.kind` from a projected seat_json, or null when it cannot be read. */
export function seatKindOf(seatJson: string | null | undefined): string | null {
  if (!seatJson) return null
  try {
    const seat: unknown = JSON.parse(seatJson)
    if (typeof seat !== 'object' || seat === null) return null
    const kind = (seat as Record<string, unknown>).kind
    return typeof kind === 'string' && kind.length > 0 ? kind : null
  } catch {
    return null
  }
}

/** True for a seat authored as anything but a customer. A failed read is false. */
export async function seatIsQuiet(db: D1Database, slug: string): Promise<boolean> {
  try {
    const row = await db
      .prepare('SELECT seat_json FROM customer_configs WHERE customer_slug = ?')
      .bind(slug)
      .first<{ seat_json: string | null }>()
    const kind = seatKindOf(row?.seat_json)
    return kind !== null && kind !== 'customer'
  } catch {
    return false
  }
}

export async function transitionIsQuiet(
  db: D1Database,
  slug: string,
  condition: string
): Promise<boolean> {
  return conditionIsQuiet(condition) || (await seatIsQuiet(db, slug))
}
