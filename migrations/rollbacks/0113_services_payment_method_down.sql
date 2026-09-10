-- Rollback for 0113: drop the retainer payment-method column.
-- Any client authored as 'card' reverts to ACH-only checkout; the code
-- must be rolled back to a commit before 0113 first or it will select a
-- column that no longer exists.
ALTER TABLE services DROP COLUMN payment_method;
