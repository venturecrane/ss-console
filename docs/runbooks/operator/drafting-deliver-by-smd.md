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

Code: `operator/runners/jobs/jobs/drafting/deliver_by_smd.py`, sharing
`operator/runners/jobs/jobs/hand_delivery.py` with the chronology's
[medchron-deliver-by-smd](medchron-deliver-by-smd.md) (tests:
`operator/runners/jobs/tests/test_drafting_deliver_by_smd.py`). The revise
routing it feeds is `DraftingVerbs._route` in
`operator/workspace_broker/drafting_verbs.py`.
