-- Manual rollback for 0111. Drops the table only; the R2 objects it pointed
-- at are NOT removed here, because the bytes are the firm's own executed
-- paper and deleting them on a schema rollback would be unrecoverable.
-- Remove them deliberately from R2 if that is actually intended.

DROP INDEX IF EXISTS idx_operator_agreement_documents_instance;
DROP TABLE IF EXISTS operator_agreement_documents;
