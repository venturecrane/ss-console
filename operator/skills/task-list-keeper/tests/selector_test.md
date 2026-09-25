# Selector test: task-list-keeper

Asserts Hermes' skill selector picks `task-list-keeper` for "our task list is full of stale overdue tasks, clean it up", not `deadline-miss-escalator` (the alarm on a slipping date) or `stalled-matter-nudge` (a matter gone quiet).

## Synthetic query

> Half the overdue tasks on our list are already done or duplicates. Go through them and tell each attorney what can be closed.

## Expected selection

`task-list-keeper`

## Why the adjacency is clean

- **vs. `deadline-miss-escalator`:** the escalator raises ONE date that is near or overdue and unhandled, to a person, until it is resolved. The keeper sorts the whole overdue list and proposes what to close, keep or hand over. "This date is slipping" goes to the escalator; "this list is not honest" goes to the keeper. The two share the casework ledger so a task the keeper holds drops out of the digest.
- **vs. `stalled-matter-nudge`:** stalled is about a matter with no activity; the keeper is about tasks whose record already shows them done, stale or duplicated.

## Result

Pending: to be verified with the blind cross-skill selector simulation the other law skills record, before the pilot's runtime proof.
