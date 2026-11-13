# What the Operator Is

This document says what the Operator is, what an industry pack is, and gives one short example of a seat at work. It describes what the Operator can do for any client SMD configures it for.

## How to read the labels

The Operator is configured for each client, so a capability in this library is something SMD can set up for a client. Describe it in the present tense, as what the Operator does.

Where a document says how a capability stands for clients today, it uses one of three labels:

- **Running for a client today**: set up and switched on for a paying client.
- **Built, set up per client**: built, and switched on for a client when the engagement calls for it.
- **Configured per engagement**: SMD sets it up, and builds what it needs, when a client's engagement calls for it.

Use a label when a person asks what is live, what a client has today, or what SMD would set up, or when it changes what to promise about timing. A label never means the Operator cannot do it.

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
- **Compliance boundaries**: a pack names its boundaries in plain words. These are carried out through the seat's written limits and the skills' own rules, and the gate can also apply an industry floor that only ever tightens a limit.
- **Default limits**: a pack sets none. What the Operator may do on its own is authored separately for each seat.

Skills are flat and shared across every seat, and the pack label is not versioned into the seat.

Besides packs, SMD has two other kinds of preset: role presets (a few standard worker roles) and three seat presets (starting configurations for a new seat).

### Which packs exist

There are 13 industry packs: accounting, dental, home services, insurance, law firm, marketing agency, medical spa, mortgage, property management, real estate, registered investment advisers, title, and veterinary. Each one maps the seat the Operator fills in that industry, the work it takes on, and the line it does not cross, as a starting point SMD shapes with the client.

| Pack             | Where it stands for clients today                                                                                                                |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| Law firm         | Running for a client today                                                                                                                       |
| Real estate      | Built, set up per client: a starting preset, run on a seat SMD pays for                                                                          |
| The other eleven | Configured per engagement. Each pack lists its skills and shares the general ones; SMD builds its industry-specific skills with the first client |

## A seat at work: one example

One pack is running for a client today, for a law practice. It covers paralegal and coordinator work: routing the inbox, intake, keeping cases moving, deadline tracking, and drafting. It never gives legal judgment, advice or representation. It connects to the practice's case-management system and to a mailbox in the practice's own email tenant. Six kinds of queued job serve that pack, including a medical chronology, a demand and document drafting. Content tied to one case cannot be sent to another case's recipient. Nothing touching deadlines or money is handled on its own, and nothing goes to opposing parties or a court without a person. This is one example. The same model applies to any business.
