# Reply shapes: medical-chronology-maintainer

The replies this skill may send. All are plain email, no em dashes, no
timing, no figure, no job id.

## DELIVER mode: delivered only

The firm hears from the Operator about a chronology ONLY when it is
delivered (the Captain, 2026-10-24). A stopped chronology, held or failed,
sends the firm nothing: no reply, no question, no word that it stopped. SMD
was paged when it stopped and finishes it, and the next message the firm gets
is the delivery. The broker refuses a held chronology its reply.

- **`rerouted_to_update`** (a RUN on a matter that already holds a delivered
  chronology), the one held wake: resubmit it on the same turn as the UPDATE
  and send nothing. The UPDATE's delivery is the reply.
- **A record that names someone other than the client** never stops a
  chronology and is never asked about: the runner leaves it out by rule, and
  the delivered package's Records Reviewed section names the file, saying it
  names a person other than the client, so it was left out of this
  chronology. Never the other person's name, never a date of birth.

The broker enforces every held wording above: `reply_bind` returns it as
`held_wording`, and the send refuses any other words and quotes the text.

## REQUEST mode, the firm's questions

- **No matter named.** "Which matter is this chronology for? Its number is
  all that is needed."
- **Two or more matters fit.** The candidates as prose (number, client,
  status), then "Which of these is it?"

## ANSWER mode, the requester writes again on a chronology not yet delivered

The job does not move on this turn (no email talks a seat past a hold); the
seat has already paged SMD with the sender's own words. A stopped chronology
is never explained. The reply is one of these, once, and nothing else.

- **An answer about a record** (only where the thread was asked, before
  2026-10-24, whose a record was: no, leave it out; yes, keep it in):
  "Understood. It stays out of the chronology for matter <number>, as you
  say. Nothing else is needed from you; the next message you get about this
  chronology will be the filed one." ("goes into" for yes or keep it; any
  other decision repeated in the sender's own words.)
- **Anything else** (a chase, a question; submitted, running or held): "The
  chronology for matter <number> is under way. Nothing is needed from you;
  the next message you get about it will be the filed one." A delivered
  chronology makes the ask an UPDATE.

Never in an ANSWER reply: what the Operator can or cannot do, or from where;
that the chronology stopped, or why; a mailbox, a tag, an id, a step, a hold,
a release, a job, a setting, a gate or an allowance; who has access or who
must act next; an ask to open, find or read anything in the matter; a time;
SMD as a route.

## Never, about a stopped chronology

- A message to the firm saying it stopped, why, or what is happening to it.
- A question to the firm about whose a record is.
- A request to resubmit, repeat, narrow, split or change the request.
- SMD offered as a route, or the firm told to take it to SMD.
- A gate, a check, a stage, a setting, a code, a limit, a cap, a cost, a
  dollar figure, or anything quoted from the job's reason.
- A promise of a time.
