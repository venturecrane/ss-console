# A document SMD drafted and filed by hand: record it as the job's delivery

**When to use it.** A drafting job (a brief, a discovery set, discovery
responses, a memo, a deposition outline) did not finish, and SMD drafted the
document and filed it into the matter by hand. Until it is recorded, the seat's
job store holds no delivered draft of that class on the matter, so the
attorney's next "please change X" queues a brand-new full draft instead of
revising what he has. After it, that request revises the filed document in
place, and the attorney is never told how the draft was made.

**A change request becomes a revision only in the original thread.** The broker
matches by email conversation: the attorney's change request must arrive in the
same Operator email thread as the request that started the job (a reply to it,
or to the Operator's answer in it). A change asked in a new email, or sent to
someone at SMD directly, is a fresh draft request.

**Before filing a hand-drafted discovery document, read it as the firm's own
attorney would.** A fresh reader, briefed only as a senior practitioner at the
firm on the client's side of the case, gets the served requests, the draft, any
index and exhibit list, and the firm's recorded rules. For each response it
answers one question: would that attorney serve this as written? It flags
anything that harms the client or that ordinary practice on the client's side
would not do. That includes producing correspondence with insurers, adjusters
or opposing counsel, settlement demands, work product, admissions, producing
too much or objecting too little, and wording the firm's rules forbid. Apply
every finding that does not conflict with the firm's rules, and list the rest,
with the reason, in the attorney notes. Applying a firm rule literally is not
enough. On 2026-11-20 a hand draft produced the claim correspondence and two
settlement demands, and then put the insurer's letters in the incident exhibit
because they were "about the incident". The attorney caught both. In a blind test
the same evening, a reader briefed this way, with neither of the later rules,
flagged both defects among its top findings, and on the corrected version as a
control it flagged neither. The same check runs on the Operator's own drafts
as an audit finding class in each firm's `audit-discovery-response` prompt.

**Removing a file SMD filed in error.** Smokeball's delete is soft: the file
leaves the matter listing, but its id stays readable (`isDeleted`), a Delete
history row names who did it, and its bytes still download. The connector has
no restore call, so undoing it means the firm restores it in Smokeball or SMD
re-uploads it. When the attorney asks for a file SMD filed to be removed, or
the file is plainly wrong (a superseded exhibit, a file filed twice), delete it
in the same filing step and read the deletion back. Don't leave it for the firm.

**Before:**

- Every document is on the matter and was read back by id when it was filed
  (its file id, exact byte count and sha256). This tool checks nothing against
  Smokeball itself: it records what that read-back said.
- The job has ended `failed`, or it was withdrawn. A job that is `held`, or
  still waiting in the queue for a lane proof, is withdrawn first:
  `operator/bin/lane-proof.sh <seat> withdraw drafting <job_id> --by <you> --why "answered by hand"`
  ([lane-proof](lane-proof.md), "Withdraw a held job"). The tool refuses a held
  job and says so.
- The job dir still keeps its envelope (a finished job's dir is wiped after 72
  hours; past that the tool refuses).

**Write the record** (`record.json`, ids, names and sizes only):

```json
{
  "folder_id": "<folder the documents were filed in>",
  "files": [
    {
      "file_id": "<file id>",
      "name": "<file name as filed>",
      "bytes": 41000,
      "sha256": "<64 hex>"
    },
    { "file_id": "<file id>", "name": "<file name as filed>", "bytes": 38000, "sha256": "<64 hex>" }
  ]
}
```

One entry per document: a discovery request yields one document per set. Each
entry's `role` is `draft` unless given (`attorney_notes` for a notes file).

**Run it as root on the seat, dry run first.** The tool needs no environment
from the shell: it reads what it needs (the broker socket, the seat slug, the
run and queue dirs) from the running jobs daemon's environment, by name only,
the same way it runs from `env -i` in the withdraw command. Copy `record.json`
onto the seat first.

```bash
fly ssh console -a hermes-<slug> --pty=false -C "env -i PATH=/usr/bin:/bin /opt/jobs/.venv/bin/python -m jobs.drafting.deliver_by_smd <job_id> /tmp/record.json --quiet --dry-run"
fly ssh console -a hermes-<slug> --pty=false -C "env -i PATH=/usr/bin:/bin /opt/jobs/.venv/bin/python -m jobs.drafting.deliver_by_smd <job_id> /tmp/record.json --quiet --confirm <digest>"
```

The dry run prints the proof row, the ledger records, the job's state entry, the
reply's wake text and a digest, and writes nothing. The live run must repeat
that digest; anything changed since is refused, so dry-run again. It writes the
`DELIVERY_PROVEN` row first, then moves the ledger row `failed` to `running` to
`delivered` with the folder and files, then marks the job `delivered` in its
daemon state. A run that stops part way exits 3 (`FAILED PARTWAY`): dry-run and
confirm again, and it finishes from where it stopped.

**What the firm's portal shows.** The activity feed lists the job's audit rows.
A withdrawn job already shows "Could not finish drafting a document" from the
withdrawal, which is true. Recording the delivery adds "Is drafting a document"
(the ledger's move through `running`) and then "Filed a drafted document on the
matter". The tool writes no "Could not finish" row of its own.

**What the attorney receives.** With `--quiet`: nothing from this tool. Use it
when SMD already answered him. Without `--quiet`: the lane's own delivered
reply, from the Operator in his request thread, exactly as a seat delivery
sends it; the daemon dispatches it on its next tick and pages SMD
(`reply_unproven`) if it is not in Sent Items within 20 minutes.

**It refuses** (exit 2, nothing written): a held job (withdraw it first), a job
still running or queued, a job already delivered, a job with a wake still to go
or a delivered reply already claimed, another live job for the same request
email and document class (it names that job), a job dir with no envelope, and a
record missing its folder, or any file's id, name, size or sha256.

**Read it back.** `operator/bin/lane-proof.sh <seat> status` shows nothing
waiting for this job; the request index shows the request delivered. Record a
`crane_verify` of that read-back.

**The attorney's next change request.** In the original thread it is queued as
a revision of these files. A revise job waits on its own lane key,
`document-drafter.revise_<class>` (`lane_unproven` on its row), until that lane
is proven, which is the intended route to SMD: the request card pages SMD and
the attorney hears only the acknowledgment. SMD then either proves the lane
([lane-proof](lane-proof.md)), or revises by hand, withdraws the waiting revise
job, and records the revised files on it with this tool.

**A served set SMD owed on a delivered response job.** When a response job
delivers the sets the Operator drafted, the attorney's reply lists the others
as "being prepared by us", SMD is paged `served_owed`, and the hourly
work-health triage lists each owed set, from the job's own ledger row with no
time limit, until it is filed AND the attorney has been told. Three steps, in
this order, on the same job:

1. Draft the set and file it on the matter.
2. Record it (dry run first; `record.json` holds only that set's filed
   documents, role `draft`):

   ```bash
   fly ssh console -a hermes-<slug> --pty=false -C "env -i PATH=/usr/bin:/bin /opt/jobs/.venv/bin/python -m jobs.drafting.deliver_by_smd <job_id> /tmp/record.json --set '<set as the reply names it>' --dry-run"
   fly ssh console -a hermes-<slug> --pty=false -C "env -i PATH=/usr/bin:/bin /opt/jobs/.venv/bin/python -m jobs.drafting.deliver_by_smd <job_id> /tmp/record.json --set '<set>' --confirm <digest>"
   ```

   It writes the proof row first (every file of the job, this one added), adds
   the files to the job's delivery, marks the set filed, and prints the one
   line to tell the attorney (`<set>: filed as "<name>".`). It refuses a job
   that is not delivered, a set the job does not owe, a file id already on the
   job, and a job whose filed files cannot be paired with their proof. A run
   that stopped part way is finished by running it again.

3. Tell the attorney: send that line as an SMD note in his request's thread
   ([smd-note](smd-note.md)), then mark it told:

   ```bash
   fly ssh console -a hermes-<slug> --pty=false -C "env -i PATH=/usr/bin:/bin /opt/jobs/.venv/bin/python -m jobs.drafting.deliver_by_smd <job_id> --set '<set>' --told --dry-run"
   fly ssh console -a hermes-<slug> --pty=false -C "env -i PATH=/usr/bin:/bin /opt/jobs/.venv/bin/python -m jobs.drafting.deliver_by_smd <job_id> --set '<set>' --told --confirm <digest>"
   ```

   Until then the set stays listed ("tell the attorney: <set> filed as
   <name>"), unless a reply in his request's thread went out after the set was
   recorded.

**A request refused on our side, which never became a job.** When the broker
refuses a drafting request for a cause on SMD's side (`filing_target`,
`lane_not_enabled`, `admins_unreadable` and the rest of
`REFUSAL_OUR_CODES`), it records the request as work SMD owes in the same step,
and the attorney gets the normal acknowledgment. The hourly work-health triage
lists it (the broker's `smd_owed` row, by its owed id) until it is closed. There
is no job, so this tool has nothing to record on until one is opened from that
record: as root on the seat, open it, then record the hand delivery as above.

```bash
PYTHONPATH=/opt/workspace-broker /opt/workspace-broker/.venv/bin/python \
  -m workspace_broker.smd_owed open-job <owed id> \
  --db /run/smd-audit/audit.db --jobs-dir /run/smd-jobs/drafting-jobs
```

It writes the broker's database directly, as root (the ledger row, `failed`,
under the owed id), then the job dir's envelope and a daemon state of `failed`.
A run that stops part way is finished by running it again; it refuses an id
with no record, or one whose job has already moved past `failed`. The triage
line closes when the job is delivered; a request answered another way is
closed in `work-health-closed.yaml` (`job: <owed id>`). A job accepted later for
the same request email and class closes the owed item by itself. Code:
`operator/workspace_broker/smd_owed.py`.

Code: `operator/runners/jobs/jobs/drafting/deliver_by_smd.py`, sharing
`operator/runners/jobs/jobs/hand_delivery.py` with the chronology's
[medchron-deliver-by-smd](medchron-deliver-by-smd.md) (tests:
`operator/runners/jobs/tests/test_drafting_deliver_by_smd.py`). The revise
routing it feeds is `DraftingVerbs._route` in
`operator/workspace_broker/drafting_verbs.py`.
