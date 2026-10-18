/**
 * hosted_agent_intake helpers (ADR 0067, migration 0087).
 *
 * One row per Hosted Agent purchase. The row is simultaneously the
 * customer's onboarding questionnaire store and the Captain's concierge
 * work item: the checkout webhook creates it, the portal intake form fills
 * it, the admin queue reads it, and the activate action closes it.
 *
 * Status lifecycle (mirrors the concierge runbook):
 *   awaiting_intake → intake_submitted → provisioning → live
 * with `cancelled` as the terminal branch when the subscription dies before
 * activation. NO secret-bearing column exists by construction.
 */

import type { D1Database } from '@cloudflare/workers-types'

export interface HostedAgentIntakeRow {
  id: string
  org_id: string
  entity_id: string
  subscription_id: string
  status: 'awaiting_intake' | 'intake_submitted' | 'provisioning' | 'live' | 'cancelled'
  agent_name: string | null
  use_cases: string | null
  telegram_handle: string | null
  timezone: string | null
  allowed_senders_json: string | null
  spend_limit_confirmed: number
  anthropic_key_status: 'pending' | 'received'
  customer_slug: string | null
  /** Captain-authored go-live channel details; rendered verbatim on the live view. */
  channel_details: string | null
  submitted_at: string | null
  created_at: string
  updated_at: string
}

const COLUMNS =
  'id, org_id, entity_id, subscription_id, status, agent_name, use_cases, telegram_handle, ' +
  'timezone, allowed_senders_json, spend_limit_confirmed, anthropic_key_status, customer_slug, ' +
  'channel_details, submitted_at, created_at, updated_at'

export async function createHostedAgentIntake(
  db: D1Database,
  row: { orgId: string; entityId: string; subscriptionId: string }
): Promise<string> {
  const id = crypto.randomUUID()
  await db
    .prepare(
      `INSERT INTO hosted_agent_intake (id, org_id, entity_id, subscription_id) VALUES (?, ?, ?, ?)`
    )
    .bind(id, row.orgId, row.entityId, row.subscriptionId)
    .run()
  return id
}

export async function getIntakeByEntity(
  db: D1Database,
  entityId: string
): Promise<HostedAgentIntakeRow | null> {
  const row = await db
    .prepare(
      `SELECT ${COLUMNS} FROM hosted_agent_intake WHERE entity_id = ? ORDER BY created_at DESC LIMIT 1`
    )
    .bind(entityId)
    .first<HostedAgentIntakeRow>()
  return row ?? null
}

export async function getIntakeById(
  db: D1Database,
  id: string
): Promise<HostedAgentIntakeRow | null> {
  const row = await db
    .prepare(`SELECT ${COLUMNS} FROM hosted_agent_intake WHERE id = ?`)
    .bind(id)
    .first<HostedAgentIntakeRow>()
  return row ?? null
}

export interface HostedAgentIntakeSubmission {
  agentName: string
  useCases: string
  telegramHandle: string | null
  timezone: string | null
  /** Validated sender addresses, stored as a JSON array. */
  allowedSenders: string[]
  spendLimitConfirmed: boolean
}

/** Record the customer's questionnaire answers and advance the work item. */
export async function submitHostedAgentIntake(
  db: D1Database,
  id: string,
  data: HostedAgentIntakeSubmission
): Promise<void> {
  await db
    .prepare(
      `UPDATE hosted_agent_intake
          SET agent_name = ?, use_cases = ?, telegram_handle = ?, timezone = ?,
              allowed_senders_json = ?, spend_limit_confirmed = ?,
              status = 'intake_submitted', submitted_at = datetime('now'),
              updated_at = datetime('now')
        WHERE id = ? AND status IN ('awaiting_intake', 'intake_submitted')`
    )
    .bind(
      data.agentName,
      data.useCases,
      data.telegramHandle,
      data.timezone,
      JSON.stringify(data.allowedSenders),
      data.spendLimitConfirmed ? 1 : 0,
      id
    )
    .run()
}

/** Captain assigns the customer slug (admin queue) before key staging. */
export async function setIntakeCustomerSlug(
  db: D1Database,
  id: string,
  customerSlug: string
): Promise<void> {
  await db
    .prepare(
      `UPDATE hosted_agent_intake SET customer_slug = ?, updated_at = datetime('now') WHERE id = ?`
    )
    .bind(customerSlug, id)
    .run()
}

export async function setIntakeStatus(
  db: D1Database,
  id: string,
  status: HostedAgentIntakeRow['status']
): Promise<void> {
  await db
    .prepare(`UPDATE hosted_agent_intake SET status = ?, updated_at = datetime('now') WHERE id = ?`)
    .bind(status, id)
    .run()
}

export async function setIntakeKeyStatus(
  db: D1Database,
  id: string,
  keyStatus: HostedAgentIntakeRow['anthropic_key_status']
): Promise<void> {
  await db
    .prepare(
      `UPDATE hosted_agent_intake SET anthropic_key_status = ?, updated_at = datetime('now') WHERE id = ?`
    )
    .bind(keyStatus, id)
    .run()
}

export interface HostedAgentQueueRow extends HostedAgentIntakeRow {
  entity_name: string
  subscription_status: string
  settings_json: string | null
}

/** Admin concierge queue: open work items first, newest first within status. */
export async function listHostedAgentQueue(db: D1Database): Promise<HostedAgentQueueRow[]> {
  const result = await db
    .prepare(
      `SELECT i.id, i.org_id, i.entity_id, i.subscription_id, i.status, i.agent_name,
              i.use_cases, i.telegram_handle, i.timezone, i.allowed_senders_json,
              i.spend_limit_confirmed, i.anthropic_key_status, i.customer_slug,
              i.submitted_at, i.created_at, i.updated_at,
              e.name AS entity_name, s.status AS subscription_status, s.settings_json
         FROM hosted_agent_intake i
         JOIN entities e ON e.id = i.entity_id
         JOIN subscriptions s ON s.id = i.subscription_id
        ORDER BY CASE i.status
                   WHEN 'intake_submitted' THEN 0
                   WHEN 'awaiting_intake' THEN 1
                   WHEN 'provisioning' THEN 2
                   WHEN 'live' THEN 3
                   ELSE 4
                 END,
                 i.created_at DESC`
    )
    .all<HostedAgentQueueRow>()
  return result.results ?? []
}
