# A firm rule the client asked for in writing: SMD records it

When an attorney writes a standing rule in an email to the Operator ("This
should be a firm rule") and the Operator did not record it (it refused on reach,
or the rule was handed to SMD as `rule_needs_person` in the owed list), SMD
records it from the seat. The attorney is not asked to confirm it again: his own
email is the confirmation. The rule goes in through the same path a confirmed
"yes" takes, and nobody is written to about it. He judges the next document.

**What the broker checks, against the mailbox itself:** the sender is on the
seat's roster (`scope.inbound_allow_from`); the email is a received message in
the Operator's Inbox from that sender; the audit log recorded it arriving; and
the quote you give appears in that email's own text (the part he wrote, not mail
quoted below it), compared ignoring spacing, case and curly quotes. Any miss is
a refusal and nothing is written.

**Find the email's Graph id** (the request index lists the seat's recent mail):

```bash
/opt/jobs/.venv/bin/python -m jobs.note_by_smd --recent --match "<surname or subject word>"
```

**Write each rule and its quote to files** (keeps apostrophes out of the shell):
`rule.txt` holds the rule as it should read in the firm's spec, one sentence;
`quote.txt` holds at least 20 characters copied exactly from his email that state
it.

**Dry run, then record, as root on the seat:**

```bash
PYTHONPATH=/opt/workspace-broker /opt/workspace-broker/.venv/bin/python \
  -m workspace_broker.rule_record --sender <attorney address> --message-id <graph id> \
  --text-file rule.txt --quote-file quote.txt --doc-kinds discovery_response \
  --by scott@smd.services --reason "<why SMD records it>" --dry-run
PYTHONPATH=/opt/workspace-broker /opt/workspace-broker/.venv/bin/python \
  -m workspace_broker.rule_record --sender <attorney address> --message-id <graph id> \
  --text-file rule.txt --quote-file quote.txt --doc-kinds discovery_response \
  --by scott@smd.services --reason "<why SMD records it>" --confirm <digest>
```

`--output-class` defaults to `work_product` and `--property` to `voice` (use
`format` for a layout rule). `--doc-kinds` is one or more of the wording store's
kinds, comma separated; `--attorney "<name>"` limits the rule to his documents.
The dry run prints the rule, the subject, the email it cites and a digest, and
writes nothing. The live run must repeat that digest. It prints the new rule's
id and its install run. One rule per run: five rules are five dry runs and five
records.

**Then probe it** (a minute or two after, once the intake has run):

```bash
PYTHONPATH=/opt/workspace-broker /opt/workspace-broker/.venv/bin/python \
  -m workspace_broker.rule_record --probe <rule id>
```

`installed_at` set and `in_wording_store: true` mean the drafting lanes read it
from the next document. `install_failed` names a failed install; it is also
recorded as owed to SMD, so the hourly triage lists it.

**Once only.** The same words already committed for that attorney under the same
subject answer with the existing rule and write nothing; the same words already
in the wording store stop the command before it reaches the broker. An open,
unconfirmed proposal of the same words is ended quietly and replaced, so a later
"yes" cannot commit it twice. The same words under a different subject are
refused: decide which is right first.

**The record.** One `RULE_RECORDED_BY_SMD` audit row: the rule and run ids, who
recorded it, who asked, the email's ids, and digests of the quote and the rule;
never their words or the reason. The installed rule names the attorney as who
asked and the SMD address as who applied it.

Exit status 0 is recorded (or a clean dry run, or already in force), 2 is
refused, 3 is the broker unreachable. Only root can run it: the agent and the
gateway are refused by the broker. Graph seats only (the email is read from the
mailbox).

Code: `operator/workspace_broker/rule_record_verbs.py` (the two verbs) and
`operator/workspace_broker/rule_record.py` (the command). Tests:
`operator/workspace_broker/tests/test_rule_record_by_smd.py`.
