-- Rollback for 0117_client_obligations.sql.
--
-- WHAT BREAKS IF YOU RUN THIS WHILE THE CODE IS STILL DEPLOYED: the admin
-- client page's obligations section and /admin/obligations both query
-- client_obligations and will 500; scripts/ci-reconcile-obligations.ts will
-- exit 1 (cannot evaluate) on its first read, which fails its workflow loudly
-- rather than silently — that is the intended failure shape.
--
-- DATA LOSS: every captured obligation is destroyed. Imported rows regenerate
-- on the next reconcile run from their sources; CAPTURED rows (letter-derived)
-- do not — their only other record is the local append-only journal at
-- ~/.claude/ss-obligation-journal/. Export before running this:
--   wrangler d1 execute ss-console-db --remote --json \
--     --command "SELECT * FROM client_obligations WHERE origin = 'captured'"
--
-- Order matters: client_obligations references reconcile_runs.

DROP INDEX IF EXISTS idx_client_obligations_closed;
DROP INDEX IF EXISTS idx_client_obligations_origin;
DROP INDEX IF EXISTS idx_client_obligations_due;
DROP INDEX IF EXISTS idx_client_obligations_open;
DROP TABLE IF EXISTS client_obligations;

DROP INDEX IF EXISTS idx_reconcile_runs_started;
DROP TABLE IF EXISTS reconcile_runs;
