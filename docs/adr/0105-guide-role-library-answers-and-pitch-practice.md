---
title: The guide role - answers from an authored library, and pitch practice
date: 2026-11-09
status: accepted
captain: Scott Durgan
related-adr: docs/adr/0090-hosted-agent-roles.md, docs/adr/0093-seat-policy-profiles.md, docs/adr/0098-marketing-seat-advertising-gate.md, docs/adr/0033-telegram-channel-native-polling.md, docs/adr/0040-operator-positioning-and-why-us.md
enforced_by:
  - tests/product-guide-library.test.ts
  - tests/guide-seat.test.ts
  - tests/role-persona-delivery.test.ts
  - tests/role-policy.test.ts
  - operator/hermes-smd-overlay/tests/test_bootstrap_translate.py
---

# ADR 0105: The guide role, answers from an authored library, and pitch practice

- **Status:** Accepted (2026-11-09)
- **Decider:** Captain (the ask, 2026-11-09: a sales representative SMD lists emails SMD's own guide seat a question about the Operator and gets an accurate, sourced answer with no client material and no pricing; then, the same day, that the representative can practice pitching with it, by email or in chat); mechanism by the agent
- **Builds on:** ADR 0090 (roles are packaged jobs selected by `seat.role`), ADR 0093 (a role's `policy:` block), ADR 0098 (the precedent for a second role added end to end, and for a role that trims its toolsets), ADR 0033 (the chat channel and its mandatory allowlist), ADR 0040 (positioning, the source of the practice rubric)
- **Scope:** a seat whose role is `guide`. No existing seat changes; `product-guide` and `pitch-practice` are enabled on no other seat.

## Context

Outside sales representatives will sell the Operator to technical buyers. They need accurate answers on demand, sourced so they can repeat them, and they must never be handed client material, prices, or a capability that is planned described as available. The Captain also wants them to learn by practicing a pitch against a buyer who pushes back with real objections.

The facts already exist: a reviewed explainer page in nine anchored sections and an industry example, and an internal product model built from the code. Neither is safe to hand an outsider as is (code paths, internal vendor names, a contradictions section). The answer has to come from material SMD authored and reviewed, not from what a model remembers about AI agents.

## Decision

1. **A `guide` role** (`operator/roles/guide/`, `SEAT_ROLES` accepts it). It is general in shape and has two jobs. Answer: answer only from the authored library its skill ships, cite the document and the `##` section for every answer, say plainly when the library does not cover a question and name team@smd.services, never answer from outside the library. Practice: on request, play an authored buyer, raise that buyer's objections one turn at a time, then step out of role and give feedback under an authored rubric, each point cited to the library. Its persona carries the rules (no pricing, no clients or specific businesses, no client documents, no claim to know the asker's business, never "AI employee", never a real person or company in role).
2. **Two skills, authored material, no new gate.** `product-guide` (the library under `references/`, an `INDEX.md`, the refusal wording) takes every email to the seat and hands a practice request to `pitch-practice` in the same turn, the way `marketing-request-router` hands work to its routed skills. `pitch-practice` carries `scenarios.yaml` (seven buyer scenarios, one marked as the industry example, each objection pointing at the library section that answers it) and `rubric.md` (the only headings feedback may use). The practice state is the thread: every in-role message opens with the marker line "Practice (playing: <role>, scenario <id>, objection <n> of <total>)", in ordinary case because the seat's reply checklist refuses capitals used for emphasis. Which files the model may read is enforced by existing mechanisms, not by its good behavior: the seat enables only these two skills, so the omitted-skill read fence refuses every other skill's files; the role drops the native `web`, `memory` and `establishment` toolsets in the overlay's translate (`_GUIDE_REFUSED_TOOLSETS`); the seat authors no record store, no connector but its own mailbox, no code execution, and native memory off. No new gate was needed.
3. **The library is representative-safe by test.** `tests/product-guide-library.test.ts` fails on a dollar amount, an em dash, a client or person name (derived from every authored seat, so a new client is covered the day it is authored), internal runtime vocabulary, a broken relative link, a missing `INDEX.md` entry, any of the reply gate's own banned fabrication markers (read from the overlay's registry, so the library never holds a sentence the send gate would block when quoted, for example "guarantee"), an explainer anchor outside the page's nine anchored sections and its industry example, or the page address anywhere but `INDEX.md`. The same test checks the answer key (25 questions, 5 of them refusals, and 5 practice-flow cases) and every scenario and rubric citation against the library's actual headings.
4. **Pictures.** The seat makes no images. When a question matches a view of the reviewed explainer page, the answer names the view and gives the page address from `INDEX.md` followed by that view's anchor; otherwise it answers with a numbered structure a drawing tool can take.
5. **The `smd-guide` seat** is authored and not provisioned (`fly_app: null`), on the precedent of the marketing seat: kind `internal`, product `operator`, role `guide`, client `smd-services`; email on its own hosted inbox; the one authored send is the reply to a rostered sender (`external_send_internal: autonomous`), every other class unauthored and so refused; roster and administrators `scott@smd.services` until the representative's address is known; escalation to team@smd.services; SMD seat cost caps.
6. **Chat.** The seat authors the chat channel, enabled, with an allowlist holding only the Captain's own id, which the repo already binds to him (captain-mark `people` p2, and scott's allowlist). The validator and the overlay both refuse an enabled block with an empty allowlist, so the channel cannot fail open; a representative's id is added by its own reviewed PR. Nothing ties chat to the hosted-agent product or to a role, so no validator change was needed.

## Consequences

- **Chat answers are not content-gated the way email replies are.** An email reply goes through the reply relay, which runs the fabrication markers and the output checklist on every reply. A chat answer is the conversation itself on the seat's own bot; it is not a send tool, no overlay hook reads the final chat text, and its only gate is the allowlist. The skill text holds the same rules in chat (marker line, citations, plain text), but in chat they rest on the skill and the library's own tested content, not on a gate. Extending the reply gates to chat is a separate decision for the whole fleet, not made here.
- **The model provider is not named** in the library. A representative who is asked is told the library does not cover it and to ask SMD.
- **Contractual notice periods** (incident notice, export and destruction windows, sub-processor notice) appear as what the standard agreement states, never as a delivery promise.
- **Representatives see a snapshot.** The library is versioned with the code; a change to the product that changes what a library page says updates that page in the same PR, and the paid answer-quality check re-runs against the answer key when it does.
- **Not built here:** provisioning the seat, its inbox and bot, the paid answer-quality run, and the representative's roster entry. Those are runtime steps the Captain owns.
