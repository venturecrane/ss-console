# Memory and Learning

This document explains what the Operator remembers, where it is kept, who can write it, and how a client reads and corrects it.

Labels used here, as defined in [INDEX.md](INDEX.md): running for a client today; built, set up per client; configured per engagement. Every capability described is one SMD can set up for a client.

## How memory is organised

The Operator does not have one undifferentiated memory. It has separate lanes, each with its own owner and its own rules for who may write to it. The Operator itself can write only to the lanes built for it, and never to the lanes that set its rules.

## Authored context

What it holds: how to work with each person, background on the business, and guardrails. This is part of the seat's configuration.

Who writes it: SMD, through a reviewed change. The Operator cannot write it.

Where it lives: in SMD's version-controlled configuration, loaded into the Operator's identity at each start.

Running for a client today.

## Learned working preferences

What it holds: working preferences a person has stated or shown, recorded per person. It records preferences such as "send me the summary first", never a label about someone's character.

Who writes it: the Operator, through a dedicated tool. The person the preference belongs to is attributed by the system, not by the agent. Capture is refused on a turn where the agent read untrusted content.

Where it lives: on the seat's own storage.

How a change sticks: when a person restates a preference, the new one supersedes the old one. Only current preferences are given to the Operator at the start of each turn.

Running for a client today.

## Runtime working memory

The underlying agent runtime keeps its own per-profile working notes on the seat's storage. It can be switched off per seat, so that on a seat serving several people one person's notes do not surface for another.

## Administrator corrections

What it holds: an administrator's statement of how a kind of output should be shaped (for example, "weekly reports open with the open items").

Who writes it: captured only from the client's named administrators, into an append-only record the Operator cannot edit. The Operator reads a standing rule back, and it takes effect once an administrator confirms it. A rule asked for by someone else waits until an administrator says to apply it.

Running for a client today.

## Voice and format specifications

What it holds: the house voice and the expected shape for each kind of output.

Who writes it: the client's administrator, by conversation with the Operator; the result is installed where the Operator can read but not change it.

How voice works: voice is an authored specification per kind of output, checked by a gate on outgoing content. An earlier approach that learned voice from sample emails has been removed.

Running for a client today: declared on the paying client's seat, and checked by the gate on outgoing content.

## Record stores

What it holds: records the Operator keeps in folders that were authored for it, and only those folders.

Who writes it: the Operator, through three tools. Writes are refused on a turn where the agent read untrusted content.

Built, set up per client.

## Inferred memory

A separate service infers patterns nobody stated outright. Configured per engagement.

## Skills the Operator writes for itself

The Operator can author new skills for itself. The tool for this is in the code execution kind of action, so it runs only where the client's limits allow code execution. Built, set up per client.

## Ownership, export and visibility

- The per-client memory belongs to the client.
- The standard agreement states that at offboarding, the memory and the audit record are exported to the client within 14 days of termination.
- SMD can read the authored and learned lanes on a read-only administrative page. Each read is logged.
- The client reads and corrects what the Operator keeps. A person corrects it by telling the Operator, and a restated preference replaces the old one. The Client portal shows a principal the established voice and output shapes, read only.
- A client-facing view of the whole memory, with a way to dismiss a single remembered item, is configured per engagement.
