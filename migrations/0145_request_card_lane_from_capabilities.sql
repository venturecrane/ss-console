-- 0145: the job lanes a request card may name come from one place (ADR 0100).
--
-- Every new job lane so far has cost a full rebuild of this table only to
-- widen the job_lane CHECK (0123-0125, 0136, 0142), and the CHECK was a second
-- copy of a list that already lives in the seat's capability table
-- (operator/hermes-smd-overlay/shared/capabilities.py JOB_LANES). The smd-note
-- lane is authorable there and was missing here. The list now has one home:
-- src/lib/operator/request-card.ts JOB_LANES derives from the console mirror
-- of that table (sections-capabilities.ts), pinned to the Python by
-- tests/customer-yaml-capabilities.test.ts, and the parse refuses any other
-- value before a row is written. The column keeps its NULL-or-text shape.
--
-- SQLite cannot ALTER a CHECK, so this is a full-table rebuild (the 0142
-- shape): identical columns, identical kind / job_state / status / lane_key
-- CHECKs, identical PK and index, every row carried across. Only the job_lane
-- CHECK is gone, so every existing value stays valid throughout.
--
-- Manual-only rollback at
-- migrations/rollbacks/0145_request_card_lane_from_capabilities_down.sql.

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
  job_lane       TEXT,
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
