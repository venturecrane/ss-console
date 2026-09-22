# Stalled Matter Nudge - Algorithm

Source of truth for catching genuine drift without crying wolf.

## Recency

Smokeball's matter resource carries a **first-class last-modification recency signal**: `list_matters(updatedSince=…)` filters server-side and `LastUpdated` is returned per matter (and on tasks/balances). The trigger reads the matter's own modification time directly - there is no field-widening to wait on and no proxy to stitch together. This is the primary clock:

```
last_activity = matter.LastUpdated      # read directly from Smokeball, not inferred
candidate     = (today − last_activity) > window
```

Implementation: `list_matters(updatedSince = today − window)` returns the matters touched **within** the window; the stalled candidates are the open matters in the complement (not returned). The firm's authored staleness window (per practice area if set) is what `updatedSince` is set to.

**What `LastUpdated` means.** It is last-record-modification - an in-app edit, a task change, or a memo bumps it. For a drift scan that is exactly the right definition of "this matter saw activity": a matter no one has touched in the window is the matter that has drifted. The signal is read from the record, never computed, so the surfaced list states a sourced date with no implied precision beyond "last modified."

The calendar/task reads below are **not** the recency clock - they are the waiting-vs-stalled refinement. Appointment recency comes from the mail/calendar binding (Google/M365); Smokeball `list_tasks` supplies open tasks + their `due_date` for the waiting filter.

## The waiting-vs-stalled filter (specificity)

A candidate is **not** actually stalled if it is legitimately waiting:

- It has an **open `list_tasks` task with a future `due_date`** (awaiting a response, a court date, a filing window), or a future appointment on the calendar binding.
- It is explicitly in a wait-state the firm records (e.g., "awaiting USCIS receipt").

These matters are quiet **on purpose** - flagging them as stalled is a false positive that erodes trust in the list. Only surface matters that are quiet **without** a pending reason.

## The conflict-hold gate

A candidate matter on CONFLICT-HOLD is surfaced **separately** - it needs human conflict clearance, not a client follow-up. Draft no client-facing follow-up for it.

## Surface + draft

- **Surface** (autonomous): the genuinely-stalled matters, each with last-activity date + days-quiet.
- **Draft** (review): a neutral, light follow-up per stalled matter. It says, in effect, "we want to keep this moving; is there anything you're waiting on us for?" - it **surfaces and offers to reconnect**. It never states or advises the next legal step. Deciding what the matter needs is the lawyer's call.

## What this algorithm is NOT

- Not a decider (flags, never prescribes the next legal step).
- Not a wolf-crier (legitimately-waiting matters are filtered out).
- Not a fabricator (recency from real timestamps).
- Not a sender (drafts only); not a nudger of held matters.
