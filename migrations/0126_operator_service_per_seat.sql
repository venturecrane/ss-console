-- 0126: an Operator price belongs to the seat, not the client.
--
-- Until now the commercial record (`services`, ADR 0046) was keyed to the
-- client entity: `idx_services_one_operator` (0068) allowed ONE operator
-- service per entity, and every reader priced a seat by its entity. Seats are
-- already many-per-entity (0089, 0090), so a client's second Operator seat
-- could only share the first seat's price and rail: authoring its price would
-- overwrite the first seat's, and its checkout would charge the first seat's
-- amount.
--
-- The commercial line stays the SERVICE. A seat (an operator `subscriptions`
-- row) points at its service through `subscriptions.service_id` (0068).
-- Several seats may share one service (SMD's own $0 seats do). Price, rail,
-- label, checkout and billing controls all resolve seat -> service.
--
-- Three changes:
--
--   * drop the one-operator-service-per-entity index;
--   * add `services.label`, a Captain-authored, client-visible line name
--     (for example "Marketing Operator"); NULL means "the Operator";
--   * backfill `subscriptions.service_id` on operator rows where it is NULL
--     and the entity has EXACTLY ONE operator service. An entity with none,
--     or (impossible before this migration) several, is left unlinked rather
--     than guessed; the services list's drift strip names those seats.
--
-- Ordering is safe: deploy.yml applies migrations before the worker deploys.
-- The old worker reads services by entity, which still returns the one row
-- each entity has until a second operator service is authored, and that can
-- only happen from the new worker.
--
-- Manual-only rollback at migrations/rollbacks/0126_operator_service_per_seat_down.sql
-- (read its precondition first).

DROP INDEX IF EXISTS idx_services_one_operator;

ALTER TABLE services ADD COLUMN label TEXT;

UPDATE subscriptions
   SET service_id = (
         SELECT s.id FROM services s
          WHERE s.entity_id = subscriptions.entity_id
            AND s.org_id = subscriptions.org_id
            AND s.type = 'operator'
       ),
       updated_at = datetime('now')
 WHERE product_slug = 'operator'
   AND service_id IS NULL
   AND (
         SELECT COUNT(*) FROM services s
          WHERE s.entity_id = subscriptions.entity_id
            AND s.org_id = subscriptions.org_id
            AND s.type = 'operator'
       ) = 1;
