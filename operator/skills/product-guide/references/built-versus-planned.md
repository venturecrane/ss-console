# What Runs Today, and What SMD Sets Up

This document lists what the Operator can do, grouped by where each capability stands for clients today. Every item on it is something SMD can set up for a client. Describe each one as a capability of the Operator, in the present tense. The grouping is for the representative: it answers "is a client running this today?" and "what would SMD set up for a new client?".

## How to use this page

- Describe the capability first, as something the Operator does once SMD configures it for a client.
- Name where it stands only when the person asks what is live, what a client has today, or what SMD would set up, or when it changes what to promise about timing. Never use it to say the Operator cannot do something.
- The labels:
  - **Running for a client today**: set up and switched on for a paying client.
  - **Built, set up per client**: built, and switched on for a client when the engagement calls for it.
  - **Configured per engagement**: SMD sets it up, and builds what it needs, when a client's engagement calls for it.
- Whether a client has used a control that is switched on (for example, pause) is not recorded here. A control can be in place and correctly never used.

## Running for a client today

- One isolated machine, storage volume and pinned runtime per client.
- A seat refuses to start if bound to another client's storage.
- A gate before every tool call that enforces the client's written limits, a gate that blocks outward action after reading untrusted content, and fencing of incoming content.
- Unset means refused.
- Drafts for a person on sends to outside recipients.
- Sends to listed staff, the client's listed customers and listed vendors, at the level the client writes.
- "Asks first" on commitments: a commitment waits for an administrator's written yes.
- Sending as a staff member: the named person approves the exact draft by email, and it goes from their address.
- The routine grid as the client's view of its limit settings.
- Client pause and resume from the Client portal.
- Gates that check where content came from and that identifiers are correct, and, on a law-firm seat, a check that blocks any legal citation the Operator did not read in that session.
- The voice and authored-specification gate on outgoing content.
- A chained audit record with one writer and one audit entry per tool call, its newest entry's fingerprint pinned off the machine, and a daily verification and archive.
- The read-only channel SMD's console uses, and the heartbeat.
- Email through a mailbox in the client's own Microsoft 365 tenant.
- Web search, where authored.
- Queued jobs with a proof gate, independent review, read-back and a watch for the reply.
- The daily safety breaker, cost collection and anomaly digest.
- The hourly work-health check, request notices, and paging SMD when a request has no answer.
- The obligations register.
- Version-controlled configuration with one publisher and a live, allow-listed apply on the seat.
- The Client portal for directing the Operator: the one-page view, skills, activity, the audit record for one reference, change requests and billing.
- The authored and learned memory lanes, and administrator corrections.
- The law-firm industry pack.
- Release rollouts in rings by a controller.

## Built, set up per client

- Sends on its own to outside parties on none of the client's lists.
- Code execution, and skills the Operator writes for itself.
- "Asks first" on sends.
- The client level dial in the Client portal: a principal moves a routine's setting up to its agreed ceiling. It is handed to the paying law-firm client's seat.
- Industry floors in the gate, which only ever tighten a limit.
- Chat and voice-note transcription.
- The AI assistant connector, with grants that expire and can be revoked.
- Google Workspace through SMD's controlled tools, and a mailbox SMD hosts for the seat.
- A durable engine for long-running agent work, separate from queued jobs.
- A signed seat specification.
- Client-operated connections, client-operated team access and the compliance dashboard.
- More than one Operator role on a seat.
- The self-serve storefront and checkout.
- Decommissioning: it has run on one of SMD's own seats, not yet for a client.

## Configured per engagement

- Texting a client's customers or staff, and reaching the Operator from team chat platforms.
- A mailbox in the client's own Google Workspace, and generic IMAP and SMTP mail.
- A connection to any other system a client runs. SMD builds or adopts the connection for that client.
- A client-facing memory view, and a way to dismiss a remembered item.
- Inferred memory: patterns the Operator works out without being told.
- A self-service evidence packet export from the Client portal.
- The industry-specific skills of the industry packs beyond the law firm. Each pack's outline exists; SMD builds its skills with the first client in that industry.
- Signing each audit entry, and a sanctioned redaction path.
- A shared-catalog check that would complete the storage-binding protection.
- Labelling every skill by how it runs, with a check.
- An independent oversight layer.

## Not a gate by design, or removed

- The "flag only" routine tier is a rule the skill follows and the audit record checks. The hard enforcement is at each tool call.
- Learning voice from sample emails was removed. Voice is now an authored specification per kind of output.

## Numbers worth knowing

| Number                                 | Value                                                                                                                                                    |
| -------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Kinds of action                        | 11 (9 that take a limit, plus read and refused)                                                                                                          |
| Limit values                           | 4                                                                                                                                                        |
| Routine tiers                          | 3                                                                                                                                                        |
| SMD-written connections                | 6 (5 real plus a test reference)                                                                                                                         |
| Tools in those connections             | 91 (56 read, 28 internal write, 2 send, 3 commitment, 2 destructive)                                                                                     |
| Registered tool-server connections     | 8                                                                                                                                                        |
| Ways a connection can be provided      | 4                                                                                                                                                        |
| Industry packs                         | 13, plus a template                                                                                                                                      |
| Kinds of queued job                    | 7 (6 covered by the hourly work-health check), plus a note SMD approves into a digest                                                                    |
| Documented scheduled jobs              | 20                                                                                                                                                       |
| Background services                    | 3                                                                                                                                                        |
| Seat configurations                    | 8 authored, plus a template. One paying client is running today; the rest are SMD's own, proving or pre-production seats, or authored but not yet set up |
| Configuration check interval on a seat | 15 seconds                                                                                                                                               |
| No answer before SMD is paged          | 30 minutes                                                                                                                                               |
| Heartbeat period and red threshold     | 60 seconds and 300 seconds                                                                                                                               |
| Operator roles per seat                | 1 on every seat today                                                                                                                                    |

Paging thresholds and watch windows, such as the no-answer page and the heartbeat red threshold, are internal monitoring. They are never response-time commitments to quote to a prospect.
