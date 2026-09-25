# Date Prep Brief: the decision catalog

The CLOSED set of steps a brief may offer. `catalog.py` derives them from facts;
this page says the same thing in prose, and the two change together. The firm turns
each step on at a level in `case_manager.date_prep.steps` (validated by
`src/lib/operator/customer-yaml/sections-case-manager.ts`). A step the firm did not
list is never derived. A step at `surfaces` is never offered.

| Step (`catalog_id`)         | Routine that runs it         | Offered when (the `basis`)                                                                                   | `params`                      |
| --------------------------- | ---------------------------- | ------------------------------------------------------------------------------------------------------------ | ----------------------------- |
| `binder_assemble`           | `trial-binder-assembler`     | the binder was never assembled on this matter, or its last `[Operator]` binder memo predates the newest file | none                          |
| `witness_list_finalize`     | `trial-binder-assembler`     | a file named like a witness list exists and every such file is a draft                                       | `file_id` of the newest draft |
| `exhibit_list_finalize`     | `trial-binder-assembler`     | a file named like an exhibit list exists and every such file is a draft                                      | `file_id` of the newest draft |
| `records_refresh:<task id>` | `medical-records-chaser`     | an OUTSTANDING roster provider never chased, or last chased at least the chaser's authored cadence ago       | `roster_task_id`, `provider`  |
| `motion_calendar_refresh`   | `motion-calendar-tracker`    | the motion calendar never ran on this matter, or its last run predates the newest file                       | none                          |
| `discovery_status_refresh`  | `discovery-response-tracker` | the discovery tracker never ran on this matter, or its last run predates the newest file                     | none                          |

## What each step does when it runs

Each step runs its routine's own procedure (`/app/skills/<skill>/SKILL.md`) on the
brief's matter, under that routine's rules, content ceiling and exposure ceilings.
The catalog adds no instruction of its own.

- **Assemble the binder**: the trial binder index from the authored components, staged
  for the attorney to finalize. Collates; never authors.
- **Finalize the witness or exhibit list**: a finalized version of the named draft,
  staged for review. Entries come from the draft and the matter's documents only; a
  change the attorney answered ("drop Natarajan") is applied as the attorney said it.
- **Refresh records**: the records chaser's chase for that one roster provider, on its
  own cadence rules and voice.
- **Refresh the motion calendar / discovery status**: the tracker's own pass on this
  matter, with its own memo.

## Levels

- `surfaces`: not in the catalog. The brief may mention the fact; it offers nothing.
- `prepares`: offered as a numbered decision. On a yes the routine runs and produces a
  draft for a person.
- `handles`: run by the brief's turn before it writes the brief, and reported as a done
  line. Not offered as a decision.

## Not offered, on purpose

- **Records already received.** The chaser works its open roster only; "request an
  update of records already on file" is not a step any routine can run today, so
  offering it would promise one. The received provider still shows in the file status.
- **Anything computed.** No step derives a deadline or a date.
