---
title: Marketing seat role and the California advertising gate
date: 2026-10-25
status: accepted
captain: Scott Durgan
related-adr: docs/adr/0090-hosted-agent-roles.md, docs/adr/0093-seat-policy-profiles.md, docs/adr/0087-chronology-package-content-control.md
enforced_by:
  - operator/templates/drafting/tests/test_advertising_gate_check.py
  - operator/hermes-smd-overlay/tests/test_advertising_gate_outbound.py
  - operator/hermes-smd-overlay/tests/test_advertising_gate_faults.py
  - operator/connectors/wordpress/tests/test_tools.py
  - tests/role-policy.test.ts
  - tests/role-persona-delivery.test.ts
---

# ADR 0098: Marketing seat role and the California advertising gate

- **Status:** Accepted (2026-10-25)
- **Decider:** Captain (the scope sent to the firm on 2026-10-15 promised the check); mechanism by the agent
- **Builds on:** ADR 0090 (roles), ADR 0093 (seat policy profiles), ADR 0087 (runner gates as content control, in the chronology package)
- **Scope:** a seat whose role is `marketing`. Every other seat is unaffected, and a test proves it.

## Context

A California personal-injury firm asked SMD for marketing and PR help on 2026-10-15. The scope SMD sent back the same day promised a second Operator, on its own machine, that drafts the firm's monthly marketing for one administrator's approval: blog posts as pending WordPress posts, social posts, YouTube descriptions for the firm's radio episodes, Business Profile posts, a reply to every new Google review, press releases from results the firm approves, and a topic slate. Nothing publishes on its own. It also promised that every draft passes a California attorney-advertising check before the firm sees it.

That promise is heavier in 2026 than it was a year ago. SB 37 (Stats. 2025 ch. 645, effective 2026-01-01) rewrote Bus. & Prof. Code 6157(b) so that any electronic communication that promotes a lawyer's services is a statutory advertisement: a blog post, a social post, a Business Profile post and a review reply all qualify. It added content rules (misleading statements about skills, experience or record; awards only when not membership-based and not paid for), a name-and-office requirement on every advertisement, and a private right of action of $5,000 to $100,000 per unique advertisement.

The research memo of 2026-09-28 distilled the rules into 48 numbered rules (R-01 to R-48) with patterns, severities and citations, and named the residue only a person can judge: legal accuracy, the truth of the firm's records, the scope of client consent, whether a reviewer was a client, and the overall impression of a post.

## Decision

1. **A `marketing` role.** `operator/roles/marketing/` carries the role package (ADR 0090): a persona that drafts for approval, never publishes, writes in the firm's voice and in plain language. A seat selects it with `seat.role: marketing`; `SEAT_ROLES` accepts it. Its skills are bound by the seat PR that builds them.
2. **A role knob that tightens.** ADR 0093's `policy:` block gains `advertising_gate`, a closed enum with one value, `ca-law-firm`; absent is off. It loosens nothing, so it needs no citation to be valid, and the marketing role cites this record anyway. Because off is the looser direction, a fault on a marketing seat keeps the gate on: an unknown or garbled value resolves to `ca-law-firm`, and so does a role whose policy is malformed elsewhere (the fallback carries the gate). When the role file cannot be read at all, the gate stays on for a role in the code-side set `ADVERTISING_ROLES` (pinned to the shipped role files). When customer.yaml cannot be read, or the role slug is garbled, the gate stays on when the seat carries a marketing mark that no config read produces: the registers file, or the boot's `/run/smd-lanes/marketing.inputs` state file, both written only by `entrypoint-marketing.sh` on a seat that is (or could not be shown not to be) a marketing seat. A fault on any other seat leaves the gate off, since without registers it would block every send. `shared/policy_profile.py` has the per-fault table.
3. **One checker, two exits.** `operator/templates/drafting/advertising_gate_check.py` (stdlib; YAML registers when PyYAML imports) implements the machine-checkable rules: never-allowed patterns in English and Spanish, required disclosures in the item's own language taken verbatim from the firm's registers, provenance of every record claim against the registers, and per-channel rules for review replies, Business Profile posts and blog posts. BLOCK keeps a draft from the reviewer; FLAG reaches the reviewer with the question to answer. Exits:
   - (a) the overlay's outbound seam (`shared/advertising_gate.py`, called from `plugins/hermes-smd-trust/outbound.py` on every send and draft body, and from the reply relay): a BLOCK refuses like an output-checklist refusal, with the rule, the quoted fragment and the reason; the third refusal of the same text withholds it; FLAG rule ids go to the audit row only.
   - (b) the WordPress connector, which runs the same checker before it creates or updates a pending post, always on the `blog` channel: a WordPress post is a blog post, and the tools take no channel argument a model could set to a lighter rule set.
4. **The draft-block convention.** One email to the firm carries a cover note and several drafts, each wrapped in `--- DRAFT <channel> ---` and `--- END DRAFT ---`. Each block is checked on its channel; the cover only with the general rules, so a disclosure rule never misfires on a cover note and a disclosure in the cover never cures a draft.
5. **Registers are authored per firm, in the private engagements repo.** Results, testimonials, awards, certifications, roster, fee terms, disclosures, blocklist, the review-reply contact and per-channel identity flags follow schema 1 in the checker's docstring and are staged to the seat at `/var/lib/smd-config/marketing/marketing-firm.yaml`. An empty register means nothing of that kind may be claimed; a null disclosure means any item needing it is blocked with "the firm has not authored that sentence yet".
6. **Fail closed.** On a seat with the gate on, a checker that cannot load, registers that are missing or unparseable, or a checker fault each refuse. A gate that silently did not run reads exactly like a gate that passed.

## Consequences

- A marketing seat cannot email the firm a draft that guarantees an outcome, cites an unregistered result, quotes a testimonial that is not verbatim and consented, omits a required disclosure in the item's language, or names a lawyer not on the roster. The firm's reviewer still owns everything in the memo's residue list; the gate says so in its FLAGs.
- No existing seat changes behavior: the gate returns before reading anything when the role does not set the knob (`tests/test_advertising_gate_outbound.py`).
- The firm must author its registers before most claims can appear at all. That is intended: until it does, drafts carry no record claims.
- Not machine-checked here: R-33 (needs the review text), R-38 (profile field edits), R-47 (archive is a system rule), R-48 (paid boosting is a channel decision). R-28 and R-43 are FLAGs whose hold is the human's.
- A new value of `advertising_gate` (another state's rules) is a new closed-enum value plus its rule set, decided by its own record.
