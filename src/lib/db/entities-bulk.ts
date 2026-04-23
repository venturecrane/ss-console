/**
 * Bulk entity actions.
 *
 * Reuses the per-entity code paths (e.g. `transitionStage`) so that every
 * selected entity gets its own structured context entry and cache recompute —
 * never a single rollup row for N entities.
 *
 * Returns a partial-success shape: some entities may fail (e.g. already in
 * the target stage, another admin dismissed them first) without aborting the
 * whole batch.
 */

import { transitionStage, type Entity } from './entities.js'
import { appendContext } from './context.js'
import { isLostReason, labelForLostReason, type LostReason } from './lost-reasons.js'

export interface BulkActionOk {
  id: string
}

export interface BulkActionFailure {
  id: string
  reason: string
}

export interface BulkActionResult {
  ok: BulkActionOk[]
  failed: BulkActionFailure[]
}

export interface BulkDismissOptions {
  reason: LostReason
  detail?: string | null
}

/**
 * Dismiss multiple entities (stage → lost) with a structured reason.
 *
 * Each entity:
 *  - goes through `transitionStage` → writes its own `stage_change` row
 *    (valid transition is enforced per-entity)
 *  - gets supplemental structured metadata (reason enum + optional detail)
 *    recorded as a separate `note` context entry, preserving queryability
 *    before #477's Lost tab filter ships
 *
 * Errors on a single entity are captured as `failed` entries and the batch
 * continues.
 */
export async function bulkDismissEntities(
  db: D1Database,
  orgId: string,
  ids: string[],
  options: BulkDismissOptions
): Promise<BulkActionResult> {
  if (!isLostReason(options.reason)) {
    throw new Error(`Invalid lost reason: ${options.reason}`)
  }

  const ok: BulkActionOk[] = []
  const failed: BulkActionFailure[] = []
  const reasonLabel = labelForLostReason(options.reason)
  const reasonText = options.detail?.trim()
    ? `${reasonLabel}: ${options.detail.trim()}`
    : reasonLabel

  for (const id of ids) {
    try {
      const updated = await transitionStage(db, orgId, id, 'lost', reasonText)
      if (!updated) {
        failed.push({ id, reason: 'not_found' })
        continue
      }

      // Store structured reason metadata as a separate context entry so
      // #477's filter can query by reason code without parsing free text.
      await appendContext(db, orgId, {
        entity_id: id,
        type: 'note',
        content: `Lost reason: ${reasonLabel}`,
        source: 'system',
        metadata: {
          lost_reason: options.reason,
          lost_reason_detail: options.detail?.trim() || null,
          bulk: true,
        },
      })

      ok.push({ id })
    } catch (err) {
      const message = err instanceof Error ? err.message : 'unknown_error'
      failed.push({ id, reason: message })
    }
  }

  return { ok, failed }
}

/**
 * Fetch name + email contact rows for a batch of entities, for CSV export
 * and mailto BCC construction.
 *
 * Returns one row per entity with the first available email contact (name
 * may be null if the entity has no contact records). Rows with no contact
 * row at all still return with nulls so the caller can surface
 * "missing email" in the export.
 */
export interface EntityExportRow {
  id: string
  name: string
  email: string | null
  contact_name: string | null
  website: string | null
  phone: string | null
  stage: string
}

export async function listEntitiesForExport(
  db: D1Database,
  orgId: string,
  ids: string[]
): Promise<EntityExportRow[]> {
  if (ids.length === 0) return []

  const placeholders = ids.map(() => '?').join(', ')
  const result = await db
    .prepare(
      `SELECT
        e.id AS id,
        e.name AS name,
        e.website AS website,
        e.phone AS phone,
        e.stage AS stage,
        (SELECT c.email FROM contacts c
           WHERE c.entity_id = e.id AND c.org_id = e.org_id AND c.email IS NOT NULL
           ORDER BY c.created_at ASC LIMIT 1) AS email,
        (SELECT c.name FROM contacts c
           WHERE c.entity_id = e.id AND c.org_id = e.org_id AND c.email IS NOT NULL
           ORDER BY c.created_at ASC LIMIT 1) AS contact_name
      FROM entities e
      WHERE e.org_id = ? AND e.id IN (${placeholders})`
    )
    .bind(orgId, ...ids)
    .all<EntityExportRow>()

  return result.results
}

export type { Entity }
