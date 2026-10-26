-- 0142: the case file manager's jobs on request cards (job_lane 'casefile').
--
-- The casefile lane (skill case-file-manager) reads each matter in a caseload,
-- files a current File Status in each, and sends a ranked list with numbered
-- changes for the firm to approve; an APPLY job writes the approved ones. Its
-- jobs end delivered, failed or held exactly as a litigation status job does,
-- and a held casefile job waiting on its lane proof posts a lane_unproven card.
-- Both carry job_lane 'casefile', which the CHECK refused.
--
-- SQLite cannot ALTER a CHECK, so this is a full-table rebuild (the 0123-0125
-- and 0136 shape): identical columns including lane_key (0125) and jobs_queued
-- (0134), identical kind CHECK (0141, followup), identical PK and index,
-- every row carried across. Every existing value stays valid throughout.
--
-- Ordering is safe: deploy.yml applies migrations before the worker deploys,
-- and no seat posts a casefile card until the overlay ships the lane.
--
-- Manual-only rollback at migrations/rollbacks/0142_request_card_casefile_lane_down.sql.

CREATE TABLE operator_request_cards_new (
  customer_slug  TEXT NOT NULL,
  card_key       TEXT NOT NULL,
  kind           TEXT NOT NULL
    CHECK (kind IN ('replied', 'no_reply', 'job_done', 'lane_unproven', 'held_reply', 'followup')),
  received_at    TEXT NOT NULL,
  event_at       TEXT NOT NULL,
  minutes        INTEGER NOT NULL,
  tools_count    INTEGER NOT NULL DEFAULT 0,
  replies        INTEGER NOT NULL DEFAULT 0,
  refused        INTEGER NOT NULL DEFAULT 0,
  failed         INTEGER NOT NULL DEFAULT 0,
  job_lane       TEXT CHECK (job_lane IS NULL OR job_lane IN ('demand', 'drafting', 'litigation', 'medchron', 'negotiation', 'casefile')),
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

INSERT INTO operator_request_cards_new (
  customer_slug, card_key, kind, received_at, event_at, minutes, tools_count, replies, refused,
  failed, job_lane, job_state, status, attempts, sent_at, first_seen_at, lane_key, jobs_queued)
SELECT
  customer_slug, card_key, kind, received_at, event_at, minutes, tools_count, replies, refused,
  failed, job_lane, job_state, status, attempts, sent_at, first_seen_at, lane_key, jobs_queued
FROM operator_request_cards;

DROP INDEX IF EXISTS idx_operator_request_cards_received;
DROP TABLE operator_request_cards;
ALTER TABLE operator_request_cards_new RENAME TO operator_request_cards;

CREATE INDEX idx_operator_request_cards_received
  ON operator_request_cards (received_at, customer_slug);
