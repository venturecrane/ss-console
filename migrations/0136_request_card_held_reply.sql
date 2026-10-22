-- 0136: the requester wrote again on a held job (kind 'held_reply').
--
-- On 2026-10-22 a chronology job held on an identity check (an emailed
-- attachment carried a date of birth that was not the client's). The Operator
-- asked the requesting attorney the one question that hold raises; he
-- answered in the thread ("just leave it out"). Nothing on our side heard
-- that answer: the seat carded his follow-up as an ordinary `replied` card
-- (the Operator's words, not his), and SMD learned his decision only when he
-- forwarded the exchange to the Captain, an hour later, saying the Operator
-- was speaking over his head.
--
-- A requester who writes again in the thread of a request whose job is HELD
-- is either answering the firm's question or chasing us; either way SMD must
-- act, and the Operator cannot (no email talks a seat past a hold:
-- docs/handbook/incident-response.md, 2026-10-13). The seat now posts a
-- `held_reply` card the moment such a message arrives, carrying the
-- requester's own opening words and the held job's lane, state and id, and
-- ss-web emails it as "[SMD Ops] <seat> REQUESTER WROTE on held <lane> job".
--
-- ADR 0052 s5 holds: the requester's words reach the email only. This table
-- records the kind, the job lane and state, and the keys.
--
-- SQLite cannot ALTER a CHECK, so this is a full-table rebuild (the 0123-0125
-- shape): identical columns including lane_key (0125) and jobs_queued (0134),
-- identical PK and index, every row carried across. Every existing value
-- stays valid throughout.
--
-- Ordering is safe: deploy.yml applies migrations before the worker deploys,
-- and no seat posts a held_reply card until the overlay ships it.
--
-- Manual-only rollback at migrations/rollbacks/0136_request_card_held_reply_down.sql.

CREATE TABLE operator_request_cards_new (
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
