/**
 * Deterministic cache recomputation for entity attributes.
 *
 * Runs synchronously after every context append (<5ms).
 * Scans context entries and extracts structured values from metadata.
 *
 * Attributes recomputed:
 *   vertical — latest non-null from signal/extraction metadata
 *   area     — latest non-null
 *
 * The pain_score / tier / employee_count attributes were retired with the
 * lead-gen machine (ADR 0060) and their columns dropped; this cache now only
 * maintains vertical/area, which the surviving Client Hub renders. Summary
 * remains LLM-derived (async/on-demand).
 */

import { isRecord } from '../api/helpers'

interface Accumulators {
  vertical: string | null
  area: string | null
}

/** Apply a single metadata record's fields to the running accumulators. */
function applyMetaToAccumulators(meta: Record<string, unknown>, acc: Accumulators): Accumulators {
  let { vertical, area } = acc

  if (typeof meta.vertical === 'string' && meta.vertical) vertical = meta.vertical
  if (typeof meta.vertical_match === 'string' && meta.vertical_match) vertical = meta.vertical_match
  if (typeof meta.area === 'string' && meta.area) area = meta.area

  return { vertical, area }
}

export async function recomputeDeterministicCache(
  db: D1Database,
  orgId: string,
  entityId: string
): Promise<void> {
  const entries = await db
    .prepare(
      `SELECT type, metadata FROM context
       WHERE entity_id = ? AND org_id = ?
       ORDER BY created_at ASC`
    )
    .bind(entityId, orgId)
    .all<{ type: string; metadata: string | null }>()

  let vertical: string | null = null
  let area: string | null = null

  for (const entry of entries.results) {
    if (!entry.metadata) continue

    let parsed: unknown
    try {
      parsed = JSON.parse(entry.metadata)
    } catch {
      continue
    }
    if (!isRecord(parsed)) continue
    const meta: Record<string, unknown> = parsed

    const updated = applyMetaToAccumulators(meta, { vertical, area })
    vertical = updated.vertical
    area = updated.area
  }

  await db
    .prepare(
      `UPDATE entities SET
        vertical = COALESCE(?, vertical),
        area = COALESCE(?, area),
        updated_at = datetime('now')
      WHERE id = ? AND org_id = ?`
    )
    .bind(vertical, area, entityId, orgId)
    .run()
}
