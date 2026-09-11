-- Rollback for 0114: drop the per-tenant Machine credentials.
-- Every seat then authenticates with the shared MACHINE_HEARTBEAT_KEY again,
-- which must still be set on the Worker and staged on each seat; the code
-- must be rolled back to a commit before 0114 first or the verifier will
-- LEFT JOIN a table that no longer exists.
DROP INDEX IF EXISTS idx_machine_credentials_entity;
DROP TABLE IF EXISTS machine_credentials;
