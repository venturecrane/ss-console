# Discovery responses: the served sets, found or named

When an attorney asks for responses to discovery, the Operator first finds the
served sets itself (the `find` stage, `operator/runners/jobs/jobs/drafting/served_find.py`),
after preflight and before anything large is paid. It writes the same
`served.json` this runbook's command writes, and a `found-report.json` beside
it in the job directory: every candidate in the matter file and why it was
kept or dropped. **SMD's command is the override**: when a `served.json` is
already in the job directory, the finder does nothing.

## What the finder does

- **Finds candidates by code**, from the first two pages of each file: an
  asking-party line (PROPOUNDING, REQUESTING or DEMANDING PARTY) and a
  RESPONDING PARTY line and a title from the closed list (form, special or
  specially prepared interrogatories; requests or demands for production or
  inspection; requests for admission), or a page that reads as the Judicial
  Council form (`DISC-001` and "Page N of 8"), which is always form
  interrogatories. Byte copies in a second folder count once (the report
  lists the copies). A set our client propounded is not served on us and is
  dropped. A form set our side propounded is dropped before any read too: a
  file SMD wrote, one in a folder that holds a set our client propounded and
  no set served on us, or one in a folder that names our client's side
  ("Plaintiff Propounded") and not the other side or what came in
  ("Received"). A file with no text whose name reads as discovery is only
  listed; a file SMD wrote (named "DRAFT ...", or in an Operator delivery
  folder) is never counted as one.
- **Recognises response papers** by their title ("RESPONSES TO ..."). One of
  ours with a later listed date answers its served set (same kind, set number
  and asking party), and that set is dropped. The other side's responses to
  our discovery are ignored.
