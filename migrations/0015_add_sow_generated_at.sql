-- Migration 0013: Add sow_generated_at to quotes table
--
-- Supports OQ-006 (stale PDF warning): when the quote is modified after the
-- last PDF generation, the UI shows a warning banner prompting re-generation.
--
-- The column is nullable because most quotes have never had a PDF generated.

ALTER TABLE quotes ADD COLUMN sow_generated_at TEXT;
