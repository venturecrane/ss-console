# A document SMD drafted and filed by hand: record it as the job's delivery

**When to use it.** A drafting job (a brief, a discovery set, discovery
responses, a memo, a deposition outline) ended `held` or `failed`, or SMD
stopped it with `jobs.stop`, and SMD drafted the document and filed it into the
matter by hand. Until it is recorded, the seat's job store holds no delivered
draft of that class on the matter, so the attorney's next "please change X" in
that thread queues a brand-new full draft instead of revising what he has. After
it, that request revises the filed document in place, and the attorney is never
told how the draft was made.

**Before:** every document is on the matter and was read back by id when it
was filed (its file id, exact byte count and sha256). This tool checks nothing
against Smokeball itself: it records what that read-back said. Nothing for the
job is in the queue (a job still held for a lane proof is queued: stop it with
`jobs.stop` first). The job dir still keeps its envelope (a finished job's dir
is wiped after 72 hours; past that the tool refuses).

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

**Run it as root on the seat, dry run first:**

```bash
/opt/jobs/.venv/bin/python -m jobs.drafting.deliver_by_smd <job_id> record.json --quiet --dry-run
/opt/jobs/.venv/bin/python -m jobs.drafting.deliver_by_smd <job_id> record.json --quiet --confirm <digest>
```

The dry run prints the ledger records, the `DELIVERY_PROVEN` row, the job's
state entry, the reply's wake text and a digest, and writes nothing. The live
run must repeat that digest; anything changed since is refused, so dry-run
again. It moves the ledger row to `delivered` with the folder and files (a held
row passes through `failed`, which writes one `DRAFTING_JOB_FAILED` audit row,
because the drafting ledger lets a held row move only to failed), writes the
proof row, and marks the job `delivered` in its daemon state.

**What the attorney receives.** With `--quiet`: nothing from this tool. Use it
when SMD already answered him. Without `--quiet`: the lane's own delivered
reply, from the Operator in his request thread, exactly as a seat delivery
sends it; the daemon dispatches it on its next tick and pages SMD
(`reply_unproven`) if it is not in Sent Items within 20 minutes.

**It refuses** (exit 2, nothing written): a job still running or queued, a job
already delivered, a job with a wake still to go or a delivered reply already
claimed, a job dir with no envelope, and a record missing its folder, or any
file's id, name, size or sha256.

**Read it back.** `operator/bin/lane-proof.sh <seat> status` shows nothing
waiting for this job; the request index shows the request delivered. Record a
`crane_verify` of that read-back.

**The attorney's next change request.** It is queued as a revision of these
files. A revise job waits on its own lane key,
`document-drafter.revise_<class>` (`lane_unproven` on its row), until that lane
is proven, which is the intended route to SMD: the request card pages SMD, the
attorney hears only the acknowledgment, and SMD either revises by hand and runs
this tool again on the revise job, or proves the lane
([lane-proof](lane-proof.md)).

Code: `operator/runners/jobs/jobs/drafting/deliver_by_smd.py`, sharing
`operator/runners/jobs/jobs/hand_delivery.py` with the chronology's
[medchron-deliver-by-smd](medchron-deliver-by-smd.md) (tests:
`operator/runners/jobs/tests/test_drafting_deliver_by_smd.py`). The revise
routing it feeds is `DraftingVerbs._route` in
`operator/workspace_broker/drafting_verbs.py`.
