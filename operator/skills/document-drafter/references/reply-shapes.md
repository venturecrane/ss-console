# Reply shapes: document-drafter

Each shape the skill may send, and the one it never sends. All are plain
email in the requester's own thread, no em dashes, no timing, no figure, no
job id. Matter numbers come only from a record read this turn or the job row.

## REQUEST mode

- **Accepted.** "Received. The mediation brief for matter 900201 is being
  prepared; I'll reply in this thread when it's filed."
- **Refused by the broker.** The broker's sentence in plain words, then
  "Nothing was queued." Example: "A deposition outline isn't switched on for
  your firm, so nothing was queued."
- **Not a Named Administrator.** A sentence naming the reservation and who at
  the firm can ask. Nothing is submitted, and no class is named as on or off.
- **Matter or class unclear.** One question, naming the candidate matters by
  number or the two classes the words could mean.

## DELIVER mode

- **delivered.** The file names exactly as filed and where (the client matter,
  or the firm's library matter for a rehearsal), then the `{{ATTORNEY}}`,
  `{{NOT IN RECORD}}` and `{{CLIENT}}` items grouped by kind, then each caption
  discrepancy, then that nothing was sent outside the firm. An empty group is
  left out.
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
