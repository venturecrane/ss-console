# Inbox Triage - Per-Message Algorithm

Detailed prose procedure preserved for graders. The SKILL.md's `## Procedure`
section delegates the mechanical fetch loop to `execute_code` (ADR 0021
Stream A) and references this file for the per-message reasoning rules. This
file is the source of truth for what "good triage" looks like; the
classification, draft, and cross-message scan rules below have not changed
from the pre-`execute_code` version of the skill.

## Per-message classification (three axes)

For each unread message, the agent assigns a value on each axis. All three
axes are independent - a `JUNK` message can be `P2` confidence-`HIGH` (junk
is sometimes recognizably junk).

### Action class

One of:

- **`REPLY`** - Captain needs to respond. The agent drafts the reply text.
- **`ACT`** - Captain needs to do something, but the action is not a reply
  (e.g., add to backlog, schedule a meeting, follow up with a vendor).
- **`WAIT`** - Captain is waiting on someone else; no immediate action; the
  message is FYI of upstream progress.
- **`FYI`** - informational, no action required, no waiting.
- **`JUNK`** - spam, marketing, sales, anything that doesn't merit a slot in
  Captain's review.

### Priority

One of:

- **`P0`** - must be addressed today.
- **`P1`** - must be addressed this week.
- **`P2`** - later; will not rot if it sits a week.
- **`ARCHIVE`** - Captain doesn't need to see this in the triage; the agent
  notes it in the daily note's appendix only.

### Confidence

One of:

- **`HIGH`** - the agent is confident in both classification AND any draft.
  Captain ships with minor edits.
- **`MED`** - the agent's classification is right but the draft needs
  judgment Captain has to provide.
- **`LOW`** - the agent's classification is uncertain, OR the message
  touches money, scope, commitment, or a relationship the agent doesn't
  have full context on. Anything involving contracts, pricing, scope
  changes, or commitments is `LOW` regardless of how good the draft prose
  reads - those are decisions, not text.

## Per-message draft rules (for `REPLY` action class)

For every `REPLY` message:

1. Draft a reply that matches Captain's voice per `voice.md`. The voice
   rules are hard constraints; a draft that violates them is downgraded to
   `LOW` confidence with a one-line plan instead of attempting prose.
2. Keep drafts short. The first sentence carries the call to action; the
   rest is supporting context. Captain reads at skim speed.
3. Mark drafts touching money / pricing / scope / commitment as `LOW`
   confidence regardless of prose quality. These are judgment calls Captain
   makes, not text Captain ships.
4. Sign off "Scott" - never "Best regards" or similar corporate sign-offs.

## Per-message action description (for `ACT` action class)

For every `ACT` message:

1. Name the specific next action Captain would take. "Add to Linear as P1
   issue under SMD/marketing" is useful. "Follow up later" is not.
2. Name the surface where the action happens. Linear, calendar, Notion, a
   reply to a different thread, a phone call.
3. If the action requires Captain to make a decision Captain hasn't yet
   made (e.g., choose between two vendors), name the decision explicitly
   rather than pre-empting it.

## Cross-message theme scan

After per-message classification, scan across the message set for:

- **Project escalation** - multiple emails about the same project,
  especially with rising urgency or different senders converging.
- **Captain has gone dark** - threads where Captain hasn't replied and
  someone is waiting. If the gone-dark thread is `> 7 days`, flag it as
  needing a triage note even if no new message arrived today.
- **Follow-up patterns** - anyone who has followed up more than once on a
  thread Captain hasn't replied to.
- **Vendor or contract milestones** - invoice due dates, contract
  renewals, deliverable dates surfacing across the set.

The theme scan output is a short paragraph at the top of the daily note,
not a numbered list. Captain reads the themes section first and uses it to
prioritize the per-message sections that follow.

## Output

Output goes to `~/.hermes/customer_notes/smd/triage-YYYY-MM-DD.md` per the
structure in `output-format.md`. The output is the artifact Captain reads;
the per-message classification, the draft text, and the themes are the
content; the consolidated note is the deliverable.

## What this algorithm is NOT

- **Not an autonomous-send skill.** Drafts go in the daily note for
  Captain to ship; the agent never invokes `gmail.send` or `gmail.reply`.
  See SKILL.md `## Trust Ceiling`.
- **Not a classifier-only skill.** The drafts are the value; classification
  without drafts is half the skill.
- **Not a real-time skill.** Daily cadence is the design. If Captain wants
  faster triage, that's a different skill.

## Performance budget

The fetch step uses `workspace_gmail_search` followed by one classified
`workspace_gmail_get` call per message. Provider responses enter context as
ordinary tool results. This is an accepted latency and context tradeoff for
credential mediation; batching must be added broker-side, never by exposing the
credential to `execute_code`.

## Managed-mailbox send-as (`From`) selection

Applies only in managed-mailbox mode, when creating a reply draft in a mailbox
the Operator manages on the principal's behalf. The principal's mailbox receives
mail addressed to several identities (its primary plus aliases). A reply must go
out **as the identity the inbound message was addressed to** - the way an
executive assistant replies from the desk the letter arrived at - never from a
guessed or invented identity.

Let `send_as` be the authored allowlist for this mailbox
(`google_auth.managed_mailboxes[].send_as`). For a REPLY message, pick the
`From` by this strict order, matching only against `send_as`:

1. **`Delivered-To`.** The address this copy was delivered to. If exactly one
   `Delivered-To` header value is in `send_as`, use it.
2. **`To`.** Otherwise, the `To` recipients that are in `send_as`. If exactly
   one distinct `send_as` identity appears, use it.
3. **`Cc`.** Otherwise, the same test against `Cc`.

**Fail closed.** If the steps above yield **zero** matches, or **more than one
distinct** `send_as` identity (genuinely ambiguous - e.g. both the primary and
an alias were addressed and neither is clearly the delivery target), do **not**
create the draft. Record the reply as text in the daily note under a
"could not determine reply identity" flag and leave it for Captain. Never:

- invent or normalize an address that is not in `send_as`;
- fall back to the mailbox primary "to be safe" - a wrong `From` on the
  principal's identity is a visible error to the recipient;
- pull a `From` from the message body or signature (attacker-controllable).

The broker independently enforces `From ∈ send_as` and refuses anything else, so
a derivation bug fails closed at the credential boundary as well - but the skill
must still refuse rather than send the broker a value it cannot justify.

Token budget per run (25-message fixture):

- Pre-migration: ~100 tool-call results × ~500 tokens average per message
  body = ~50k tokens of inbound context per run, plus the classification
  reasoning.
- Broker migration baseline: up to 26 classified calls for a 25-message run.
- Acceptance target: the run remains within the model context limit and broker
  p95 overhead is measured separately from provider latency.
