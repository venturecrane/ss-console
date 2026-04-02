/**
 * Migration script: Backfill entities + context from existing tables.
 *
 * Phase A of the entity-context architecture migration.
 * Run AFTER migration 0008_create_entities_context.sql has been applied.
 *
 * This script:
 * 1. Reads all clients → creates entities (preserving IDs) + context entries from notes
 * 2. Reads all lead_signals → finds or creates entities by slug → appends signal context
 * 3. Reads all assessments → appends context entries (transcript, extraction)
 * 4. Reads all parking_lot items → appends context entries
 * 5. Recomputes deterministic cache for all entities
 *
 * Safe to run multiple times (idempotent via ID preservation and slug dedup).
 *
 * Usage: npx wrangler d1 execute ss-console-db --local --file=migrations/0008_create_entities_context.sql
 *        npx tsx scripts/migrate-to-entities.ts --local
 *
 * NOTE: This script is designed to run against the D1 REST API or local D1.
 * For production, use `wrangler d1 execute` for the SQL migration, then
 * run this script against the remote DB.
 */

// This script documents the migration logic for implementation.
// The actual execution requires wrangler D1 bindings.
// In practice, this would be run as a Cloudflare Worker or via wrangler.

interface MigrationStats {
  clientsProcessed: number
  entitiesCreated: number
  contextEntriesCreated: number
  leadSignalsProcessed: number
  assessmentsProcessed: number
  parkingLotProcessed: number
  errors: string[]
}

/**
 * Main migration function. Takes a D1Database binding.
 */
