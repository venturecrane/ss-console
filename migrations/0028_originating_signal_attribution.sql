-- Originating-signal attribution on lifecycle artifacts (#589).
--
-- Why: signals (rows in `context` with type='signal') are the leading indicator
-- of which lead-gen pipeline produced revenue. Without an FK from the
-- downstream artifact (meeting / quote / engagement) back to the originating
-- signal, ROI per pipeline is unknowable — we can see signal volume per
-- pipeline and revenue per engagement, but the join in between requires
-- attribution that only the admin can author.
--
-- Strategy: nullable FK on each lifecycle artifact, defaulting to the most
-- recent qualified signal for the entity at creation time. Admin UI lets the
-- operator pick a different signal or unset attribution entirely. NULL is
-- valid — pre-migration rows stay NULL, and artifacts created against an
-- entity with no signals (e.g. inbound referrals) also stay NULL.
--
-- Strictly additive. No backfill (pre-existing rows keep NULL — attribution
-- prior to this migration is unrecoverable). No drops. Safe to roll back by
-- ignoring the new columns; downstream code treats NULL as "unknown".
--
-- Choice of FK target: `context.id`. Signals are stored as context entries
-- with type='signal', not as their own table (the `lead_signals` table from
-- migration 0007 was superseded by the entity/context architecture in 0008).
-- See `src/lib/db/context.ts` for the append-only contract.

-- ============================================================================
-- meetings.originating_signal_id
-- ============================================================================
ALTER TABLE meetings ADD COLUMN originating_signal_id TEXT REFERENCES context(id);
CREATE INDEX IF NOT EXISTS idx_meetings_originating_signal
  ON meetings(originating_signal_id)
  WHERE originating_signal_id IS NOT NULL;

-- ============================================================================
-- quotes.originating_signal_id
-- ============================================================================
ALTER TABLE quotes ADD COLUMN originating_signal_id TEXT REFERENCES context(id);
CREATE INDEX IF NOT EXISTS idx_quotes_originating_signal
  ON quotes(originating_signal_id)
  WHERE originating_signal_id IS NOT NULL;

-- ============================================================================
-- engagements.originating_signal_id
-- ============================================================================
ALTER TABLE engagements ADD COLUMN originating_signal_id TEXT REFERENCES context(id);
CREATE INDEX IF NOT EXISTS idx_engagements_originating_signal
  ON engagements(originating_signal_id)
  WHERE originating_signal_id IS NOT NULL;
