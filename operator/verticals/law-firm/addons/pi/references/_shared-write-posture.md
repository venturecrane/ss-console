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
  `add_file`; `list_folders` after `create_folder`; `get_memos_on_matter` after
  `create_memo`).
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

## 4. `create_memo` is the audit log — but it too can fail

The internal `create_memo` (the audit/training-output record) has an ASSUMED body
schema. A failed memo means the action has no logged record even though a human may
already have the surfaced note. Treat it under rule 1 (confirm or surface); do not
assume the log persisted.
