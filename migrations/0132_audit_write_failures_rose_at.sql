-- 0132: when a seat last lost an audit row, beside how many it ever has (2026-10-21)
--
-- THE GAP. audit_write_failures (0107) is cumulative by design: the seat-side
-- tally is one byte per lost row, never reset, so it answers "how many has this
-- seat ever lost". The shortfall email read it as if it were current and printed
-- "this seat reports N lost audit write(s), so this list may be short" under a
-- list of the last 24 hours. On 2026-10-21 every ashton-price shortfall carried
-- "22 lost audit write(s)" while its reason histogram was empty, which means no
-- row had been lost since reasons began to be recorded on 10-20: an old count
-- presented as a reason to doubt today's list.
--
-- This column is set by the heartbeat upsert to the ingest time whenever the
-- reported count rises above the stored one, and is otherwise kept. A falling
-- count (a volume reset) is not a loss and leaves it alone. NULL means no rise
-- has been seen since this column existed, never "no losses ever".

ALTER TABLE fleet_status ADD COLUMN audit_write_failures_rose_at TEXT;
