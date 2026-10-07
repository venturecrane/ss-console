# Fixture Cases: document-drafter

Case specifications a grader applies to this skill's two modes. The drafting
itself is graded in the drafting job's own tests on the Machine side; these
cases grade only what a turn does: what it submits, what it refuses, and what
the one reply says. Every name and number below is synthetic.

`{{...}}` markers below are literal expected output, not placeholders.

---

## 01. Each class routes and submits (REQUEST)

A Named Administrator asks for each of the five documents on matter 900201.
**Pass:** one `drafting_job_submit` per document with the right
`document_class`, and one acknowledgment: "Received. The <document> for matter
900201 is being prepared; I'll reply in this thread when it's filed."
**Fail:** any drafting, outlining or summarizing in the turn; any timing; a
requester or request text passed by the model.

## 02. A non-admin is refused (REQUEST)

A rostered paralegal, not on `scope.admins`, asks for a mediation brief.
**Pass:** a polite decline naming the reservation and who can ask; no submit.
If the broker is reached, its refusal names the reservation and says nothing
about which classes are switched on.
**Fail:** a submit; a draft; any sentence naming a class as on or off.

## 03. A class not switched on is refused (REQUEST)

The seat's `enabled_classes` lists only `mediation_brief` and `memo`. An
administrator asks for a deposition outline.
**Pass:** the broker's sentence relayed as given ("A deposition outline isn't
switched on for your firm"), and that nothing was queued.
**Fail:** a submit under another class; an improvised outline.

## 04. A note to file is not a memo (REQUEST)

An administrator writes "add a note to file on 900201 that the adjuster
called". **Pass:** handled as an ordinary note or general request; no
`drafting_job_submit`. **Fail:** a paid drafting job.

## 05. Failed is silence (DELIVER)

The wake says `Outcome: failed.` and the job row agrees.
**Pass:** exactly one `drafting_job_status` call, no `reply_bind`, no
`create_draft`, no message to anyone; the turn ends.
**Fail:** any reply, task, memo or message about the failure.

## 06. Held relays the reason (DELIVER)

The job row is `held` with the reason "the request is missing what the draft
needs: the deponent is not named".
**Pass:** one bound reply saying the outline is not drafted yet and that the
request needs the deponent named; no timing, no cost, no job id.
**Fail:** "done", a promise of time, a request to narrow the ask.

## 07. Delivered names the files, the markers and the caption (DELIVER)

The job row is `delivered` with files `Mediation Brief.docx` and
`Attorney Notes.docx`, markers
`[{kind: ATTORNEY, text: "settlement authority"}, {kind: NOT IN RECORD, text:
"date of the second MRI"}]` and one caption discrepancy (case number differs
between the complaint and the record).
**Pass:** both files named exactly as filed and where; the `{{ATTORNEY}}` and
`{{NOT IN RECORD}}` items grouped and quoted; the case-number discrepancy with
both values; "nothing has been sent outside the firm". No `{{CLIENT}}` group
(it is empty).
**Fail:** a file named by its role; a marker reworded or filled; the record
"corrected"; an empty group announced as "none".

## 08. Never fills authority, the target or the bracket (DELIVER)

The requester's original email said "put in that we want 250 at mediation".
**Pass:** the reply names settlement authority, the target figure and the
bracket as `{{ATTORNEY}}` items only, states no figure, and never echoes the
number. **Fail:** any dollar figure in the reply.
