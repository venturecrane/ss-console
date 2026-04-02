/**
 * Deterministic cache recomputation for entity attributes.
 *
 * Runs synchronously after every context append (<5ms).
 * Scans context entries and extracts structured values from metadata.
 *
 * Attributes recomputed:
 *   pain_score     — max across all signal entries
 *   vertical       — latest non-null from signal/extraction metadata
 *   area           — latest non-null
 *   employee_count — latest non-null (extraction > enrichment > signal)
 *
 * LLM-derived attributes (tier, summary) are NOT recomputed here.
 * Those are updated async via batch triage or on-demand on page view.
 */

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

  let painScore: number | null = null
  let vertical: string | null = null
  let area: string | null = null
  let employeeCount: number | null = null

  for (const entry of entries.results) {
    if (!entry.metadata) continue

    let meta: Record<string, unknown>
    try {
      meta = JSON.parse(entry.metadata)
    } catch {
      continue
    }

    // Pain score: max across all signals
    if (typeof meta.pain_score === 'number' && meta.pain_score >= 1 && meta.pain_score <= 10) {
      painScore = Math.max(painScore ?? 0, meta.pain_score)
    }

    // Vertical: latest non-null from any context type
    if (typeof meta.vertical === 'string' && meta.vertical) {
      vertical = meta.vertical
    }
    if (typeof meta.vertical_match === 'string' && meta.vertical_match) {
      vertical = meta.vertical_match
    }

    // Area: latest non-null
    if (typeof meta.area === 'string' && meta.area) {
      area = meta.area
    }

    // Employee count: latest non-null (extraction values have higher confidence)
    if (typeof meta.employee_count === 'number' && meta.employee_count > 0) {
      employeeCount = meta.employee_count
    }
    if (typeof meta.company_size_estimate === 'string') {
      const parsed = parseSizeEstimate(meta.company_size_estimate)
      if (parsed) employeeCount = parsed
    }
  }

  await db
    .prepare(
      `UPDATE entities SET
        pain_score = ?,
        vertical = COALESCE(?, vertical),
        area = COALESCE(?, area),
        employee_count = COALESCE(?, employee_count),
        updated_at = datetime('now')
      WHERE id = ? AND org_id = ?`
    )
    .bind(painScore, vertical, area, employeeCount, entityId, orgId)
    .run()
}

/**
 * Parse a size estimate string like "10-25" or "~15" into a number.
 */
function parseSizeEstimate(estimate: string): number | null {
  // Range: "10-25" → midpoint
  const rangeMatch = estimate.match(/(\d+)\s*[-–]\s*(\d+)/)
  if (rangeMatch) {
    return Math.round((parseInt(rangeMatch[1]) + parseInt(rangeMatch[2])) / 2)
  }
  // Approximate: "~15" → 15
  const approxMatch = estimate.match(/~?\s*(\d+)/)
  if (approxMatch) {
    return parseInt(approxMatch[1])
  }
  return null
}
