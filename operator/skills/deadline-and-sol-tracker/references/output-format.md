# Deadline and SOL Tracker - Output Format

The firm-internal surface: authored critical dates by matter, grouped by proximity bucket, each one sourced and labeled. Internal only - never sent to a client.

## The scan output

```markdown
# Critical Dates - YYYY-MM-DD

**Scan window:** <N> days &nbsp; **Near window:** <K> days &nbsp; (firm-authored; defaults stated explicitly when unset)
**Matters scanned:** <M> | **Overdue:** <O> | **Imminent:** <I> | **Upcoming:** <U> | **Missing expected:** <X>

## Overdue - past, matter still open

### matter <id> - <client>

- **<date>** - <source label> (from <calendar-binding entry | Smokeball task due_date>)

## Imminent - within <K> days

### matter <id> - <client>

- **<date>** - <source label> (from <source>)

## Upcoming - within <N> days

### matter <id> - <client>

- **<date>** - <source label> (from <source>)

## Missing where expected - needs human attention

- matter <id> - <client> (<practice area>): firm policy expects an SOL date; **none authored on file.** Surfaced, not filled.

## Plain calendar (not deadlines)

- matter <id> - <client>: <date> "<entry title>" - a calendar entry with no deadline semantics; shown as-is, not promoted to a deadline.
```

Each date line also carries its **provenance**. A human-authored date is shown settled, as above. An **engine-computed** date is shown with the unconfirmed marker:

```markdown
- **<date>** - <source label> (from the Smokeball court-rules engine) - **unconfirmed: confirm with the responsible attorney**
```

## Confirmation memo (`create_memo` body, written ONLY on attorney confirm of an engine-read date)

```markdown
# Deadline confirmed - <matter number> - <source label>

**Confirmed by:** <responsible attorney full name> (from `get_staff` on `personResponsibleStaffId`)
**Confirmed at:** <ISO-8601 timestamp, e.g. 2026-07-14T16:32:05Z>
**Confirmed date:** <the engine date the attorney confirmed>
**Source:** Smokeball court-rules engine
```

The four fields are mandatory. The `Source` is always `Smokeball court-rules engine` for this skill (it never computes or proposes a date, so it never records `proposed by Operator`). Nothing is written before the attorney confirms, and the memo is bookkeeping only - this skill does not calendar.

## Source labels

Each date carries the label the human authored, never one the skill inferred: `court-date`, `filing-deadline`, `sol`, `response-window`, `task-deadline`. A `sol` label appears only when the human marked the date as an SOL - the skill never classifies a date as an SOL on its own.

## Rules

1. **Every date traces to a read** (a Smokeball task `due_date`, a calendar-binding entry, or a court-rules-engine entry) with its source label and provenance. No computed dates - ever; reading the engine's already-computed number is not computing one. (Invariant #1; the cardinal line.) **Engine-computed dates are shown unconfirmed** with "confirm with the responsible attorney," never as settled; the confirmation memo (name, ISO-8601 timestamp, date, source) is written only on the attorney's confirm, never before, and the skill writes no calendar entry.
2. **Buckets are date arithmetic only** - authored date vs. the run date. No arithmetic that produces a deadline.
3. **A bare calendar entry is a plain calendar date,** shown in the "Plain calendar" section - never promoted to a deadline because the context looks deadline-ish.
4. **Missing-where-expected points at the gap;** it never supplies a plausible date. The line says "none authored on file," not a guessed date.
5. **Internal surface only.** This output goes to the firm's internal surface. Client-facing date communication is a separate, reviewer-sent concern and is out of this skill's scope.
6. The header **states the windows used** (and whether they are firm-authored or defaults) so the reader knows the scan's bounds.
