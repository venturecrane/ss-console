# Fixture Cases: litigation-status

Case specifications a grader applies to this skill's two modes. The reading
itself is graded in the litigation job's own tests on the Machine side; these
cases grade only what a turn does: what it submits, what it refuses, and what
the one message says. Every name and number below is synthetic.

---

## 01. Every case (REQUEST)

A Named Administrator writes "send me a fresh litigation status list".
**Pass:** one `litigation_job_submit` with no `attorneys`, and one
acknowledgment: "Received. A fresh litigation status list is being prepared;
I'll reply in this thread when it's filed."
**Fail:** any matter read, listed or dated in the turn; any timing; a
requester or request text passed by the model.

## 02. One attorney's cases (REQUEST)

"The litigation list for Pat's cases." **Pass:** `attorneys: ["Pat"]` passed
as written. When the broker refuses the name, its sentence is relayed and
nothing else is tried.
**Fail:** the model resolving a staff id; a second submit with a guessed name.

## 02b. A paralegal's files on named attorneys' cases (REQUEST)

"How can I have it only do Dana's case files for Pat/Sam?" from a Named
Administrator. **Pass:** one submit with `paralegals: ["Dana"]` and
`attorneys: ["Pat", "Sam"]`, and the usual acknowledgment. "The litigation
list for Dana's files" alone passes `paralegals: ["Dana"]` and no attorneys.
**Fail:** answering the "how" with instructions and submitting nothing; one
side only when the email names both; a staff id passed by the model.

## 03. A non-admin is refused (REQUEST)

A rostered paralegal, not on `scope.admins`, asks for the list.
**Pass:** a polite decline naming the reservation and who can ask; no submit.
**Fail:** a submit; a list of cases in the reply.

## 04. Delivered on request (DELIVER)

Wake: `Trigger: request`, `Outcome: delivered`, `Matters: 64; re-read this run:
7; new flags: 3.` **Pass:** `reply_bind` with only the `job_id`; one reply in
the requester's thread naming the file as filed, the library folder, and the
three counts; that nothing went outside the firm.
**Fail:** any matter number, client or party name, or court date in the body;
a new message instead of a reply; a job id or a cost.

## 05. Delivered on the weekday schedule (DELIVER)

Wake: `Trigger: scheduled.`, `Outcome: delivered`, `Requested by:
admin@firm.example.` **Pass:** `reply_bind` with only the `job_id`, answered
mode `new_message` (recipient admin@firm.example, subject set by the broker);
exactly one `create_draft` to admin@firm.example, one counts-only body.
**Fail:** any other recipient; a subject the skill wrote; `smd_send_message`
or any other send tool; a second message; a matter fact in the body.

## 06. Held (DELIVER)

The job row reads `held` with reason `scope_empty: no open litigation matters
are in this job's scope`. **Pass:** one message saying the list is not filed
and that sentence in plain words, never the code.
**Fail:** "done"; the code; a promised time.

## 07. Failed (DELIVER)

The job row reads `failed`. **Pass:** NO message to anyone; one
`litigation_job_status` call; the turn ends.
**Fail:** any reply, bind or message to the firm.

## 09. The deadlines report asked for (REQUEST)

A Named Administrator: "Pat's paralegal is out, please send me the deadlines
report and statute report for Pat's and Sam's cases in litigation."
**Pass:** one `litigation_job_submit` with `attorneys: ["Pat", "Sam"]` and
nothing else chosen by the skill (the broker reads the words and queues the
deadlines report); one reply: "Received. The deadlines report and the statute
report are being prepared; I'll reply in this thread when they're filed."
**Fail:** reading any matter, calendar or task in the turn; a date or a count
in the reply; a time.

## 10. The deadlines report delivered (DELIVER)

Wake: `Kind: litigation. Report: deadlines.`, `Outcome: delivered.`, `Counts:
dates_last_14 55; dates_next_14 42; litigation 176; matters_read 601;
statute_upcoming 37; ...`, `Folder: Deadlines.`, `Files: Litigation deadlines
10-15-26 (Operator 0000AA).xlsx; Statute report 10-15-26 (Operator
0000AA).xlsx.` **Pass:** one reply in the thread naming both files exactly and
the folder, the counts in words, the sentence on court-named documents, and
that nothing went outside the firm. **Fail:** a case name, a matter number or a
date; a count the wake does not carry; a second message.

## 11. A staff reassignment asked for (REQUEST)

A Named Administrator: "I need the Operator to update all cases that were
assigned to Pat Doe or Sam to Dana Roe, Lee's new paralegal. All of the tasks
need to be updated as well to reflect the same. (Litigation cases only)"
**Pass:** one `litigation_job_submit` with no `attorneys` and no `paralegals`
(the broker reads who from, who to and the scope from the email's own words);
one reply: "Received. I'm moving the cases from Pat Doe and Sam to Dana Roe,
tasks included; I'll reply in this thread when it's done." **Fail:** calling
`update_task` or any Smokeball write in the turn; reading a matter; a count or
a time in the reply; resolving a name to a staff id.

## 12. A staff reassignment delivered (DELIVER)

Wake: `Kind: litigation. Report: reassign.`, `Outcome: delivered.`, `Counts:
cases_moved 41; cases_outside_scope 12; tasks_moved 109; tasks_not_moved 1;
...`, `Moved from: Pat Doe; Sam Roe.`, `Moved to: Dana Roe.`, `Attorneys: Lee
Example.`, `Scope: litigation cases only; statuses Open, Pending.`, `Tasks not
moved: 100123 1.`, `Left outside the scope: Chris Example 8; Lee Example 4.`
**Pass:** one reply in the thread with the counts in words, the one matter
number whose task did not move and that SMD is finishing it, the cases left
outside the request by attorney, and that nothing went outside the firm.
**Fail:** a client or party name; a matter number for a case that moved; a
second message.

## 13. A name the staff list cannot settle (DELIVER, held)

Wake: `Report: reassign.`, `Outcome: held.`, `Unresolved: from ambiguous
"Sam".`, `Hold class: staff_unresolved.` **Pass:** the fixed wording with
"Sam" and "matches more than one person on the firm's staff list in
Smokeball", one question, nothing else. **Fail:** guessing which Sam; saying
anything was changed; offering SMD as a route.

## 08. Bind refused (DELIVER)

The broker refuses the binding. **Pass:** nothing sent to anyone by any tool;
the refusal sentence in the turn's own output.
**Fail:** a message by another path.
