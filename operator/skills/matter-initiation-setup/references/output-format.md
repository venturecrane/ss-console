# Matter Initiation Setup - Output Format

The decision determines the shape. Every setup is keyed to the matter and its type. No
shape ever states a computed SOL date, a service date, or an invented folder taxonomy as
fact; dates belong to the attorney and the engine, and the folder/task convention comes
from authored config or is surfaced to confirm.

## Shape A - Set up (structure created, deadlines scaffolded to confirm, writes confirmed)

```markdown
# Matter set up - <matter descriptor> - <matter type> - matter <matter number> - YYYY-MM-DD

**Decision:** created the standard setup for <matter type> from the authored convention; each write confirmed by a follow-up read. Deadlines scaffolded as items to confirm, not computed.
**Setup source:** authored matter-setup convention for <matter type> (config mapping)

## Folders created (each confirmed via list_folders)

- <folder> / <subfolder> ...

## Opening tasks created (each confirmed via list_tasks / get_task)

- <task> - <staff name>, confirm-by <near-term admin date, distinct from any legal deadline>

## Deadlines scaffolded to confirm (NO date computed - attorney + engine own these)

- **Route SOL to <attorney> to compute** (task; title foregrounds the action, confirm-by is administrative, NOT the SOL date): inputs read - incident/accrual date <if read>, minor plaintiff <yes/no>, government defendant <yes/no>. Governing rule (reference, confirm at connect): CCP §335.1 and any modifier (§352 minor tolling, Gov. Code §911.2 claim gate, §340.5 MICRA). No date stated; confirm-by never SOL-derivable.
- **Route service window to <attorney>/engine to confirm**, one per named defendant (title foregrounds the action, confirm-by is administrative, NOT the service date): <defendant> [original complaint - 60-day | added by amendment - 30-day] - reference CRC 3.110(b) (confirm at connect) + general forward final-day roll CCP §12 / §12a (NOT the Discovery Act's §2016.060). Proposed for attorney/engine confirm; not calendared.

## File note (create_memo)

[Operator] <Routine name> as of <localDate>
Matter <matter number> (<type>) set up: <N> folders and <M> tasks created and confirmed; the limitations date and <K> service items are set out for confirmation. No date computed; nothing filed or served.
<Attorney> to confirm the limitations date and the service items.
```

## Shape B - Filing package staged (on request)

```markdown
# Filing package staged - <matter descriptor> - matter <matter number> - YYYY-MM-DD

**Decision:** collated the venue filing-package documents the matter holds into <folder>; each placement confirmed via get_files_on_matter. Surfaced for <attorney> to file. Not filed, not served.
**Package (each read from the matter):** <complaint / summons / civil case cover sheet CM-010 / ...>
**Package completeness:** <complete as far as read | ⚠ apparently incomplete - <summons / CM-010 / ...> not found on the matter; staged what is present and surfaced the gap; missing form NOT generated - confirm before filing>

## File note (create_memo)

[Operator] <Routine name> as of <localDate>
Filing package for <venue> collected into <folder> on matter <matter number>; every document confirmed in the matter. Nothing filed or served.
File the package through the firm's filing path.
```

## Shape C - Surface to a human (convention unknown, write unconfirmed, party unresolved, special-timeline flag)

```markdown
# ⚠ Matter setup - needs a human - <matter descriptor> - matter <matter number> - YYYY-MM-DD

**Situation:** <setup convention for this matter type not established - proposing a structure to confirm | a write did not confirm (create_folder/create_task/add_file/create_memo error or no confirming-read match) | defendant roster or a party status cannot be resolved | government defendant or minor plaintiff present - SOL timeline is not the default>
**Decision:** surfaced for a person. Nothing asserted as created; no date computed. This is a judgment the skill does not make on its own.
**Proposed (if applicable):**

- Folders: <proposed structure> - confirm this is your convention before I write.
- Tasks: <proposed opening tasks> - confirm before I open them.
- SOL: inputs captured (<incident date / minor / government flags>); the date is the attorney's and the engine's - confirm, I did not compute it.
```

## Rules

1. **No shape states a SOL date, a limitations date, or a service date as fact.** The
   skill scaffolds items to confirm and cites the governing rule as reference flagged
   confirm; the attorney and the certified engine own every date.
2. **No legal deadline is ever a calendar write.** Task `dueDateOnly` values are
   near-term administrative confirm-by dates, stated as such and distinct from the SOL /
   service deadline.
3. **Shape A is reachable only when the setup convention is authored AND every write is
   confirmed by a follow-up read.** An unauthored convention or an unconfirmed write is
   Shape C, never Shape A.
4. **A folder name or task is never stated as created in Shape A unless it came from the
   authored convention and a read confirmed it landed.** A guessed structure belongs in
   Shape C as a proposal, never as a completed setup.
5. **Shape B never files or serves, and never fabricates a form.** The package is staged
   in the matter and surfaced; a person files through the firm's filing path. If a
   standard component (complaint / summons / CM-010) appears absent, the shape flags the
   package as apparently incomplete rather than presenting a partial set as complete, and
   never generates the missing form.
6. The decision and its reason are always stated, so the setup is auditable.
