-- Manual-only rollback of 0150. Drops the volume epoch; `fleet build` then fails
-- its insert and the rollout reads every release as epoch 1, so run this only
-- with the code revert.
ALTER TABLE fleet_releases DROP COLUMN volume_epoch;
