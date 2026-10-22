-- Rollback for 0134: drop jobs_queued. Fail-soft: the column is a derived
-- reading of the card's tool list, nothing else depends on it. Roll the
-- worker back first (its INSERT names the column), then run this.
--
-- Manual-only; coordinate with Captain.

ALTER TABLE operator_request_cards DROP COLUMN jobs_queued;
