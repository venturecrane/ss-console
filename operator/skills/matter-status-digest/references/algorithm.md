# Matter Status Digest - Algorithm

The rules that make the digest accurate and honest about Smokeball's limits.

## Sectioning

1. **Open by stage.** Bucket `list_matters(status=Open)` by Smokeball stage. Stage is resolved via the stage-model join - `matterTypeId` → stage sets (`get_stage_sets`) → stage-to-matter mappings (`get_stage_to_matter_mappings`) - not read off a flat field on the matter. Counts at the top so the principal sees the shape before the detail.
2. **Upcoming.** Per matter: `list_tasks` with a near `due_date` (Smokeball), and calendar appointments from the mail/calendar binding (Google/M365) in the window. Sourced dates only - a matter with no scheduled date shows none, never an invented one.
3. **Quiet.** Reuse `stalled-matter-nudge`'s recency: Smokeball's native `last_activity` (`updatedSince`/`LastUpdated`) floored by the latest calendar-entry end time. Past the firm's window AND not legitimately waiting (an open task with a future `due_date` = waiting on purpose, not quiet). The recency signal is first-class in Smokeball (no field-widening caveat), shared with the nudge skill.
4. **Low trust.** Per matter, the native `get_matter_balances` `availableBalance` vs. the firm's authored floor. Trust is NOT AR - `get_matter_billing_config`/`get_fees`/`get_expenses` is AR; do not conflate. Read-only: the flag never triggers a fund movement (`create_transaction`/`protect_funds`/`unprotect_funds` are never invoked).
5. **Held.** Conflict-hold matters in their own section - they need human clearance, not a status line.

## Field provenance (native in Smokeball)

`personResponsibleStaffId` (responsible attorney) and the last-activity signal (`updatedSince`/`LastUpdated`) are first-class Smokeball fields - the Clio-era field-widening caveat is gone. The attorney column is populated directly; the quiet flag uses native recency. Stage still requires the explicit join above (it is the one read that is not a flat field). Never fabricate an attorney, stage, or recency the read does not supply.

## What this algorithm is NOT

- Not a decider - flags state, never prescribes the legal next step.
- Not a fabricator - every stage, date, and balance is sourced; gaps show as gaps.
- Not a trust-mover - the low-trust flag reads `availableBalance` only.
- Not client-facing - internal digest for the principal.
