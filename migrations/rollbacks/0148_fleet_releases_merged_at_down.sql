-- Manual-only rollback of 0148. Drops the merge time; the Fleet page's merge to
-- seat card then fails its query and says so, and `fleet build` fails its
-- insert, so run this only with the code revert.
ALTER TABLE fleet_releases DROP COLUMN merged_at;
