# An ops card whose requester already has an answer: close it without a letter

An operations request (an `[ops <tag>]` card) ends in one of three ways from the
seat, and two of them email the requester: SMD replying "done" or "declined"
sends a template notice, and a card nobody answers lapses after seven days into
a "lapsed unanswered" letter. When SMD has finished the work by hand and the
requester was already answered (the Operator replied in their own thread, for
example after [a chronology SMD filed by hand](medchron-deliver-by-smd.md)), any
of those letters repeats or contradicts that answer. Close the card instead. No
letter is sent for it, ever, and the audit ledger records who closed it and the
evidence of the earlier answer.

**Before:** the requester's answer is confirmed (in Sent Items, their thread) and
recorded as a `crane_verify`; that `vfy_` id is the `--source-ref`. Never close a
card whose requester has not actually been answered: that is the silence the
card exists to prevent.

**Run it as root on the seat, dry run first:**

```bash
PYTHONPATH=/opt/workspace-broker /opt/workspace-broker/.venv/bin/python \
  -m workspace_broker.ops_close <tag> --by scott@smd.services \
  --source-ref vfy_<id> --reason "<why no letter is owed>" --dry-run
PYTHONPATH=/opt/workspace-broker /opt/workspace-broker/.venv/bin/python \
  -m workspace_broker.ops_close <tag> --by scott@smd.services \
  --source-ref vfy_<id> --reason "<why no letter is owed>"
```

`<tag>` is the eight hex characters in `[ops <tag>]`. The dry run names the
card's current state and what it would become, and writes nothing. The live run
ends the card as `closed_by_smd` and appends one `OPS_REQUEST_CLOSED_BY_SMD`
ledger row carrying ids only (the card, who closed it, the source ref, who asked,
the text digest, `has_reason`); the reason stays on the card and never reaches
the ledger. Exit status 0 is closed, 2 is refused, 3 is the broker unreachable.

**It closes** an open card, and one past its seven days that the seat has marked
lapsed but has not yet started writing to. **It refuses** a card already answered
done or declined, one withdrawn, one whose lapse letter was sent, one whose
letter an observer has claimed (it may already be on its way), a second close,
and anything that is not an ops card. Only root can run it: the agent and the
gateway are refused by the broker. A refusal writes nothing.

Code: `operator/workspace_broker/ops_close.py` (the CLI) and `ops_close_by_smd`
in `operator/workspace_broker/establishment_ops.py` (tests:
`operator/workspace_broker/tests/test_ops_close_by_smd.py`).
