---
title: Start Here
section: running
order: 1
owner: captain
review_every: 30
last_reviewed: "2026-10-21"
summary: Your first week reading the Playbook, and what you should be able to do on your own by day 30.
sources:
  - label: Handbook guide (who this is written for)
    href: docs/handbook/README.md
  - label: Operator implementation playbook
    href: docs/runbooks/operator/implementation-playbook.md
---

You run SMD Services day to day. AI agents and, when needed, contractors do the technical work; you direct them, approve what is yours to approve, and bring the Captain what is the Captain's. This page is the order to read the Playbook in, and the test of when you are ready.

## Your first week

Read in this order. Everything in the Engineering Appendix is reached by link when an agent's report sends you there; you do not need to read it up front.

| Day | Read | You will know |
|---|---|---|
| 1 | [Overview](/admin/playbook/overview), [Business Model](/admin/playbook/business-model), [The Operator, in Plain Terms](/admin/playbook/the-operator), [The Operator Thesis](/admin/playbook/operator-thesis), [Positioning & Voice](/admin/playbook/positioning-voice) | What SMD is, what it sells, what the Operator does for a client, and how SMD talks |
| 2 | [Who Does What](/admin/playbook/who-does-what), [Money](/admin/playbook/money) | What you may decide alone, what goes to the Captain, and how clients pay |
| 3 | [Clients & Commitments](/admin/playbook/clients-and-commitments), [What We Owe Clients](/admin/playbook/obligation-register) | Who the clients are and what we have promised them |
| 4 | [The Administrator's Week](/admin/playbook/administrators-week), [When Something Goes Wrong](/admin/playbook/when-something-goes-wrong), [Work Health](/admin/playbook/work-health) | What arrives in the team inbox and what to do about each kind |
| 5 | [Changing a Client's Operator](/admin/playbook/changing-a-clients-operator), [The Operator Console](/admin/playbook/operator-console), [The Client Portal](/admin/playbook/client-portal), [Glossary](/admin/playbook/glossary) | How a client's Operator is changed, and what the client sees |
| Week 2 | [Continuity](/admin/playbook/continuity), [Handoff Companion](/admin/playbook/handoff-companion), [Disaster Recovery](/admin/playbook/disaster-recovery), the [Operator implementation playbook](https://github.com/venturecrane/ss-console/blob/main/docs/runbooks/operator/implementation-playbook.md) | What happens when the Captain is away, where the private material is, and how a new client is stood up |

Every page shows who owns it and when it was last checked against its sources. "Not yet reviewed" means nobody has checked it since the review system started, not that it is wrong.

## By day 30, you can do these on your own

The test of this Playbook is whether you can do each of these without asking the Captain. Some still depend on material that has not been written yet; those are marked, and they are gaps in the Playbook, not in you.

| # | Task | Ready now? |
|---|---|---|
| 1 | Explain in a paragraph what SMD sells, to whom, and how a sale goes from first conversation to paying client | Yes |
| 2 | Name the current clients, what each has, and what we owe each one | Yes, with an agent reading the register for you |
| 3 | Run the daily and weekly routine, including the GitHub issues no email announces | Yes |
| 4 | Respond when a client's Operator stops doing its scheduled work | Yes |
| 5 | Take a client's request for a new capability from ask to done | Yes |
| 6 | Take a new client from signed to live | Partly: the implementation playbook covers the work; the commercial steps are in each client's folder in the private repository |
| 7 | Issue and present an invoice, and know what SMD spends each month | Partly: the mechanics are here; the monthly amounts in `admin/vendor-billing.md` are still blank for the Captain to fill |
| 8 | Get your own access to every system you need | Partly: `admin/accounts.md` lists every account and how it transfers; who owns each and how it is recovered are still blank for the Captain, and several accounts are under the Captain's name today |
| 9 | Say what you must never do (client data, voice, sends) | Yes |
| 10 | Know the plan if the Captain is unreachable, a key leaks, or a vendor goes down | Partly: the Captain-away posture is written; key compromise and vendor outage plans are not |
| 11 | Look up any term an agent's report uses | Mostly: the glossary still lacks some terms |

Rows marked "partly" are waiting on facts only the Captain holds, written as blanks in the private companion ([Handoff Companion](/admin/playbook/handoff-companion)).
