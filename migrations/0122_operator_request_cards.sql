-- 0122: SMD hears about every request a person sends an Operator seat.
--
-- The gap: when someone at a client firm emails their seat, the only record
-- of what they asked and how the Operator answered is the thread in that
-- person's mailbox and the seat's own audit ledger. A request nobody answered
-- looks exactly like a quiet day. The seat now posts one "card" per request
-- to POST /api/internal/operator-request-card, and ss-web emails it to SMD
-- ops (ALERT_TO_EMAIL, team@smd.services) through Resend:
--
--   replied    the Operator's first reply went out (minutes = time to it)
--   no_reply   nothing answered within the seat's alarm window
--   job_done   a queued job the request started (demand, chronology)
--              reached a terminal state
--
-- ADR 0052 s5: ss-console persists no client text. The card's sender,
-- subject, reply opening and matter exist only in the request body and the
-- email it becomes; NONE of them is a column here. This table holds the
-- send ledger and the counts the `.claude/bin/requests` reader lists:
-- `card_key` is "<sha256 hex of the seat's message id>:<kind>", an opaque
-- seat-computed key, and the PK makes a re-post (the seat retries until it
-- sees a 200) a duplicate rather than a second email.
--
-- status: pending until Resend accepts the email, then sent. A failed send
-- leaves the row pending with attempts incremented and answers 502, so the
-- seat retries; the Resend Idempotency-Key "<slug>:<card_key>" keeps two
-- racing retries to one email.
--
-- Manual-only rollback at migrations/rollbacks/0122_operator_request_cards_down.sql.

CREATE TABLE operator_request_cards (
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
  job_lane       TEXT CHECK (job_lane IS NULL OR job_lane IN ('demand', 'medchron')),
  job_state      TEXT CHECK (job_state IS NULL OR job_state IN ('delivered', 'failed', 'held')),
  status         TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'sent')),
  attempts       INTEGER NOT NULL DEFAULT 0,
  sent_at        TEXT,
  first_seen_at  TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (customer_slug, card_key)
);

CREATE INDEX idx_operator_request_cards_received
  ON operator_request_cards (received_at, customer_slug);
