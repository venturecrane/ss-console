-- Add entity_id to contacts table.
--
-- Migration 0010 dropped client_id from contacts and added entity_id to
-- assessments, quotes, engagements, invoices, and follow_ups — but missed
-- contacts. The DAL (src/lib/db/contacts.ts) and the SignWell webhook
-- handler both reference contacts.entity_id, causing silent failures.

ALTER TABLE contacts ADD COLUMN entity_id TEXT REFERENCES entities(id);

CREATE INDEX idx_contacts_entity_id ON contacts(org_id, entity_id);
