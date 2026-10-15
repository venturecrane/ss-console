-- Rollback for 0126: restore one operator service per entity and drop the label.
--
-- PRECONDITION. Recreating `idx_services_one_operator` FAILS once any entity
-- holds two operator services (a client with a second priced seat). That
-- failure is the safeguard: it stops the rollback before it can silently
-- collapse two prices into one. Before running this file, for each entity
-- that the query below returns, decide which service survives, repoint the
-- other seats to it, and delete the rest:
--
--   SELECT entity_id, COUNT(*) FROM services
--    WHERE type = 'operator' GROUP BY entity_id HAVING COUNT(*) > 1;
--
--   UPDATE subscriptions SET service_id = '<surviving svc id>'
--    WHERE service_id = '<other svc id>';
--   DELETE FROM services WHERE id = '<other svc id>';
--
-- Any live Stripe subscription started on the deleted service keeps charging
-- its own amount at Stripe; cancel or reprice it there first.
--
-- Roll the code back to a commit before 0126 first, or the worker will
-- select a column that no longer exists.
--
-- The `subscriptions.service_id` values the up migration backfilled are left
-- in place: the column predates 0126 and a correct link is harmless.
--
-- Manual-only; coordinate with Captain.

-- The index goes first, so an unmet precondition stops the file before the
-- column is dropped.
CREATE UNIQUE INDEX idx_services_one_operator ON services(entity_id) WHERE type = 'operator';

ALTER TABLE services DROP COLUMN label;
