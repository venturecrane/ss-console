# Drafting rules confirmed by reply: seed each attorney's signature block

An attorney's own drafting rule is recorded only when he replies with a plain
yes to its read-back. A plain yes may end with his usual closing, his name, and
his signature block exactly as he writes it. The seat knows that block only from
SMD's root seed, which reads his newest 50 emails in the seat mailbox. Nothing
the seat reads at runtime teaches it a block, so no email can make a qualifier
look like part of his signature.

**After every release that carries this code**, run the seed as root on the
seat once for each attorney on the roster (repeat `--sender` per address):

```bash
PYTHONPATH=/opt/workspace-broker /opt/workspace-broker/.venv/bin/python \
  -m workspace_broker.sender_block seed --sender <roster address> --sender <roster address>
```

It prints one line per address and never the block's text:

```
<address>: block 7 lines, matched 45 of 50 emails
```

Check that each attorney shows a block (more than 0 lines) and that the matched
count is most of the emails read. The seed is idempotent and safe to run again
at any time. A block needs at least 3 lines ending at least 3 of his emails and
a majority of the emails read. When the line ends `kept the current block`, the
mail now ends with his block plus extra lines above or below it, and the seed
kept the block it had. That is expected after he adds a postscript or a footer
to several emails. If he really changed his signature, look at his recent mail
before you decide anything.

**A seat with no block is safe.** His signed yes reads as not plain, so the rule
is read back once more. A second reply that is not a plain yes hands the rule to
SMD (`rule_needs_person` in the owed list), and he is told SMD will confirm it
in his thread. Nothing is ever recorded short of his plain yes.

Exit status 0 is done, 2 is refused (for example, no `--sender`), and 3 is the
broker unreachable. Only root can run it: the agent and the gateway are refused
by the broker.

Code: `operator/workspace_broker/sender_block.py` (the seed and its CLI),
`operator/workspace_broker/readback_rounds.py` (at most two read-backs, then
SMD), `operator/hermes-smd-overlay/shared/reply_shape.py` (the plain-yes
grammar). Tests: `operator/workspace_broker/tests/test_sender_block.py` and
`operator/hermes-smd-overlay/tests/test_rule_confirm_live_path.py`.
