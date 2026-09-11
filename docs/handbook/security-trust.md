---
title: Security & Trust
section: operations
order: 6
summary: The two halves of trust - content integrity (no fabricated client-facing content, enforced by tests and merge gates) and Operator runtime security (a maintained threat model verified against the running system)
sources:
  - label: CLAUDE.md - No fabricated client-facing content
    href: https://github.com/venturecrane/ss-console/blob/main/CLAUDE.md
  - label: docs/style/empty-state-pattern.md
    href: https://github.com/venturecrane/ss-console/blob/main/docs/style/empty-state-pattern.md
  - label: tests/forbidden-strings.test.ts
    href: https://github.com/venturecrane/ss-console/blob/main/tests/forbidden-strings.test.ts
  - label: docs/security/operator-threat-model.md
    href: https://github.com/venturecrane/ss-console/blob/main/docs/security/operator-threat-model.md
  - label: docs/security/smd-services-security-overview.md
    href: https://github.com/venturecrane/ss-console/blob/main/docs/security/smd-services-security-overview.md
  - label: operator/bin/lib/chain_pin.py
    href: https://github.com/venturecrane/ss-console/blob/main/operator/bin/lib/chain_pin.py
  - label: .github/workflows/audit-chain-verify.yml
    href: https://github.com/venturecrane/ss-console/blob/main/.github/workflows/audit-chain-verify.yml
  - label: operator/adapter/evidence/packet.py
    href: https://github.com/venturecrane/ss-console/blob/main/operator/adapter/evidence/packet.py
  - label: src/lib/portal/operator/object-audit-record.ts
    href: https://github.com/venturecrane/ss-console/blob/main/src/lib/portal/operator/object-audit-record.ts
---

## Two halves of trust

Trust in this venture has two distinct surfaces, and they are protected by different machinery:

1. **Content integrity** - what the software is allowed to *say* to a client. The risk is fabrication: software inventing a commitment, a timeline, or a name that no engagement actually authored.
2. **Operator runtime security** - what an autonomous agent is allowed to *do* on a client's behalf, on their live business data. The risk is an autonomous system taking a consequential action it was never authorized to take, or being driven to do so by malicious input.

This page is the navigable summary of both. It does not re-narrate the controls; it points to where each is owned.

## Content integrity: no fabricated client-facing content

The rule, in full in `CLAUDE.md`: any information shown to a client - timelines, deliverables, pricing, consultant names, dates, scope language, any first-person promise about future business behavior - must come from data authored for that specific engagement (a human-reviewed database column, CMS content, or a Captain-reviewed source file). Violations are P0.

Two failure patterns are prohibited:

- **Pattern A - committed template sentences.** Hardcoded sentences in source that promise specific business behavior the engagement has not contracted, even when they interpolate authored values. The canonical examples (from the 2026-04-15 audit) are sentences like a hardcoded "we will reach out to schedule kickoff" or a baked-in start-window promise. They read as commitments; the firm never made them per-client.
- **Pattern B - runtime fabrication from non-authoritative fields.** Values rendered from sources never authored as client-facing content: placeholder defaults, parsed or derived text, brief-borrowed copy. The canonical example is a SOW signed as "Business Owner" because a contact name fell back to a hardcoded default, or an engagement overview injected from a constant string.

### The empty-state pattern is the sanctioned alternative

