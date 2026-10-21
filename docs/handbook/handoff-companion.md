---
title: Handoff Companion
section: running
order: 9
owner: captain
review_every: 30
last_reviewed: "2026-10-21"
summary: Where the private material lives, what it holds today, what it will hold, and how you get access.
sources:
  - label: Repository visibility and the engagement split (ADR 0081)
    href: docs/adr/0081-repository-visibility.md
  - label: Who holds each secret (secret custody)
    href: operator/bin/lib/secret_custody.py
---

This Playbook is public, because the repository it lives in is public. Anything about a specific client's terms, SMD's own accounts, or how to recover them lives in a private companion instead, and the Playbook only points to it.

## Where it is

The private repository **`venturecrane/engagements`**. Access is granted by the Captain, who adds you to it on GitHub. Agents read it from a local copy (usually `~/dev/engagements`); safety checks refuse to let an agent write about a client until it has read that client's dossier there, and they refuse rather than guess if the copy is missing.

## What it holds today

| Path | What is there |
|---|---|
| `operator/customers/<slug>/dossier.md` | What an agent must know before touching that client's engagement |
| `operator/customers/<slug>/correspondence/` | The letters, word for word |
| `operator/customers/<slug>/agreements/` | Signed and draft agreements |
| `operator/customers/<slug>/` other files | Proposals, scoping, test plans and other client documents |
| `pricing/` | The internal price records, including the Operator's launch pricing |

Each client folder has the same name as that client's seat (see [Clients & Commitments](/admin/playbook/clients-and-commitments)). The seat's operational configuration stays in this public repository; the narrative and the terms stay private.

## The administrator's folder: `admin/`

Added 2026-10-21. Start with `admin/README.md`, then `accounts.md`.

| File | Answers |
|---|---|
| `admin/accounts.md` | Every vendor account: who owns it, how it is recovered, what plan it is on, what card pays for it, whether it is shared with Venture Crane, and how it transfers |
| `admin/recovery-contacts.md` | Who holds the break-glass access for the domain, email, GitHub, Fly, Cloudflare and the secrets vault |
| `admin/entity.md` | SMDurgan, LLC: where its records are, the bank, the accountant, the tax calendar |
| `admin/insurance.md` | The policy, carrier, limits and renewal date |
| `admin/vendor-billing.md` | What SMD pays each vendor each month, and which costs a client carries |
| `admin/domains.md` | The domains, their registrar, renewal dates and the mail service |
| `admin/clients.md` | Each client's status, agreement, billing method and contact |
| `admin/crane-dependency.md` | Each point where SMD depends on Venture Crane, and the agreement that covers it |

Everything the systems can prove is filled in, with its source: the domains and their expiry dates, the insurance in force, which accounts are shared with Venture Crane, where each credential lives. Facts only the Captain holds, such as who owns each account, how it is recovered, the bank and the monthly vendor spend, are written `TBD (Captain)` and stay visibly blank until filled; nothing is guessed. Each file carries the date it was last checked against the live account.

A check in that repository fails when the system gains a credential for a vendor that has no row in `accounts.md`, so a new vendor cannot arrive without an owner of record.

## Rules for the private material

- Secret values never go in either place. They live in the secrets vault; the companion says where and who holds access.
- Client documents (matter files, medical records, anything made from them) are not in the companion either. They are opened only in that client's ops workspace ([The Ops Machine](/admin/playbook/ops-machine)).
