# deadline-and-sol-tracker algorithm

Source of truth for surfacing critical dates without ever computing one.

## The read-only rule

Every date this skill surfaces is **read**, never computed - from a calendar-binding `list_calendar_entries` entry (court dates, hearings), a Smokeball `list_tasks` task `due_date`, or a court-rules-engine entry the engine posted into the matter. The skill performs exactly one kind of arithmetic: comparing a read date to today, to assign a bucket. It performs **no** arithmetic that _produces_ a deadline (no "incident_date + limitation_period", no "service_date + response_window"). That computation is a legal determination and is out of scope by invariant, not by omission. Reading the engine's already-computed number is not producing one.

## Provenance and the engine-confirm flow

Each read date carries a provenance: **human-authored** or **engine-computed**. The provenance is read from the entry (the engine's entries carry an identifiable source tag / category - a firm-configuration fact confirmed at connect); it is never guessed.

```
for each read_date d on an open matter:
    if provenance(d) == human_authored:
        surface d settled, with its authored source label   # unchanged behavior
    elif provenance(d) == engine_computed:
        surface d labeled "unconfirmed: confirm with the responsible attorney"
        on attorney_confirm(d):
            create_memo(matter, {
                confirmed_by: get_staff(matter.personResponsibleStaffId).name,  # full name
                confirmed_at: <ISO-8601 timestamp>,
                confirmed_date: d,
                source: "Smokeball court-rules engine",   # this skill never records "proposed by Operator"
            })
    else:  # provenance unknown (engine source tag not yet configured)
        surface d and ask which provenance governs; never treat an engine date as settled
```

The skill still computes **nothing**. It reads the engine's date, presents it unconfirmed, and on confirmation records who confirmed it and when. No confirmation memo is written before the attorney confirms, and the memo is bookkeeping only - this skill does not calendar.

## Buckets

```
today = run date (passed in; no wall-clock in scripts)
for each authored_date d on an open matter:
    if d < today and matter open:  bucket = overdue
    elif d <= today + near_window: bucket = imminent     # firm-authored near window, e.g. 7d
    elif d <= today + scan_window: bucket = upcoming      # firm-authored scan window, e.g. 30d
    else:                          omit (outside scan)
```

`near_window` and `scan_window` are firm-authored (from `customer.yaml` where set; otherwise sensible defaults that the surface header states explicitly).

## Source labels

Each date carries the label the human gave it: `court-date`, `filing-deadline`, `sol`, `response-window`, `task-deadline`. The label is read, never inferred. A bare calendar entry with no deadline semantics is surfaced as a plain calendar date, not promoted to a deadline.

## Missing-where-expected

```
if firm_policy.expects_sol(matter.practice_area) and no authored sol on matter:
    surface "no authored deadline on file - needs human attention"
```

This is the only place the skill speaks about a date that does not exist, and it speaks by **pointing at the gap**, never by filling it. `firm_policy.expects_sol(...)` is firm-authored configuration; absent that configuration, the skill does not guess which matters "should" have an SOL.

## Why the line is absolute

A computed limitation date that is wrong is a malpractice-grade error, and a computed date that is _right_ still launders a legal judgment through an automated surface. Either way the firm would come to rely on a number the system is not competent to produce. So the skill produces no such number - it is a mirror for authored dates and a flag for missing ones, nothing more.
