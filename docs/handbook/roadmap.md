---
title: Roadmap & Current Phase
section: business
order: 6
summary: Where the venture is right now, what the priorities are, and what stands between here and profitability.
sources:
  - label: Decision Stack
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/decision-stack.md
  - label: ADR index
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/index.md
  - label: Venture model (priority checklist)
    href: https://github.com/venturecrane/ss-console/blob/main/.claude/rules/venture-model.md
---

This page is the page most likely to drift. Treat its dates and statuses as a snapshot
(last refreshed 2026-09-21) and update it in the same PR as any change that moves the
venture. A mirror of it lives in crane-console (`docs/ventures/ss/roadmap.md`) for
cross-venture context; this page is the source.

## Current phase: Launched, first client

The objective is still to **reach profitability.** The venture is past pre-launch: the
first Operator client, a personal-injury law firm, started its paid Service in the portal
on 2026-09-15 after a pilot, and Smokeball approved SMD's production integration on
2026-09-21 (UTC). The gating work now is delivering reliably on that client and converting
the proof into the next ones. Client specifics live in the private engagements repo, never
here.

## Priority tracks

| Track | State |
|---|---|
| **1 - Deliver on the first client** | Live. Routines run on request, by the client's authored intent. The medical chronology is metered in pages against an allowance that follows the firm's billing cycle. |
| **2 - Change requests** | Handled under the agreement's new-work clause: trial, measure, then propose written terms (see [Pricing & Economics](/admin/playbook/pricing-economics)). The first batch, three drafted legal documents, is being measured. |
| **3 - Operator hardening** | Oversight plane (ADR 0074), work-liveness and connector-outage alerting (ADR 0079, 0080), sticky-stop, vendor tool-surface drift detection, and the [obligation register](/admin/playbook/obligation-register) (ADR 0088). |
| **4 - Acquisition** | The automated lead-gen machine is retired (ADR 0060). Acquisition runs through the Phoenix referral network and a guarded paid-acquisition round (ADR 0066). The site carries real proof: the first case study and a PI-led law pack. |
| **5 - Hosted Agent** | Self-serve subscription published at `/agent` (ADR 0067, Decision #51). |

## What is built vs what is next

- **Built and running:** the three-subdomain web app (marketing, admin, portal) on
  Cloudflare Workers; portal billing as a ledger with the Operator subscription and its start
  door; the Operator platform on Fly + Hermes with one seat per client. See
  [Architecture Map](/admin/playbook/architecture-map) and
  [Operator Platform Architecture](/admin/playbook/operator-platform).
- **The leading edge:** confirming the newly approved Smokeball scopes reach the first
  client's existing production grant, and pricing the first change request from the firm's
  measured historical volume.
- **Next:** turn the law-firm engagement into a repeatable vertical pack and a second
  Operator client; run scope-based consulting engagements alongside the Operator.

## Constraints

- Phoenix metro, in-person default for the first engagements; remote-capable after.
- No dollar amounts published externally except the Hosted Agent page (page-scoped
  exemption); see [Pricing & Economics](/admin/playbook/pricing-economics).
- Client material lives in the private engagements repo; ss-console is public.
- The venture is run by a single Captain directing a fleet of agents; throughput scales with
  the fleet, judgment does not delegate (see [Operating Model & the Fleet](/admin/playbook/operating-model)).

## Related

- [Business Model](/admin/playbook/business-model) - the offerings these priorities serve
- [The Decision Stack](/admin/playbook/decision-stack) - the locked decisions behind the strategy
