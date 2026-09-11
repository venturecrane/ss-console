-- 0112: carry the CAUSE of a sticky stop, not just its level.
--
-- `sticky_stop_level` (0082) tells the fleet that a seat stopped. It cannot
-- tell anyone WHY, and four different meters drive that ladder — consecutive
-- tool failures, refusal cascade, runtime budget, cost threshold — each
-- needing a different investigation. On 2026-09-01 the ashton-price seat
-- stopped on a bad credential (8 consecutive mcp_smokeball_list_matters
-- failures) and the SEV1 page said "Cost breaker HARD_STOP"; the real cause
-- took a separate trip to the seat to learn.
--
-- The seat has recorded both fields on the transition all along
-- (sticky_stop_state.reason / .condition); the overlay now carries them on the
-- beat (hermes-smd-overlay#341) and these columns store them.
--
-- Both are NULLABLE and both overwrite every beat alongside sticky_stop_level,
-- including back to NULL: a cause that outlived the level it explained would
-- be worse than no cause at all.
--
--   condition — one of the ladder's enum values (consecutive_tool_failures,
--               refusal_cascade, time_budget_exceeded, cost_threshold,
--               captain_clear). Stable enough to group and route on.
--   reason    — the seat's own free-text counter line, capped seat-side at
--               300 chars. Operational text, never client content.

ALTER TABLE fleet_status ADD COLUMN sticky_stop_reason TEXT;
ALTER TABLE fleet_status ADD COLUMN sticky_stop_condition TEXT;
