# Reply shapes: litigation-status

Each message the skill may send, and the ones it never sends. All are plain
email, no em dashes, no timing, no figure, no job id, and **no matter fact**:
no matter number, no client or party name, no court date, no flag's content.
The counts and the workbook's file name are the whole of the facts.

## REQUEST mode (a reply in the requester's thread)

- **Accepted.** "Received. A fresh litigation status list is being prepared;
  I'll reply in this thread when it's filed."
- **Refused by the broker.** The broker's sentence in plain words, then
  "Nothing was queued." Example: "A list for the same cases is already being
  prepared, so nothing new was queued."
- **Not a Named Administrator.** A sentence naming the reservation and who at
  the firm can ask. Nothing is submitted.
- **Attorney unclear.** One question: which attorney's cases the email means.
- **Asked to schedule it.** The weekday list is switched on by the firm
  through SMD; the one list asked for now is queued as usual.

## DELIVER mode

- **delivered, requested.** A reply in the requester's thread: "The litigation
  status list is filed in the firm's Operator Library, folder Litigation
  Status, as <file name>. It covers 64 matters; 7 were re-read because their
  files changed since the last list, and it carries 3 new flags for review.
  Nothing has been sent to anyone outside the firm."
- **delivered, scheduled.** `reply_bind` with the job id answers mode
  `new_message` with the recipient and the subject set by the broker; one
  `create_draft` to that person with the same counts-only body. The subject
  is the broker's, never written by the skill.
- **held.** That the list is not filed yet, and what the files or the request
  need, in the firm's terms, from the job's reason. The reason reads
  `<code>: <sentence>`; relay the sentence after the code, never the code
  itself, and leave out any matter or party it names.
- **failed.** NO message. One `litigation_job_status` call (which raises SMD's
  shortfall alert) and the turn ends. The firm hears nothing about our own
  machinery.
- **bind refused.** Nothing to anyone, by any tool. The refusal sentence goes
  in the turn's own output only.

## Never

- A list of cases, a matter number, a name or a date in the body.
- A second message, a follow-up, a task or a memo.
- A message to anyone but the person the broker binds.
- Any send tool other than `create_draft` after a bind.
