-- Rename client_id → entity_id in all tables that reference entities.
--
-- The entity-context architecture (migration 0008) introduced the `entities`
-- table to replace `clients`. Application code was updated to use `entity_id`
-- but the column rename in existing tables was never migrated.
--
-- SQLite 3.25.0+ supports ALTER TABLE ... RENAME COLUMN.

ALTER TABLE contacts RENAME COLUMN client_id TO entity_id;
ALTER TABLE assessments RENAME COLUMN client_id TO entity_id;
ALTER TABLE quotes RENAME COLUMN client_id TO entity_id;
ALTER TABLE engagements RENAME COLUMN client_id TO entity_id;
ALTER TABLE invoices RENAME COLUMN client_id TO entity_id;
ALTER TABLE follow_ups RENAME COLUMN client_id TO entity_id;
