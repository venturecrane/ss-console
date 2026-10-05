-- 0120: when the Operator could not give a client what they asked, SMD hears
-- about it (Captain, 2026-10-05).
--
-- The gap: a person asks their seat for something, the Operator says it
-- cannot (a refusal, a size or page limit, a broken call) or quietly does
-- less than it was handed (2026-10-01: a 52-page scan, 0 pages filed), and
-- the only record is the reply in the person's own thread. Every one of
-- those tool results scored "ok" in the seat's audit ledger, so no meter saw
-- it; send_refused (0109) watches only cron-turn sends, tool_failing (0119)
-- watches two named tools.
--
-- The overlay now classifies those results as a third audit outcome,
-- `shortfall`, and the heartbeat reports the trailing day's events from all
-- sessions, person and cron alike:
--
--   shortfalls           total events in the window (uncapped)
--   shortfalls_last_ts   newest event, the seat-side paging marker
--   shortfalls_json      the oldest 20 events, each {ts, class, tool,
--                        routine, code, key}. class is not_allowed | limit
--                        | failed | partial. code is a closed vocabulary
--                        (a reason enum, a gate prefix, or "filed N of M"),
--                        never free text: these rows reach email.
--
-- COALESCE at ingest, like send_refusals: an absent key holds the last value.
--
-- operator_shortfalls is the ledger the fleet-alerts Worker pages from. A
-- heartbeat carries a sliding 24-hour window, so the same event arrives on
-- every beat for a day; `event_key` (seat-computed, stable per event) makes
-- the upsert idempotent and `notified_at` makes the page once-only.
-- limit/failed/partial page within the tick (throttled per seat);
-- not_allowed waits for the Monday digest. Event-shaped like send_refused,
-- so no fleet_alert_state condition and no CHECK rebuild.
--
-- fleet_alert_state.alert_message_id: the Message-ID minted for an ALERT
-- email, so its RECOVERED notice threads under it in Gmail instead of
-- landing as a second inbox item (about 200 alert mails a month, most of
-- them ALERT/RECOVERED pairs).
--
-- Manual-only rollback at migrations/rollbacks/0120_operator_shortfalls_down.sql.

ALTER TABLE fleet_status ADD COLUMN shortfalls INTEGER;
ALTER TABLE fleet_status ADD COLUMN shortfalls_last_ts TEXT;
ALTER TABLE fleet_status ADD COLUMN shortfalls_json TEXT;

ALTER TABLE fleet_alert_state ADD COLUMN alert_message_id TEXT;

CREATE TABLE operator_shortfalls (
  customer_slug  TEXT NOT NULL,
  event_key      TEXT NOT NULL,
  ts             TEXT NOT NULL,
  class          TEXT NOT NULL
    CHECK (class IN ('not_allowed', 'limit', 'failed', 'partial')),
  tool           TEXT NOT NULL,
  routine        TEXT,
  code           TEXT NOT NULL,
  first_seen_at  TEXT NOT NULL DEFAULT (datetime('now')),
  notified_at    TEXT,
  PRIMARY KEY (customer_slug, event_key)
);

CREATE INDEX idx_operator_shortfalls_unnotified
  ON operator_shortfalls (notified_at, class, customer_slug);
