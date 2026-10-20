# A staged send: SMD's exact words and files, sent by the seat

When SMD needs a seat to send one specific message with files SMD made (the first
case, 2026-10-20: two pilot flyers, as PDFs, to The Brokery's broker), the seat
sends it, through its own gate, from its own mailbox, with its own audit row.
Never from a laptop with the vendor key: that skips every fence and leaves no row.
Never by asking the model: the model would choose the words and the files.

The overlay's `shared/staged_send.py` writes the same consume-once envelope a
routine's pre_run writes (`shared/prerendered_dispatch.py`). A one-off managed
cron job wakes that routine's turn, and the trust plugin's `pre_llm_call` sends
the envelope out of turn through the full gate. The broker re-validates the files
(`operator/workspace_broker/agentmail_attachments.py`), fences the recipients,
sends from the seat's pinned inbox and writes the `CONFIRM_SEND_DISPATCHED` row
with every file's sha256.

**What it carries.** One message, one to three files: PDFs (2 MiB each) and at
most one `.xlsx` (512 KiB), 3 MiB together. AgentMail seats only; a Graph seat
still carries exactly one workbook. A PDF is refused if it is encrypted, carries
JavaScript, an automatic action, any action but an in-document jump or a web
link, an embedded or referenced file, rich media or an XFA form, has more than 50
pages, or has no readable text. The gate reads each file's text, as it reads the
words.

**All or nothing.** If any file is refused, by the gate or the broker, nothing is
sent and the turn says so. There is no "send it without the files" fallback.

## Before

1. The Captain approves the exact words, the subject, the recipient and the
   files (by name and sha256). No em dashes. Plain and short.
2. The recipient must be one the seat may write to on that routine's lane. On
   Brokery that is a rostered address (`scope.inbound_allow_from`) on the
   `open-house-visitor-capture` lane (`settings.recipients: roster`).
3. Read the memory trap first: a seat whose roster includes the client can reach
   the client. Read back the recipient in the dry run.

## Run it

Copy the files and the words to the seat (hash them after the copy: `sftp put`
can leave an empty file; the sha256 pin below catches it, but check first):

```bash
fly ssh sftp shell -a hermes-<slug>      # put each file into /tmp/staged/
fly ssh console -a hermes-<slug> -C "sh -c 'chmod -R a+rX /tmp/staged && sha256sum /tmp/staged/*'"
```

Dry run, as the hermes user (`operator/bin/seat-probe.sh` drops to it). Put the
command in a script on the seat (`/tmp/staged/stage.sh`), because seat-probe
wraps a one-string command in `sh -c '...'` and a quoted subject breaks it:

```bash
# /tmp/staged/stage.sh
/opt/hermes/.venv/bin/python -m shared.staged_send \
  --persona <persona> --skill <routine skill> --to <address> --subject "<subject>" \
  --body-file /tmp/staged/body.txt \
  --attach /tmp/staged/<file-a>.pdf=<sha256> --attach /tmp/staged/<file-b>.pdf=<sha256> "$@"
```

```bash
operator/bin/seat-probe.sh <slug> sh /tmp/staged/stage.sh
```

It prints the recipient, the subject, the words, each file with its size,
sha256 and the text the gate reads in it, the dates it seeds, any checklist
warning, and a digest. It writes nothing.

With the Captain's yes on that exact output,
`operator/bin/seat-probe.sh <slug> sh /tmp/staged/stage.sh --confirm <digest>`. It writes the envelope and its provenance handoff to
`/opt/data/profiles/<persona>/.smd/pre_run/`, refuses to overwrite one that has
not been consumed, and prints the cron command. Within 20 minutes (the envelope's
binding window), run it:

```bash
operator/bin/seat-probe.sh <slug> hermes -p <persona> cron create 1m "<printed prompt>" \
  --repeat 1 --name op-managed:<persona>:<routine skill>
```

The managed name is load-bearing: it is how the routine (and its lane) is
resolved for that turn. The turn itself may send nothing but one fixed line.

## After

1. The envelope is renamed `<skill>.dispatch.consumed.json` when the turn takes
   it. Still `.dispatch.json` after the job fired means it never bound (late,
   wrong name, wrong persona).
2. The terminal seam: the sent message on the seat's inbox, read back through the
   AgentMail API, shows every file; download each and compare its sha256 with the
   pin. The `CONFIRM_SEND_DISPATCHED` row's `attachment_sha256` lists the same
   hashes, comma-joined in order. Record a `crane_verify` with both.
3. Record the delivery in the obligation register and archive the sent message
   in the engagements repo's correspondence for the client.

**It refuses** (exit 2, nothing written): a file whose sha256 is not the pin, a
file that is not a `.pdf` or `.xlsx`, any refused file, no file, no recipient, a
bad address, an empty or two-line subject, an identifier in the words or files
other than a date (nothing on the seat could verify it), a digest that does not
match, and an unconsumed envelope already in place.
