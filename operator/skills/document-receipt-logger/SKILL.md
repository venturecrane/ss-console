---
name: document-receipt-logger
description: >-
  Logs an inbound document against the right matter. It resolves sender to matter,
  proposes the filing location, drafts a receipt entry, and surfaces it. Records
  that a document arrived; never interprets its contents.
version: 0.1.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: [python3]
metadata:
  hermes:
    tags: [Law, Documents, Filing, Intake, DraftForReview]
  smd:
    vertical: law-firm
    skill_type: read + assembly (filing proposal)
    action_class: read + write
    connectors:
      - email # customer-bound — the inbound message + its attachment
      - smokeball # PracticeManagement — resolve sender→matter, draft the receipt memo (read; create_memo write gated)
      - document-storage # DocumentStorage (mcp:ms-365) — proposed filing target (write gated)
---

# Document Receipt Logger

When a document arrives in the firm's inbox — a signed agreement, a returned form, a record from a third party — this skill records that it came in and proposes where it belongs: it resolves the sender to a matter, identifies the filing location, drafts a receipt log entry, and surfaces the proposal. It is the firm's "we received this and here is where it goes" step, so a document never sits unfiled and untracked.

It logs **receipt**, not meaning. It records that a document arrived, from whom, and against which matter — it does **not** read the document for legal content, summarize its terms, or act on what it says. Interpreting an inbound document is legal work; this skill is filing hygiene.

## When to Use

Use when inbound documents would otherwise pile up unlogged — the firm wants every received document tied to its matter with a receipt trail. No wedge step depends on a logged document, which is why it is a parallel workflow rather than part of the named-job loop, but unlogged documents are how things get lost.

Runs event-driven (an inbound message with an attachment) and scheduled (sweep the inbox for unlogged attachments).

## Prerequisites

Reads the customer-bound **Email** connector (the inbound message + attachment metadata), Smokeball (`get_contacts`, `list_matters` to resolve sender→matter; the receipt memo is a gated write), and the **DocumentStorage** connector (the proposed filing target; a gated write). Requires `python3` for the fetch block.

## How to Run

```
hermes run document-receipt-logger                     # sweep inbox for unlogged attachments
hermes run document-receipt-logger --message <id>      # log one inbound document
```

## Procedure

Two phases (ADR 0021 Stream A). The mechanical inbox/attachment + Smokeball resolution runs in one `execute_code` block; the filing proposal stays in the agent's reasoning loop.

### Phase 1 — Fetch (single `execute_code` block)

Enumerate inbound messages carrying attachments in the window. For each, capture sender, subject, and attachment **metadata only** (filename, type, size) — not the document body. Resolve the sender via Smokeball (`get_contacts` → `list_matters(contactId=…)`). Accumulate in-process; `print()` one JSON document of (message → sender, attachment metadata, resolved matter candidates). A single unreadable message is `parse_failed`; the sweep continues.

### Phase 2 — Reason (agent, in-context)

Per `references/algorithm.md`:

1. **Resolve the matter.** Map the sender (and any matter reference in the subject) to a single matter via Smokeball. A confident single match proceeds; an ambiguous or unknown sender is surfaced for a human to assign, not guessed.
2. **Propose the filing location** — the matter's document area in DocumentStorage, plus a category if the firm authors one (correspondence, signed-docs, records). The proposal names where; it does not classify the document's legal nature.
3. **Draft the receipt entry** — a Smokeball memo (`create_memo`) recording: document received, from whom, when, attachment filename/type, and the resolved matter. Receipt facts only.
4. **Surface for review.** The proposal (matter + location + receipt draft) is surfaced; in this phase the actual file move and memo write are **gated** — a human confirms before the document is filed and the receipt committed.

## Trust Ceiling

**Resolve + propose + draft autonomous; the file move and receipt write are gated (`draft_for_review`).**

The agent MAY: read inbound message + attachment metadata; resolve sender→matter via Smokeball; propose a filing location; draft the receipt memo.

