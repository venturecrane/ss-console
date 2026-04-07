-- Drop legacy client_id column and reconcile contacts schema with code.
--
-- The entity-context architecture (migration 0008) introduced the entities
-- table and an additive entity_id column on related tables. The legacy
-- client_id column was left in place as NOT NULL, blocking all INSERTs
-- since the application code only writes entity_id.
--
-- This migration:
--   1. Drops client_id from 5 tables (contacts, assessments, quotes,
--      engagements, invoices). follow_ups already had it dropped.
--   2. Drops the legacy contacts.notes column (replaced by contacts.role
--      semantically and not referenced by any code).
--   3. Adds the contacts.role column the application code expects.
--
-- Tables are empty for the affected columns — verified before migration.
-- ALTER TABLE DROP COLUMN requires SQLite 3.35.0+ (D1 supports this).

ALTER TABLE contacts DROP COLUMN client_id;
ALTER TABLE contacts DROP COLUMN notes;
ALTER TABLE contacts ADD COLUMN role TEXT;

ALTER TABLE assessments DROP COLUMN client_id;
ALTER TABLE quotes DROP COLUMN client_id;
ALTER TABLE engagements DROP COLUMN client_id;
ALTER TABLE invoices DROP COLUMN client_id;
