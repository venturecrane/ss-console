# Reply shapes: litigation-status

Each message the skill may send, and the ones it never sends. All are plain
email, no em dashes, no timing, no figure, no job id, and **no matter fact**:
no matter number, no client or party name, no court date, no flag's content.
The counts and the workbook's file name are the whole of the facts. The one
exception is a staff reassignment, which names the matter numbers of what did
not move (never a name or a date with them).

## REQUEST mode (a reply in the requester's thread)

- **Accepted.** "Received. A fresh litigation status list is being prepared;
  I'll reply in this thread when it's filed."
- **Accepted, the deadlines report.** "Received. The deadlines report and the
  statute report are being prepared; I'll reply in this thread when they're
  filed." Only the reports the email named.
- **Accepted, a staff reassignment.** "Received. I'm moving the cases from Pat
  Doe and Sam to Dana Roe, tasks included; I'll reply in this thread when it's
  done." The names as the email gives them.
- **Refused by the broker.** The broker's sentence in plain words, then
  "Nothing was queued." Example: "A list for the same cases is already being
  prepared, so nothing new was queued."
- **Not a Named Administrator.** A sentence naming the reservation and who at
  the firm can ask. Nothing is submitted.
- **Attorney or paralegal unclear.** One question: whose cases or files the
  email means.
- **A name the broker cannot place.** Its sentence, which names the firm's
  list: '"Pat" is not a paralegal on the firm's list (the list: ...)'.
- **Asked to schedule it.** The weekday list is switched on by the firm
  through SMD; the one list asked for now is queued as usual.

## DELIVER mode

- **delivered, requested.** A reply in the requester's thread: "The litigation
  status list is filed in the firm's Operator Library, folder Litigation
  Status, as <file name>, with the same list one sheet per case beside it, as
  <per-case file name>. It covers 64 matters; 7 were re-read because their
  files changed since the last list, and it carries 3 new flags for review.
  Nothing has been sent to anyone outside the firm."
- **delivered, the deadlines report.** A reply in the requester's thread:
  "The deadlines report and the statute report are filed in the firm's
  Operator Library, folder Deadlines, as Litigation deadlines 10-15-26
  (Operator 0000AA).xlsx and Statute report 10-15-26 (Operator 0000AA).xlsx.
  They cover 601 Open and Pending matters, 176 of them in litigation: 42 dates
  in the next 14 days and 55 in the last 14, 88 open tasks due or overdue (74
  overdue), and 37 cases with a statute date ahead and no filing recorded. A
  case can hold filed papers that were never entered as filed, so the statute
  report shows each case's court-named documents to check first. Nothing has
  been sent to anyone outside the firm." Only the counts the wake carries,
  each left out at zero.
- **delivered, a staff reassignment.** A reply in the requester's thread:
  "Done. 41 cases moved from Pat Doe and Sam Roe to Dana Roe (Lee Example's
  litigation cases, Open and Pending), with 109 of their open tasks. One task
  on 100123 still names the earlier person; SMD has been told and is finishing
  it. 12 cases outside this request were left as they were (Chris Example 8,
  Lee Example 4, not in litigation), in case you want those moved too. Nothing
  has been sent to anyone outside the firm." Each part only when its line
  applies; matter numbers only for what did not move.
- **delivered, scheduled.** `reply_bind` with the job id answers mode
  `new_message` with the recipient and the subject set by the broker; one
  `create_draft` to that person with the same counts-only body. The subject
  is the broker's, never written by the skill.
- **held**, by the wake's `Hold class:` line, never by the job's reason (SMD
  already has the reason, paged with it):
  - `smd_finishing` (our side, the default for any class not listed): "The
    litigation status list is not filed yet, and nothing new has been put in
    the firm's Operator Library. Nothing is needed from you; SMD is finishing
    it, and the next message you get about this list will be the filed list."
  - `nothing_in_scope`: "No list was filed: none of the firm's open litigation
    matters fit this request. Nothing is needed from you."
  - `staff_unresolved` (a staff reassignment): "Nothing was changed: <name>
    <why>, so I did not guess. Which person did you mean? Send the request again
    with their full name as Smokeball shows it; that is all that is needed."
    `<why>` by the `Unresolved:` kind, as the skill words it. One of the two
    holds whose answer is the firm's.
  - `requester_unresolved` (the sender's own files, from an address no staff
    member in Smokeball carries; the status list or the deadlines report): "I
    could not match your email address to a staff member in Smokeball, so I did
    not guess whose files to list. Which attorney did you mean? Reply with their
    name as Smokeball shows it; that is all that is needed." The other hold whose
    answer is the firm's.
  - `smd_finishing` on a staff reassignment: "The cases have not all been moved
    yet. Nothing is needed from you; SMD is finishing it, and the next message
    you get about this will say what was moved."
  - `smd_finishing` on the deadlines report: "The deadlines report is not filed
    yet, and nothing new has been put in the firm's Operator Library. Nothing is
    needed from you; SMD is finishing it, and the next message you get about this
    report will be the filed report."
  - `nothing_in_scope` on the deadlines report: "No report was filed: none of
    the firm's Open or Pending matters fit this request. Nothing is needed from
    you."
  - The broker enforces every held wording above: `reply_bind` returns it as
    `held_wording`, and the send refuses any other words and quotes the text.
- **failed.** NO message. One `litigation_job_status` call (which raises SMD's
  shortfall alert) and the turn ends. The firm hears nothing about our own
  machinery.
- **bind refused.** Nothing to anyone, by any tool. The refusal sentence goes
  in the turn's own output only.

## Never

- A list of cases, a matter number, a name or a date in the body (a staff
  reassignment's not-moved matter numbers excepted).
- On a hold other than `staff_unresolved` or `requester_unresolved`: a request to resubmit, repeat, narrow, split or change the
  request; SMD offered as a route; a gate, a code, a stage or anything from the
  job's reason; a time.
- A second message, a follow-up, a task or a memo.
- A message to anyone but the person the broker binds.
- Any send tool other than `create_draft` after a bind.
