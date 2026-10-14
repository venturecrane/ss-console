# A chronology SMD filed by hand: record it, and let the Operator tell the requester

When SMD builds and files a chronology (or an update into one) by hand while the
seat's own job for that request sits `failed` or `held`, the requester is still
owed the notice a seat delivery gives: from the Operator, as a reply in their
own request thread, copying the firm people on that request. Only the job's
completion wake can bind that reply, so record the delivery on the job and let
the daemon send its own wake. Never answer the request's ops card ("done" and
"declined" both email the requester a second, template notice), and never write
the notice yourself.

**Before:** the file is on the matter and read back by id at its exact byte
count; the independent review and the proofs passed; nothing for the job is in
the queue.

**Write the record** (`record.json`, ids and counts only, no dollar figure
anywhere a person reads; `cents` is the ledger's cost column):

```json
{
  "documents": 12,
  "pages": 400,
  "cents": 1500,
  "folder_id": "<delivered folder id>",
  "files": [
    { "file_id": "<new file id>", "name": "<file name>", "bytes": 737000, "sha256": "<hex>" }
  ],
  "replaced": { "file_id": "<old file id>" },
  "covered": { "covered": ["<doc id>"], "uncovered": ["<doc id>"], "pulled": 2 }
}
```

`covered` is required: it is what the next update reads its delta from.
`documents` and `pages` are what the delivery read, and they move the firm's
page allowance exactly as a seat run would.

**Run it as root on the seat, dry run first:**

```bash
/opt/medchron/.venv/bin/python -m medchron.deliver_by_smd <job_id> record.json --update --dry-run
/opt/medchron/.venv/bin/python -m medchron.deliver_by_smd <job_id> record.json --update
```

`--update` adds the wake's `Request: update into the delivered chronology.`
line, so the reply says the chronology was updated in its own folder. The dry
run prints the ledger records, the `DELIVERY_PROVEN` row, the state entry and the
wake text, and writes nothing. The live run moves the row to `running` and then
`delivered`, writes the proof row, and marks the job's daemon state `delivered`
with a pending wake. The daemon dispatches the wake on its next tick and arms the
reply watch, which pages SMD (`reply_unproven`) if the reply is not in Sent Items
within 20 minutes.

**It refuses** a row already delivered, a job still queued, a job with no
envelope (no requester or request email to answer), a hold reply still to go,
a delivered reply already claimed, and a record missing its folder, files or
covered set. A refusal writes nothing.

**After:** confirm the reply in the requester's thread (Sent Items) and record a
`crane_verify` for it.

Code: `operator/runners/medchron/medchron/deliver_by_smd.py` (tests:
`operator/runners/medchron/tests/test_deliver_by_smd.py`). The reply binds when
the job's request reference is a mailbox Graph id as well as an RFC Message-ID
(`operator/workspace_broker/reply_binding.py`, 2026-10-14).
