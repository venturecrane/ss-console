# Connections and Channels

This document explains how the Operator connects to a client's systems and how people reach it.

Maturity labels used in this document:

- **In production**: built and authored on the paying client's seat.
- **Built, not yet in client use**: built and tested, but runs only on SMD's own or proving seats, or is authored on no seat.
- **Specified, not built**: decided and written down, not built.

## How a connection is built

There are four ways a connection can be provided:

| Pattern                | What it is                                                                                                                                                                                                                                          | Maturity                             |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| Standard tool server   | A connection that speaks a common open protocol for AI tools. It can be the vendor's official server, a vetted community server, or one SMD writes. SMD-written servers ship in the shared seat image and stay inert until a seat is bound to them. | In production; the main path         |
| Built into the runtime | Capabilities the agent runtime provides natively. Today that is web search and web page reading.                                                                                                                                                    | Web search in production as authored |
| SMD-maintained adapter | A custom adapter for work that is not tool calls.                                                                                                                                                                                                   | Specified, not built                 |
| Stand-in               | An inert placeholder used for templates and testing.                                                                                                                                                                                                | Inert by design                      |

When a new system is needed, SMD looks in this order: the vendor's official server, then a vetted community server, then a server SMD writes.

Every tool a connection offers is classified into a kind of action before it can be used, so the same permission rules apply to every system. See [the permission model](permission-model.md).

## Connections by client system type

| Client system type                              | What the connection does                                                                                                                          | Tools and kinds                                                   | Maturity                       |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- | ------------------------------ |
| Email in the client's own Microsoft 365 tenant  | Reads and drafts in one pinned mailbox. Sending runs through a separate, locked-down path. No delete tool.                                        | 8 tools: 5 read, 1 internal write, 2 send                         | In production                  |
| Industry system of record (practice management) | Reads and updates matters, contacts and documents in a vertical system of record. Money movement and trust-account writes are on the banned list. | 67 tools: 39 read, 23 internal write, 3 commitment, 2 destructive | In production                  |
| Web analytics                                   | Reads site analytics.                                                                                                                             | 4 read tools                                                      | Built, not yet in client use   |
| The client's own website                        | Reads pages and drafts or updates content.                                                                                                        | 8 tools: 5 read, 3 internal write                                 | Built, not yet in client use   |
| Public video feed                               | Reads a public video channel.                                                                                                                     | 2 read tools                                                      | Built, not yet in client use   |
| SMD-hosted mailbox                              | A dedicated inbox SMD provides for the seat. Its send and admin tools are excluded; sending runs through SMD's controlled path.                   | 26 classified tools                                               | In production on SMD-run seats |
| Google Workspace (managed through SMD)          | Mail, calendar and files served through SMD's own controlled tools rather than a separate connection.                                             | 18 classified tools                                               | Built, not yet in client use   |
| Second practice-management option               | A community-built server for another vertical system of record.                                                                                   | Registered                                                        | Built, bound by no seat        |

The SMD-written connections together hold 91 tools. Many other systems appear in SMD's planning tables and industry presets but are not wired. A system not in this table should be treated as not connected today.

## Email channels

| Channel                                                                    | How it works                                                                                                                                   | Maturity                                       |
| -------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------- |
| Hosted per-seat inbox                                                      | A signed notification brings each email in; SMD's controlled path sends replies.                                                               | In production on SMD-run seats                 |
| A mailbox in the client's own Microsoft 365 tenant                         | Two separate app registrations: a read app with no send permission for the agent, and a send app whose send credentials only the broker holds. | In production                                  |
| A mailbox in the client's own Google Workspace; generic IMAP and SMTP mail | Specified, not built.                                                                                                                          | Specified, not built                           |
| Sending as a staff member on approval                                      | The Operator proposes; the named staff member approves by replying; the controlled path sends from that person's address.                      | Built and authored on the paying client's seat |

## Other channels

| Channel                                               | How it works                                                                                                                                                                                                  | Maturity                                            |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------- |
| AI assistant connector                                | Lets a client's staff reach the Operator from their own AI chat assistant. Each person logs in; each request checks a grant that can be revoked and always expires; all traffic passes through SMD's console. | Built; authored on two seat configurations          |
| Chat                                                  | A messaging-app bot with a mandatory allow-list of who may talk to it.                                                                                                                                        | Built, not yet in client use (SMD's own seats only) |
| Voice notes in                                        | Transcription of voice notes from listed senders.                                                                                                                                                             | Built, not yet in client use                        |
| System events in                                      | Other systems can notify the Operator of events, routed through the gate.                                                                                                                                     | In production                                       |
| SMS, team chat platforms, phone calls, spoken replies | None.                                                                                                                                                                                                         | Not available                                       |

## Rules that apply to every channel

A channel is only a pipe. Whatever comes in, on any channel, passes the same checks: where it came from, fencing of untrusted content, marking the turn as tainted when content is untrusted, classifying the sender against the client's list of people, and recipient-aware limits on any reply. An unknown sender's content is fenced: no reply, and no send, commitment or delete on that turn.
