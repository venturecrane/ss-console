-- 0108: the audit chain head, pinned off the Machine (ss#2500)
--
-- WHAT WAS MISSING. The per-seat audit ledger is a SHA-256 hash chain
-- (operator/workspace_broker/chain.py). Mutating or deleting a row in the
-- MIDDLE of that chain breaks it at a verifiable point. Removing rows off the
-- END does not: the remaining prefix is a perfectly valid chain, and
-- `verify-audit-chain.py` reports it INTACT. Proven, not assumed
-- (vfy_01M0H8D1CV2X8J9ZACMAC8E6E2, run against copies of a live 1,473-row
-- export): deleting the last 50 rows, deleting the last 1 row, appending a
-- forged row with a valid hash, and mutate-then-rehash-everything-after all
-- passed the verifier.
--
-- The verifier's own header already named the fix -- "tail truncation is caught
-- by comparing the reported head against an externally pinned head" -- and
-- nothing pinned one. The head never left the Machine: the heartbeat shipped
-- `last_audit_ts` and nothing else about the chain.
--
-- WHAT THIS TABLE IS. Every heartbeat's chain head, persisted OFF the Machine,
-- so a later export can be required to descend from a head root already holds.
-- Root on the Machine can still rewrite the ledger; what it cannot do is reach
-- back and change what this table already recorded.
--
-- APPEND-ONLY, AND WHY THE ONE UPDATE IS NOT A HOLE. `audit_head`, `audit_rows`
-- and `first_seen_heartbeat_ts` are written once and never updated -- those are
-- the pin. `last_seen_heartbeat_ts` and `beats` are refreshed while the SAME
-- head keeps arriving, which is how a row records "this head was still current
-- at T" without storing 1,440 identical rows per seat per day. A NEW head is
-- always a new row, so no head can ever be overwritten by a later one. The
-- issue asks for every head persisted rather than a daily sample, and that is
-- exactly what this stores: one row per distinct head, none dropped.
--
-- NOT fleet_status. fleet_status is a one-row-per-seat CURRENT-state projection
-- whose columns are overwritten every beat by design; a pin that a later beat
-- can overwrite is not a pin. ss#2498 adds `audit_head` / `audit_rows` columns
-- there for the live dashboard read. Same two wire fields, two different jobs:
-- that projection answers "what is the head now", this table answers "what
-- heads has root already been unable to take back".
--
-- The heartbeat handler writes both from one intake
-- (src/pages/api/internal/heartbeat.ts -> src/lib/operator/audit-head.ts).

CREATE TABLE IF NOT EXISTS audit_head_history (
  id                      INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_slug           TEXT NOT NULL,
  -- Refreshed from the request like fleet_status does: several seats can share
  -- one entity, so the slug is the identity and this is a convenience join key.
  entity_id               TEXT,
  -- row_hash of the chain tip: sha256 hexdigest, 64 lowercase hex characters.
  -- NOT NULL by construction -- a beat with no head has nothing to pin and
  -- writes no row at all, rather than pinning a NULL that later reads as "the
  -- ledger was empty then".
  audit_head              TEXT NOT NULL,
  -- COUNT(*) of the ledger at the moment the head was read. Nullable: an
  -- overlay that reports a head without a count is still worth pinning, and
  -- inventing a number here would make a shrink undetectable in the direction
  -- that matters.
  audit_rows              INTEGER,
  first_seen_heartbeat_ts TEXT NOT NULL,
  last_seen_heartbeat_ts  TEXT NOT NULL,
  beats                   INTEGER NOT NULL DEFAULT 1,
  recorded_at             TEXT NOT NULL DEFAULT (datetime('now'))
);

-- The daily verifier's only read: newest pinned head for one seat.
CREATE INDEX IF NOT EXISTS idx_audit_head_history_seat
  ON audit_head_history(customer_slug, id DESC);

-- Answers "was this head ever pinned" without a table scan. That question is
-- the descends check run backwards, and an auditor asking it about a specific
-- hash is the point of keeping the whole history rather than the last row.
CREATE INDEX IF NOT EXISTS idx_audit_head_history_head
  ON audit_head_history(audit_head);
