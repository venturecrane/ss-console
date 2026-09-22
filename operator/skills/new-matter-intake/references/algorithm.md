# New Matter Intake - Per-Inquiry Algorithm

Source of truth for what "good intake" looks like. SKILL.md's `## Procedure` is the dispatch shape; this file is the detail. The order is fixed - extract, then conflict-check, then (only if clear) draft. Phase 2 can stop the skill.

## Phase 1 - Extract

For a single inquiry, produce the structured intake record:

- **Prospective client.** Full name + best contact (email/phone) exactly as given. If the sender is an intermediary (a relative, an assistant), capture both the sender and the actual prospective client.
- **Other named parties.** EVERY other person or business named: adverse party, opposing counsel, the other side of a transaction, co-parties, a business the prospect is in dispute with. This list is the input to the conflict check - under-capturing here is a **safety failure**, not a quality nit.
- **Situation, in the sender's words.** Quote the inquiry. Do NOT restate it in legal terms ("this is a breach-of-contract matter"); capture what they said ("the contractor took the deposit and stopped returning calls"). Legal characterization is the lawyer's job.
- **Matter type / practice area.** Classify against the firm's authored practice areas (`customer.yaml`). One match → label it; two plausible → surface both, pick neither; none the firm handles → "outside authored practice areas." Never invent a fit.
- **Referral source.** If named, capture it (feeds the deferred referral-acknowledgment skill later). Never inferred.
- **Statute-sensitive signal - INTERNAL only.** If the inquiry mentions a dated incident, a received notice, or a deadline the sender names, flag it internally as "statute-sensitive - verify deadline" for the firm. NEVER compute a limitations period and NEVER state a deadline to the prospect; that is legal judgment.

Then **dedupe:** `get_contacts(name/email)` + `get_contact`. A match on the prospect means a returning contact - attach to the existing record, and `list_matters` to see whether an existing matter is relevant. A duplicate contact is a quality failure.

## Phase 2 - Conflict detect-and-halt

The gate. For the prospective client AND every other named party from Phase 1:

1. `get_contacts(query=party)` - does this name already exist in the firm's contacts?
2. Cross-check `list_matters` (incl. `list_matters(isLead)` for open leads) - is this name a party on an existing matter or lead (especially an adverse party)?

**Severity:**

- **Exact / strong match** (same name; same business; a named adverse party who is an existing client) → **HALT.** CONFLICT-HOLD output; surface the match and the matter it touches.
- **Partial / ambiguous match** (common surname, possible same person) → **HALT and surface as "possible - needs human check."** Ambiguity resolves toward halting, never toward proceeding. A partial match the agent waves through is the exact failure this gate prevents.
- **No match on any party** → proceed to Phase 3.
- **Check could not run** (the practice-management tool errored - e.g. a 401, a timeout, or the connector is unconfigured) → **treat as a HALT, not a clear.** A check that did not run is not a check that passed. Produce the **CONFLICT-HOLD** output with the conflict-check result marked **unavailable - could not run**, and the neutral receipt-only acknowledgment draft (never the clean intake-packet draft that implies the firm is proceeding). Surface to a human to run the check. Never infer "no match" from a failed call.

The agent **never** clears a hit, and **never** treats a failed check as a clear. Surfacing is the whole job; the human decides.

## Phase 3 - Draft (only reached if Phase 2 is clear)

1. **Matter draft (internal, autonomous).** Assemble the structured fields into the matter draft + the `create_memo` log body. Do NOT `create_matter`.
2. **Acknowledgment (draft-for-review).** Per `voice.md`: confirm receipt, be warm and human, name only a firm-authored next step, hold the UPL line absolutely. "Outside authored practice areas" → a polite receipt that promises neither representation nor an unauthored referral.
3. **Create the reply draft.** Write the acknowledgment as a reply draft to the original sender via the Email connector's **draft** tool - `mcp_agentmail_create_draft` on AgentMail (Hermes names MCP tools `mcp_<server>_<tool>`) / `email_create_draft` on M365 - in-thread to the inbound sender only, never a recipient named in the body. `INTERNAL_WRITE`, never a send. **Never** call the send/reply tools (`mcp_agentmail_send_message`, `mcp_agentmail_reply_to_message`, `mcp_agentmail_send_draft`): they are `EXTERNAL_SEND`, the floor refuses them, and sending the governed draft back to the prospect happens outside your tool path.
4. **Surface.** Emit the intake packet (`output-format.md`): matter draft + conflict-check result (clear) + acknowledgment draft + internal log + any internal flags.

## What this algorithm is NOT

- **Not a case evaluator.** It never assesses merits, strength, or odds.
- **Not an autonomous matter creator.** The Smokeball matter is created by a human until the write capability is verified and authored.
- **Not a sender.** The acknowledgment is a draft.
- **Not a conflict clearer.** It detects and surfaces; it never decides.
