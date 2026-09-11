---
name: inbox-triage
description: >-
  Triages the owner's inbox and drafts the replies. Daily Gmail triage with
  categorized reply drafts for the owner.
version: 0.2.1
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: []
metadata:
  hermes:
    tags: [Email, Triage, Draft, SMD, Customer-Zero]
---

# SMD Inbox Triage Drafter

## When to Use

Reads unread mail from Captain's Gmail, produces a structured triage document with categorization, priority, and (for replies) draft text. Writes output to a daily note file. **Never sends, never archives, never replies on the user's behalf.** Captain reads, ships, and grades.

This is SMD's customer-zero capability. We are using ourselves to learn the delivery shape before we sell it to marketing agencies.

## Mode

**Gmail triage (scheduled / on-demand)** — read Captain's unread Gmail, produce the triage note, draft replies for Captain to send. Never sends from Gmail.

This mode can target **either** Crane's own mailbox (default) **or an authored managed mailbox** — the principal/team inbox Crane manages on Captain's behalf, the way an executive assistant works a principal's inbox alongside their own. Pass `--mailbox <address>` (the authored primary, e.g. `smdurgan@smdurgan.com`). When a managed mailbox is targeted, every `workspace_gmail_*` call carries that `mailbox`, and REPLY messages get a **real Gmail draft** written into that mailbox's Drafts (so Captain edits and sends from Gmail), with the `From` chosen by the send-as rule in `references/algorithm.md`. With no `--mailbox`, behavior is unchanged (Crane's own box; draft text goes in the note only — Crane has no principal identity to draft as in its own mailbox). The broker fail-closes any mailbox or `From` not authored in `google_auth.managed_mailboxes`; this skill never sends.

## Prerequisites

Requires `workspace_gmail_search`, `workspace_gmail_get`,
`workspace_calendar_list`, and (managed-mailbox mode only)
`workspace_gmail_create_draft`. Do not use the Hermes-native `google-workspace`
skill, connector CLIs, `terminal`, or `execute_code`. Those paths have no
Workspace credential.

## How to Run

Triage the current unread inbox:

```
hermes run inbox-triage
```

Triage a specific window:

```
hermes run inbox-triage --window "newer_than:2d"
```

Triage at most N messages (cost / latency cap):

```
hermes run inbox-triage --max 25
```

## Procedure

The skill runs in two phases.

### Phase 1 — Fetch

1. Call `workspace_gmail_search` with query `is:unread <window>` and the
   requested maximum. In managed-mailbox mode, pass `mailbox: <address>`.
2. Call `workspace_gmail_get` for each returned message ID (same `mailbox`).
   Request enough of the headers to read `Delivered-To`, `To`, and `Cc` — the
   send-as rule depends on them.
3. Continue past an individual read failure and record the failed ID. Never
   replace source data with inferred fields.

### Phase 2 — Reason (agent, in-context)

The agent reads the broker tool results and, per the rules in `references/algorithm.md`:

1. **Classify each message** along three axes — `action_class`, `priority`, `confidence`. See `references/categorization-rubric.md`.
2. **Draft replies** for `REPLY`-classified messages, matching Captain's voice per `references/voice.md`. Drafts touching money / scope / commitment are forced `LOW` confidence regardless of prose quality. In **managed-mailbox mode**, write the reply as a real Gmail draft with `workspace_gmail_create_draft` (`mailbox`, `thread_id`, and `from` set per the send-as rule in `references/algorithm.md`); if that rule cannot pick a single authored `From`, **do not create the draft** — record the reply as text in the note and flag it for manual handling. In own-mailbox mode, the draft is text in the note only.
3. **Name the next action** for `ACT`-classified messages — the specific tool/surface and the concrete step.
4. **Cross-message theme scan** — escalation patterns, gone-dark threads, repeated follow-ups, vendor/contract milestones.
5. **Write the daily note** to `~/.hermes/customer_notes/smd/triage-YYYY-MM-DD.md` per `references/output-format.md`.

Detailed per-axis rules and cross-message scan heuristics live in `references/algorithm.md`. The reference is the source of truth for what "good triage" looks like; this procedure is the dispatch shape.

### Trust Ceiling

Customer-zero ceiling for SMD: **draft only.**

The agent MAY:

- Read mail through `workspace_gmail_*`.
- Write to the local file system inside `~/.hermes/customer_notes/smd/`.
- Use `workspace_calendar_list` to check Captain's availability.
- In managed-mailbox mode, create a **reply draft** with
  `workspace_gmail_create_draft` (the review artifact — a draft is not a send).
  The `From` must be an authored send-as; the broker refuses anything else.

The agent MUST NOT, without explicit Captain instruction in the current invocation:

- Send mail (`gmail.send`) — there is no send tool in this skill's surface.
- Reply-and-send, or send a draft.
- Modify labels, archive, or delete (`gmail.modify`).
- Create calendar events.
- Modify any file outside `~/.hermes/customer_notes/smd/`.

A created draft sits in the mailbox's Drafts for Captain to review and send. It
is never sent by this skill.

If the agent infers it would help to do one of these, it MUST instead include a "Recommended action that I did not take" note in the daily triage with the exact command it would have run.

### Voice Rules

**Two distinct identities — never conflate them:**

**1. Draft replies** (replies the agent prepares for Captain to send to third parties). These go out AS Captain, in Captain's voice. See `references/voice.md` for the long form. Hard rules:

- No em dashes. Period.
- No "I hope this email finds you well." No "Just wanted to follow up." No "Touching base."
- No "circle back," "synergy," "leverage," "level-set," "deep dive."
- Active voice. Short sentences. Plainspoken.
- Sign-off: "Scott" — never "Best regards" or similar.
- No emojis in business correspondence unless the inbound thread is already using them.

If the agent cannot write a draft that passes these rules, it marks the message `LOW` confidence and writes a one-line plan for the reply instead of attempting prose.

**2. The triage report itself** (the note/email the agent sends to Captain or `team@`). This is **Crane's own communication to its principal**, sent from Crane's own identity (`smdcrane@agentmail.to`) — Chief-of-Staff voice: plainspoken, direct, executive-summary first. It is authored AS Crane.

- **NEVER sign the report "Scott."** Crane is not the principal; signing as Scott is an identity error. Sign as "Crane" or use no sign-off.
- The same em-dash / no-AI-tell discipline applies.
- The "Sign-off: Scott" rule above governs the embedded draft _replies_ only, never the report envelope.

## Pitfalls

Common failures: high confidence on drafts that touch money/scope/commitment (these must be LOW even if prose is good); AI-tells leaking through; missing cross-message themes.

## Verification

A successful triage run satisfies all of:

1. Every unread message in the window gets a category and priority.
2. Every `REPLY` either has a draft Captain can ship with at most minor edits, OR is flagged `LOW` confidence with a one-line plan.
3. Themes are surfaced (the things Captain would otherwise miss by reading message-by-message).
4. No false confidence: anything that touches money, scope, or commitment is `LOW` confidence even if the prose is good.
5. Output is read in under 5 minutes by Captain. If the agent's draft is longer than that to skim, it's failing.

## References

- `references/algorithm.md` — detailed per-message classification, draft, and cross-message theme rules
- `references/voice.md` — Captain's voice rules, with positive and negative examples
- `references/output-format.md` — exact structure of the daily triage note
- `references/categorization-rubric.md` — how the agent decides between action classes
- `references/test-cases.md` — synthetic inbox samples for regression testing
