# Reply shapes: case-file-manager

Each message the skill may send, and the ones it never sends. All are plain
email, no em dashes, no timing, no figure, no job id, and **no matter fact**:
no matter number, no client or party name, no date from a document, no task's
words. The counts are the whole of the facts; the File Status in each file and
the ranked list hold the rest.

## REQUEST mode (a reply in the requester's thread)

- **Accepted.** "Received. A review of these files is queued: each file with
  something new gets a current File Status, and the caseload's ranked list is filed
  in the firm's Operator Library." No promise of a later message.
- **Refused by the broker.** The broker's sentence in plain words, then "Nothing
  was queued."
- **Not a Named Administrator.** A sentence naming the reservation and who at the
  firm can ask. Nothing is submitted.
- **Whose caseload unclear.** One question: whose files, or which matters.
- **Asked to schedule it.** A standing schedule is switched on by the firm through
  SMD; the one review asked for now is queued as usual.

## DELIVER mode

- **delivered, a review.** A reply in the requester's thread: "The ranked list is
  filed in the firm's Operator Library. It covers 40 files: 12 read this run and
  28 already current with nothing new. Each file read has a current File Status in
  its File Status folder. The list's Proposed changes sheet numbers 9 changes; to
  make them, reply in this thread with the numbers, for example yes to 1, 3 and 4,
  or yes to all. Nothing changes until then, and nothing has been sent to anyone
  outside the firm." Each count only when above zero. A `recheck` reads the same.
  A scheduled job's message is one NEW email to the firm's authored recipient
  (the broker binds it and sets its subject), never a reply; when the wake carries
  `Count on the 1st: 41 Open, 7 Pending.`, that line opens the message unchanged.
- **delivered, an apply.** A reply in the approver's thread: "Changes 1, 3 and 4
  are made in Smokeball and checked. Change 2 was left as it is because the file
  changed since it was proposed, so nothing was guessed."
- **held**, by the wake's `Hold class:` line, never by the job's reason:
  - `smd_finishing` (our side, the default for any class not listed; SMD is paged
    for every one), worded by what is true of the job:
    - a review that filed nothing: "The case file review is not filed yet, and
      nothing new has been put in the firm's files. Nothing is needed from you; SMD
      has been told and is finishing it."
    - a review that filed some File Statuses first: "The case file review is not
      finished: some files have a new File Status, and the ranked list is not filed
      yet. Nothing is needed from you; SMD has been told and is finishing it."
    - an apply: "The approved changes have not all been made. Nothing is needed from
      you; SMD has been told and is looking at them."
  - The broker enforces every held wording: `reply_bind` returns it as
    `held_wording`, and the send refuses any other words.
  - `nothing_in_scope`: "No files were reviewed: none of the firm's Open or
    Pending matters fit this request. Nothing is needed from you."
  - `staff_unresolved`: "Nothing was read: a name in the request does not match
    exactly one person on the firm's staff list in Smokeball, so I did not guess.
    Whose files did you mean? Reply with their full name as Smokeball shows it;
    that is all that is needed." The one hold whose answer is the firm's.
- **failed.** NO message. One `casefile_job_status` call (which raises SMD's
  shortfall alert) and the turn ends.
- **bind refused.** Nothing to anyone, by any tool.

## Never

- A list of cases, a matter number, a name or a date in the body.
- On a hold other than `staff_unresolved`: a request to resubmit, repeat, narrow or
  split the request; SMD offered as a route; anything from the job's reason; a time.
- A second message, a follow-up, a task or a memo.
- A message to anyone but the person the broker binds.
- A task closed or a Medicals row added in a turn: approvals go through the
  casework reply path and the job's apply.