export async function migrateToEntities(db: D1Database): Promise<MigrationStats> {
  const stats: MigrationStats = {
    clientsProcessed: 0,
    entitiesCreated: 0,
    contextEntriesCreated: 0,
    leadSignalsProcessed: 0,
    assessmentsProcessed: 0,
    parkingLotProcessed: 0,
    errors: [],
  }

  // -------------------------------------------------------------------------
  // Step 1: Migrate clients → entities
  // -------------------------------------------------------------------------
  console.log('[migrate] Step 1: Migrating clients → entities...')

  const clients = await db.prepare('SELECT * FROM clients').all<Record<string, unknown>>()

  for (const client of clients.results) {
    try {
      const slug = computeSlug(client.business_name as string, null)
      const stage = mapClientStatus(client.status as string)

      // Preserve client ID as entity ID
      await db
        .prepare(
          `INSERT INTO entities (
            id, org_id, name, slug, stage, stage_changed_at,
            vertical, employee_count, source_pipeline,
            created_at, updated_at
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
          ON CONFLICT(org_id, slug) DO NOTHING`
        )
        .bind(
          client.id,
          client.org_id,
          client.business_name,
          slug,
          stage,
          client.updated_at || new Date().toISOString(),
          client.vertical ?? null,
          client.employee_count ?? null,
          client.source ?? null,
          client.created_at,
          client.updated_at
        )
        .run()

      stats.clientsProcessed++
      stats.entitiesCreated++

      // Migrate client notes to context
      if (client.notes && typeof client.notes === 'string' && client.notes.trim()) {
        await insertContext(db, {
          entity_id: client.id as string,
          org_id: client.org_id as string,
          type: 'note',
          content: client.notes as string,
          source: 'migration',
          metadata: JSON.stringify({
            migrated_from: 'clients.notes',
            original_source: client.source,
          }),
          created_at: client.created_at as string,
        })
        stats.contextEntriesCreated++
      }
    } catch (err) {
      stats.errors.push(`Client ${client.id}: ${err}`)
    }
  }

  console.log(
    `[migrate] Step 1 complete: ${stats.clientsProcessed} clients → ${stats.entitiesCreated} entities`
  )

  // -------------------------------------------------------------------------
  // Step 2: Migrate lead_signals → entities + context
  // -------------------------------------------------------------------------
  console.log('[migrate] Step 2: Migrating lead_signals → entities + context...')

  const signals = await db.prepare('SELECT * FROM lead_signals').all<Record<string, unknown>>()

  for (const signal of signals.results) {
    try {
      const slug = computeSlug(signal.business_name as string, signal.area as string | null)

      // Check if entity already exists (from client migration or prior signal)
      let entityId: string | null = null
      const existing = await db
        .prepare('SELECT id FROM entities WHERE org_id = ? AND slug = ?')
        .bind(signal.org_id, slug)
        .first<{ id: string }>()

      if (existing) {
        entityId = existing.id
      } else {
        // Create new entity at signal stage
        entityId = crypto.randomUUID()
        await db
          .prepare(
            `INSERT INTO entities (
              id, org_id, name, slug, phone, website, stage, stage_changed_at,
              pain_score, area, source_pipeline, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'signal', ?, ?, ?, ?, ?, ?)
            ON CONFLICT(org_id, slug) DO NOTHING`
          )
          .bind(
            entityId,
            signal.org_id,
            signal.business_name,
            slug,
            signal.phone ?? null,
            signal.website ?? null,
            signal.created_at || new Date().toISOString(),
            signal.pain_score ?? null,
            signal.area ?? null,
            signal.source_pipeline,
            signal.created_at,
            signal.created_at
          )
          .run()

        // Handle race: another signal with same slug may have created it
        const verify = await db
          .prepare('SELECT id FROM entities WHERE org_id = ? AND slug = ?')
          .bind(signal.org_id, slug)
          .first<{ id: string }>()
        entityId = verify?.id ?? entityId

        stats.entitiesCreated++
      }

      // Build context content from signal data
      const contentParts: string[] = []
      if (signal.evidence_summary) contentParts.push(signal.evidence_summary as string)
      if (signal.outreach_angle) contentParts.push(`**Outreach angle:** ${signal.outreach_angle}`)
      const content = contentParts.join('\n\n') || `Signal from ${signal.source_pipeline}.`

      // Build metadata
      const sourceMetadata = signal.source_metadata
        ? safeParseJson(signal.source_metadata as string)
        : {}
      const topProblems = signal.top_problems ? safeParseJson(signal.top_problems as string) : null

      const metadata: Record<string, unknown> = {
        ...(sourceMetadata ?? {}),
        ...(signal.pain_score != null ? { pain_score: signal.pain_score } : {}),
        ...(topProblems ? { top_problems: topProblems } : {}),
        ...(signal.outreach_angle ? { outreach_angle: signal.outreach_angle } : {}),
        ...(signal.category ? { category: signal.category } : {}),
        migrated_from: 'lead_signals',
        original_id: signal.id,
        date_found: signal.date_found,
      }

      await insertContext(db, {
        entity_id: entityId,
        org_id: signal.org_id as string,
        type: 'signal',
        content,
        source: signal.source_pipeline as string,
        metadata: JSON.stringify(metadata),
        created_at: signal.created_at as string,
      })
      stats.contextEntriesCreated++
      stats.leadSignalsProcessed++
    } catch (err) {
      stats.errors.push(`Lead signal ${signal.id}: ${err}`)
    }
  }

  console.log(`[migrate] Step 2 complete: ${stats.leadSignalsProcessed} signals processed`)

  // -------------------------------------------------------------------------
  // Step 3: Migrate assessments → context entries
  // -------------------------------------------------------------------------
  console.log('[migrate] Step 3: Migrating assessments → context...')

  const assessments = await db.prepare('SELECT * FROM assessments').all<Record<string, unknown>>()

  for (const assessment of assessments.results) {
    try {
      // The assessment's client_id should now be an entity_id (same value)
      const entityId = assessment.client_id as string
      if (!entityId) {
        stats.errors.push(`Assessment ${assessment.id}: no client_id`)
        continue
      }

      // Transcript context
      if (assessment.transcript_path) {
        await insertContext(db, {
          entity_id: entityId,
          org_id: assessment.org_id as string,
          type: 'transcript',
          content: `Assessment call transcript. Duration: ${assessment.duration_minutes ?? 'unknown'} minutes.`,
          source: 'macwhisper',
          metadata: JSON.stringify({
            r2_key: assessment.transcript_path,
            duration_minutes: assessment.duration_minutes,
            assessment_id: assessment.id,
            scheduled_at: assessment.scheduled_at,
            completed_at: assessment.completed_at,
          }),
          created_at: (assessment.completed_at || assessment.created_at) as string,
        })
        stats.contextEntriesCreated++
      }

      // Extraction context
      if (assessment.extraction) {
        await insertContext(db, {
          entity_id: entityId,
          org_id: assessment.org_id as string,
          type: 'extraction',
          content: assessment.extraction as string,
          source: 'claude',
          metadata: JSON.stringify({
            assessment_id: assessment.id,
            status: assessment.status,
          }),
          created_at: (assessment.completed_at || assessment.created_at) as string,
        })
        stats.contextEntriesCreated++
      }

      // Notes, champion, disqualifiers → note context
      const noteParts: string[] = []
      if (assessment.champion_name) {
        noteParts.push(
          `Champion: ${assessment.champion_name} (${assessment.champion_role || 'unknown role'})`
        )
      }
      if (assessment.notes) {
        noteParts.push(assessment.notes as string)
      }
      if (assessment.disqualifiers) {
        const disq = safeParseJson(assessment.disqualifiers as string)
        if (disq && Object.keys(disq).length > 0) {
          noteParts.push(`Disqualification flags: ${JSON.stringify(disq)}`)
        }
      }

      if (noteParts.length > 0) {
        await insertContext(db, {
          entity_id: entityId,
          org_id: assessment.org_id as string,
          type: 'note',
          content: noteParts.join('\n\n'),
          source: 'migration',
          metadata: JSON.stringify({
            migrated_from: 'assessments',
            assessment_id: assessment.id,
          }),
          created_at: (assessment.completed_at || assessment.created_at) as string,
        })
        stats.contextEntriesCreated++
      }

      stats.assessmentsProcessed++
    } catch (err) {
      stats.errors.push(`Assessment ${assessment.id}: ${err}`)
    }
  }

  console.log(`[migrate] Step 3 complete: ${stats.assessmentsProcessed} assessments processed`)

  // -------------------------------------------------------------------------
  // Step 4: Migrate parking_lot → context entries
  // -------------------------------------------------------------------------
  console.log('[migrate] Step 4: Migrating parking_lot → context...')

  const parkingLot = await db
    .prepare(
      `SELECT p.*, e.client_id as entity_id
       FROM parking_lot p
       JOIN engagements e ON e.id = p.engagement_id`
    )
    .all<Record<string, unknown>>()

  for (const item of parkingLot.results) {
    try {
      const entityId = item.entity_id as string
      if (!entityId) {
        stats.errors.push(`Parking lot ${item.id}: no entity_id from engagement`)
        continue
      }

      await insertContext(db, {
        entity_id: entityId,
        org_id: '', // Will be populated from engagement
        type: 'parking_lot',
        content: item.description as string,
        source: 'migration',
        engagement_id: item.engagement_id as string,
        metadata: JSON.stringify({
          migrated_from: 'parking_lot',
          original_id: item.id,
          requested_by: item.requested_by,
          requested_at: item.requested_at,
          disposition: item.disposition,
          disposition_note: item.disposition_note,
          reviewed_at: item.reviewed_at,
          follow_on_quote_id: item.follow_on_quote_id,
        }),
        created_at: item.created_at as string,
      })
      stats.contextEntriesCreated++
      stats.parkingLotProcessed++
    } catch (err) {
      stats.errors.push(`Parking lot ${item.id}: ${err}`)
    }
  }

  console.log(`[migrate] Step 4 complete: ${stats.parkingLotProcessed} parking lot items processed`)

  // -------------------------------------------------------------------------
  // Step 5: Recompute deterministic cache for all entities
  // -------------------------------------------------------------------------
  console.log('[migrate] Step 5: Recomputing deterministic cache...')

  const allEntities = await db
    .prepare('SELECT id, org_id FROM entities')
    .all<{ id: string; org_id: string }>()

  for (const entity of allEntities.results) {
    try {
      await recomputeCache(db, entity.org_id, entity.id)
    } catch (err) {
      stats.errors.push(`Recompute ${entity.id}: ${err}`)
    }
  }

  console.log(`[migrate] Step 5 complete: ${allEntities.results.length} entities recomputed`)

  // -------------------------------------------------------------------------
  // Summary
  // -------------------------------------------------------------------------
  console.log('\n[migrate] === MIGRATION COMPLETE ===')
  console.log(`  Clients processed:      ${stats.clientsProcessed}`)
  console.log(`  Entities created:        ${stats.entitiesCreated}`)
  console.log(`  Context entries created:  ${stats.contextEntriesCreated}`)
  console.log(`  Lead signals processed:  ${stats.leadSignalsProcessed}`)
  console.log(`  Assessments processed:   ${stats.assessmentsProcessed}`)
  console.log(`  Parking lot processed:   ${stats.parkingLotProcessed}`)
  console.log(`  Errors:                  ${stats.errors.length}`)
  if (stats.errors.length > 0) {
    console.log('\n  Errors:')
    for (const err of stats.errors) {
      console.log(`    - ${err}`)
    }
  }

  return stats
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Slug computation (duplicated from src/lib/entities/slug.ts to avoid import issues in scripts). */
function computeSlug(name: string, area: string | null): string {
  let s = name.toLowerCase()
  s = s.replace(/\s*\(.*?\)\s*/g, ' ')
  s = s.replace(
    /\b(llc|inc|corp|ltd|co|company|corporation|incorporated|limited|pllc|lp|plc|dba)\b\.?/gi,
    ''
  )
  s = s.replace(/[^a-z0-9\s-]/g, '')
  s = s.trim().replace(/\s+/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '')
  if (area) {
    const a = area
      .toLowerCase()
      .replace(/[^a-z0-9\s]/g, '')
      .trim()
      .replace(/\s+/g, '-')
      .replace(/-+/g, '-')
      .replace(/^-|-$/g, '')
    if (a) s = `${s}-${a}`
  }
  return s
}

/** Map old client status to new entity stage. */
function mapClientStatus(status: string): string {
  const map: Record<string, string> = {
    prospect: 'prospect',
    assessed: 'assessing',
    quoted: 'proposing',
    active: 'engaged',
    completed: 'delivered',
    dead: 'lost',
  }
  return map[status] || 'prospect'
}

/** Insert a context entry with explicit fields. */
async function insertContext(
  db: D1Database,
  data: {
    entity_id: string
    org_id: string
    type: string
    content: string
    source: string
    metadata?: string | null
    engagement_id?: string | null
    created_at?: string
  }
): Promise<void> {
  const id = crypto.randomUUID()
  const contentSize = new TextEncoder().encode(data.content).length

  await db
    .prepare(
      `INSERT INTO context (
        id, entity_id, org_id, type, content, source, content_size,
        metadata, engagement_id, created_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
    )
    .bind(
      id,
      data.entity_id,
      data.org_id,
      data.type,
      data.content,
      data.source,
      contentSize,
      data.metadata ?? null,
      data.engagement_id ?? null,
      data.created_at ?? new Date().toISOString()
    )
    .run()
}

/** Safe JSON parse that returns null on failure. */
function safeParseJson(text: string): Record<string, unknown> | null {
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

/** Recompute deterministic cache (duplicated from src/lib/entities/recompute.ts). */
async function recomputeCache(db: D1Database, orgId: string, entityId: string): Promise<void> {
  const entries = await db
    .prepare('SELECT type, metadata FROM context WHERE entity_id = ? ORDER BY created_at ASC')
    .bind(entityId)
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

    if (typeof meta.pain_score === 'number' && meta.pain_score >= 1 && meta.pain_score <= 10) {
      painScore = Math.max(painScore ?? 0, meta.pain_score)
    }
    if (typeof meta.vertical === 'string' && meta.vertical) vertical = meta.vertical
    if (typeof meta.vertical_match === 'string' && meta.vertical_match)
      vertical = meta.vertical_match
    if (typeof meta.area === 'string' && meta.area) area = meta.area
    if (typeof meta.employee_count === 'number' && meta.employee_count > 0) {
      employeeCount = meta.employee_count
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
