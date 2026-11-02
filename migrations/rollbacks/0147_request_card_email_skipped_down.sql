-- Manual-only rollback of 0147. Drops the marker; every skipped card then reads
-- NOT EMAILED in .claude/bin/requests, so run this only with the code revert.
ALTER TABLE operator_request_cards DROP COLUMN email_skipped;
