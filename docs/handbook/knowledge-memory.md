---
title: Knowledge & Memory
section: product
order: 3
summary: The three knowledge lanes - authored, learned, inferred - that compose the Operator's model of a client, why the operator reads all three but writes none of the authored lane, and why the memory is part of the moat
sources:
  - label: ADR 0048 - Operator relationship model
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0048-operator-relationship-model.md
  - label: ADR 0012 - customer.yaml storage
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0012-customer-yaml-storage.md
  - label: ADR 0019 - customer.yaml to profile translation
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0019-customer-yaml-to-profile-config-translation.md
  - label: ADR 0016 - Honcho disposition
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0016-honcho-disposition.md
  - label: ADR 0083 - Authorship model and output classes
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0083-authorship-model-output-classes.md
  - label: ADR 0037 - The Operator Thesis
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0037-operator-thesis.md
---

## Three lanes, one legible surface

The Operator's knowledge of a client composes three lanes into one human-readable, Captain-reviewable view (ADR 0048). A human employee's model of you is opaque; the Operator's is legible and correctable - that is delight, trust, and governance in one surface.

| Lane | Source | What it holds | Status |
| --- | --- | --- | --- |
| **Authored** | `customer.yaml`, Captain-set | How to work with each person; context; guardrails | Shipped - the seed |
| **Learned** | Overlay `hermes-smd-peer-memory` plugin, keyed per peer, stored on the seat's volume | How each person actually works, observed over sessions | Shipped |
| **Inferred** | `persona_observations` / Honcho | Unstated patterns from history | Deferred (Phase 2) |

The operator **reads all three**. It **cannot write the authored lane and cannot raise its own ceiling** (ADR 0048 §2c; see `/admin/playbook/autonomy-governance`). Relationship state shapes help, style, and anticipation; it never self-grants capability or autonomy. A good employee learns your coffee order on their own; they do not start wiring money because they feel trusted (ADR 0048 §2c).

## The authored lane: customer.yaml is the guide

The authored lane is `customer.yaml` - the Captain-authored configuration that is the high-confidence backbone of the model and the seed of every per-person relationship. It is where the human guide encodes how a specific business works, who its people are, and what the operator may do. This is the "guide" leg of the harness-plus-guide-plus-memory moat (ADR 0037, Tenet 4).

**Storage (ADR 0012).** Git is the source of truth. Each customer's config lives at one canonical path, is PR-reviewed, schema-validated, and secret-scanned before merge. On merge, CI projects the YAML into two read replicas: a normalized JSON projection in portal D1 (for fast portal reads on the hot path) and the canonical YAML in the customer's R2 prefix (which the Machine reads at boot). The replicas are never edited directly - if drift is ever detected, the resolution is always "re-sync from git," never "patch the replica." This is the GitOps pattern that Kubernetes manifests and Terraform state converged on: reviewability and version history come from git, read-path latency comes from local replicas. Onboarding and offboarding are themselves PRs. Critically, `customer.yaml` never contains literal secret values - the OAuth section declares scopes only, and secrets stay in Infisical or on the customer's volume (ADR 0012; see `/admin/playbook/operator-platform`).

**Translation (ADR 0019).** At Machine boot, the `hermes-smd bootstrap` CLI translates the authored `customer.yaml` into each persona's Hermes-native `config.yaml` and `SOUL.md`. The relationship block is materialized into `SOUL.md` and served back to the legible surface through a config-export seam. The translation is deterministic and idempotent (covered in `/admin/playbook/operator-platform`).

## The learned lane: per-person working preferences

The learned lane captures how each person likes to work - cadence (bullets versus prose), autonomy (act versus ask), how they word requests and corrections, how they refer to others - learned from the content of requests across sessions and channels (ADR 0048 §3, as amended 2026-06-16).

It is built by the overlay's **`hermes-smd-peer-memory`** plugin on top of Hermes, because Hermes' native memory is per-profile and identity-blind (one memory file per profile, no per-peer keying). The plugin supplies:

- **Explicit capture.** The agent records a stated or demonstrated preference with the `record_peer_preference` tool; the write happens server-side in `post_tool_call`, so the agent never names whose preference it is.
- **Per-peer keying.** Each unit is keyed on the person the turn belongs to (the verified inbound sender on email, the per-user id on channels that thread one), so one person's preferences stay separate from another's, and that peer's active preferences are injected at the start of each turn.
- **Inspectable storage with provenance.** Rows live in the Machine-local agent-state SQLite database on the seat's own volume (`SMD_D1_AGENT_STATE_BINDING`), with source and session recorded, so they are Captain-inspectable and reversible (ADR 0016's mirror-don't-gate posture). The per-customer D1 this lane was first designed against was never built (ADR 0062).

