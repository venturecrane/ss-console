"""The staff reassignment: a litigation job kind that WRITES to the firm's
matters and tasks, with no model call.

What the firm asks for when a paralegal leaves or a new one starts (the first
client's office manager, 2026-10-15): "update all cases that were assigned to
<one or more people> to <one person> ... all of the tasks need to be updated as
well (litigation cases only)". The broker reads the email's shape in code
(``workspace_broker/litigation_reassign.py``); this runner resolves each name
against the practice system's staff list, moves each matching matter's Person
Assisting, lets the practice system move that person's open tasks itself, then
moves the tasks it did not, and reads every write back.

Execution class N (ADR 0050): reads and writes, $0. Ported rule for rule from
the hand run of 2026-10-15 (vfy_01M50G3CBSHTXJPK1XR55N6WH7,
vfy_01M50H3Z461FTKV2RSV7HKS5HN):

* a matter's paralegal is ``personAssistingStaffId``; ``PATCH /matters/{id}``
  with that SINGULAR field applies (the plural list is not written);
* when it changes, Smokeball moves that person's open tasks on the matter
  itself, within about a minute; the rest need ``PUT /tasks/{id}``, which is a
  FULL REPLACE built from a fresh read (``merge_task_update``), carrying
  ``categories`` when the task has any; subtasks survive it;
* a task PUT can take more than one read to show: every write is read back,
  re-sent on a mismatch in a bounded loop, and one that never converges is
  reported by matter number, never dropped.

Stages (``data/state.json``, resumable): staff, select, matters, settle,
tasks, report. A proof run stops before ``matters`` (the first write).
"""
