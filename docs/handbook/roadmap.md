---
title: Roadmap & Current Phase
section: business
order: 9
owner: captain
review_every: 180
last_reviewed: "2026-10-21"
summary: Where the venture stands on 2026-10-21 - what is live, what is in flight, and the decided next steps - from documented sources only.
sources:
  - label: Law-firm seat (billing start, allowances)
    href: operator/customers/ashton-price/customer.yaml
  - label: ADR 0080 - Connector-outage alerting (Smokeball approval date)
    href: docs/adr/0080-connector-outage-alerting.md
  - label: ADR 0060 - Retire the automated lead-gen machine
    href: docs/adr/0060-retire-automated-lead-gen-machine.md
  - label: ADR 0040 - Operator positioning, law-first
    href: docs/adr/0040-operator-positioning-and-why-us.md
  - label: ADR 0066 - Paid acquisition round one
    href: docs/adr/0066-paid-acquisition-round-one.md
  - label: Paid-ads launch kit (gate status)
    href: docs/marketing/paid-ads-launch-kit.md
  - label: ADR 0067 - Hosted Agent self-serve SKU
    href: docs/adr/0067-hosted-agent-self-serve-sku.md
  - label: ADR 0090 - Hosted-agent roles
    href: docs/adr/0090-hosted-agent-roles.md
  - label: ADR 0088 - Obligation register
    href: docs/adr/0088-obligation-register.md
  - label: Case study page
    href: src/pages/case-studies/personal-injury-law-firm.astro
  - label: Decision Stack
    href: docs/adr/decision-stack.md
---

SMD is past launch: it has one paying client, an Operator client, and the work now is delivering well for that client and turning the proof into the next ones. This page records what is live, what is in flight and what has been decided as next, as of 2026-10-21; it states only what a decision, a merged change or an open item records, and says so where something is not recorded.

This is the page most likely to fall out of date. Update it in the same pull request as any change that moves the venture.

## The objective

Reach profitability (`CLAUDE.md`). No date or revenue target for it is recorded beyond the consulting planning model in Decision #25 ([Pricing & Economics](/admin/playbook/pricing-economics)).

## What is live

| Area | State | Source |
|---|---|---|
| **First Operator client** | A personal-injury law firm (seat `ashton-price`) started its paid service in the portal on 2026-09-15, after a pilot | `operator/customers/ashton-price/customer.yaml`, billing-cycle note |
| **Practice-management connection** | Smokeball approved SMD's integration on 2026-09-21 (UTC) | ADR 0080, corrected note of 2026-10-12 |
| **Medical chronology** | Delivered on request for the firm and metered in pages against a monthly allowance that follows the firm's billing cycle | ADR 0087; `customer.yaml` |
| **Litigation status work** | A litigation status lane on the firm's seat, activated 2026-10-07 | `customer.yaml` |
| **What we owe the client** | Tracked in the obligation register, with overdue or unprovable rows flagged nightly | ADR 0088; [What We Owe Clients](/admin/playbook/obligation-register) |
| **Monitoring** | Work-liveness and connector-outage alerting to team@ | ADR 0079, ADR 0080; [Work Health](/admin/playbook/work-health) |
| **Hosted Agent** | Storefront and self-serve checkout at `/agent`; two hosted seats, both paid for by SMD while they are proven, one of them the first `couples` role seat | ADR 0067, ADR 0090; [Clients & Commitments](/admin/playbook/clients-and-commitments) |
| **Website** | Firm with flagship: home, `/operator`, industry packs, `/book`, `/about`, and a case study from the first client | `docs/marketing/positioning-spine.md`; `src/pages/case-studies/personal-injury-law-firm.astro` |
| **Consulting** | The full lead-to-handoff flow is built in the admin console and the collateral is written | [Customer Lifecycle](/admin/playbook/customer-lifecycle) |

## What is in flight

Each item is an open GitHub issue or an open register row on 2026-10-21.

- **Change requests from the first client.** The register holds the firm's open work, including change requests under the agreement's new-work clause; for some, SMD has proposed written terms and is waiting for the firm's written acceptance, and for others terms are not yet proposed (`.claude/bin/register list --client ashton-price`). Proposing terms is the Captain's ([Pricing & Economics](/admin/playbook/pricing-economics)).
- **The medical chronology as a durable routine.** Epic #2618 and its open slices (#2611 to #2617): contract limits, the runner on the client's machine, delivery into the matter.
- **Hardening.** Epic #2392, "confidence in what we ship to clients," with its open findings.
- **Client-operable controls.** #2003: a kill switch and limit controls the client can use in the portal, every change attributable.
- **Couples role seat.** #3264, the reachability contract for the first `couples` seat.
- **Configuration sync.** #1308: a client's configuration reaches the admin console's copy automatically on merge.
- **Runtime upgrades.** Newer versions of the agent runtime the seats use are waiting to be adopted (#3315 and earlier).

## Acquisition

- **Founder-run, law-first.** The automated lead-generation machine was retired on 2026-07-01; prospects are added by hand (ADR 0060). The Operator is positioned for law firms first (ADR 0040), and the website carries a law-firm pack and the first case study.
- **Trusted intermediaries.** ADR 0040 names intermediaries who already serve small firms (legal bookkeepers and practice-management consultants first) as the first lever, but records this track as parked, not decided.
- **Paid advertising.** A bounded first round of paid ads for law-firm operations was decided on 2026-07-05 (ADR 0066). The launch kit's last recorded status (2026-07-05) shows no spend until every gate is checked, with the SMD-owned ad accounts still the Captain's to set up. Whether the round has run since is not recorded in this repository.

## Decided next steps

- **More Operator clients from the law-firm proof.** The flagship goes to market law-first (ADR 0040) and packs are how it is made recognizable for each industry (ADR 0037, Tenet 5). No second client or date is recorded.
- **Hosted Agent roles.** Brokery and Draft Crane are to move under `operator/roles/` when work next touches their packaging (ADR 0090, decision 6).
- **The paid assessment** switches on after the first delivered consulting engagement (Decision #13).

Nothing else forward-looking is recorded. Do not let an agent fill this section with plans that no decision or open item supports.

## Constraints

- No dollar amounts on any public page except the Hosted Agent's (Decision #51).
- Client material stays in the private `venturecrane/engagements` repository; `ss-console` is public (Decision #52).
- Consulting is Phoenix-first, in person for the first engagements (Decision #5).
- The Captain, the administrator, and AI agents: the agents add throughput, and the decisions listed on [Who Does What](/admin/playbook/who-does-what) stay with people ([Operating Model](/admin/playbook/operating-model)).

## Related

- [Business Model](/admin/playbook/business-model): the offers these priorities serve.
- [The Decision Stack](/admin/playbook/decision-stack): the decisions behind them.
