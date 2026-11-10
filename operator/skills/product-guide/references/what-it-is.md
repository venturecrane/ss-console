# What the Operator Is

This document says what the Operator is, what an industry pack is, and gives one short example of a seat at work. It describes the product as built, not as planned.

## How to read the maturity labels

Every capability in this library carries one of three labels.

- **In production**: built, and set up on a paying client's seat.
- **Built, not yet in client use**: the code exists and is merged, but no paying client's seat uses it yet. It runs only on SMD's own test seats, or on no seat at all.
- **Specified, not built**: decided and written down as a design, but not built.

Only capabilities marked "In production" should be described to a prospect as something a client has today.

## Definition

The Operator is a managed AI worker. A business decides what it does, and SMD Services runs it.

Each client gets one isolated seat. A seat is a dedicated machine of its own, with its own storage volume. It runs an open-source agent runtime (the agent), wrapped in SMD's own control layer. It connects to the client's own systems, such as the client's email or its system of record. People reach it over channels they already use, mainly email.

The Operator has no fixed job. What it does is authored for each engagement across five surfaces:

- **Skills**: the kinds of work it knows how to do.
- **Limits**: for each kind of action, whether it acts on its own, asks first, drafts for a person, or has no limit written and so may not do it at all.
- **Voice**: how its written work should read, set per kind of output.
- **Connectors**: which of the client's systems it can reach.
- **Memory**: what it keeps about the client's work and people.

Anything not authored is refused. If a capability is not switched on for a seat, the Operator cannot use it.

Every tool the Operator calls passes through a gate written in code. The gate reads the client's written limits before the tool runs. One audit entry is written per tool call, to an audit record the agent cannot rewrite.

Work that has many steps or costs real effort runs as a queued job, not as a chat reply. A queued job has cost limits, an independent review, and proof that it was delivered to the right place. See [jobs.md](jobs.md).

SMD provisions the seat, releases updates to it, monitors it, and changes it as a managed service. The client directs the work. Where the agreement allows, the client can also move how much the Operator does on its own and can pause it. The client does this from the Client portal, which never shows the client's business data.

Commercially, the Operator is positioned as an alternative to the next hire. The client's existing systems are things it connects to, not things it replaces.

## Industry packs

An industry pack is a starting point for a client in a particular industry. It is used for onboarding and for selling. It is not a separate product, and nothing in the running seat behaves differently because of the pack's label. Every seat is still authored for its own client.

### What a pack holds

A pack is a short declarative description with nine parts:

- a name and a version
- compliance boundaries, named in words
- persona archetypes (typical roles the worker plays)
- a list of skills
- suggested connectors (which kinds of system to connect)
- templates
- test fixtures
- evaluations

### How a pack maps to a seat

- **Skills**: a pack lists skills by name. The skill content itself lives in one shared library that every seat draws from.
- **Connectors**: a pack suggests which systems to connect. The seat's own configuration decides what is actually connected.
- **Compliance boundaries**: a pack names its boundaries in plain words. These are carried out through the seat's written limits and the skills' own rules. There is no pack-level rule enforced in code today: the code table for pack-level limits exists but is empty.
- **Default limits**: a pack sets none. What the Operator may do on its own is authored separately for each seat.

The original design described three layers (platform, industry pack with add-ons, client) with version pinning per pack. That is not how it is built. Skills are flat and shared, and the pack label is not versioned into the seat.

Besides packs, SMD has two other kinds of preset: role presets (a few standard worker roles) and three seat presets (starting configurations for a new seat).

### Which packs exist

There are 13 industry packs: accounting, dental, home services, insurance, law firm, marketing agency, medical spa, mortgage, property management, real estate, registered investment advisers, title, and veterinary.

| Pack             | Maturity                                                                                                                                         |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| Law firm         | In production, on one paying client's seat                                                                                                       |
| Real estate      | A minimal one-skill preset, used only on a seat SMD pays for. Built, not yet in client use                                                       |
| The other eleven | Specified, not built. Each lists 14 skills. Only the 2 or 3 shared general skills they reuse exist; their industry-specific skills are not built |

A pack that is only written down is a brief, not a shipped pack. Do not describe the eleven specified packs as available.

## A seat at work: one example

One pack is in production, for a law practice. It covers paralegal and coordinator work: routing the inbox, intake, keeping cases moving, deadline tracking, and drafting. It never gives legal judgment, advice or representation. It connects to the practice's case-management system and to a mailbox in the practice's own email tenant. Six kinds of queued job serve that pack, including a medical chronology and document drafting. Content tied to one case cannot be sent to another case's recipient. Nothing touching deadlines or money is handled on its own, and nothing goes to opposing parties or a court without a person. This is one example. The same model applies to any business.
