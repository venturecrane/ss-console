-- Rollback for 0115: drop the edge-poll counters. Roll the fleet-alerts Worker
-- back to a commit before 0115 first, or leave EDGE_POLL_TARGETS empty on the
-- deployed Worker: the poll is fail-soft and logs a missing table on every
-- tick, but it would count nothing and page nothing. Any open edge_down row in
-- fleet_alert_state stays as it was and needs the manual resolve UPDATE.
DROP TABLE IF EXISTS edge_poll_state;