Two disciplines keep this lane honest. First, each unit is a **stated or concretely demonstrated** preference, written as preference plus why plus how-to-apply - **never a trait or psychological label** (ADR 0048 §3). The model records "prefers bulleted summaries, asked for them twice," not "is a detail-oriented person." Second, **taint-aware learning**: a capture sourced from a session that ingested untrusted inbound is not promoted to a standing preference, so an injected message can never become a learned rule (ADR 0048 §2f; the taint mechanism is in `/admin/playbook/autonomy-governance`).

There is no approval gate between the agent and its memory. Safety is the trust discipline (stated/demonstrated only), inspectability (the D1 mirror), and taint-awareness - not an interposed queue (ADR 0048 §3).

## Adjacent stores: corrections and record stores

Two other overlay plugins hold durable seat knowledge outside the three lanes. **Correction capture** (`hermes-smd-corrections`, ADR 0083 §4) records an administrator's statement about how an output class should be shaped into an append-only ledger the agent cannot promote from; a Named Administrator promotes it portal-side, and it refuses capture on a tainted turn or from anyone not on `scope.admins`. **Record stores** (`hermes-smd-record-store`) give a seat that authors `record_stores` in `customer.yaml` three tools to list, read, and write records inside those authored directories only, never by path, with writes refused on a tainted turn and optional per-person privacy.

A note on what the learned lane is **not**: an earlier design modeled it as a draft-diff "style/correction" lane that captured how a message reads. That was retracted on 2026-06-16 - diffing corrected drafts captures voice, not personality, which is the wrong surface (ADR 0048 amendment banner). Voice is a separate concern handled by the voice layer. The relationship lane is about how a person wants to be worked with, not how a message reads.

## The inferred lane: deferred to Phase 2

The inferred lane would surface unstated patterns the agent infers from history. It is built on **Honcho**, the memory-inference engine Hermes ships first-class, which builds a per-peer model from session messages via LLM-driven reasoning loops (ADR 0016).

Honcho is **not running today.** ADR 0016's 2026-05-30 revision deferred the inference engine to Phase 2 behind the owned-memory file; the in-container version was never booted, and a memory export on the live customer-zero Machine returns an empty set (ADR 0048 §Context; ADR 0016 Revision). So the automatic "infer unstated preferences from conversation" capability does not exist yet, and standing it up is a separate, heavy, per-Machine infrastructure effort (it requires a pgvector Postgres, a deriver worker, and a continuous-LLM-spend cost line that has to be priced).

When Honcho does land, the disposition is fixed: **mirror, don't gate.** Honcho runs from a pinned upstream image, byte-for-byte unmodified (preserving the AGPL safe harbor and zero rebase cost), and the overlay's memory-mirror plugin copies its conclusions to the seat's Machine-local `persona_observations` store with full provenance and an `evidence_status` flag. The evidence flag is the defense against Honcho's documented hallucination failure mode (issue #626, where one mention of a dog produces "the user has a dog"): a fabricated conclusion has no valid source-message provenance and surfaces in a prominent Captain review queue before it can shape a draft. Captain dismissal physically deletes the conclusion from Honcho (working around Honcho's temporal-awareness bug, #658, by removal rather than contradiction), and a TTL archival job caps unbounded growth (ADR 0016).

Two binding policies carry across all three lanes (ADR 0048 §2): the overlay captures only deterministic signals Honcho will never compute (no heuristic dressed as a fact), and `persona_observations` stays non-runtime-read - the agent never reads inferred conclusions at runtime, because an unreviewed inference must not silently shape drafts.

## Why the memory is part of the moat

The reason this page sits in the product section and not a reference appendix: the memory is one of the three things that are scarce and compound (ADR 0037, Tenet 4). A human hire starts over at offboarding and exits with everything they learned. The Operator's per-customer operating memory deepens with every session and raises switching cost over time. Competitors will have configurable agents; they will not easily have the accumulated, legible, correctable memory of a specific business - and they will not have the guide who authored the seed well. The harness, the guide, and the memory are the system; no single feature is the moat (ADR 0037; see `/admin/playbook/operator-platform`).
