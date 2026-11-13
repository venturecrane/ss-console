# Connections and Channels

This document explains how the Operator connects to a client's systems and how people reach it.

Labels used here, as defined in [INDEX.md](INDEX.md): running for a client today; built, set up per client; configured per engagement. Every capability described is one SMD can set up for a client.

## How a connection is built

There are four ways a connection can be provided:

| Pattern                | What it is                                                                                                                                                                                                                                          | Today                                     |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------- |
| Standard tool server   | A connection that speaks a common open protocol for AI tools. It can be the vendor's official server, a vetted community server, or one SMD writes. SMD-written servers ship in the shared seat image and stay inert until a seat is bound to them. | Running for a client today; the main path |
| Built into the runtime | Capabilities the agent runtime provides natively. Today that is web search and web page reading.                                                                                                                                                    | Web search running for a client today     |
| SMD-maintained adapter | A custom adapter for work that is not tool calls.                                                                                                                                                                                                   | Configured per engagement                 |
| Stand-in               | An inert placeholder used for templates and testing.                                                                                                                                                                                                | Inert by design                           |

When a new system is needed, SMD looks in this order: the vendor's official server, then a vetted community server, then a server SMD writes.

Every tool a connection offers is classified into a kind of action before it can be used, so the same permission rules apply to every system. See [the permission model](permission-model.md).

## Connections by client system type

| Client system type                              | What the connection does                                                                                                                                     | Tools and kinds                                                   | Today                      |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------- | -------------------------- |
| Email in the client's own Microsoft 365 tenant  | Reads and drafts in one pinned mailbox. Sending runs through a separate, locked-down path. No delete tool.                                                   | 8 tools: 5 read, 1 internal write, 2 send                         | Running for a client today |
| Industry system of record (practice management) | Reads and updates matters, contacts and documents in a vertical system of record. It never moves money: money movement and trust-account writes are refused. | 67 tools: 39 read, 23 internal write, 3 commitment, 2 destructive | Running for a client today |
| Web analytics                                   | Reads site analytics.                                                                                                                                        | 4 read tools                                                      | Built, set up per client   |
| The client's own website                        | Reads pages and drafts or updates content.                                                                                                                   | 8 tools: 5 read, 3 internal write                                 | Built, set up per client   |
| Public video feed                               | Reads a public video channel.                                                                                                                                | 2 read tools                                                      | Built, set up per client   |
| SMD-hosted mailbox                              | A dedicated inbox SMD provides for the seat. Its send and admin tools are excluded; sending runs through SMD's controlled path.                              | 26 classified tools                                               | Built, set up per client   |
| Google Workspace (managed through SMD)          | Mail, calendar and files served through SMD's own controlled tools rather than a separate connection.                                                        | 18 classified tools                                               | Built, set up per client   |
| Second practice-management option               | A community-built server for another vertical system of record.                                                                                              | Registered                                                        | Built, set up per client   |

The SMD-written connections together hold 91 tools. Any other system a client runs is connected the same way, in the order above: the vendor's official server, a vetted community server, or one SMD writes. That is configured per engagement.

## Email channels

| Channel                                                                    | How it works                                                                                                                                   | Today                      |
| -------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------- |
| Hosted per-seat inbox                                                      | A signed notification brings each email in; SMD's controlled path sends replies.                                                               | Built, set up per client   |
| A mailbox in the client's own Microsoft 365 tenant                         | Two separate app registrations: a read app with no send permission for the agent, and a send app whose send credentials only the broker holds. | Running for a client today |
| A mailbox in the client's own Google Workspace; generic IMAP and SMTP mail | SMD sets up the mailbox through the same separated read and send paths.                                                                        | Configured per engagement  |
| Sending as a staff member on approval                                      | The Operator proposes; the named staff member approves by replying; the controlled path sends from that person's address.                      | Running for a client today |

## Other channels

| Channel                                                   | How it works                                                                                                                                                                                                  | Today                      |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------- |
| AI assistant connector                                    | Lets a client's staff reach the Operator from their own AI chat assistant. Each person logs in; each request checks a grant that can be revoked and always expires; all traffic passes through SMD's console. | Built, set up per client   |
| Chat                                                      | A messaging-app bot with a mandatory allow-list of who may talk to it.                                                                                                                                        | Built, set up per client   |
| Voice notes in                                            | Transcription of voice notes from listed senders.                                                                                                                                                             | Built, set up per client   |
| System events in                                          | Other systems can notify the Operator of events, routed through the gate.                                                                                                                                     | Running for a client today |
| Texting, team chat platforms, phone calls, spoken replies | SMD sets up the channel a client wants. It comes in through the same gate, so every rule below applies to it.                                                                                                 | Configured per engagement  |

## When a connection drops

When a connection to a client system drops, the Operator stops the action that needs it rather than work from stale data, and resumes when the connection is back. SMD's monitoring tracks failures for each connection, so a connection that stays down reaches SMD.

## Rules that apply to every channel

A channel is only a pipe. Whatever comes in, on any channel, passes the same checks: where it came from, fencing of untrusted content, marking the turn as tainted when content is untrusted, classifying the sender against the client's list of people, and recipient-aware limits on any reply. An unknown sender's content is fenced: no reply, and no send, commitment or delete on that turn.
