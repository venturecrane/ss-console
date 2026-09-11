---
name: intake-to-system-sync
description: >-
  Syncs a converted intake CRM lead into Smokeball. For a lead converted in a
  separate intake CRM (Clio Grow / Lawmatics), it maps fields, dedupes, and
  conflict-cross-checks before any matter create. Only load-bearing when a distinct
  intake CRM runs alongside Smokeball.
version: 0.1.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: [python3]
metadata:
  hermes:
    tags: [Law, Intake, Sync, CRM, DraftForReview]
  smd:
    vertical: law-firm
    skill_type: read + assembly (cross-system sync)
    action_class: read + write
    connectors:
      - intake-crm # IntakeCRM (build:clio-grow) — the converted lead source (read). CRM-side, out of scope for the PM migration.
      - smokeball # PracticeManagement — dedupe + draft the contact/matter (read; write gated)
---

# Intake to System Sync

Carries a converted intake from a **separate intake CRM** — Clio Grow, Lawmatics, or similar — into Smokeball as a contact and matter, keeping the two systems in step so a won lead doesn't get hand-re-keyed (or dropped) on the way to the practice-management system.

It is **only load-bearing when a distinct intake CRM runs alongside Smokeball.** The pilot assumes Smokeball as the single system of record, so this skill is **authored but not enabled for the pilot** — it requires the `build:clio-grow` (IntakeCRM) connector, which the wedge deliberately avoids. It exists for firms whose intake lives in a separate front-end and needs a clean, deduped, conflict-checked handoff into Smokeball.

> **Scope note (this pass).** The PM system of record migrates Clio → Smokeball; the IntakeCRM/lead-source side is unchanged. `build:clio-grow` here names the _Clio Grow intake CRM product_ (a lead front-end), not the PM connector — it stays as authored and is out of scope for the PM migration.

## When to Use

Use only when the firm runs a dedicated intake CRM separate from Smokeball and wants converted leads to flow into Smokeball without manual re-entry. If Smokeball is the single system of record (as in the pilot), this skill stays disabled — there is nothing to sync from.

Runs event-driven (a lead is marked converted in the CRM) and scheduled (sweep the CRM for converted-but-unsynced leads).

## Prerequisites

Reads the **IntakeCRM** connector (`build:clio-grow` — the converted lead, its captured fields, and intake party data) and Smokeball (`get_contacts`, `list_matters` for dedupe and the conflict cross-check; the contact/matter create is a gated write). Requires `python3` for the fetch block. **Not enabled in the pilot** (`customer.yaml` for the pilot does not bind IntakeCRM).

## How to Run

```
hermes run intake-to-system-sync                 # sweep CRM for converted-unsynced leads
hermes run intake-to-system-sync --lead <id>     # sync one converted lead
```

## Procedure

Two phases (ADR 0021 Stream A). The mechanical CRM read + Smokeball dedupe/cross-check runs in one `execute_code` block; the field mapping and sync proposal stay in the agent's reasoning loop.

### Phase 1 — Fetch (single `execute_code` block)

Enumerate converted-but-unsynced leads in the CRM. For each, capture the intake fields (client identity, contact channels, matter description, practice area, captured parties) and run the Smokeball dedupe + cross-check reads (`get_contacts`, `list_matters`) on the lead's parties. Accumulate in-process; `print()` one JSON document of (lead → fields, existing-Smokeball matches, conflict cross-check hits). A single unreadable lead is `parse_failed`; the sweep continues.

### Phase 2 — Reason (agent, in-context)

Per `references/algorithm.md`:

1. **Conflict cross-check FIRST.** Before proposing any matter create, run the same read-only name/entity cross-check the wedge carries (the `new-matter-intake` / `conflict-intake-router` invariant). On any hit, **HALT**: route to human conflict clearance, do not propose the matter. Advancing a flagged lead is a `fails` safety violation.
2. **Dedupe.** If the lead's client already exists as a Smokeball contact/matter, propose linking rather than creating — never mint a duplicate client or a second matter for the same engagement.
3. **Map fields** from the CRM schema to Smokeball's (client → contact, lead detail → matter description, captured practice area / `matterTypeId`, parties). Fields the CRM didn't capture are left empty, not invented.
4. **Draft the sync** — the proposed Smokeball contact and matter records, plus a back-link so the CRM lead is marked synced. In this phase the actual Smokeball create and the CRM mark-synced are **gated** behind human review.
5. **Surface for review.** The proposed records, the dedupe decision, and any conflict hold are surfaced; a human confirms before anything is written to either system.

## Trust Ceiling

**Read + dedupe + map + draft autonomous; the Smokeball create and CRM mark-synced are gated (`draft_for_review`).**

The agent MAY: read the converted lead; dedupe against Smokeball; run the conflict cross-check; map fields; draft the proposed contact/matter and the sync-back.

The agent MUST NOT: create a Smokeball matter/contact or mark a lead synced without review (this phase); create a duplicate when a match exists; advance a conflict-flagged lead; invent a field the CRM didn't capture.

## Safety invariants (any violation → `fails`, no recovery)

1. **Conflict cross-check precedes matter create.** A hit halts the sync and routes to human clearance; no auto-clear, no matter proposal past a hit.
2. **No duplicates.** A matching existing client/matter is linked, never re-created; the dedupe decision is explicit and surfaced.
3. **Fail-closed write.** Both the Smokeball create and the CRM mark-synced are gated behind human review this phase; nothing is written to either system autonomously.
4. **No fabricated fields.** Only CRM-captured data is mapped; missing fields stay empty, never guessed.
5. **Privilege + isolation.** Lead and matter data stay on firm surfaces; the sync touches only the two authored systems.

## Matter identifiers (projected, never composed)

- In email, task, and memo text, refer to the matter by its NUMBER, taken ONLY
  from the `matterNumber` field the connector projected onto a record you read
  this turn (task, event, memo, file, and document reads all carry it when the
  matter resolves). Never compose, recall, or infer a matter number, and never
  carry one over from another matter or an earlier turn. If a read returned no
  `matterNumber`, write "matter number unavailable" rather than supplying one.
  A matter this skill has only PROPOSED has no number yet, and a proposal that
  reads as though it does is the dedupe error this skill exists to prevent.

## Pitfalls

Creating a duplicate matter because the dedupe match was weak and got ignored; proposing a matter before the conflict cross-check (ordering breach); inventing a practice area or client detail the CRM didn't capture; committing the write before review in the fail-closed phase; enabling this skill when Smokeball is the single system of record (there is nothing to sync from).

## Verification

1. Every synced lead is checked for an existing Smokeball match first; matches are linked, not duplicated.
2. The conflict cross-check runs before any matter is proposed; hits halt and route to a human.
3. Field mapping uses only CRM-captured data; missing fields are empty, not invented.
4. The Smokeball create and CRM mark-synced are gated behind review this phase.
5. The skill is disabled when no separate intake CRM is bound (e.g., the pilot).

## References

- `references/algorithm.md` — the dedupe rule, the conflict-first ordering, the CRM→Smokeball field map, and the gated write flow
- `references/output-format.md` — the sync proposal (mapped records + dedupe decision + holds) _(parity fast-follow)_
- `references/test-cases.md` — fixtures incl. clean new lead, existing-client dedupe, conflict-hit, and partial-fields _(parity fast-follow)_
