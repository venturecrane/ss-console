# Shared: Write Posture (PI-litigation pack)

Every skill that writes to Smokeball must follow this. It encodes what the
connector surface (`operator/verticals/law-firm/smokeball-surface.md`) actually
guarantees today — which is: **not much is verified against a live tenant.** Fix the
posture here; every skill inherits it.

## 1. ALL writes are unverified-at-connect — confirm by read, never assert success

`smokeball-surface.md` marks the write bodies — `create_task`/`update_task`,
`create_event`/`update_event`, `create_folder`, `add_file`/`delete_file`, and the
`create_memo` body field — as **UNVERIFIED against a live tenant** ("re-confirm ALL
writes at the A&P prod connect"). Since then `create_folder` and `add_file` have
delivered sixteen chronology packages into the A&P production tenant (August 2026,
runner-side through the connector), and the task DTO was verified on prod
2026-08-31 (`POST /tasks` is 202-async - an immediate read 404s, so a confirming
read must retry; `update_task` needs `staff_id` because the vendor PUT is a full
replace). The earlier staging 403 is history, not a live constraint; the rule
below still governs every agent-side write.

So the rule is uniform, not scoped to one write:

- A write is only reported as done **after a confirming read** shows it landed
  (`list_tasks`/`get_task` after `create_task`; `get_files_on_matter` after
  `add_file`; `list_folders` after `create_folder`). A memo is the exception:
  `create_memo` reads its own memo back by id and returns `confirmed`. Report a
  memo as logged only when `confirmed` is `true`. On `"unknown"`, never write it
  again (it very likely exists); report its id as unconfirmed. On `false`,
  surface the failure. Do NOT call `get_memos_on_matter` to confirm a memo: on a
  scheduled scan across matters the seat refuses every memo read after the
  first matter, and each refusal counts toward the seat's stop brake.
- If the confirming read does not show it, the correct output is **surface the
  failure** ("the draft is in the matter but I could not confirm the review task was
  created"), never a Shape that asserts the action completed.
- **Escalation** covers a failure of ANY write (routing task, log memo, calendar,
  file, folder) — not just the staging write.
- This is the same fail-closed discipline across the pack: never assert an
  unconfirmed write.

## 2. `create_task` requires `staffId` + `dueDateOnly` — and the date must not cross the deadline lane

The surface pins `TaskDto` as requiring **`staffId`** and **`dueDateOnly`**. A skill
that opens a confirm/review/routing task must supply both. But most pack skills are
forbidden to assert a legal deadline. Resolve it explicitly:

- The task's `dueDateOnly` is a **near-term administrative "confirm-by" date**
  (e.g. 1-2 business days out) — the date by which a human should act on the
  surfaced item — and it is stated in the task body as such, **distinct from any
  discovery/response/court deadline** (which stays with the deadline lane, presented
  for attorney confirm, never silently calendared).
- `staffId` is the responsible staff resolved from the matter
  (`personResponsibleStaffId`) or the routing target.

## 3. No move / no delete of a document the firm did not direct

There is **no move tool** in the surface. "Filing" or "routing" a document is
**in-place**: point the review task at the document where it already sits. Never use
`delete_file` (destructive, banned) or an `add_file` copy to "move" a document, which
would duplicate it. Before re-staging an input, read `get_files_on_matter` and
skip/surface if it is already present (avoid duplicate drops into the drafting
folder). `add_file` never overwrites: a superseding file is uploaded first and the
prior one removed by id only after the read-back confirms the new one (the August
2026 delivery posture); a skill never deletes.

Calendar events are deleted only as an act an Operator administrator confirms,
and never by a skill on its own initiative. When an administrator asks, call
`prepare_event_deletion` for the matters, then `delete_events` with the `events`
it returned, unchanged (at most 50 per act). The seat withholds the delete and
returns an `[act ...]` line listing every event; put it in the reply verbatim.
Only the administrator's yes performs it. The result reads each event back and
reports `deleted`, `pending` (accepted, not yet applied: never delete again),
`skipped` with the reason (changed, moved, or already gone since the proposal)
and `failed`. Recurring events are never deleted.

## 4. `create_memo` is the audit log — but it too can fail

The internal `create_memo` (the audit/training-output record) has an ASSUMED body
schema. A failed memo means the action has no logged record even though a human may
already have the surfaced note. Treat it under rule 1: act on the `confirmed` field
the write returns; do not assume the log persisted.

## 5. One file note per routine per matter, plain and headed

Every memo a skill writes (`create_memo`) is a **file note** in one shape, the same in
every skill's `references/output-format.md`:

```
[Operator] <Routine name> as of <localDate>
<one line: what it found>
<one line: what a person needs to do, or "Nothing to do.">
```

- **One note per routine per matter.** The note opens with the header line.
  `<Routine name>` is this skill's label in the seat's `routine_names` map (the
  firm's own words for the routine); `<localDate>` is today's date in the firm's time
  zone, written the way the firm writes a date (Sep 29, 2026). Call `create_memo`
  every run; never look for the old note first. The connector finds the routine's
  latest `[Operator]` note on the matter by that header's routine name and updates it
  in place, so the matter holds one note per routine:
  - **Nothing changed:** only the note's "as of" date moves to today, and the result
    says `unchanged: true`. That is a success; report it as "checked, nothing new",
    never as a failure and never by writing again. A date that stops moving is how a
    person sees the routine stopped.
  - **Something changed:** the note's lines are replaced with the new ones, and the
    note keeps a short `Previously (<date>): <old first line>` trail (the last three)
    so the firm can see what it said before. The result says `updated: true`.
  - **No note yet:** a new one is written (`created: true`).
  - **Dedup keys a skill's own output-format names** (`fileId <id> recorded`, the
    `op-mmou:` change key, the chronology's `Package job: ...; covered document ids:`
    line) are kept on every update, so a later run still finds them in the note.

  A note with no header is written as a new note every time; use that only for a
  one-off record, never for a routine. `update_memo(matter_id, memo_id, text)`
  replaces a particular note whose id you hold, under the same rules; a routine never
  needs it.

- **Plain text.** No `>` quote marks, no `**`, no `#`, no tables, no bullets inside
  the note. At most 15 lines. Anything longer, and any table or list of exhibits or
  witnesses, is a Word document filed with `render_docx_draft`, and the note names
  the file. The connector enforces this: it removes `#`, `>`, `**`, backticks and
  bullet marks, numbers a list 1, 2, 3, and refuses a note holding a table with that
  remedy.
- **Times and dates are the firm's.** A court time is the event's `localTime` on its
  `localDate` ("Oct 6 at 9:30 a.m."), a task's day is its `localDueDate`; never a
  `startTime` or `dueDate`, which are UTC.
- **The matter by its number**, never an internal id, except a dedup key a skill's
  own output-format names (for example `fileId <id> recorded`), which stays exactly
  as that skill writes it, alone on its own line, because a later run reads it back
  and the seat passes a machine-marker line only when it stands alone.
- The training note (`_shared-training-output.md`) rides in the two lines: what and
  why in the first, what next and when to bring in the attorney in the second.
