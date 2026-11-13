# What the Client Sees and Controls

This document explains the Client portal: what it shows, what a client can change, and what it deliberately leaves out.

Labels used here, as defined in [INDEX.md](INDEX.md): running for a client today; built, set up per client; configured per engagement. Every capability described is one SMD can set up for a client.

## What the portal is

The Client portal directs the Operator; it is not a workspace, and it is separate from SMD's console. It has three jobs: Direct, Account and Administer. It never shows client business data. It holds no system of record, no copy of the client's systems, no workspace, and no approve or send button. Where it refers to a client's own items, it uses opaque references, not their content.

## Roles

There are three Client portal roles: principal, staff and compliance.

## Managed and client-operated controls

Each area of control is set per seat to one of two modes:

- **Managed**: the client can see it and ask SMD for a change.
- **Client**: a principal can operate it directly.

The default for every area is managed. A seat's agreement decides which areas, if any, are handed to the client.

## Direct

| Feature                                                                                | Who                                                  | Today                                                             |
| -------------------------------------------------------------------------------------- | ---------------------------------------------------- | ----------------------------------------------------------------- |
| A one-page view of the Operator: its role, duties, access and the people it works with | All roles                                            | Running for a client today                                        |
| The Operator's skills and what starts each one                                         | All roles, read only                                 | Running for a client today                                        |
| Set each routine's limit setting, one of three, up to the agreed ceiling               | A principal, where this area is handed to the client | Built, set up per client; handed over to the paying client's seat |
| Pause and resume the Operator, with a reason required                                  | A principal, where this area is handed to the client | Running for a client today                                        |
| The established voice and output shapes, read only                                     | A principal                                          | Running for a client today                                        |

## Account

| Feature                                                                | Who                                                      | Today                      |
| ---------------------------------------------------------------------- | -------------------------------------------------------- | -------------------------- |
| Activity, including pauses and limit-setting changes and who made them | All roles                                                | Running for a client today |
| The audit record for one reference, downloadable as a spreadsheet file | A principal and the compliance role                      | Running for a client today |
| A compliance dashboard and retention posture                           | A principal and the compliance role, if the seat opts in | Built, set up per client   |
| Plan and billing, including starting the subscription                  | A principal                                              | Running for a client today |

## Administer

| Feature                                                                                                                               | Who                                                                  | Today                                                                                     |
| ------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Connections: status, who holds each connection credential, reconnect, and entering a credential that can be written but not read back | A principal, where this area is handed to the client                 | Built, set up per client                                                                  |
| Team access: grant, revoke and invite                                                                                                 | A principal, only where this area is explicitly handed to the client | Built, set up per client. Where it is not handed over, SMD manages team access on request |

## Request a change

Any role can file a change request from the Client portal. It is emailed to SMD and tracked until it is acknowledged, resolved or declined. Running for a client today.

## What the portal does not show

- **Cost.** It is the one area the client never sees.
- **AI assistant connector grants.** Issuing and revoking these is done by SMD.
- **Evidence packet export.** SMD provides an evidence packet on request. A self-service export from the portal is configured per engagement.
- **The whole memory.** A principal sees the established voice and output shapes; a view of the whole memory is configured per engagement. See [memory](memory.md).
