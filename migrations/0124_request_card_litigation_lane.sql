-- 0124: admit 'litigation' into operator_request_cards.job_lane's CHECK.
--
-- The litigation status lane (a status workbook of the firm's open litigation
-- matters, queued as a job: operator/workspace_broker/litigation_verbs.py)
-- ends the same way the drafting, demand and chronology jobs do, and the seat
-- posts a job_done card for it. 0123 admitted 'demand', 'drafting' and
-- 'medchron' only, so the INSERT for a litigation card would be rejected and
-- the handler would answer 500 until the seat gave up: SMD would never hear
-- that a litigation job ended.
--
-- SQLite cannot ALTER a CHECK, so this is a full-table rebuild (the 0123
-- shape): identical columns, identical PK and index, every row carried
-- across, 'litigation' added to the job_lane IN list. Every existing value
-- stays valid throughout.
--
-- Ordering is safe: deploy.yml applies migrations before the worker deploys,
-- and no seat posts a litigation card until the overlay ships its lane.
--
-- Manual-only rollback at migrations/rollbacks/0124_request_card_litigation_lane_down.sql.

CREATE TABLE operator_request_cards_new (
  customer_slug  TEXT NOT NULL,
  card_key       TEXT NOT NULL,
  kind           TEXT NOT NULL
    CHECK (kind IN ('replied', 'no_reply', 'job_done')),
  received_at    TEXT NOT NULL,
  event_at       TEXT NOT NULL,
  minutes        INTEGER NOT NULL,
  tools_count    INTEGER NOT NULL DEFAULT 0,
  replies        INTEGER NOT NULL DEFAULT 0,
  refused        INTEGER NOT NULL DEFAULT 0,
  failed         INTEGER NOT NULL DEFAULT 0,
  job_lane       TEXT CHECK (job_lane IS NULL OR job_lane IN ('demand', 'drafting', 'litigation', 'medchron')),
  job_state      TEXT CHECK (job_state IS NULL OR job_state IN ('delivered', 'failed', 'held')),
  status         TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'sent')),
  attempts       INTEGER NOT NULL DEFAULT 0,
  sent_at        TEXT,
  first_seen_at  TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (customer_slug, card_key)
);

INSERT INTO operator_request_cards_new (
  customer_slug, card_key, kind, received_at, event_at, minutes, tools_count, replies, refused,
  failed, job_lane, job_state, status, attempts, sent_at, first_seen_at)
SELECT
  customer_slug, card_key, kind, received_at, event_at, minutes, tools_count, replies, refused,
  failed, job_lane, job_state, status, attempts, sent_at, first_seen_at
FROM operator_request_cards;

DROP INDEX IF EXISTS idx_operator_request_cards_received;
DROP TABLE operator_request_cards;
ALTER TABLE operator_request_cards_new RENAME TO operator_request_cards;

CREATE INDEX idx_operator_request_cards_received
  ON operator_request_cards (received_at, customer_slug);
