# Built Versus Planned

This document lists what is in production, what is built but not yet in client use, and what is only specified. Use it before describing any capability to a prospect. Anything not in production must not be described as available.

Maturity labels used in this document:

- **In production**: built and authored on the paying client's seat.
- **Built, not yet in client use**: built and tested, but runs only on SMD's own or proving seats, or is authored on no seat.
- **Specified, not built**: decided and written down, not built.

This list rests on what SMD authors and documents. It does not record which controls a client has actually exercised.

## In production

- One isolated machine, storage volume and pinned runtime per client.
- A seat refuses to start if bound to another client's storage.
- A gate before every tool call that enforces the client's written limits, a gate that blocks outward action after reading untrusted content, and fencing of incoming content.
- Unset means refused.
- Drafts for a person on sends to outside recipients.
- Acts on its own for sends to listed staff, the client's listed customers and listed vendors, where written.
- The routine grid as the client's view of its limit settings.
- Client pause and resume from the Client portal.
- Gates that check where content came from and that identifiers are correct. (A citation check is not yet wired.)
- The voice and authored-specification gate on outgoing content, as wired.
- A chained audit record with one writer and one audit entry per tool call, its newest entry's fingerprint pinned off the machine, and a daily verification and archive.
- The read-only channel SMD's console uses, and the heartbeat.
- Email through a mailbox in the client's own Microsoft 365 tenant.
- Web search, where authored.
- Queued jobs with a proof gate, independent review, read-back and a watch for the reply.
- The daily safety breaker, cost collection and anomaly digest. (A time-budget part of the breaker exists but stops nothing.)
- The hourly work-health check, request notices, and paging SMD when a request has no answer.
- The obligations register.
- Version-controlled configuration with one publisher and a live, allow-listed apply on the seat.
- The Client portal for directing the Operator: reading, change requests and billing.
- The authored and learned memory lanes.
- The first industry pack, on one paying seat.
- Release rollouts in rings by a controller.

## Built, not yet in client use

- Acts on its own for sends to outside parties on none of the client's lists (written on no seat).
- Code execution (authored on no seat).
- The asks first limit (`confirm`) on sends. Built; whether a client has used it is not recorded here.
- Sending as a staff member on approval. Authored on the paying client's seat; use not recorded here.
- The client level dial (limit settings) in the Client portal. Built and handed to the paying client's seat; its live check is not yet promoted.
- Industry floors in the gate (the floor table is empty).
- The chat channel and voice-note transcription.
- The AI assistant connector with grants and revoke. Authored on two seats; client use not recorded here.
- Google Workspace through SMD's controlled tools, and the SMD-hosted mailbox, for clients.
- A durable engine for long-running agent work, separate from queued jobs.
- A signed seat specification, running in shadow mode only.
- Client-operated connections, client-operated team access and the compliance dashboard (no seat hands these over).
- More than one Operator role on a seat (every seat has one).
- The self-serve storefront and checkout. Provisioning is done by hand.
- Decommissioning tooling (not yet run for a client).

## Specified, not built

- A shared-catalog check that would complete the storage-binding protection.
- Signing each audit entry, and a sanctioned redaction path.
- Email adapters for a mailbox in the client's own Google Workspace, and for generic IMAP and SMTP.
- SMD-maintained custom adapters, and a Client portal relay for fixed connection credentials.
- Labelling every skill by how it runs, with a check.
- Inferred memory, and a queue for dismissing remembered items.
- A client-facing memory view.
- A self-service evidence packet export.
- Other industry packs beyond the first: their outlines exist, their skill instructions are not built, apart from one minimal preset on an SMD-paid seat.
- An independent oversight layer.

## Not a gate by design, or removed

- The "flag only" routine tier is a label, not an enforced gate.
- Learning voice from sample emails was removed. Voice is now an authored specification per kind of output.

## Numbers worth knowing

| Number                                 | Value                                                                                                                                                    |
| -------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Kinds of action                        | 11 (9 that take a limit, plus read and refused)                                                                                                          |
| Limit values                           | 4                                                                                                                                                        |
| Routine tiers                          | 3                                                                                                                                                        |
| Classified tool names                  | 299 (171 read, 96 internal write, 11 commitment, 8 code execution, 7 send, 6 destructive)                                                                |
| SMD-written connections                | 6 (5 real plus a test reference)                                                                                                                         |
| Tools in those connections             | 91 (56 read, 28 internal write, 2 send, 3 commitment, 2 destructive)                                                                                     |
| Registered tool-server connections     | 8                                                                                                                                                        |
| Ways a connection can be provided      | 4                                                                                                                                                        |
| Control-layer plugins                  | 29                                                                                                                                                       |
| Skills in the library                  | 87                                                                                                                                                       |
| Industry packs                         | 13, plus a template                                                                                                                                      |
| Kinds of queued job                    | 8 (6 covered by the hourly work-health check)                                                                                                            |
| Heartbeat fields                       | 44                                                                                                                                                       |
| Documented scheduled jobs              | 20                                                                                                                                                       |
| Background services                    | 3                                                                                                                                                        |
| Seat configurations                    | 8 authored, plus a template. One paying client is in production; the rest are SMD's own, proving or pre-production seats, or authored but not yet set up |
| Seats in release rings                 | 6                                                                                                                                                        |
| Configuration check interval on a seat | 15 seconds                                                                                                                                               |
| No answer before SMD is paged          | 30 minutes                                                                                                                                               |
| Heartbeat period and red threshold     | 60 seconds and 300 seconds                                                                                                                               |
| Operator roles per seat                | 1 on every seat                                                                                                                                          |
