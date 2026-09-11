/**
 * The audit chain head, pinned off the Machine (ss#2500).
 *
 * The per-seat audit ledger is a SHA-256 hash chain. Breaking it in the MIDDLE
 * is detectable from the export alone; truncating the TAIL is not, because the
 * surviving prefix is a valid chain. The only thing that makes tail truncation
 * detectable is a head recorded somewhere the seat cannot reach, which a later
 * export must then be shown to descend from.
 *
 * This module is that recording half. The heartbeat carries `audit_head` (the
 * `row_hash` of the chain tip) and `audit_rows` (the ledger's row count); the
 * ingest hands them here and this appends the pin to `audit_head_history`
 * (migration 0108).
 *
 * WHY A SEPARATE MODULE AND NOT MORE LINES IN heartbeat.ts. The same two wire
 * fields also land in `fleet_status` (ss#2498) for the live dashboard read.
 * That is a current-state projection whose columns are overwritten every beat;
 * this is an append-only pin history. Two destinations, one parse: the handler
 * parses each field once and hands the result to both, so a future change to
 * what counts as a valid head cannot make the dashboard and the pin disagree
 * about the same beat. The write below is the part that is genuinely different
 * -- append-with-dedupe rather than overwrite -- and that is what lives here.
 *
 * WHAT THIS DOES NOT DO. It does not verify anything. A pin is a fact recorded
 * about a moment, and the seat is the one asserting it: a root user who
 * rewrites the ledger AND controls the next heartbeat can pin the rewritten
 * head. What root cannot do is reach back and change a pin already stored here,
 * which is what makes every row OLDER than the rewrite an accusation. The
 * comparison itself runs daily off-box in
 * `.github/workflows/audit-chain-verify.yml`.
 */

/**
 * ss#2498 landed the two parsers for these fields inside the heartbeat handler
 * itself (`parseAuditHead` for the head, the file's existing `parseNonNegInt`
 * for the row count), and this module deliberately does NOT define a second
 * pair. Both destinations -- the `fleet_status` projection the dashboard reads
 * and the pin history below -- take their values from that one parse, so they
 * cannot disagree about what a given beat said. Two parsers with the same rule
 * written twice is exactly how they start to differ.
 *
 * What that parse guarantees, and what this module therefore relies on: a head
 * is 64 lowercase hex characters or it is null. Junk is never pinned, because a
 * pinned value that could never appear in any export would make the daily
 * verifier accuse a healthy ledger every day until someone read the row.
 */

export interface AuditHeadBeat {
  entityId: string
  slug: string
  heartbeatTs: string
  auditHead: string | null
  auditRows: number | null
}

/**
 * Append this beat's head to the pin history.
 *
 * Three behaviours worth stating because each is a decision:
 *
 * 1. A beat with no parseable head writes NOTHING. A seat whose ledger has no
 *    chained rows yet, or an overlay that predates the field, has no head to
 *    pin; writing a NULL row would later read as "the ledger was empty at T",
 *    which is a claim neither side made.
 *
 * 2. A beat whose head equals the newest pinned head for this seat refreshes
 *    that row's `last_seen_heartbeat_ts` and `beats` instead of inserting. The
 *    pin fields (`audit_head`, `audit_rows`, `first_seen_heartbeat_ts`) are
 *    never touched. At a 60s beat this is the difference between one row per
 *    distinct head and 1,440 identical rows per seat per day; no head is lost
 *    either way, because a DIFFERENT head always inserts.
 *
 * 3. A head that reappears after a different head was pinned in between still
 *    INSERTS a new row. That is a head regression -- the ledger moved backwards
 *    -- and collapsing it into the earlier row would erase the evidence. The
 *    comparison is against the NEWEST pin only, never against the whole set.
 */
export async function recordAuditHead(db: D1Database, beat: AuditHeadBeat): Promise<void> {
  if (beat.auditHead === null) return

  const newest = await db
    .prepare(
      `SELECT id, audit_head FROM audit_head_history
        WHERE customer_slug = ?
        ORDER BY id DESC LIMIT 1`
    )
    .bind(beat.slug)
    .first<{ id: number; audit_head: string }>()

  if (newest && newest.audit_head === beat.auditHead) {
    await db
      .prepare(
        `UPDATE audit_head_history
            SET last_seen_heartbeat_ts = ?,
                beats                  = beats + 1,
                entity_id              = ?
          WHERE id = ?`
      )
      .bind(beat.heartbeatTs, beat.entityId, newest.id)
      .run()
    return
  }

  await db
    .prepare(
      `INSERT INTO audit_head_history
         (customer_slug, entity_id, audit_head, audit_rows,
          first_seen_heartbeat_ts, last_seen_heartbeat_ts, beats, recorded_at)
       VALUES (?, ?, ?, ?, ?, ?, 1, datetime('now'))`
    )
    .bind(
      beat.slug,
      beat.entityId,
      beat.auditHead,
      beat.auditRows,
      beat.heartbeatTs,
      beat.heartbeatTs
    )
    .run()
}
