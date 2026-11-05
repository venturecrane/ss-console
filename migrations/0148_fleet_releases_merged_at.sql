-- 0148: an image release records when its change merged.
--
-- The Captain wants to see how many minutes a merged change takes to reach
-- every paying client's seat (merge to the LAST client seat done), per release
-- and as a weekly median, on the admin Fleet page. The rollouts already carry
-- when each seat finished; the release lacked the start of the clock.
--
--   merged_at  the merge commit's committer time (UTC, YYYY-MM-DDTHH:MM:SSZ),
--              read by `fleet build` from `git show -s --format=%cI HEAD`
--   NULL       every release built before this column, and every skills or
--              bundle row (their writers do not set it); the page renders a
--              missing value, never an invented one
--
-- A plain ADD COLUMN: every existing row stays valid. Ordering is safe:
-- deploy.yml applies migrations before the worker deploys, and only the new
-- build code names the column.
--
-- Manual-only rollback at migrations/rollbacks/0148_fleet_releases_merged_at_down.sql.

ALTER TABLE fleet_releases ADD COLUMN merged_at TEXT;
