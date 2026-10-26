-- 0141: a reply SENT that no `replied` card counted (kind 'followup').
--
-- On 2026-10-26 a session told the Captain a requester had not been told
-- something the Operator had told them, in a second reply 50 minutes after its
-- first. A `replied` card counts the replies
-- sent before it lands and is never sent again, so that correction reached the
-- client and no record SMD reads. The seat now posts a `followup` card for every
-- such reply (a later reply in the same request, or a reply to a message it has
-- no request for), quoting that reply's own opening, and ss-web emails it as
-- "[SMD Ops] <seat> later reply sent at +<n>m".
--
-- ADR 0052 s5 holds: the reply's words reach the email only; this table keeps
-- numbers, kinds and keys. Same rebuild shape as 0136 (SQLite cannot widen a
-- CHECK in place).

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
