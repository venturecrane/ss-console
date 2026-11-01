---
title: 'Operator Pack Delivery SOP (generic, honed per client)'
date: 2026-06-02
status: draft
captain: Scott Durgan
related-adr: 0022-vertical-pack-architecture.md, 0037-operator-thesis.md, 0019-customer-yaml-to-profile-config-translation.md
---

# Operator Pack Delivery SOP

How we take a customer from "yes" to a running Operator on a vertical pack. Generic across packs; honed per client (every firm runs a little differently). The pack is the ~80% starting point; this SOP is how we close the last 20% for a specific customer. The example notes are for the **law-firm** pack; swap them per vertical.

## Onboarding checklist (what we need before Day 1)

- [ ] **System of record + access** — which practice-management / AMS / PIMS system, and admin consent to connect it (e.g. Clio for law). MCP-first; a BUILD adapter is our job, not theirs.
- [ ] **Email + calendar** — the mailbox the Operator drafts into and the calendar it books on (M365 or Google), with scoped OAuth consent.
- [ ] **Document storage** — where files live (SharePoint/OneDrive or Drive).
- [ ] **Voice samples** — a folder of real sent messages so the Operator drafts in the firm's voice (per-reviewer where it matters).
- [ ] **The seat definition** — which connective tasks this customer wants the Operator to own first (a subset of the pack's `templates[]`).
- [ ] **Reviewers + exposure** — who reviews drafts, and which message classes may eventually go out autonomously vs. stay draft-for-review (the vertical floor is non-negotiable; everything above it is authored).
- [ ] **Escalation contacts** — who gets the red-flag and failure notices.
- [ ] **Scope blocks** — folders, domains, keywords, or matters the Operator must not touch.

These map directly to `customer.yaml` fields (`connectors`, `voice_library`, `personas[].skills`, `scope`, `escalation`). The pack manifest supplies the defaults; onboarding fills the brackets.

## Per-phase runbook

### 1. Configure

Copy `operator/customers/_template/presets/pi-law-firm/customer.yaml`, set `vertical:` to the pack (e.g. `law-firm`) and any `addons:` (e.g. `law-firm/pi@...`), and fill the customer-specific fields from the onboarding checklist. Validate:

```
npx tsx scripts/validate-customer-yaml.ts operator/customers/<slug>/customer.yaml
```

### 2. Shadow

Stand the Operator next to the work with its external exposure at the fail-closed floor (everything drafts, nothing sends). Let it watch real situations; check its drafts against what the team would have written. This is where the pack's voice and judgment get tuned to the customer.

### 3. Author entitlements

As the team signs off on a class of message, raise that class's exposure in `customer.yaml` (never above the vertical floor; for law, client-bound and tribunal-bound mail stays draft-for-review). The Operator can never raise its own ceiling. Every change is principal-authenticated and audited.

### 4. Go

The Operator owns its scoped seat. Drafts it is not yet trusted on still route to a reviewer who sends under their own name. We keep it wired, watch that it stays working, and stay accountable for it. The customer gives feedback to an Operator, not a second job managing one.

### 5. Hone

Real use surfaces the firm-specific edges. Adjust the skills, the voice, the scope, the entitlements. The understanding the Operator builds is captured in per-customer memory that belongs to the customer.

## The one hard rule

Nothing the Operator sends a client is fabricated or promised on the firm's behalf without the firm's authored content behind it. The compliance floor in the pack's manifest is the floor, not a suggestion. When in doubt, it drafts and a human sends.