The agent MUST NOT: interpret, summarize, or act on the document's contents; file a document or write the receipt without review (this phase); attach a document to a matter it could not confidently resolve; alter or delete an existing document.

## Safety invariants (any violation → `fails`, no recovery)

1. **Receipt, not meaning.** The skill records that a document arrived; it never reads it for legal content or advises on it.
2. **No fabricated filing.** A document is tied to a matter only via a confident Smokeball resolution; an unresolved sender is surfaced for human assignment.
3. **Fail-closed write.** The file move and receipt memo are gated behind human review this phase; nothing is filed autonomously.
4. **No destructive document action.** Never overwrites, moves, or deletes an existing filed document — it only adds a received one.
5. **Privilege.** Sender, matter, and attachment metadata stay on firm surfaces.

## Pitfalls

Reading the attachment to "be helpful" and summarizing its terms (out of scope — that is legal work); filing to a guessed matter when the sender is ambiguous; classifying the document's legal type instead of just its filing category; committing the receipt write before review in the fail-closed phase.

## Verification

1. Every logged document traces to a real inbound message and a confidently resolved matter; ambiguous senders are surfaced, not guessed.
2. The receipt entry contains receipt facts only — no content summary or interpretation.
3. The file move and memo write are gated behind review this phase.
4. No existing document is altered or deleted.
5. Attachment contents never appear in the surface or logs — metadata only.

## References

- `references/algorithm.md` — the sender→matter resolution, the metadata-only rule, and the gated filing/receipt flow
- `references/output-format.md` — the filing proposal + receipt-memo draft _(parity fast-follow)_
- `references/test-cases.md` — fixtures incl. clean match, ambiguous sender, unknown sender, and multi-attachment _(parity fast-follow)_

## Delivery channels + refusal fallback (law seat rule)

Email is a citation-free channel. Any output delivered by email (create_draft,
a reply, a chase, an attorney-confirm note) states the governing rule in plain
words ("responses are due 30 days from service by mail, plus five calendar
days for mail service; confirm before relying") and never as a citation: no
section numbers, no "CCP"/"CRC" references, no rule-format strings. The mail
channel enforces the legal-citation filter and will refuse the draft. Statute
citations belong only in matter-internal artifacts (memos, internal notes,
tasks). Write the FIRST draft citation-free; do not write a cited draft and
wait for the gate to teach you.

Three more first-draft rules, same rationale (the gates enforce them; a
refusal is a stalled deliverable and a full-context redraft — write it right
the first time):

- No em dashes anywhere, in any channel. Use commas, colons, or periods.
- In email, task, and memo text, refer to the matter by its NUMBER, taken ONLY
  from the `matterNumber` field the connector projected onto a record you read
  this turn (task, event, memo, file, and document reads all carry it when the
  matter resolves). Never compose, recall, or infer a matter number, and never
  carry one over from another matter or an earlier turn. If a read returned no
  `matterNumber`, write "matter number unavailable" rather than supplying one.
  Never refer to the matter by its case caption. The matter's own caption is
  acceptable inside matter memos; cited case law is never acceptable anywhere.
- State a specific dollar figure only when it exists in an authored source
  on the matter, and name that source in the same sentence ("per the MedFin
  payoff letter dated..."). Never total, estimate, or round figures into
  existence.

If a delivery tool refuses a draft or write (citation filter, banned-typography
gate, or any other content gate): do not retry the same content, and do not
drop the work. Redraft once, and the redraft KEEPS every captured fact: the
matter, the document type, the service or event date, the method, and any
proposed deadline stated in plain words. Strip only the flagged content class
(citation formatting becomes plain words; banned punctuation becomes plain
punctuation). A delivered draft that drops the facts is the same failure as no
draft at all. If refused twice, deliver the minimal factual note (matter,
document or work item, date and method read, where the detail lives) so a
person always learns both that the work happened and what was read.

Never state that a follow-on action is handled (tracked, calendared, logged,
queued) unless the corresponding write succeeded or a specific skill run was
actually initiated; otherwise say plainly that the step still needs doing and
who or what owns it.
