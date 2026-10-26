-- Rollback for 0141: narrow job_lane back to
-- ('demand', 'drafting', 'litigation', 'medchron', 'negotiation').
-- NOT fail-soft. Every row with job_lane 'casefile' would violate the narrowed
-- CHECK, so those rows are dropped here (their emails were already sent; the
-- rows are only the send ledger). Roll the overlay's casefile lane back first,
-- then run this.
--
-- Manual-only; coordinate with Captain.

CREATE TABLE operator_request_cards_old (
  customer_slug  TEXT NOT NULL,
  card_key       TEXT NOT NULL,
  kind           TEXT NOT NULL
    CHECK (kind IN ('replied', 'no_reply', 'job_done', 'lane_unproven', 'held_reply')),
  received_at    TEXT NOT NULL,
  event_at       TEXT NOT NULL,
  minutes        INTEGER NOT NULL,
  tools_count    INTEGER NOT NULL DEFAULT 0,
  replies        INTEGER NOT NULL DEFAULT 0,
  refused        INTEGER NOT NULL DEFAULT 0,
  failed         INTEGER NOT NULL DEFAULT 0,
  job_lane       TEXT CHECK (job_lane IS NULL OR job_lane IN ('demand', 'drafting', 'litigation', 'medchron', 'negotiation')),
  job_state      TEXT CHECK (job_state IS NULL OR job_state IN ('delivered', 'failed', 'held')),
  status         TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'sent')),
  attempts       INTEGER NOT NULL DEFAULT 0,
  sent_at        TEXT,
  first_seen_at  TEXT NOT NULL DEFAULT (datetime('now')),
  lane_key       TEXT CHECK (lane_key IS NULL OR length(lane_key) BETWEEN 1 AND 105),
  jobs_queued    TEXT,
  PRIMARY KEY (customer_slug, card_key)
);

INSERT INTO operator_request_cards_old (
  customer_slug, card_key, kind, received_at, event_at, minutes, tools_count, replies, refused,
  failed, job_lane, job_state, status, attempts, sent_at, first_seen_at, lane_key, jobs_queued)
SELECT
  customer_slug, card_key, kind, received_at, event_at, minutes, tools_count, replies, refused,
  failed, job_lane, job_state, status, attempts, sent_at, first_seen_at, lane_key, jobs_queued
FROM operator_request_cards
WHERE job_lane IS NULL OR job_lane != 'casefile';

DROP INDEX IF EXISTS idx_operator_request_cards_received;
DROP TABLE operator_request_cards;
ALTER TABLE operator_request_cards_old RENAME TO operator_request_cards;

CREATE INDEX idx_operator_request_cards_received
  ON operator_request_cards (received_at, customer_slug);
