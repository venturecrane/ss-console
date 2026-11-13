# The Permission Model

This document explains how the Operator decides what it may do. Every tool call the agent makes is checked in code against the limits written for that client before the call runs. The agent cannot talk its way past the check.

Labels used here, as defined in [INDEX.md](INDEX.md): running for a client today; built, set up per client; configured per engagement. Every capability described is one SMD can set up for a client.

## Action classes

Every tool the Operator can call belongs to exactly one kind of action (internally, an action class). The gate decides by kind of action, not by tool name. There are 11 kinds: 9 that can be given a limit, plus `read` and `refused`.

| Kind of action           | What it covers                                                                | Example                                                                |
| ------------------------ | ----------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| `read`                   | Looking things up. Always allowed; not authorable                             | Search a mailbox, look up a record, search the web                     |
| `internal_write`         | Notes, drafts and internal state                                              | Create a mail draft, write a note to the system of record, queue a job |
| `external_send`          | Sending to an outside party who is on none of the client's lists              | Send, reply, forward                                                   |
| `external_send_internal` | Sending to the client's own rostered staff                                    | A staff digest, an escalation                                          |
| `external_send_client`   | Sending to one of the client's customers on its list                          | A proactive chase                                                      |
| `external_send_vendor`   | Sending to a rostered vendor                                                  | A records chase                                                        |
| `external_send_as_staff` | Sending from a staff member's own address, on that person's approval          | A send that carries a "from" address                                   |
| `commitment`             | Binding the business: sign, accept terms, agree to dates, create a new record | Create a new record, register a subscription                           |
| `destructive`            | Deleting or anything irreversible                                             | Delete a file, delete a thread                                         |
| `code_execution`         | Running arbitrary code, a shell, or a sub-agent                               | Execute code, author a new skill                                       |
| `refused`                | Any tool that is not registered. Terminal                                     | Not applicable                                                         |

The registry classifies every tool name the Operator can call. A tool that is not in the registry is refused.

## The banned list

The Operator never moves money. Moving money sits outside the kinds of action entirely and is refused before any policy is consulted, and no setting turns it on.

## Ceilings from least to most restrictive

Each kind of action that can be given a limit gets a ceiling. A limit has four values, from least to most restrictive. The internal ceiling name is shown in brackets:

1. Acts on its own (`autonomous`): the Operator may act on its own.
2. Asks first (`confirm`): the Operator proposes, and a person approves in the current exchange.
3. Drafts for a person (`draft_for_review`): the Operator prepares a draft and a person sends or acts.
4. No limit written (`refused`): the Operator may not do it. Writing nothing and writing a refusal have the same effect.

What each limit means differs by kind of action, because the risks differ:

| Kind of action         | Acts on its own                              | Asks first                                           | Drafts for a person             | No limit written |
| ---------------------- | -------------------------------------------- | ---------------------------------------------------- | ------------------------------- | ---------------- |
| The four kinds of send | Sends                                        | Held until a person approves in the current exchange | Blocked and turned into a draft | Refused          |
| Send as staff          | Refused                                      | Proposed to the named staff member                   | Refused                         | Refused          |
| `internal_write`       | Runs                                         | Refused                                              | Runs, recorded as a draft       | Refused          |
| `commitment`           | Still needs approval in the current exchange | Held for an administrator's written instruction      | Blocked                         | Refused          |
| `destructive`          | Still needs approval in the current exchange | Approvable for one narrow kind of deletion only      | Refused                         | Refused          |
| `code_execution`       | Runs                                         | Refused                                              | Refused                         | Refused          |

Two points representatives are often asked about:

- Asks first works for sends, for commitments, and for one narrow kind of deletion (deleting a listed set of calendar events). Any other deletion given the limit asks first is refused. Asks first on commitments and on sends as a staff member is running for a client today; on other sends it is built, set up per client.
- Commitments and destructive actions never run on the Operator's word alone. Even at acts on its own, they need a person's approval in the current exchange.

