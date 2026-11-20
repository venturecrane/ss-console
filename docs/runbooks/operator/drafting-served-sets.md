# Discovery responses: name the served sets

When an attorney asks for responses to discovery, the Operator drafts only the
sets SMD has named as served. Until SMD names them, the job stops before
anything is paid and SMD is paged `job_stopped` with `served_unknown`. The
attorney has the acknowledgment and hears nothing more until the drafts are
filed.

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
(`discovery_defaults`), else the one set the request describes, as before.
