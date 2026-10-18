/**
 * The couples joint section: versioned settings plus per-person confirmations
 * (ADR 0090 decision 4: "anything the role's schema marks joint is not final
 * until every person on the seat confirms it"; migration 0127).
 *
 * Every save writes a NEW version and records the saver's confirmation of it,
 * because proposing a value is agreeing to it. The other partner's earlier
 * confirmation stays on the earlier version, so an edit by one partner always
 * re-opens the section for the other. Confirming names the version the partner
 * saw; a confirmation of a version that is no longer current is refused, so
 * nobody can confirm settings they were not shown.
 */

import type { D1Database } from '@cloudflare/workers-types'
import { parseJointSettings, type JointSettings } from '../hosted-agent/couples/joint-settings'

export interface JointState {
  version: number
  settings: JointSettings
  updatedBy: string
  updatedAt: string
  /** Person ids that confirmed the CURRENT version. */
  confirmedBy: string[]
  /** Confirmation time per person id, current version only. */
  confirmedAt: Record<string, string>
  /** True only when every partner on the instance confirmed the current version. */
  final: boolean
}

interface SettingsRow {
  version: number
  settings_json: string
  updated_by: string
  updated_at: string
}

export async function getJointState(db: D1Database, intakeId: string): Promise<JointState | null> {
  const row = await db
    .prepare(
      `SELECT version, settings_json, updated_by, updated_at
         FROM hosted_agent_joint_settings WHERE intake_id = ?`
    )
    .bind(intakeId)
    .first<SettingsRow>()
  if (!row) return null
  let parsed: unknown
  try {
    parsed = JSON.parse(row.settings_json)
  } catch {
    return null
  }
  const settings = parseJointSettings(parsed)
  if (!settings) return null

  const confirmations = await db
    .prepare(
      `SELECT person_id, confirmed_at FROM hosted_agent_joint_confirmations
        WHERE intake_id = ? AND version = ?`
    )
    .bind(intakeId, row.version)
    .all<{ person_id: string; confirmed_at: string }>()
  const confirmedAt: Record<string, string> = {}
  for (const c of confirmations.results ?? []) confirmedAt[c.person_id] = c.confirmed_at

  const people = await db
    .prepare(`SELECT id FROM hosted_agent_people WHERE intake_id = ?`)
    .bind(intakeId)
    .all<{ id: string }>()
  const ids = (people.results ?? []).map((p) => p.id)
  const final = ids.length === 2 && ids.every((id) => id in confirmedAt)

  return {
    version: row.version,
    settings,
    updatedBy: row.updated_by,
    updatedAt: row.updated_at,
    confirmedBy: Object.keys(confirmedAt),
    confirmedAt,
    final,
  }
}

/**
 * Save a new version proposed by one partner, and record that partner's
 * confirmation of it. Returns the new version number.
 */
export async function saveJointSettings(
  db: D1Database,
  intakeId: string,
  personId: string,
  settings: JointSettings
): Promise<number> {
  const current = await db
    .prepare(`SELECT version FROM hosted_agent_joint_settings WHERE intake_id = ?`)
    .bind(intakeId)
    .first<{ version: number }>()
  const version = (current?.version ?? 0) + 1
  const json = JSON.stringify(settings)
  await db.batch([
    db
      .prepare(
        `INSERT INTO hosted_agent_joint_settings (intake_id, version, settings_json, updated_by, updated_at)
         VALUES (?, ?, ?, ?, datetime('now'))
         ON CONFLICT (intake_id) DO UPDATE SET version = excluded.version,
           settings_json = excluded.settings_json, updated_by = excluded.updated_by,
           updated_at = excluded.updated_at
         WHERE hosted_agent_joint_settings.version = ?`
      )
      .bind(intakeId, version, json, personId, version - 1),
    db
      .prepare(
        `INSERT OR IGNORE INTO hosted_agent_joint_confirmations (id, intake_id, person_id, version)
         SELECT ?, ?, ?, ? WHERE EXISTS (
           SELECT 1 FROM hosted_agent_joint_settings WHERE intake_id = ? AND version = ? AND updated_by = ?)`
      )
      .bind(crypto.randomUUID(), intakeId, personId, version, intakeId, version, personId),
  ])
  return version
}

export type ConfirmResult = 'confirmed' | 'stale'

/** Record one partner's confirmation of the version they were shown. */
export async function confirmJointSettings(
  db: D1Database,
  intakeId: string,
  personId: string,
  version: number
): Promise<ConfirmResult> {
  const current = await db
    .prepare(`SELECT version FROM hosted_agent_joint_settings WHERE intake_id = ?`)
    .bind(intakeId)
    .first<{ version: number }>()
  if (!current || current.version !== version) return 'stale'
  await db
    .prepare(
      `INSERT OR IGNORE INTO hosted_agent_joint_confirmations (id, intake_id, person_id, version)
       VALUES (?, ?, ?, ?)`
    )
    .bind(crypto.randomUUID(), intakeId, personId, version)
    .run()
  return 'confirmed'
}