- **Reads each served set once** (at most 12 sets, at most 0.50 USD for the
  stage): kind, set number, both parties, the request count and the date
  served. Code checks every value against the paper: the kind against the
  title, each party on the page, the set number on a SET line, the responding
  party our client, the date on the page, and the requests cut from the file
  numbering 1 to the count (labelled "REQUEST FOR PRODUCTION NO. 3:" or a bare
  "3." under the section heading; blank "RESPONSE:" lines are removed). A form
  set carries no request text: its `source` names the file, and its parties
  come from the form's text or the matter record (the report says which). A
  served form's text layer is usually the printed blank form, its fill-ins and
  checks drawn, so the read may return no set number, count or parties. Then
  the count is the free pixel read's checked boxes, the set number is the file
  name's ("Set Two"), or 1 for the matter's only form set when no form response
  or draft is on file (the one draft's number when one is), and our client is
  the answering party when the record names exactly one. The asking party is
  the one the text sets served with it name (a set that shares a served or
  listed date with the form, or whose proof of service lists form
  interrogatories), else the job's caption record, else the read's value
  marked for SMD to confirm. Sets served with it that name different parties,
  or one the caption record does not name, stop `served_ambiguous`, as does
  anything else no source settles.
- **Narrows to the request**: "responses to the special interrogatories"
  plans only those; otherwise every unanswered set. Only a sentence that asks
  for responses to be drafted narrows; a kind named in passing, or in a
  sentence about numbered requests ("Requests 5 and 8 of the Response to
  Request for Production"), does not.
- **Who drafts**: special interrogatories and requests for production, the
  Operator; form interrogatories, requests for admission and anything else,
  SMD (reported to the attorney as "being prepared by us").

## When the finder stops

Each is a failed job before any large spend, resumable once SMD writes
`served.json` with the command below. The attorney has the acknowledgment and
hears nothing more. Read `found-report.json` in the job directory first.

| Code                 | What it means                                                                                                                | What staff do                                                                                   |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `served_none`        | No served set in the matter file (or every one is answered, or none of the kinds asked)                                      | Check the file. Name the sets with `write` and resume, or tell the attorney through an SMD note |
| `served_scan`        | Nothing read as text, and a no-text discovery file sits in a discovery folder                                                | Read the scan, name the set with `write` and resume, or draft by hand                           |
| `served_ambiguous`   | Two different sets of one kind, number and asking party; a read value not on the paper; or a response paper code cannot pair | Decide from the papers, name the sets with `write`, resume                                      |
| `served_unnumbered`  | The requests do not number 1 to the count                                                                                    | Put the requests in a text file numbered as served, `write`, resume                             |
| `served_many`        | More than 12 served sets, or the stage would pass its 0.50 USD cap                                                           | Name the sets with `write`, resume                                                              |
| `served_prior_draft` | An Operator draft of a set still owed is already on the matter                                                               | It is a revision: draft the change by hand                                                      |
| `served_amended`     | The request asks for amended, further or supplemental responses (whether or not a served response is on file)                | Draft the amended responses by hand                                                             |
| `served_other_set`   | A served set of a kind outside the list                                                                                      | Record it as type `other`, drafted by `smd`, with `write`; resume                               |

## A served form interrogatory set

Form sets are SMD's until the lead turns on the one switch,
`FORM_SETS_BY_OPERATOR` in `operator/runners/jobs/jobs/drafting/served.py`
(off). With it on, the finder marks a found form set `operator`, and the same
`find` stage reads its checked boxes three ways (two model reads and the
pixel read, `forms/read_stage.py`), writing `form`, `form_revision` and
`checked` into `served.json` and every read into `form-read.json`. Each stop
below is a failed job before the transcription is paid, resumable; the
attorney hears nothing more.

| Code               | What it means                                                                                       | What staff do                                                                        |
| ------------------ | --------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `form_incomplete`  | The served form lacks one of its eight pages, or its file is not on disk                            | Find the whole form and resume, or draft the set by hand                             |
| `form_unconfirmed` | The reads disagree, an addition is attached, the revision is not carried, or the count does not fit | Read `form-read.json`, confirm the boxes with the form keys and `write`, then resume |
| `policy_missing`   | The firm authored no form-response policy, or its shell lacks the body slot                         | Author `form_responses` in `drafting-firm.yaml`, then resume                         |
| `rule_unplaced`    | A frozen wording rule the policy neither applies to form sets nor excludes                          | Place it in `form_responses.rules`, then resume                                      |

A delivered form set's answers are kept in the lane's store,
`<run dir>/drafting-form-answers/<job id>.json`, so a change request revises
them. A delivered response job's served sets (the requests as served) and what
each was filed as are kept in the same record, so a change request in the
thread revises every filed response, each pinned to the request numbers served
(`revise/set_run.py`). They are text from the matter file. Remove a job's or a
matter's with `python -m jobs.drafting.forms.saved purge --job <id>` or
`--matter <id>`.

Rehearse a revision of every filed response at $0, before the lane takes one,
with a folder per filed document holding `base.docx` (as filed) and `key.docx`
(revised by hand):
`python -m jobs.drafting.revise.rehearse --units <dir> --request request.txt --rules wording.json`.
The first run lists each model call it needs a recording for; add them to
`<dir>/recordings.jsonl` and run again. It passes only when each document
changed exactly the responses its key changed, and nothing else.

## SMD names the sets by hand

Until the finder ran, the Operator drafted only the sets SMD named. The command
below is still how SMD names or corrects them.

The page says what to do next. The job directory holds `smd-needs.json`: why
the job stopped, the shape of the file to write, and the firm's wording rules
frozen for this job, so a set SMD drafts by hand follows the same rules.

**What SMD decides for each served set.**

- Its kind, from a closed list: `special_interrogatories`,
  `requests_for_production`, `form_interrogatories`, `requests_for_admission`.
  A kind outside the list is recorded as `other` with its `label` (the kind as
  the papers name it, for example "Demands for Inspection of Land"), drafted by
  `smd`, so the attorney's reply still names it.
- Its set number, the party that served it, our client as the set names it
  (it must be one of our client's names in the job's record), and how many
  requests it carries.
- The requests, word for word, in a text file beside the served-sets file.
  Each request starts its own line with its label and number ("SPECIAL
  INTERROGATORY NO. 3:"); the numbers must run 1 to the count.
- Who drafts it: `operator` or `smd`. A form interrogatory set is `smd` for
  now. At least one set must be `operator`.

## Run it, as root on the seat

The command needs no environment from the shell: like `deliver_by_smd`, it
reads the broker socket and the run dir from the running jobs daemon's
environment, by name only. Copy the file and the request text files onto the
seat first (`/tmp/served/`).

```bash
J='env -i PATH=/usr/bin:/bin /opt/jobs/.venv/bin/python -m jobs.drafting.served'
# 1. A file in the right shape, to fill in.
fly ssh console -a hermes-<slug> --pty=false -C "$J template <job_id>"
# 2. Fill it in; put each set's requests in a file beside it (request_text_path).
#    Check every count and party against the served papers themselves.
# 3. Validate and write it into the job. It prints each set, its count and who drafts it.
fly ssh console -a hermes-<slug> --pty=false -C "$J write <job_id> --from /tmp/served/served.json"
# 4. Resume the job.
fly ssh console -a hermes-<slug> --pty=false -C "$J resume <job_id> --why 'named the served sets'"
# Any time: what the job holds and why it stopped.
fly ssh console -a hermes-<slug> --pty=false -C "$J show <job_id>"
```

`write`, `replan` and `resume` refuse a job that is not a discovery response
job, or one that is running or delivered. `write` refuses a file outside the
shape and says why; nothing is written then. It writes the requests into the
job's own file, so the text files can be deleted afterwards.

**A file that was wrong.** Once a job has planned from a file, that plan
stands. While no document has been composed, replace it:

```bash
fly ssh console -a hermes-<slug> --pty=false -C "$J replan <job_id> --from /tmp/served/served.json --by <who> --why '<what was wrong>'"
fly ssh console -a hermes-<slug> --pty=false -C "$J resume <job_id> --why '<what was wrong>'"
```

It records who and why in the job's `data/served-replans.jsonl`. After a
document has been composed it refuses; draft the change by hand.

## What happens next

The job drafts one document per `operator` set, each with that set's requests
word for word, and checks in code before anything is filed that each document
restates every request as served, answers each once, numbered 1 to the set's
count, in order. A document that does not is held for SMD (`review_held`).
The firm's reply lists every served set: the ones filed, by document name, and
the others as "being prepared by us". SMD is paged `served_owed` for those, and
the hourly work-health triage lists each until it is filed and the attorney is
told: record it with `deliver_by_smd --set`, send him the line it prints as an
SMD note, then mark it with `--told` (`operator/drafting-deliver-by-smd.md`).

## The other stops before spend

- `served_other_set`: a kind outside the list was in the file. Record it as
  type `other` with its `label`, drafted by `smd`, with `write`, and resume.
- `served_invalid`: the file was outside the shape. Fix it with `write` and
  resume, or draft everything by hand.

A propounded set (discovery to the other side) has no stop before spend: it
plans after its transcription and caption, as it always has. "To the defense"
means every defendant on the court's caption; when our client is a defendant
there, the job ends `party_unknown` for SMD (name the parties in `parties.json`
in the job directory and resume, or draft by hand). A request naming no kind
of set drafts the firm's default sets when the firm authored them
(`discovery_defaults`). Else, when it asks for "discovery" or "sets" ("sets of
discovery to the school district"), it drafts the kinds the same requester's
earlier set job on the matter planned when it says "also", or the standard
package (special interrogatories, requests for admission, requests for
production) to each party it names, and the attorney notes say which in one
line (`set_package.py`). Any other request naming no kind drafts the one set
it describes, as before. A short reference to a party ("to the middle school")
names the caption party whose last words it is, when exactly one does
(`party_tail.py`); two or none end `party_unknown` for SMD. A
`request_incomplete` hold is SMD's: the attorney is never sent its question.
