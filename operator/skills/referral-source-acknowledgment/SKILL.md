---
name: referral-source-acknowledgment
description: Drafts a thank-you to the source who referred a matter. The courtesy note is warm, prompt, and confidential; it acknowledges the referral without disclosing client identity or matter detail. Drafted for review by default; sending follows the firm's authored send ceiling.
version: 0.1.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: [python3]
metadata:
  hermes:
    tags: [Law, Referral, Relationship, Confidentiality, DraftForReview]
  smd:
    vertical: law-firm
    skill_type: drafting (relationship)
    action_class: read + external_send
    connectors:
      - smokeball # PracticeManagement — matter → referral source contact (read)
      - email # customer-bound — the drafted thank-you (send is draft-for-review)
---

# Referral Source Acknowledgment

When a new matter arrives through a referral, this skill drafts a prompt, warm thank-you to the person who sent it — the courtesy that keeps a firm's referral network alive. It identifies the referral source from the matter, drafts an acknowledgment in the firm's voice, and surfaces it for a human to send.

Its defining constraint is **confidentiality.** A referral source is, by definition, _not_ the firm's client, and the existence and details of the new engagement are confidential. So the thank-you acknowledges the gesture **without disclosing who the client is or what the matter concerns** unless the firm has explicit authorization to do so. The relationship value is in the promptness and warmth, not in confirming details the firm may not share.

## When to Use

Use when the firm wants to reliably acknowledge referrals — the small, often-skipped courtesy that compounds into a referral pipeline. It is off the matter-progression path (no matter step depends on it), which is exactly why it tends to get dropped without a skill that catches it.

Runs event-driven (a new matter is opened with a referral source recorded) and scheduled (sweep recent matters for un-acknowledged referrals).

## Prerequisites

Reads Smokeball (`get_matter` / `get_contact`) to identify the referral source on a new matter, and the customer-bound **Email** connector to draft the thank-you. Requires `python3` for the fetch block. Whether the thank-you sends or drafts follows the firm's authored `external_send` ceiling (see `operator/references/send-posture.md`).

## How to Run

```
hermes run referral-source-acknowledgment                  # sweep recent matters for un-acked referrals
hermes run referral-source-acknowledgment --matter <id>    # acknowledge one matter's referral
```

## Procedure

Two phases (ADR 0021 Stream A). The mechanical referral-source resolution runs in one `execute_code` block; the confidentiality-bound drafting stays in the agent's reasoning loop.

### Phase 1 — Fetch (single `execute_code` block)

Enumerate recent matters carrying a recorded referral source. For each, resolve the source contact (`get_contact`) and capture only what the thank-you needs — the referrer's name and contact, and whether the firm has authored a flag permitting client/matter detail to be shared with this source. Accumulate in-process; `print()` one JSON document. A matter whose source can't be resolved is `parse_failed`; the sweep continues.

### Phase 2 — Reason (agent, in-context)

Per `references/algorithm.md` and `references/voice.md`:

1. **Confirm the referral source** — a real, resolved Smokeball contact marked as the matter's referral source. An unrecorded or unresolved source is surfaced for a human, never guessed.
2. **Apply the confidentiality gate.** Default: acknowledge the referral generally ("thank you for thinking of us / sending someone our way") **without** naming the client or describing the matter. Only if the firm has authored explicit permission to share detail with this source does the draft reference specifics.
3. **Draft in the firm's voice** (`voice.md`) — brief, warm, genuine; a real thank-you, not a templated form-letter.
4. **Surface for review.** The draft is surfaced; a human reviews and sends under their own identity. No autonomous send.

## Trust Ceiling

**Read + draft autonomous; send is draft-for-review (`draft_for_review`, non-raisable).**

The agent MAY: read the matter's referral source from Smokeball; draft a confidentiality-respecting thank-you; surface it for review.

The agent MUST NOT: send autonomously; disclose the client's identity or matter detail to the referral source without authored permission; invent a referral link that isn't recorded in Smokeball; thank the wrong person.

## Safety invariants (any violation → `fails`, no recovery)

1. **Confidentiality first.** No client identity or matter detail goes to a referral source absent the firm's authored permission. When in doubt, acknowledge generally.
2. **External send follows the authored ceiling.** Whether the thank-you sends or drafts is the firm's authored `external_send` ceiling, not a fixed rule (`draft_for_review` — shipped under a human's identity — is the recommended starting posture). See `operator/references/send-posture.md`.
3. **No fabricated referral.** The source is acknowledged only when Smokeball records the referral link; an unresolved source is surfaced, not assumed.
4. **Right recipient.** The thank-you goes to the resolved referral source, never to the client or another party.
5. **Privilege.** The matter detail used to identify the source stays internal and out of the outbound text by default.

## Matter identifiers (projected, never composed)

- In email, task, and memo text, refer to the matter by its NUMBER, taken ONLY
  from the `matterNumber` field the connector projected onto a record you read
  this turn (task, event, memo, file, and document reads all carry it when the
  matter resolves). Never compose, recall, or infer a matter number, and never
  carry one over from another matter or an earlier turn. If a read returned no
  `matterNumber`, write "matter number unavailable" rather than supplying one.
  On this skill the number belongs to the internal log, not the thank-you: the
  outbound text carries no matter identifier at all.

## Pitfalls

Naming the client or the matter to the referrer out of friendliness (the central confidentiality breach); thanking a guessed source when none is recorded; a generic form-letter tone that reads as automated and undercuts the relationship; letting the draft send without review.

## Verification

1. The referral source is a resolved Smokeball contact marked as the matter's referrer; unresolved sources are surfaced, not guessed.
2. No client identity or matter detail appears in the draft unless the firm authored permission to share it.
3. The recipient is the referral source, not the client or another party.
4. The draft is surfaced for review; no autonomous send.
5. The voice reads as a genuine, brief thank-you, not a template.

## References

- `references/algorithm.md` — referral-source resolution and the confidentiality gate (the load-bearing logic)
- `references/voice.md` — the firm's brief, warm thank-you voice _(parity fast-follow)_
- `references/output-format.md` — the acknowledgment draft _(parity fast-follow)_
- `references/test-cases.md` — fixtures incl. general acknowledgment, authored-permission-to-share, unresolved source, and a confidentiality-bait case _(parity fast-follow)_
