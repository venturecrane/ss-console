-- Post-migration verification for 0011_booking_tables.sql
--
-- Run this AFTER applying migration 0011 against any environment.
-- Every check should return zero rows or the expected count.
-- Abort cutover if any check fails or returns unexpected output.
--
--   wrangler d1 execute ss-console-db --remote --file=scripts/verify-migration-0011.sql
--
-- Note: D1 blocks PRAGMA statements from user SQL (SQLITE_AUTH), so the
-- traditional foreign_key_check / integrity_check are not available here.
-- Equivalent referential checks are done via SELECT below.

-- 1. No assessment row may carry an unknown status.
--    Returns rows on failure.
SELECT 'INVALID assessments.status' AS error, id, status FROM assessments
  WHERE status NOT IN ('scheduled', 'completed', 'disqualified', 'converted', 'cancelled');

-- 2. The new partial unique index must exist.
SELECT 'MISSING uniq_assessments_scheduled_at_active' AS error
  WHERE NOT EXISTS (
    SELECT 1 FROM sqlite_master
    WHERE type = 'index' AND name = 'uniq_assessments_scheduled_at_active'
  );

-- 3. The original index must still exist after the rewrite.
SELECT 'MISSING idx_assessments_org_status' AS error
  WHERE NOT EXISTS (
    SELECT 1 FROM sqlite_master
    WHERE type = 'index' AND name = 'idx_assessments_org_status'
  );

-- 4. All five new tables must exist.
SELECT 'MISSING table: ' || expected.name AS error
  FROM (
    SELECT 'integrations' AS name UNION ALL
    SELECT 'oauth_states' UNION ALL
    SELECT 'assessment_schedule' UNION ALL
    SELECT 'booking_holds' UNION ALL
    SELECT 'availability_blocks'
  ) AS expected
  WHERE NOT EXISTS (
    SELECT 1 FROM sqlite_master
    WHERE type = 'table' AND name = expected.name
  );

-- 5. quotes(assessment_id) FK must still resolve. Catches the case where the
--    table rewrite somehow broke the cross-table reference.
SELECT 'ORPHAN quote ' || q.id || ' references missing assessment ' || q.assessment_id AS error
  FROM quotes q
  WHERE q.assessment_id IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM assessments a WHERE a.id = q.assessment_id);

-- 6. Snapshot of assessments column structure.
--    Compare manually against pre-migration to confirm column preservation.
SELECT name, type, "notnull", dflt_value FROM pragma_table_info('assessments');

-- 7. Row count snapshot — compare to pre-migration value (should be unchanged).
SELECT COUNT(*) AS assessment_count FROM assessments;
