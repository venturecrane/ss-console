# An SMD note: have the Operator tell a firm colleague something

When SMD did something for a firm colleague (filed a document she asked for,
finished a request by hand) and she needs to hear it, the Operator should tell
her: from the Operator mailbox, as a reply in the thread she started, copying
the firm people on her request. A turn an inbound email opened may answer only
its sender, so SMD cannot simply ask the Operator to write to her, and SMD never
writes to the firm in the Operator's place. An SMD note is how: SMD records the
exact words on the seat, and the smd-note skill sends them, verbatim.

**Two forms.**

- **A reply** answers a colleague in a thread SHE started in the Operator mailbox
  within the last 14 days. Her email is what keeps the note on the right matter.
  Use it whenever such a thread exists.
- **A new email** (2026-10-16) is for when she has no thread here: she asked
  Scott directly, or nobody asked (the "it is live" letter). It goes to ONE
  Named Administrator the seat authors in the smd-note skill's
  `settings.scheduled_recipients` (A&P: Christa only; the firm's 10-07 rule bars
  mailing an attorney who did not ask). With no request email, every number in
  the words is checked against Smokeball instead. See "The new-email form" below.

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

The live run must repeat the dry run's digest, which covers the email, the words,
the copy list and the approver: if any of them changed since the dry run, it is
refused and you dry-run again. It then records the note (`NOTE_JOB_RECORDED`,
ids and a digest only, never the words) and queues the smd-note skill's wake in
the note's job dir. It never sends the wake itself: the daemon's note lane, the
one process holding the wake secret, sends it on its next tick and arms the
reply watch then, so there is exactly one DELIVER turn per note. The command
waits up to `--wait-min` (default 20) minutes from the note's creation and
prints one of:

- `reply: sent+located`: the reply went and was found in Sent Items (exit 0);
- `reply: sent (no Sent Items lookup reported)`: the reply went and the send
  reported no lookup; the reply watch counts this proven too (exit 0), so
  confirm it in Sent Items yourself;
- `reply: sent-not-located`: it went, but the send's own Sent Items lookup did
  not find it (SMD is paged `reply_not_in_sent_items`);
- `reply: unknown`: the send failed in transport and may or may not have gone
  (SMD is paged `reply_unproven`); do not resend, check Sent Items;
- `reply: wake still pending`: the note lane has not sent the wake yet (it
  retries, and pages `wake_lost` if it never can);
- `reply: wake failed: ...`: the lane gave up on the wake; SMD is paged
  `wake_lost`;
- `reply: not sent`: the wake went and no reply settled in the window. The
  reply watch pages `reply_unproven` at its deadline. A content gate on the turn
  (a citation or tier-1 marker) refuses a note rather than rewording it, so check
  the `bind:` lines and the turn, then rewrite the note and record it again.

Every ending but the first two exits 1. `bind:` lines show each `REPLY_BINDING`
verdict on the note.

**It refuses** (exit 2, nothing recorded): an email that is not a received
Inbox message, a sender the seat may not answer, an email older than 14 days,
empty words, more than 2000 characters, an em dash, no approver, an identifier
the email does not carry, a digest that does not match a fresh dry run, and the
same words already recorded on the same email.

## The new-email form

Same words rules as above (the Operator's voice, no em dash, the Captain approves
the exact printed text), plus a **subject** the Captain approves too. Then:

```bash
# Dry run: To, Subject, each matter with the name Smokeball gives it, the plain
# numbers, any dates or case numbers the words state, the words, the digest.
/opt/medchron/.venv/bin/python -m medchron.note_by_smd --to <admin address> --subject "<subject>" \
  --text-file note.txt --approved-by <who> [--plain-number 140 ...] --dry-run

# Live, with the Captain's approval of that output:
/opt/medchron/.venv/bin/python -m medchron.note_by_smd --to <admin address> --subject "<subject>" \
  --text-file note.txt --approved-by <who> [--plain-number 140 ...] --confirm <digest>
```

- **Every three-to-ten-digit number** in the words is looked up as a Smokeball
  matter number. Read each printed matter name: it is how a mistyped number
  shows. A number that is not a matter (a count, a year, an amount) must be
  passed as `--plain-number`, or the note is refused with that hint. Smokeball
  unreachable refuses ("could not look"); it is never read as "no matter".
- **Dates and case numbers** are listed, not checked: nothing on the seat can
  vouch for them without her email. Check them against the documents yourself
  before the Captain approves.
- The live run looks every matter up again; a matter whose name changed since
  the dry run changes the digest and is refused.
- **It refuses** (exit 2): a recipient who is not a Named Administrator or not
  in the skill's `scheduled_recipients`, no subject or a subject over 200
  characters, on two lines or with an em dash, a number neither found nor
  declared plain, a declared number the words do not carry, and the same
  subject and words already recorded for that person.
- It goes as one new email with no copies; the broker sets To and Subject. The
  endings printed are the same as the reply form's. A send Graph refused cannot
  be proven unsent (there is no thread to search), so it stays `unknown` and is
  never resent: check Sent Items.

## After

1. Confirm it in Sent Items: for a reply, the right thread and copy list; for a
   new email, the recipient and subject; the words verbatim either way. Record a
   `crane_verify` with that observation.
2. Record what was promised or delivered in the obligation register:
   `.claude/bin/register deliver` (or `register add` with a verbatim quote of the
   note) for the client.
3. Archive the sent note in the engagements repo's correspondence for the
   client.

## Known gaps

- Request cards and the participant-fence replay do not list note jobs.
- A note is one message. A conversation is not a note: if she answers, her
  reply is an ordinary inbound turn.

Code: `operator/runners/medchron/medchron/note_by_smd.py` and `note_lane.py`
(job dir `/run/smd-medchron/note-jobs`, root-only; boot smoke `note-lane-ticking`);
broker `operator/workspace_broker/note_verbs.py`, `note_ledger.py`,
`note_identifiers.py`; skill `operator/skills/smd-note/SKILL.md`. Tests:
`operator/workspace_broker/tests/test_note_verbs.py`,
`operator/runners/medchron/tests/test_note_by_smd.py`.
