# Reply shapes: medical-chronology-maintainer

The held replies the DELIVER mode may send, one per hold class, and the
firm's questions REQUEST mode may ask. All are plain email, no em dashes, no
timing, no figure, no job id. A held reply is worded by the wake's
`Hold class:` line, never by the job's `reason`: the reason is SMD's
technical record, and every hold our side caused already paged SMD with it.

## DELIVER mode, held

- **`smd_finishing`** (our side: a gate or check, the final citation check,
  the independent review, a refused stage, a delivered chronology our
  renderer could not prove, a filing or read-back that did not finish, a cost
  cap, budget or page allowance; also the default for any class not listed):
  "The chronology for matter <number> is not filed yet, and nothing new has
  been put on the matter (on an update: the chronology already there is
  unchanged). Nothing is needed from you; SMD is finishing it, and the next
  message you get about this chronology will be the filed one."
- **`smd_finishing_filed`** (the update is filed; removing the earlier
  version did not finish): "The updated chronology for matter <number> is
  filed in its folder. The earlier version is still in the same folder beside
  it; SMD is removing it. Nothing is needed from you."
- **`already_current`**: "The chronology for matter <number> already covers
  every visit the new records hold, so nothing was added and the chronology
  is unchanged. Nothing is needed from you."
- **`another_person`** (a question, the only one a record can raise): "Before
  the chronology for matter <number> can go on, one question: <file name>
  names someone other than the client. Is it the client's record? A yes or no
  in this thread is all that is needed." The file name comes from
  `get_files_on_matter` for the document ids in the reason; the other person
  is never named and nothing the record says is quoted. A `msgatt-` id is a
  record attached to an email saved in the matter, which `get_files_on_matter`
  cannot name: when the job's record states the attachment's file name and the
  saved email's file name, they stand in for the file name ("<attachment file
  name>, attached to the email <saved email file name>"); when it does not, the
  question is: "Before the chronology for matter <number> can go on, one
  question: a record attached to one of the emails saved in the matter names
  someone other than the client. Is it the client's record? You do not have to
  find it: if it is not, or you would rather not have it in, say so and it
  stays out. A yes or no in this thread is all that is needed." Never the id,
  never a guess at which email, never a date of birth.
- **`which_chronology`** (a question): "Two chronologies for this client on
  matter <number> carry the same date. Which one should the new records go
  into? Its file name in this thread is all that is needed."

- **`rerouted_to_update`** (a RUN on a matter that already holds a delivered
  chronology; resubmitted on the same turn as the UPDATE): "The new records
  for matter <number> are going into the chronology already on the matter, so
  it stays one complete document. Nothing is needed from you; the next message
  you get about this chronology will be the updated one." If the resubmission
  is refused, the `smd_finishing` wording instead.

## REQUEST mode, the firm's questions

- **No matter named.** "Which matter is this chronology for? Its number is
  all that is needed."
- **Two or more matters fit.** The candidates as prose (number, client,
  status), then "Which of these is it?"

## ANSWER mode, the requester writes again on a held chronology

The job does not move on this turn (no email talks a seat past a hold); the
seat has already paged SMD with the sender's own words, and SMD records the
decision. The reply is one of these, once, and nothing else.

- **An answer** (no, leave it out; yes, keep it in; any decision about the
  record): "Understood. It stays out of the chronology for matter <number>,
  as you say. Nothing else is needed from you; the next message you get about
  this chronology will be the filed one." ("goes into" for yes or keep it;
  any other decision repeated in the sender's own words.)
- **"Which record is it?"**, when the job's record names it: "It is
  <attachment file name>, attached to the email <saved email file name> in
  the matter's documents. Is it the client's record? A yes or no in this
  thread is all that is needed." When it does not: "It is a record attached
  to one of the emails saved in the matter, and the matter's documents do not
  name it. You do not have to find it: if it is not the client's record, or
  you would rather not have it in, say so and it stays out. A yes or no in
  this thread is all that is needed."
- **A chase**: "The chronology for matter <number> is waiting on the question
  above. A yes or no in this thread is all that is needed; the next message
  you get about it will be the filed one."
- **Not held** (submitted or running): "The chronology for matter <number> is
  under way. Nothing is needed from you; the next message you get about it
  will be the filed one." A delivered chronology makes the ask an UPDATE.

Never in an ANSWER reply: what the Operator can or cannot do, or from where; a
mailbox, a tag, an id, a step, a hold, a release, a job, a setting, a gate or
an allowance; who has access or who must act next; an ask to open, find or
read anything in the matter; a time; SMD as a route.

## Never, on a hold

- A request to resubmit, repeat, narrow, split or change the request.
- SMD offered as a route, or the firm told to take it to SMD.
- A gate, a check, a stage, a setting, a code, a limit, a cap, a cost, a
  dollar figure, or anything quoted from the job's reason.
- A promise of a time.
