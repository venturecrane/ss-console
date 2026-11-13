# Isolation and Data Custody

This document explains how each client's Operator is kept apart from every other client, what SMD's console and SMD's agents can and cannot see, where credentials live, how the audit record works, and what happens to data at the end.

Labels used here, as defined in [INDEX.md](INDEX.md): running for a client today; built, set up per client; configured per engagement. Every capability described is one SMD can set up for a client.

## Isolation between clients

- Each client gets its own seat: its own application, its own machine, its own storage volume and its own store for skill instructions. Running for a client today.
- Two stores are shared and separated per client by key: the configuration store and the audit archive. SMD does not warehouse copies of the client's files in either.
- A seat refuses to start if it is bound to another client's storage. Running for a client today.
- Client machines may not query each other. SMD's control plane may read audit data across clients.
- Only the gate's entry point is public. The agent runtime's own port is not exposed.
- SMD's console finds a seat from an explicit list, never by deriving it from a client's name.
- Each client's data lives on that seat's own machine-local storage.
- Seats run in two west-coast United States regions, chosen per seat.

## How SMD's console reads a seat

SMD's console reads each seat through one narrow, read-only channel. Its rules:

| Rule                                     | How it holds                                                                                                                                                                                                                                                             |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| One client per call                      | Each seat has its own derived key. The master key lives only in SMD's console, so a seat's key cannot read another seat.                                                                                                                                                 |
| Read-only                                | Every read opens a fresh read-only connection.                                                                                                                                                                                                                           |
| Audited                                  | Every read SMD's console makes is logged.                                                                                                                                                                                                                                |
| Fails closed                             | Any fault returns an empty result rather than a partial or wrong one.                                                                                                                                                                                                    |
| Governance records, not business content | It serves the audit record and its export, the memory export, the configuration export, job states, limits and usage. Requests for drafts, records or activity return nothing by design. Job information is limited to states, counts and identifiers, never a document. |

## What SMD can see beyond the console

Stated plainly, so representatives do not overclaim:

- **Request notices.** When someone asks the Operator for something, SMD's operations mailbox receives a request notice that carries the requester's words and the Operator's reply. This is how SMD watches that every request is answered.
- **Per-client ops workspace.** When a client's documents need work beyond the seat, SMD's agents do it in that client's ops workspace, which is sealed to that client's seat. Work there is kept for up to 30 days before it is deleted. No person at SMD reads client files, and SMD does not warehouse copies of them.
- **Administrative access to the seat.** An SMD administrator with full access to a seat can reach that seat's connection credentials and send credentials.

So the accurate statement is: SMD's console never shows client business content. Client files are handled only by AI agents: the client's own Operator, and SMD's agents in the workspace sealed to that client. What reaches SMD's operations mailbox is the request notice, with the requester's words and the reply.

## Credential custody

| Credential                               | Where it rests                                                                                                                                                                                                                                                                                                    | Who can read it                      |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| The principle                            | A client's connection credentials stay on that client's machine, never in a shared store. Decommissioning deletes them with the storage volume.                                                                                                                                                                   | Not applicable                       |
| Send credentials for mail                | Files owned by the broker on the seat, readable only by the broker. The agent's own credentials cannot send.                                                                                                                                                                                                      | The broker only                      |
| Google Workspace credentials             | Seat secrets, written each start to a file readable only by the broker.                                                                                                                                                                                                                                           | The broker only                      |
| Delegated sign-in for a system of record | A refresh token stored as a file on the seat's storage volume. The sign-in returns to the seat's own gate, which completes it with the seat's own secret, bound to that client. The file is readable by the Operator's account and one connection group. It is not separately encrypted at the application layer. | The Operator's account and one group |

## The audit record

| Property                 | Fact                                                                                                                                                                                                                                                                                    | Today                      |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------- |
| Chained                  | Each audit entry carries a hash that includes the previous entry's hash, so any change to an earlier entry breaks the chain.                                                                                                                                                            | Running for a client today |
| One writer               | Only the broker writes it, one audit entry per tool call. The agent can read it but not change it. There is no update or delete operation.                                                                                                                                              | Running for a client today |
| Pinned off the machine   | Every heartbeat sends the newest audit entry's fingerprint to SMD's console, which keeps the history.                                                                                                                                                                                   | Running for a client today |
| Daily verify and archive | A daily job pulls each seat's record, checks that the pinned fingerprint is still there, pages SMD at the highest priority if not, and writes a dated copy to the audit archive, shared and keyed per client. The archive is locked so that no copy can be overwritten for seven years. | Running for a client today |
| Client-checkable sends   | Each reply's audit entry carries a fingerprint of the sent body that the client can recompute from its own mail system.                                                                                                                                                                 | Running for a client today |

The record is cryptographically chained and verified every day, and the agent cannot rewrite it, so it proves nothing was altered. For an IT reviewer, the mechanics plainly:

- The chain links every entry to the one before it, and the newest entry's fingerprint leaves the machine every minute, so the daily check has an outside anchor.
- Entries are chained, not individually signed.
- There is no redaction path: an entry, once written, stays.

## Retention and offboarding

- The standard agreement states that the audit record and the memory are returned to the client within 14 days of termination.
- The standard agreement states that return and destruction are completed within 30 days of termination.
- Only legal, tax, accounting and audit records are kept, and the offboarding states which and why.

## Model and provider posture

- Language-model processing runs through a major model provider's commercial service. Content passes through transiently, and under the provider's commercial terms it is not used to train models.
- SMD chooses the model provider. Models are chosen per seat, with a separate model for harder steps.
- Provisioning prefers a separate provider credential per seat.
- Some seats also use a speech-to-text processor and a web-search processor.
- SMD holds no SOC 2 or ISO 27001 certification of its own.
- No provider retention period, zero-retention arrangement or processing region is documented. Representatives should not claim one.