When authored data is missing, the rule is: render nothing, or an explicit "TBD in SOW" marker. Never synthesize, never borrow brief copy, never fall back to a sensible default. This is not a style preference - it is the only path the no-fabrication rule permits, and it exists as a documented pattern (`docs/style/empty-state-pattern.md`) specifically because the proposal-page incident (#377, hotfix #378) showed that agents and reviewers will accept fabricated content over a visually empty section unless the right move is made the path of least resistance. The rule prohibits invention; the pattern shows what to do instead. The two work as a pair.

### Adjacent guardrails

Pattern A/B is not the whole policy. The repo also blocks three nearby failure modes (per `CLAUDE.md`):

- **Style markers in shipped copy.** No em dashes, no "coming soon" placeholder copy on any prospect or client surface.
- **Enrichment-prompt drift.** Prompts that enrich a lead must stay extractive and evidence-bound - they must not ask a model to infer management style, personality, communication preferences, or likely objections.
- **Shared-flow drift.** Canonicalized product flows (the shared intake questionnaire) must not drift back into duplicate implementations.

### The enforcement machinery

The policy is not enforced by vigilance alone; it is wired into CI and into tests:

| Mechanism | What it does |
|---|---|
| `tests/forbidden-strings.test.ts` | Regression guard: the historical Pattern A/B phrases, the user-facing style-marker checks (em dash, "coming soon"), and portal registry guardrails must not appear in shipped source. |
| `tests/intake-questionnaire.test.ts` | Shared-surface regression coverage for the canonical intake questionnaire. |
| `.github/workflows/scope-deferred-todo.yml` | Merge gate: blocks a PR that defers an acceptance criterion via a TODO without the `scope-deferred` label. |
| `.github/workflows/unmet-ac-on-close.yml` | Issue-close gate: reopens an issue closed with unchecked acceptance criteria. |

The content-integrity controls also feed the external story: the same fail-closed posture and no-storage architecture are what `docs/security/smd-services-security-overview.md` presents to a partner's security review.

## The buyer-facing trust surface

A prospect's compliance reviewer gets the same story in three client-visible artifacts (added 2026-07-04, #1680): the public security page (`src/pages/security.astro`, at smd.services/security - architecture-derived claims, the sub-processor list, and a plain statement of what we do not claim, including no SOC 2 of our own), the AI-disclosure page (`src/pages/ai-disclosure.astro` - what the Operator is, that review posture is an authored choice with no imposed default, and what it never does), and the data processing addendum template (`docs/legal/operator-dpa-template.md` - the contractual half, carrying the notification and return/destruction windows the public pages deliberately leave to paper; counsel review precedes first execution). All three derive from the security overview and the ADR spine; if a control changes, they change in the same wave.

## Operator runtime security

The Operator is an autonomous agent acting on a client's live business data, so its security model is about constraining action, not just constraining output. The full analysis is `docs/security/operator-threat-model.md` - a maintained, adversarially-tested register, not a one-time design doc. Its shape:

- **A strong perimeter, a softer core.** The front door (the capability broker plus authored entitlement ceilings on registered tools) is verified-strong. The historically harder problems were ungoverned code execution defaulting to read, an account-wide secret in the agent's environment, a broker that validated identity but not intent, and an inbound fence that covered the webhook channel but not the managed mailbox. These are tracked as P0/P1 findings with live-exploit verification and a remediation program (see the threat model's "Closed" section for what has been shut, including the broker-owned audit ledger).
- **The verified strengths to protect from regression.** Per-customer Machine isolation, the fail-closed authority model (unconfigured can read but not act), the hard ban on principal-identity send, and the tamper-resistant audit log the agent cannot rewrite (hash-chained since 2026-07-04, #1686). The threat model names these explicitly so a future change does not quietly undo them.

### What the audit record can and cannot prove

This is the claim a firm's insurer, the State Bar, or opposing counsel would test, so it is stated exactly rather than generously.

**The agent cannot alter or erase the record.** One process writes the ledger (the capability broker), the agent's user account has read-only access to the file, and the write surface exposes no update or delete verb. That part is structural.

**The chain catches a row changed, removed, or inserted before the end of the log.** Every row's hash commits to the row before it, so any of those breaks the chain at a point a verifier can name.

**The chain alone does not catch rows cut off the end of the log.** What remains after such a cut is itself a valid chain, and a verifier reading only the export says so. This was measured on 2026-08-20 against a copy of a live 1,473-row export, not reasoned about: deleting the last fifty rows, deleting the last one row, and altering a row then recomputing every hash after it all reported the chain intact.

**What closes that is a head pinned off the machine.** Every heartbeat carries the hash of the newest row, and the control plane appends it to a table on our side that nothing on the client's machine can reach backwards into (`audit_head_history`, migration 0108). A daily job (`.github/workflows/audit-chain-verify.yml`) pulls each seat's full ledger and requires the newest pinned hash to still be in it. If it is gone, rows that existed at that moment are gone, and that opens a P0 issue and an alert on the fleet dashboard. The same job writes the ledger to object storage under `audit/<slug>/<date>/<HHMMSS>Z-<head12>.json.gz`, a fresh object for every run because that storage refuses overwrites, so the record survives loss of the machine. Before that job existed, the only copy off the machine was a five day volume snapshot, and one seat's ledger had already lost its early rows once during a rebuild.

**The honest limit.** A pinned head protects the rows older than the last pinned head. Rows newer than it stay rewritable by a root user on the machine until the next heartbeat lands. That window is one heartbeat wide and shortening it means beating more often, not a different mechanism. A forged row appended with a correct hash also descends from the pin like a real one; per-row signing was considered and rejected (ADR 0074). Both limits are stated on the public security page too, in the same words.

### A firm can check a sent message without asking us

An audit row that only we can verify is a row that asks the reader to trust us, which is the opposite of what an evidence record is for. Rows for the Operator's replies therefore carry digests a firm can recompute from the copy its OWN mail system stored (ss#2501):

- `body_digest_authored` - SHA-256 of exactly the plain-text bytes handed to the mail system.
- `body_digest_authored_html` - the same for the HTML body, when one went out.
- `body_digest` - the older, internal one. It covers subject and body joined, which is what the content checks read. The subject is not on the wire for a reply, so nobody outside can reproduce that input, and the packet says so rather than leaving an auditor to chase it.

**The check depends on what the mail system did with the body, and that is structural rather than a caveat.** Where it stores the bytes it was handed, the test is equality: hash the stored body, compare. Where it COMPOSES the stored message, the test is containment: the authored bytes must appear verbatim inside it. Graph composes, so every reply the paid seat has ever sent carries Microsoft's wrapper plus the quoted original beneath our text, and a hash of that stored body can never match. Publishing equality alone would look thorough and send counsel after a value that cannot exist.

The coverage is stated rather than implied, for the same reason: today these fields ride the Operator's REPLY rows. A message the Operator sends on its own initiative records a hash of the whole send request (`input_digest`), which is internal like `body_digest`, so the packet says the check does not apply to it rather than leaving a reader to discover an absent column.

Nothing is normalized or canonicalized before hashing. A recipe that first massages the bytes is a recipe someone can argue with, and a mail system that re-encoded the body on the way in should show up as a mismatch rather than be smoothed away. The packet says so in those words: whether the hashes match is a fact about the firm's mail system, not a promise from us. Whether Graph embeds our bytes unchanged inside its own wrapper is Microsoft's behavior and is not probed anywhere in this repo, which is what the live check on the pilot sandbox is for.

Both columns ride the per-matter audit export (`src/lib/portal/operator/object-audit-record.ts`), and the worked `openssl` and `grep` commands are printed in the evidence packet's own README (`operator/adapter/evidence/packet.py`), where the reader who needs them already is.

The controls themselves - the action-class ceilings, the capability broker, the inbound-content taint gate, and the fail-closed default - are owned and explained in `/admin/playbook/autonomy-governance`. The secrets and credential-custody side (Infisical, per-customer OAuth tokens, the broker-only secret materialization) is owned by `/admin/playbook/secrets-access`. This page does not duplicate them; it points to them so the two halves of trust read as one map.
