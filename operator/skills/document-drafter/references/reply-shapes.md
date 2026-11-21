# Reply shapes: document-drafter

Each shape the skill may send, and the one it never sends. All are plain
email in the requester's own thread, no em dashes, no timing, no figure, no
job id. Matter numbers come only from a record read this turn or the job row.

**The firm never reads about our machinery.** A reply speaks of the document,
the file and the record, never of a job, a run, a pipeline, a model, a lane or
the system. Not "The job searched his reports" but "His reports were
searched"; not "The job's source for this is the complaint" but "The source
for this is the complaint". The seat holds a job's delivery reply that names
the job (the output checklist's `system_vocabulary` rule) and asks for a
redraft in the firm's terms.

## REQUEST mode

- **Submitted, accepted or refused.** The submit's `say`: the reply must
  contain this sentence exactly, as its own paragraph; answer anything else the
  email asked in other paragraphs; never describe how the work is done. The seat
  refuses a reply that alters or leaves it out. Accepted: "Received. The
  mediation brief for matter 900201 is with us, and you'll get a reply in this
  thread when the draft is filed in the matter." A revision of a filed draft:
  "Received. We'll make those changes to the mediation brief for matter 900201
  and reply in this thread when the new version is filed." A refusal is one
  plain sentence the tool wrote, for example "Received. Drafting a deposition
  outline isn't switched on for your firm, so we have not started the
  deposition outline for matter 900201." One email naming a second matter for
  the same kind of draft: "Received. One email starts one draft of each kind,
  and this one already started the mediation brief for matter 900201. Please
  send the request for matter 900202 in its own email." Too many Word
  documents: "... Please send the request again with at most 6 Word documents
  attached." (nothing was queued, so a re-send starts clean). Stopped on our
  side: the same acknowledgment as accepted, because the request is recorded as
  work we owe and is followed up; the firm is never told of our failure. When
  the tool's `say` is `none` (nothing could be recorded), say nothing about that
  request; answer the rest of the email.
- **Not a Named Administrator, not submitted.** A sentence naming the
  reservation and who at the firm can ask. No class is named as on or off.
- **A follow-up on a draft underway.** The tool's `say`, as given, and nothing else
  about it. Only result `applied` may say the words reached the draft. Examples:
  "Received. We added what you wrote to the instructions for the propounded discovery
  for matter 900201, and it will be drafted with them. We'll reply in this thread when
  it's filed." or "Received. The propounded discovery for matter 900201 was already
  being written when your email arrived, so what you wrote is not in this draft. We'll
  say so again when we reply with the filed document, and you can ask for the change
  then." A follow-up on a draft that stopped on our side gets the revise
  acknowledgment ("Received. We'll make those changes to the memo for matter
  900201 and reply in this thread when the new version is filed."), because the
  addition is recorded as work we owe; the firm is never told of our failure.
  When the `say` is `none`, say nothing about that draft; answer the rest.
- **Matter or class unclear.** One question, naming the candidate matters by
  number or the two classes the words could mean.

## DELIVER mode

- **delivered.** The file names exactly as filed and where (the client matter,
  or the firm's library matter for a rehearsal), then the `{{ATTORNEY}}`,
  `{{NOT IN RECORD}}` and `{{CLIENT}}` items grouped by kind, then each caption
  discrepancy, then each of the requester's later words the job reports as
  `additions_not_covered`, then that nothing was sent outside the firm. An empty
  group is left out.
- **held.** That the document is not drafted yet, and what the file or the
  request needs, in the firm's terms, from the job's reason. The reason reads
  `<code>: <sentence>`; relay the sentence after the code, never the code
  itself (`request_incomplete`, `destination_mismatch` and the like are SMD's
  labels).
- **failed.** NO reply. One `drafting_job_status` call (which raises SMD's
  shortfall alert) and the turn ends. The firm hears nothing about our own
  machinery.
- **bind refused.** Nothing to anyone, by any tool. The refusal sentence goes
  in the turn's own output only.