## Rules that hold everywhere

| Rule                                          | What it means                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Unset means refused                           | A kind of action with no limit written is refused. A missing or garbled setting gives no access, never a default. Running for a client today.                                                                                                                                                                                                                                                                                                                                                                                               |
| Two separate dials                            | Exposure says how far each kind of action may go. Initiation says what may start each skill: a person asking, a schedule, or an incoming system event. Both are authored.                                                                                                                                                                                                                                                                                                                                                                   |
| The routine grid is what the client sees      | The client sees a grid of routines and limit settings. The grid compiles down to the gate's settings: ceilings per recipient type, what may start each routine, content rules and banned tools. Running for a client today.                                                                                                                                                                                                                                                                                                                 |
| Ceilings come from the agreement              | Each routine and kind of action has an agreed ceiling. The Client portal, the seat on write and again on read, and a per-client test all enforce it. A client can lower a limit setting or raise it back up to the ceiling, never past it.                                                                                                                                                                                                                                                                                                  |
| The Operator cannot raise its own ceiling     | Its configuration is owned by a separate system account and is read-only to the agent.                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| Untrusted content cannot drive outward action | On a turn where the agent read untrusted content (an email from an unknown sender, a web page, a document), sends, commitments, deletions and code execution are refused. The Operator may still read and draft. There are three narrow exceptions written into the code. Each covers a send whose recipients the content that was read cannot change (for example, recipients fixed by code before the turn started, or only the client's own authored administrators), and each is marked in its audit entry. Running for a client today. |
| Approvals cannot be forged                    | An approval flag the agent tries to put in its own request is stripped. Approval only counts when it comes from a trusted channel: a plain yes from the allow-listed owner in chat, an administrator's emailed yes, or the named staff member's emailed reply.                                                                                                                                                                                                                                                                              |
| Industry floors can only narrow               | The gate can apply an industry-wide floor that only ever tightens a ceiling. Industry discipline is applied through each seat's own written limits, and a floor can be set on top of them. Built, set up per client.                                                                                                                                                                                                                                                                                                                        |

## What is in use today

- Drafts for a person on outside sends: running for a client today.
- Sends to listed staff, the client's listed customers and listed vendors, at the level the client writes: running for a client today.
- Sending as a staff member on that person's approval: running for a client today.
- Sends on its own to outside parties on none of the client's lists: built, set up per client.
- Code execution: built, set up per client.

## Pause, stop and safety

| Control                       | What it does                                                                                                                                                                                                                                                        | Today                      |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------- |
| Client pause                  | Every tool call is refused. Connection and handoff entry points return "unavailable". Incoming system events are parked. The pause survives a restart. Queued jobs wait and resume when the pause lifts. A reason is required.                                      | Running for a client today |
| SMD clear                     | Clearing a stop is done from SMD's console and is recorded.                                                                                                                                                                                                         | Running for a client today |
| Safety breaker                | Two states only: normal and stopped. It trips on 8 consecutive tool failures within 10 minutes, on 20 refusals within 30 minutes, or when a day's model usage reaches twice the written daily limit. Once stopped, the seat stays stopped until a person clears it. | Running for a client today |
| Job cost limits               | Each kind of queued job has a per-job cost limit and a monthly cost limit, checked before every paid model call.                                                                                                                                                    | Running for a client today |
| Inbound wake cap              | A storm limiter on how many incoming events may wake the Operator in a day (platform default 200). It protects against floods; it is not the spending control.                                                                                                      | Running for a client today |
| AI assistant connector revoke | Where a client's staff reach the Operator from their own AI assistant, each grant is checked on every request, revoking it cuts the next call, and every grant expires.                                                                                             | Built, set up per client   |
| Scheduled-task containment    | SMD can set a switch so the next start runs no scheduled tasks at all.                                                                                                                                                                                              | Running for a client today |
| Job stop                      | SMD can stop a queued job: no further model call, nothing filed, no reply sent.                                                                                                                                                                                     | Running for a client today |
