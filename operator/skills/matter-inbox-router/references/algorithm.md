# Matter Inbox Router - Algorithm

The ordering that keeps routing fast, conflict-safe, and substance-free.

## Phase 1 - Fetch (mechanical, one `execute_code` block)

Resolve the Email backend from `customer.yaml` - read the binding, do not hardcode a provider. A Google-backed customer uses the governed `workspace_gmail_*` broker tools (`workspace_gmail_search` + `workspace_gmail_get`); a production law tenant binds the M365 MCP wrapper. Enumerate `is:unread {window}`, fetch each body, accumulate in-process, `print()` one JSON document (`window`, `fetched`, `messages[]`). A single unparseable message becomes a `parse_failed` row; the batch continues. Only the final document enters context (ADR 0021 Stream A) - the same shape as `inbox-triage` Phase 1. (Rework pending: the former `crane_gmail.py` CLI - which spilled each body to a temp file to avoid stdout overflow, #1167 - was retired with the ADR 0045 Workspace-broker move; Phase 1 must be reworked to call the registered Email tools directly, mirroring how `inbox-triage` uses `workspace_gmail_get`.)

## Phase 2 - Reason (agent, in-context)

Per message, in this fixed order:

1. **Resolve sender.** `get_contacts(from_name_or_email)` → contact?; `list_matters(contactId)` → matter(s)? Yields one of: known-client+matter, known-contact-no-matter, unknown. Record the resolution; never associate a matter the resolution did not return (invariant 3).

2. **Conflict cross-check - before any classification commits to a route.** Read-only `get_contacts` + `list_matters` over the sender and any parties named in the body, cross-checked against existing matters' parties. On a hit, one question decides the branch: is this message FORMAL SERVICE of a captioned litigation document (rubric: served-document-intake)? If yes → capture handoff to `discovery-served-watch`, no reply, done. If no → set class `conflict-signal`, create the ONE fixed-shape clearance task the rubric defines (sender + subject + matter candidates; never body text), stop processing this message (no reply, no wedge handoff, no draft). A silent halt - no task left behind - is the same `fails` violation as advancing the message. This ordering is the point: the router decides "is this a conflict?" before it decides "who handles this?"

3. **Classify** into one inbound class (`references/routing-rubric.md` tells). Multi-intent → apply the tie-breaks: conflict wins over everything except served-document capture; primary = the action that advances the matter furthest; record the secondary; a legal question routes to acknowledge-and-defer, never to "answer."

4. **Build the handoff.** Emit `{ target_skill, contact_id?, matter_id?, message_id, inbound_class, extracted_ask, secondary? }`. The `message_id` lets the routed-to skill reply in-thread under the firm's authored send posture; the resolved IDs save it a lookup.

5. **Surface.** One list for the team: each message, its class, its route; the conflict-held and ambiguous ones called out in their own sections so a human sees the exceptions first.

## What this algorithm is NOT

- Not an answerer - it routes substance to the skill that defers it, never resolves it.
- Not a guesser - unknown senders are classed unknown, not forced onto a matter.
- Not a sender or writer - zero outbound, zero Smokeball writes.
- Not blind to conflict - the cross-check precedes routing, structurally.
