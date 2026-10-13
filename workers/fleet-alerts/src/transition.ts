/**
 * Alert-state transitions: the open / resolve / remind edges for one condition,
 * and the fleet_alert_state rows behind them. Split out of index.ts when the
 * deadline reminder (./reminder) joined, to keep that file under its ceiling.
 */

import { mintAlertMessageId } from './alert-thread'
import type { ConditionState, Env, FleetCondition, Transition } from './index'
import { d1Timestamp, markReminded, reminderDue } from './reminder'
import { sendTransitionEmail } from './transition-email'

interface AlertStateRow {
  status: 'open' | 'resolved'
  alert_message_id: string | null
  updated_at: string | null
}

async function getAlertState(
  db: D1Database,
  slug: string,
  condition: FleetCondition
): Promise<AlertStateRow | null> {
  const row = await db
    .prepare(
      'SELECT status, alert_message_id, updated_at FROM fleet_alert_state WHERE customer_slug = ? AND condition = ?'
    )
    .bind(slug, condition)
    .first<AlertStateRow>()
  return row ?? null
}

async function markOpen(
  db: D1Database,
  s: ConditionState,
  resendId: string | null,
  messageId: string,
  nowMs: number
): Promise<void> {
  await db
    .prepare(
      `INSERT INTO fleet_alert_state (customer_slug, condition, status, opened_at, resolved_at, last_alert_id, alert_message_id, updated_at)
       VALUES (?, ?, 'open', datetime('now'), NULL, ?, ?, ?)
       ON CONFLICT (customer_slug, condition) DO UPDATE SET
         status = 'open', opened_at = datetime('now'), resolved_at = NULL,
         last_alert_id = excluded.last_alert_id,
         alert_message_id = excluded.alert_message_id, updated_at = excluded.updated_at`
    )
    .bind(s.customer_slug, s.condition, resendId, messageId, d1Timestamp(nowMs))
    .run()
}

async function markResolved(db: D1Database, s: ConditionState): Promise<void> {
  await db
    .prepare(
      `UPDATE fleet_alert_state SET status = 'resolved', resolved_at = datetime('now'), updated_at = datetime('now')
       WHERE customer_slug = ? AND condition = ?`
    )
    .bind(s.customer_slug, s.condition)
    .run()
}

/**
 * Fire the edge transition for one condition, if any. Marks alert state ONLY on
 * a successful send: a failed Resend POST leaves the row unmarked so the next
 * 2-minute cron retries — otherwise one transient failure silences the alert
 * forever (it would exist only in Worker logs). Returns the recorded transition
 * or null (silent / send-failed).
 */
export async function processTransition(
  env: Env,
  s: ConditionState,
  nowMs: number
): Promise<Transition | null> {
  const prior = await getAlertState(env.DB, s.customer_slug, s.condition)
  if (s.active && prior?.status !== 'open') {
    const messageId = mintAlertMessageId()
    const sent = await sendTransitionEmail(env, s, 'opened', messageId)
    if (!sent.ok) return null
    await markOpen(env.DB, s, sent.resendId ?? null, messageId, nowMs)
    return {
      customer_slug: s.customer_slug,
      condition: s.condition,
      kind: 'opened',
      detail: s.detail,
      emailed: true,
      resendId: sent.resendId,
    }
  }
  if (!s.active && prior?.status === 'open') {
    const sent = await sendTransitionEmail(env, s, 'resolved', prior.alert_message_id)
    if (!sent.ok) return null
    await markResolved(env.DB, s)
    return {
      customer_slug: s.customer_slug,
      condition: s.condition,
      kind: 'resolved',
      detail: s.detail,
      emailed: true,
      resendId: sent.resendId,
    }
  }
  if (s.active && prior?.status === 'open' && reminderDue(s.condition, prior.updated_at, nowMs)) {
    const sent = await sendTransitionEmail(env, s, 'reminder', prior.alert_message_id)
    if (!sent.ok) return null
    await markReminded(env.DB, s, nowMs)
    return {
      customer_slug: s.customer_slug,
      condition: s.condition,
      kind: 'reminder',
      detail: s.detail,
      emailed: true,
      resendId: sent.resendId,
    }
  }
  return null
}
