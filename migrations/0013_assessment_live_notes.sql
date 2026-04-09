-- Add live_notes column to assessments table.
--
-- Stores auto-saved notes from the assessment detail page during
-- the live call. Pre-filled from booking notes if present.

ALTER TABLE assessments ADD COLUMN live_notes TEXT;
