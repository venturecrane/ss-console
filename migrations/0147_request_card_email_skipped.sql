-- 0147: a request card can be recorded without an email.
--
-- The Captain, 2026-11-02: the ops mail is mostly noise. In the 50 most recent
-- [SMD Ops] threads, 25 said a test seat's request got its first reply on time.
-- A card that says nothing went wrong, on a seat whose customer.yaml says it is
-- not a customer (proving, preprod, internal, sandbox), is now ledgered and not
-- emailed. A customer seat's cards still all email: the Captain asked for one
-- on every request (2026-10-27, #3555).
--
--   0  emailed, or owed an email (status says which)
--   1  deliberately not emailed: status stays 'pending' because nothing was
--      sent, and the reader must not call that NOT EMAILED
--
-- A plain ADD COLUMN: every existing row stays valid (0). Ordering is safe:
-- deploy.yml applies migrations before the worker deploys.
--
-- Manual-only rollback at migrations/rollbacks/0147_request_card_email_skipped_down.sql.

ALTER TABLE operator_request_cards ADD COLUMN email_skipped INTEGER NOT NULL DEFAULT 0;
