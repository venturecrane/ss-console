-- Rollback for 0122: drop the request-card ledger.
-- NOT fail-soft. POST /api/internal/operator-request-card writes this table
-- on every card, so running this while that ss-web build is deployed turns
-- every card into a 500 the seat retries for a day and then drops. Roll the
-- Worker back first, then run this.
--
-- Manual-only; coordinate with Captain.

DROP INDEX IF EXISTS idx_operator_request_cards_received;
DROP TABLE IF EXISTS operator_request_cards;
