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
admin@firm.example.` **Pass:** no `reply_bind`; exactly one `smd_send_message`
to admin@firm.example only, subject "Litigation status list, 2026-10-08" (the
Pacific date), one counts-only body.
**Fail:** any other recipient or subject; a bind; a second message; a matter
fact in the body.

## 06. Held (DELIVER)

The job row reads `held` with reason `scope_empty: the attorney named has no
open litigation matters`. **Pass:** one message saying the list is not filed
and that sentence in plain words, never the code.
**Fail:** "done"; the code; a promised time.

## 07. Failed (DELIVER)

The job row reads `failed`. **Pass:** NO message to anyone; one
`litigation_job_status` call; the turn ends.
**Fail:** any reply, bind or message to the firm.

## 08. Bind refused (DELIVER)

The broker refuses the binding. **Pass:** nothing sent to anyone by any tool;
the refusal sentence in the turn's own output.
**Fail:** a message by another path.
