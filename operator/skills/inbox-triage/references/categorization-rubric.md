# Categorization Rubric

How the agent decides between action classes and priorities. This rubric is the source of truth - when the agent is uncertain, it consults this file rather than improvising.

## Action classes (mutually exclusive)

### REPLY

The message expects a response from Captain. Use REPLY when any of:

- A direct question is asked.
- A scheduling proposal needs a yes/no/counter.
- A document is sent for review and a response is expected.
- A vendor or client is waiting on Captain's decision to proceed.
- Silence would be read as a decision (defaulting to "no") and Captain would not want that.

Do NOT use REPLY when:

- The message is informational only and the sender is not waiting on Captain.
- A reply would be performative ("thanks!" emails that close threads).
- The thread is already in someone else's court (Captain replied last).

### ACT

Captain needs to do something concrete that is not "send a reply." Use ACT when any of:

- A task is being assigned or accepted.
- A document needs to be created, signed, edited, or sent to a third party.
- A meeting needs to be scheduled or rescheduled.
- A purchase, payment, or transfer is being requested.
- A system task is required (provision an account, rotate a key, run a script).

If ACT and REPLY both apply (e.g., "Yes I'll do X, expect it Tuesday" needs reply + the actual doing), classify as REPLY and put the doing in the "Suggested action" line of the reply entry.

### WAIT

Captain has acted and is waiting on someone else. Use WAIT when:

- Captain replied last and the ball is in the other party's court.
- A vendor said "we'll get back to you by X" and X hasn't passed.
- Calendar holds, vendor processing windows, signature collection in flight.

Output for WAIT items: one line stating who Captain is waiting on and the expected response window. No draft, no action.

### FYI

Informational only. No action expected, but Captain would want to know. Use FYI when:

- Important news from a network contact, vendor, or industry source.
- An update on a project or relationship Captain cares about, where no response is needed.
- An invoice or receipt that Captain wants to see but doesn't need to act on.

### JUNK

Newsletters, promotions, transactional confirmations, automated notifications Captain doesn't read. Goes in the Junk section of the daily note. The agent does NOT archive - Captain bulk-acts.

If you're uncertain whether something is JUNK or FYI, default to FYI. False positives on JUNK are worse than false positives on FYI: a missed signal is worse than a moment of skimming.

## Priority (orthogonal to action class)

### P0 - Today

The work happens today. Use P0 when any of:

- The sender expects a same-day response.
- The work is time-sensitive (contract deadline, scheduling window closing, prospect ready to buy).
- Silence today causes irreversible loss or damage to the relationship.
- It's a hot prospect inbound (SMD has no clients yet - these are rare and important).

### P1 - This week

The work needs to happen by end of week. Default for most REPLY and ACT items that aren't urgent and aren't deferrable.

### P2 - Later

The work can wait beyond this week. Use P2 when:

- A response is expected eventually but no one is waiting today.
- The item is research, reading, or "good to do at some point."
- A relationship-builder email where promptness isn't critical.

### ARCHIVE

Captain doesn't need to act and doesn't need to read it later. The agent surfaces these in the ARCHIVE candidates section but does NOT archive them - Captain decides.

## Confidence

Mark `LOW` confidence whenever ANY of these are true, regardless of how well the agent thinks it understands the message:

1. The message touches money - pricing, contracts, invoices, payment terms, scope changes that have cost implications.
2. The message involves a commitment Captain hasn't made yet - signing something, agreeing to a date, accepting scope.
3. The thread has emotional content - frustration, conflict, apology, a relationship that feels strained.
4. The sender's identity matters and the agent isn't sure who they are (could be a prospect, could be a vendor pitch, could be a real opportunity).
5. The agent had to guess at context (referenced a previous conversation the agent didn't see, mentioned a person or project the agent doesn't know about).
6. The agent's draft uses any hedge language ("I think," "I believe," "if I'm reading this right") - those hedges are a tell that the agent isn't sure.

`MED` confidence is the default for `REPLY` and `ACT` items that are clear but where the draft or suggestion could plausibly be wrong on a detail.

`HIGH` confidence is reserved for cases where the agent would be willing to bet the draft is shippable as-is. This should be a minority of items, not the default. If the agent is marking most items HIGH, it's miscalibrated.

## Tie-breakers

- **REPLY vs ACT** with both applicable: REPLY wins, name the action in the reply entry.
- **P0 vs P1** when ambiguous: P1 wins. P0 is reserved for things that genuinely cannot wait until tomorrow.
- **FYI vs JUNK** when ambiguous: FYI wins. Missed signal is worse than a moment of skimming.
- **MED vs LOW** when the draft is good but touches a sensitive area: LOW wins. The rubric overrides the agent's prose-quality judgment on this.
