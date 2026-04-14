-- Migration 0019: Portal redesign foundation
--
-- Adds consultant attribution and next-touchpoint fields to engagements so the
-- client portal can render a named human and concrete next event on every
-- surface. See issue #360 and .stitch/portal-ux-brief.md.
--
-- Invoices table already exposes amount (REAL), status, and paid_at — the
-- engagement ledger helper (src/lib/portal/ledger.ts) derives paid /
-- remaining / next-charge figures from those columns. No schema change
-- required on invoices for this migration.

ALTER TABLE engagements ADD COLUMN consultant_name TEXT;
ALTER TABLE engagements ADD COLUMN consultant_photo_url TEXT;
ALTER TABLE engagements ADD COLUMN consultant_role TEXT;
ALTER TABLE engagements ADD COLUMN consultant_phone TEXT;
ALTER TABLE engagements ADD COLUMN next_touchpoint_at TEXT;
ALTER TABLE engagements ADD COLUMN next_touchpoint_label TEXT;
