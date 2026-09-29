# Date Prep Brief: the decision catalog

The CLOSED set of steps a brief may offer. `catalog.py` derives them from facts;
this page says the same thing in prose, and the two change together. The firm turns
each step on at a level in `case_manager.date_prep.steps` (validated by
`src/lib/operator/customer-yaml/sections-case-manager.ts`). A step the firm did not
list is never derived. A step at `surfaces` is never offered.

| Step (`catalog_id`)         | Routine that runs it         | Offered when (the `basis`)                                                                                                                                                                                                                                 | `params`                                                                |
| --------------------------- | ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| `binder_assemble`           | `trial-binder-assembler`     | the binder was never assembled on this matter, or its last `[Operator]` binder memo predates the newest file                                                                                                                                               | none                                                                    |
| `witness_list_finalize`     | `trial-binder-assembler`     | a file named like a witness list exists and every such file is a draft                                                                                                                                                                                     | `file_id` of the newest draft                                           |
| `exhibit_list_finalize`     | `trial-binder-assembler`     | a file named like an exhibit list exists and every such file is a draft                                                                                                                                                                                    | `file_id` of the newest draft                                           |
| `records_refresh:<task id>` | `medical-records-chaser`     | `mode: chase`: an OUTSTANDING roster provider never chased, or last chased at least the chaser's authored cadence ago. `mode: update`: a RECEIVED provider whose newest record on file is older than `records_stale_days` and who has not been asked since | `roster_task_id`, `provider`, `mode`, and `newest_record` for an update |
| `motion_calendar_refresh`   | `motion-calendar-tracker`    | the motion calendar never ran on this matter, or its last run predates the newest file                                                                                                                                                                     | none                                                                    |
| `discovery_status_refresh`  | `discovery-response-tracker` | the discovery tracker never ran on this matter, or its last run predates the newest file                                                                                                                                                                   | none                                                                    |

Every entry's `params` also carries `job` (below).

## Every entry names its job

Several steps share one routine (the trial binder routine assembles the binder AND
finalizes the lists), so the routine alone cannot say what a yes asked for. Every
entry's `params` carries `job`, one of a closed set (`catalog.py` `JOB_SKILLS`), and
the routine's SKILL.md has a `### Job: <job>` section for each job it owns (pinned by
the tests). A step runs exactly that section, nothing else from the routine. A routine
with no section for the job runs nothing and says so plainly.

| `job`                      | Step                       | Routine                      | What it produces                                                                                                                  |
| -------------------------- | -------------------------- | ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `assemble_binder`          | `binder_assemble`          | `trial-binder-assembler`     | the trial binder index from the authored components, staged for the attorney to finalize. Collates; never authors.                |
| `finalize_witness_list`    | `witness_list_finalize`    | `trial-binder-assembler`     | a finalized witness list from the named draft (`file_id`), staged in the matter for review and filing. No binder.                 |
| `finalize_exhibit_list`    | `exhibit_list_finalize`    | `trial-binder-assembler`     | a finalized exhibit list from the named draft (`file_id`), staged in the matter for review and filing. No binder.                 |
| `chase_records`            | `records_refresh` (chase)  | `medical-records-chaser`     | the chase for that one outstanding roster provider, on the chaser's own cadence rules and voice.                                  |
| `request_updated_records`  | `records_refresh` (update) | `medical-records-chaser`     | a request for records dated after `newest_record`: at `prepares` a draft on the matter, at `handles` sent under the send ceiling. |
| `refresh_motion_calendar`  | `motion_calendar_refresh`  | `motion-calendar-tracker`    | the tracker's own pass on this matter, with its own memo.                                                                         |
| `refresh_discovery_status` | `discovery_status_refresh` | `discovery-response-tracker` | the tracker's own pass on this matter, with its own memo.                                                                         |

Each job runs under its routine's rules, content ceiling and exposure ceilings. The
catalog adds no instruction of its own.

## The question's answer carries into the step

A brief's question often asks the attorney to confirm a fact before the step can run
("The draft notes the final figures need confirming. Are they confirmed? If yes, I'll
finalize the list."). The yes IS that confirmation. The step proceeds on it, the
finalized document drops the pending-confirmation note the draft carried, and the reply
never asks the attorney for the same confirmation again. The confirmation extends only
as far as the question's own words: a yes to "are the figures confirmed" confirms the
figures already in the draft; it does not supply a figure the draft does not hold. A
change the attorney wrote in the reply ("take the second witness off") is applied as
written. Nothing is attributed to anyone that their reply does not say.

## The reply after a step

The step's report never restates the approval: the reply already opens with code's
line naming what was approved ("Got it. I'll prepare 1 (...)"). The report starts with
what was done and where it is in the matter ("The final exhibit list is in the matter
as <file name>."). No to-do list for the attorney unless
the job hit a real gap it must name (a draft that could not be read, a blank the draft
leaves open).

## Levels

- `surfaces`: not in the catalog. The brief may mention the fact; it offers nothing.
- `prepares`: offered as a numbered decision. On a yes the routine runs and produces a
  draft for a person.
- `handles`: run by the brief's turn before it writes the brief, and reported as a done
  line. Not offered as a decision.

## Not offered, on purpose

- **An update with no threshold.** Without an authored `records_stale_days` no
  received provider is offered an update: how old is too old is the firm's call. A
  provider whose newest record cannot be found by name on file is unknown, not stale.
- **Anything computed.** No step derives a deadline or a date.
