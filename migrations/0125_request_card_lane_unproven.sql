-- 0125: the lane-proof gate's card in operator_request_cards.
--
-- A seat now posts a `lane_unproven` card the moment a request asks for a
-- kind of job not proven on that seat (the job waits, queued, until SMD
-- proves the lane: docs/runbooks/operator/lane-proof.md). Three changes:
--
--   * kind admits 'lane_unproven';
--   * job_lane admits 'negotiation' (a held negotiation-watch run is the
--     first card that lane has ever had);
--   * lane_key, new: the lane the card is about (a code-authored key such as
--     `document-drafter.discovery_set`, never client text; ADR 0052 s5 holds).
--
-- SQLite cannot ALTER a CHECK, so this is a full-table rebuild (the 0123/0124
-- shape): identical columns plus lane_key, identical PK and index, every row
-- carried across. Every existing value stays valid throughout.
--
-- Ordering is safe: deploy.yml applies migrations before the worker deploys,
-- and no seat posts a lane card until the overlay ships the gate.
--
-- Manual-only rollback at migrations/rollbacks/0125_request_card_lane_unproven_down.sql.

CREATE TABLE operator_request_cards_new (
  customer_slug  TEXT NOT NULL,
  card_key       TEXT NOT NULL,
  kind           TEXT NOT NULL
    CHECK (kind IN ('replied', 'no_reply', 'job_done', 'lane_unproven')),
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
