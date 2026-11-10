# What the Client Sees and Controls

This document explains the Client portal: what it shows, what a client can change, and what it deliberately leaves out.

Maturity labels used in this document:

- **In production**: built and authored on the paying client's seat.
- **Built, not yet in client use**: built and tested, but runs only on SMD's own or proving seats, or is authored on no seat.
- **Specified, not built**: decided and written down, not built.

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

| Feature                                                                                | Who                                                  | Maturity                                                                          |
| -------------------------------------------------------------------------------------- | ---------------------------------------------------- | --------------------------------------------------------------------------------- |
| A one-page view of the Operator: its role, duties, access and the people it works with | All roles                                            | Built                                                                             |
| The Operator's skills and what starts each one                                         | All roles, read only                                 | Built                                                                             |
| Set each routine's limit setting, one of three, up to the agreed ceiling               | A principal, where this area is handed to the client | Built; handed over on two seat configurations, including the paying client's seat |
| Pause and resume the Operator, with a reason required                                  | A principal, where this area is handed to the client | In production                                                                     |
| The established voice and output shapes, read only                                     | A principal                                          | Built                                                                             |

## Account

| Feature                                                                | Who                                                      | Maturity                                       |
| ---------------------------------------------------------------------- | -------------------------------------------------------- | ---------------------------------------------- |
| Activity, including pauses and limit-setting changes and who made them | All roles                                                | Built                                          |
| The audit record for one reference, downloadable as a spreadsheet file | A principal and the compliance role                      | Built                                          |
| A compliance dashboard and retention posture                           | A principal and the compliance role, if the seat opts in | Built, not yet in client use (no seat opts in) |
| Plan and billing, including starting the subscription                  | A principal                                              | In production                                  |

## Administer

| Feature                                                                                                                               | Who                                                                  | Maturity                                                                                                                        |
| ------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| Connections: status, who holds each connection credential, reconnect, and entering a credential that can be written but not read back | A principal, where this area is handed to the client                 | Built, not yet in client use (no seat hands it over)                                                                            |
| Team access: grant, revoke and invite                                                                                                 | A principal, only where this area is explicitly handed to the client | Built, not yet in client use. It is refused unless enabled, and no seat enables it, so today SMD manages team access on request |

## Request a change

Any role can file a change request from the Client portal. It is emailed to SMD and tracked until it is acknowledged, resolved or declined. In production.

## What the portal does not show

- **Cost.** It is the one area the client never sees.
- **AI assistant connector grants.** Issuing and revoking these is done by SMD.
- **Evidence packet export.** A self-service export is specified, not built.
- **Memory.** There is no client-facing memory view. See [memory](memory.md).
