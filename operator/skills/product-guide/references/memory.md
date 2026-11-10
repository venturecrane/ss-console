# Memory and Learning

This document explains what the Operator remembers, where it is kept, who can write it, and what is and is not built.

Maturity labels used in this document:

- **In production**: built and authored on the paying client's seat.
- **Built, not yet in client use**: built and tested, but runs only on SMD's own or proving seats, or is authored on no seat.
- **Specified, not built**: decided and written down, not built.

## How memory is organised

The Operator does not have one undifferentiated memory. It has separate lanes, each with its own owner and its own rules for who may write to it. The Operator itself can write only to the lanes built for it, and never to the lanes that set its rules.

## Authored context

What it holds: how to work with each person, background on the business, and guardrails. This is part of the seat's configuration.

Who writes it: SMD, through a reviewed change. The Operator cannot write it.

Where it lives: in SMD's version-controlled configuration, loaded into the Operator's identity at each start.

Maturity: in production.

## Learned working preferences

What it holds: working preferences a person has stated or shown, recorded per person. It records preferences such as "send me the summary first", never a label about someone's character.

Who writes it: the Operator, through a dedicated tool. The person the preference belongs to is attributed by the system, not by the agent. Capture is refused on a turn where the agent read untrusted content.

Where it lives: on the seat's own storage.

How a change sticks: when a person restates a preference, the new one supersedes the old one. Only current preferences are given to the Operator at the start of each turn.

Maturity: in production.

## Runtime working memory

The underlying agent runtime keeps its own per-profile working notes on the seat's storage. This is on by default and switched off on seats that serve more than one person, so one person's notes cannot surface for another.

## Administrator corrections

What it holds: an administrator's statement of how a kind of output should be shaped (for example, "weekly reports open with the open items").

Who writes it: captured only from the client's named administrators, into an append-only record the Operator cannot edit. A correction takes effect when it is promoted.

Maturity: built, not yet in client use.

## Voice and format specifications

What it holds: the house voice and the expected shape for each kind of output.

Who writes it: the client's administrator, by conversation with the Operator; the result is installed where the Operator can read but not change it.

How voice works: voice is an authored specification per kind of output, checked by a gate on outgoing content. An earlier approach that learned voice from sample emails has been removed.

Maturity: built and declared on some seat configurations; the gate on outgoing content is in production as wired.

## Record stores

What it holds: records the Operator keeps in folders that were authored for it, and only those folders.

Who writes it: the Operator, through three tools. Writes are refused on a turn where the agent read untrusted content.

Maturity: built, in use on several seat configurations.

## Inferred memory

A separate service that would infer unstated patterns is specified, not built. It runs on no seat.

## Skills the Operator writes for itself

The design allows the Operator to author new skills for itself. In practice the tool for this is the kind of action code execution, for which no seat writes a limit. Treat it as not available.

## Ownership, export and visibility

- The per-client memory belongs to the client.
- The standard agreement states that at offboarding, the memory and the audit record are exported to the client within 14 days of termination.
- SMD can read the authored and learned lanes on a read-only administrative page. Each read is logged.
- A client-facing memory view is not built. The earlier plan for a single client-owned memory artifact with its own client view was superseded; its principles were kept, but no client view exists today.
- There is no client-facing way to delete a single learned preference. A preference changes when the person restates it.
