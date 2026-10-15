# An SMD note: have the Operator tell a firm colleague something, in her own thread

When SMD did something for a firm colleague (filed a document she asked for,
finished a request by hand) and she needs to hear it, the Operator should tell
her: from the Operator mailbox, as a reply in the thread she started, copying
the firm people on her request. A turn an inbound email opened may answer only
its sender, so SMD cannot simply ask the Operator to write to her, and SMD never
writes to the firm in the Operator's place. An SMD note is how: SMD records the
exact words on the seat, and the smd-note skill sends them, verbatim.

**Scope.** A note answers a colleague in a thread SHE started within the last 14
days. There is no new-message mode: the anchor email is what keeps a note on the
right matter. If no such thread exists, this is not the tool.

## Before: the words

1. Write the note in the Operator's voice, as the Operator would say it to her.
   No em dashes. Plain, short, factual. It is sent word for word, followed by
   "Thanks,". The Operator adds nothing and changes nothing.
2. Check every status or date claim against the documents themselves before it
   goes into the note (a matter's file, the filed document, the calendar), never
   from memory or a summary.
3. Every matter number, date and id in the note must appear in her request email
   (its subject or body). The broker refuses the note otherwise: a number the
   request never names is the note most likely to be about the wrong matter.
   Today's and yesterday's dates are exempt.
4. **The Captain approves the exact printed text** (the dry run's output below),
   and names who approved it for `--approved-by`.

## Run it, as root on the seat

In the runner daemon's environment (`SMD_WORKSPACE_BROKER_SOCKET`,
`SMD_MEDCHRON_RUN_DIR`), with the medchron venv's python. Put the words in a file
(`note.txt`) so line breaks survive.

```bash
# 1. Find her request email's Graph id.
/opt/medchron/.venv/bin/python -m medchron.note_by_smd --recent --match "<surname or matter number>"

# 2. Dry run: who it reaches, who is copied, the exact words, the digest. Writes nothing.
/opt/medchron/.venv/bin/python -m medchron.note_by_smd <graph_id> --text-file note.txt --approved-by <who> --dry-run

# 3. Read the To, the Cc and the words. Then, with the Captain's approval of that output:
/opt/medchron/.venv/bin/python -m medchron.note_by_smd <graph_id> --text-file note.txt --approved-by <who> --confirm <digest>
```

The live run must repeat the dry run's digest, which covers the email, the words
and the copy list: if any of them changed since the dry run, it is refused and
you dry-run again. It then records the note (`NOTE_JOB_RECORDED`, ids and a
digest only, never the words), sends the smd-note skill's wake, arms the
daemon's reply watch, and waits up to `--wait-min` (default 20) minutes, then
prints one of:

- `reply: sent+located`: the reply went and was found in Sent Items (exit 0);
- `reply: sent-not-located`: it went, but the send's own Sent Items lookup did
  not find it (SMD is paged `reply_not_in_sent_items`);
- `reply: unknown`: the send failed in transport and may or may not have gone
  (SMD is paged `reply_unproven`); do not resend, check Sent Items;
- `reply: not sent`: nothing went in the wait. The reply watch stays armed and
  pages `reply_unproven` at its deadline. A content gate on the turn (a citation
  or tier-1 marker) refuses a note rather than rewording it, so check the
  `bind:` lines and the turn, then rewrite the note and record it again.

`bind:` lines show each `REPLY_BINDING` verdict on the note.

**The wake secret** (`WEBHOOK_SECRET_MCP`) is read in-process from the
environment, the runner daemon's, or the gateway's. It is never printed. When it
cannot be read, or the gate cannot be reached, the wake is left pending and the
daemon's note lane sends it on its next tick.

**It refuses** (exit 2, nothing recorded): an email that is not a received
Inbox message, a sender the seat may not answer, an email older than 14 days,
empty words, more than 2000 characters, an em dash, no approver, an identifier
the email does not carry, a digest that does not match a fresh dry run, and the
same words already recorded on the same email.

## After

1. Confirm the reply in her thread (Sent Items): the right thread, the right
   copy list, the words verbatim. Record a `crane_verify` with that observation.
2. Record what was promised or delivered in the obligation register:
   `.claude/bin/register deliver` (or `register add` with a verbatim quote of the
   note) for the client.
3. Archive the sent note in the engagements repo's correspondence for the
   client.

## Known gaps

- Request cards and the participant-fence replay do not list note jobs.
- A note is one reply. A conversation is not a note: if she answers, her reply
  is an ordinary inbound turn.

Code: `operator/runners/medchron/medchron/note_by_smd.py` and `note_lane.py`;
broker `operator/workspace_broker/note_verbs.py`, `note_ledger.py`,
`note_identifiers.py`; skill `operator/skills/smd-note/SKILL.md`. Tests:
`operator/workspace_broker/tests/test_note_verbs.py`,
`operator/runners/medchron/tests/test_note_by_smd.py`.
