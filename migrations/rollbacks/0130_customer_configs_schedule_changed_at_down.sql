-- Rollback for 0130: drop customer_configs.schedule_changed_at. NOT fail-soft:
-- the projection SQL (scripts/project-customer-config.ts) names the column, so
-- roll the code back to a commit before 0130 first, or the next customer.yaml
-- sync fails. work-health falls back to synced_at once the column is gone only
-- after its own code is rolled back too (its SELECT names the column).
--
-- Manual-only; coordinate with Captain.

ALTER TABLE customer_configs DROP COLUMN schedule_changed_at;
