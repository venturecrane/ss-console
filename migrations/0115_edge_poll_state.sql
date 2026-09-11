-- 0115: edge_poll_state, the fleet-alerts pager's memory of its own outside-in
-- probe of the web Worker (code review 2026-09-10, wave 8.2).
--
-- Every condition the pager evaluates is read out of fleet_status, which the
-- seats write INTO the web Worker. A dead web Worker freezes that table and
-- the pager, reading the frozen table from the same database, sees nothing
-- wrong. The pager now also GETs /api/health from outside every two minutes.
--
-- A probe is a sample, not a state, and one sample can lie (a cold start, a
-- routing blip, a timeout at the deadline). So the poll counts: a target is
-- DOWN after EDGE_POLL_FAIL_THRESHOLD consecutive failures and UP again after
-- EDGE_POLL_RECOVER_THRESHOLD consecutive successes, and between the two the
-- pager pushes nothing (the connector_down hold). This table holds those two
-- runs per target; the open/recovered alert itself lives in fleet_alert_state
-- under the target name as customer_slug and the edge_down condition, so the
-- one-page-per-transition machinery is reused unchanged.
--
-- Written and read by: workers/fleet-alerts/src/edge-poll.ts only.

CREATE TABLE edge_poll_state (
  target TEXT PRIMARY KEY,
  url TEXT NOT NULL,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  consecutive_successes INTEGER NOT NULL DEFAULT 0,
  -- 1 or 0 for the most recent probe. NULL never occurs after the first write.
  last_ok INTEGER,
  -- What the last probe saw: the HTTP status and body status, a timeout, or
  -- the fetch error. Bounded by the writer, never rendered unescaped.
  last_detail TEXT,
  last_checked_at TEXT NOT NULL,
  -- Kept across failed probes, so the row always says when the edge last
  -- answered well.
  last_ok_at TEXT,
  updated_at TEXT NOT NULL
);
