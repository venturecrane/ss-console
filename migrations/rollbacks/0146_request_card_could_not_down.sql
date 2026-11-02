-- Rollback for 0146: drop operator_request_cards.could_not.
-- Roll ss-web back first: a Worker that still writes the column fails its
-- INSERT once it is gone. The cards survive; only the NOT FULFILLED marker on
-- the ledger is lost (the emails already sent keep it).
--
-- Manual-only; coordinate with Captain.

ALTER TABLE operator_request_cards DROP COLUMN could_not;
