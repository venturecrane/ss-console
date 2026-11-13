# How the Operator Works

The Operator is a managed worker that SMD builds for one business and runs for it. It does the connecting work between that business's people and its systems, inside limits the business writes down, and it keeps an audit record of every action. This document explains the product in seven views, each with one figure, from the idea to the mechanism, followed by the service around it, what runs today and what SMD sets up, and one industry example.

## One worker, configured for one business

The Operator has no fixed job. Five things are written down for each client, and anything not written down is refused.

| What is written down | What it means                                                          |
| -------------------- | ---------------------------------------------------------------------- |
| Skills               | The kinds of work it knows how to do, and what sets each one off.      |
| Limits               | How far it may go for each kind of action, set by the client.          |
| Voice                | How the business writes, by type of document.                          |
| Connections          | The client's systems it may reach, each one granted.                   |
| Memory               | What it knows about how this business works. It belongs to the client. |

It is sold against the next hire, a coordinator or an assistant, and never against software. The client's existing systems are things it connects to.

## Before and after: what changes for the business

Explainer view: #idea

View 1, the idea. What changes for the business? Figure 1 compares two pictures:

1. **Today.** The client's people (owners, managers, staff) sit on one side and the client's systems (email, records, calendar, files) on the other. Between them, done by hand: read the email, update the system, chase the document, log it, book the follow-up. The connecting work lives in people's heads and inboxes. It stops when someone is out, and it leaves when they do.
2. **With an Operator, people to Operator.** The client's people ask it and direct it (an arrow to the Operator), and it delivers work back to them (an arrow back). The people judge the work.
3. **The Operator itself.** One per client, on the client's seat. It works under limits the client writes, and every action is recorded.
4. **Operator to systems.** The Operator reads and writes in the client's own systems, which are unchanged and still the system of record.
5. **SMD Services underneath.** SMD builds, runs and watches the Operator as the managed service.
6. **For staff,** there is nothing new to log in to: they email it like a colleague.

## System context: what it touches, and who runs what

Explainer view: #context

View 2, system context. The client's systems stay the systems of record. Each client gets its own machine, its seat. SMD runs the service from outside it. Figure 2 shows four zones:

1. **The client's environment (client-owned, outside the seat):** administrators (named by the client); the Operator's mailbox (in the client's own mail tenant where the client has one, otherwise a mailbox SMD hosts); staff (the people the client has listed); the system of record (where the client keeps its work); and other connected systems (each one granted by the client).
2. **The Operator, one seat per client:** a front door (verifies every inbound message); the agent (does the work, and cannot send on its own keys); a broker (holds the send credentials and checks recipients); the audit record (one entry per action, append-only, chained); memory and configuration (what it has learned, and configuration it cannot edit); and a jobs runner (queued jobs: cost-limited, reviewed, proven).
3. **SMD Services (run by SMD):** the Client portal (pause, activity, limit settings); the pager and hourly triage (watches every seat); releases and configuration (by reviewed change only); and a per-client ops workspace (one per client, sealed to that client's seat, where SMD's agents work).
4. **The model provider (third party):** AI processing, which does not train on client content. Some seats also use a speech-to-text service and a web-search service.

Nine numbered connections link the zones:

1. **Staff ask by email.** A request is an email to the Operator's own mailbox, which sits in the client's mail tenant where the client has one, otherwise in a mailbox SMD hosts. Only people on the client's staff list are answered.
2. **Mail is pulled in and verified.** The seat pulls new mail from that mailbox, and the front door verifies every inbound message before any work starts.
3. **It works in the client's systems.** It reads what the task needs and writes results back, where granted. SMD does not warehouse copies of the client's files. Where a client's documents need work beyond the seat, SMD's agents do it in that client's own ops workspace, and work there is kept for up to 30 days. No person at SMD reads client files.
4. **Sending is held apart.** The agent cannot send on its own keys. A separate broker holds the send credentials, checks every recipient, and is the sole writer of the audit record, with one entry for each action.
5. **The portal directs, it does not hold data.** Administrators see what the Operator is set up to do and its activity, look up the audit record for any reference, move limit settings up to the agreed ceiling, and pause it.
6. **The seat reports out.** A health signal every minute, the newest audit entry's fingerprint, and a notice for each staff request.
7. **Changes come in one way.** Configuration is written by SMD as a reviewed change and published to the seat. The agent cannot edit its own limits.
8. **SMD's access is fenced too.** When a client's documents need work beyond the seat, SMD's agents do it from a workspace sealed to that one seat. No person at SMD reads client files.
9. **The model provider processes the task and does not train on it.** Task content is sent for processing under terms that bar training on it. The standard agreement states 30 days' notice before the provider changes. Some seats also use a speech-to-text service and a web-search service.

## The life of a request

Explainer view: #lifecycle

View 3, one request start to finish. Every request passes the same checkpoints, top to bottom. Each one names what happens when it fails. This is condensed from 15 control points enforced in code. Figure 3 shows nine steps in three groups: steps 1 to 3 happen once, when the request arrives; steps 4 to 7 repeat for every tool call the agent makes, until the work is done; steps 8 and 9 happen when the work is done. Each step names the part of the seat that does it.

1. **Arrive** (front door). Email, system event or schedule. The signature is verified. If it fails: a bad or stale signature is rejected before any work.
2. **Sender check** (front door). Matched to the client's staff list. If it fails: an unknown sender's content is fenced, with no reply, and no send, commitment or delete on that turn.
3. **Skill and sender's authority** (agent). The matching skill loads. The agent is told who is asking. If it fails: a skill not switched on for this seat is not there to load, and a skill reserved for administrators is declined to anyone else.
4. **Tool lookup** (agent). Every tool has one fixed kind of action. If it fails: a tool not in the registry never runs.
5. **Limit check** (agent). The kind of action is checked against the client's written limit. If it fails: when no limit is written for it, it is refused. That is the default.
6. **Content checks** (agent). Numbers and dates must trace to something it read. If it fails: a reference number or date that cannot be traced to something read blocks the action, and money or contract content is routed to a draft for a person.
7. **Act** (agent and broker). The tool runs in the client's system, and one audit entry is written for that tool call. Long or costly work is not done here: it becomes a queued job, as in the queued jobs view. If it fails: a check that cannot run blocks the tool call.
8. **Deliver** (broker). A reply in the thread, a draft for a person, or ask first. If it fails: a recipient not on the thread or the staff list is blocked, and SMD is told.
9. **Report** (front door). A notice goes to SMD for each staff request. If it fails: no answer in 30 minutes, and SMD is paged.

Notes on the steps:

- **Nothing unsigned gets in (step 1).** Email arrives through the Operator's mailbox; system events and schedules arrive by their own doors. A bad or stale signature is rejected before any work.
- **Outside content is fenced (step 2).** Text from an unlisted sender is quarantined so that instructions hidden in it cannot drive actions. A turn that read untrusted content cannot send, commit or delete.
- **Skills guide, the checks enforce (step 3).** A skill tells the agent how to do a kind of work. It is not the control. The control is at every tool call, in steps 4 to 6.
- **Every tool is classified once (step 4).** Each tool has one fixed kind of action. A tool that is not in the registry never runs.
- **Unwritten means refused (step 5).** The kind of action is checked against the limit the client wrote for it. Where no limit is written, the call is refused.
- **Content is checked before it leaves (step 6).** A reference number or date that cannot be traced to something the agent read blocks the action. Money or contract content going outside the client's staff becomes a draft for a person.
- **The action and its audit entry go together (step 7).** The tool runs in the client's system and the broker writes one audit entry for that tool call. A check that cannot run blocks the call.
- **Approvals cannot be faked by the agent (step 8).** A yes counts only from an administrator, or from the named person whose approval is needed, through their own channel.
- **Silence is a failure (step 9).** If a listed person's request has no sent reply, no queued job and no held reply after 30 minutes, SMD is paged. The hourly triage catches it again.

## The permission model: the client writes the limits

Explainer view: #limits

View 4, the permission model. Unwritten means no. Every action the Operator can take belongs to a kind of action, and the client sets a limit for each kind. A limit is one of four values: acts on its own, asks first, drafts for a person, or no limit written. Figure 4 shows one rule set, six attempts and six outcomes. Each attempt passes through the client's limits, which are written by the client, stored read-only on the seat, and checked at every tool call:

1. **Files an internal note,** for example a note in the system of record (kind of action: internal write). The limit is "acts on its own". Outcome: runs.
2. **Emails the client's staff,** for example a morning digest to the team (kind of action: send to staff). The limit is "acts on its own". Outcome: sent.
3. **Emails the client's customers,** for example a follow-up on a missing document (kind of action: send to the client's customers). The limit is "drafts for a person". Outcome: a draft, which a person sends.
4. **Accepts a vendor's terms,** for example agreeing to a delivery date (kind of action: commitment). The limit is "asks first". Outcome: held for an administrator.
5. **Emails an outside party,** someone on none of the client's lists (kind of action: send to outside parties). No limit is written. Outcome: refused.
6. **Moves money,** for example paying an invoice (kind of action: money movement). Banned for every client. Outcome: refused, whatever is written.

Unwritten means refused. The Operator cannot change its own limits.

### Every kind of action, by limit

| Kind of action                      | Acts on its own                                                                                   | Asks first                                                 | Drafts for a person            | No limit written |
| ----------------------------------- | ------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- | ------------------------------ | ---------------- |
| Sends (four kinds, by who receives) | Sends                                                                                             | Held until approved in the conversation                    | Becomes a draft a person sends | Refused          |
| Internal writes                     | Runs                                                                                              | Not offered                                                | Runs                           | Refused          |
| Commitments                         | Still needs a yes in the moment                                                                   | Held for an administrator                                  | Blocked                        | Refused          |
| Destructive actions                 | Still needs a yes in the moment                                                                   | One narrow case only                                       | Refused                        | Refused          |
| Sending as a staff member           | Refused                                                                                           | Proposed to the named person, who approves the exact draft | Refused                        | Refused          |
| Code execution                      | Runs                                                                                              | Refused                                                    | Refused                        | Refused          |
| Reading                             | Always allowed inside the systems the client connected. It is the only thing that needs no grant. |                                                            |                                |                  |

- **Who writes the rule:** the client decides, and SMD records it as a reviewed change. **Where it is enforced:** on the client's seat, at every tool call. **The default:** refused.
- **Sends are split by recipient:** the client's own staff, the client's customers, its vendors, and everyone else. Each has its own limit.
- **Moving money is banned outright,** whatever the client writes. Sending in a staff member's name happens only when that person approves the exact draft.
- **The portal shows each limit as one of three plain settings per routine:** "Surfaces it," "Prepares it for you," "Handles it." Each routine has a starting setting and a ceiling taken from the agreement.
- **Ceilings hold.** An administrator can raise a routine up to its ceiling. Nobody, including SMD and the Operator, can go above it without changing the agreement.
- **Said accurately:** "Surfaces it" rests on the skill's instructions and is checked in the audit record. The hard enforcement is at each tool call, as in the life of a request.

## Queued jobs: big jobs run as queued work

Explainer view: #jobs

View 5, queued jobs. Big jobs run as queued work, with proof at both ends. Work that takes many steps or real cost does not run in a chat reply. It runs as a queued job with fixed stages written in code. Figure 5 shows eight stages, left to right, each with what happens when it fails; stages 2 and 7 are proof steps a job cannot skip:

1. **Submit.** The requester and the monthly allowance are checked. If refused: the requester is told why.
2. **Proof gate (proof step).** For the first job of its kind only: a proof run and SMD's yes. Until then it waits, and SMD is paged.
3. **Estimate.** The cost is projected before any paid step. Over the cost limit: it holds, and SMD is paged.
4. **Run.** The cost limit is checked before every paid step. Over the cost limit: it holds, and SMD is paged.
5. **Review.** The work is checked against its sources by an independent review. A must-fix finding: it holds, nothing is filed, and SMD is paged.
6. **File.** The result is written to the client's system of record.
7. **Read back (proof step).** The delivered file is fetched and compared with what was built. A mismatch: the job fails, and SMD is paged.
8. **Reply.** The reply goes to the original requester, and no one else. Not sent in 20 minutes: SMD is paged.

A queued job is in one of five main states: queued, running, delivered, held (stopped at a failed stage, before anything is filed) or failed (ended without a proven delivery). Two other final states exist: stopped by SMD, and observed.

- **Proof before first use.** A kind of job never run for this client does not run on a real request. It waits while SMD runs it on that client's own data, stopped before anything is written, and a person at SMD accepts the result.
- **Cost has a limit.** A per-job cost limit and a monthly cost limit are checked before every paid step. Over the limit, the job holds and SMD is paged.
- **Delivery is proven, not assumed.** A queued job counts as delivered only when the delivered file has been fetched from the client's system of record and matches what was built.
- **If the reply does not go out,** SMD is paged after 20 minutes.

## Isolation: one seat per client, and what is shared

Explainer view: #isolation

View 6, isolation. Is one client's data mixed with another's? No. Each client's seat is its own machine with its own storage volume. What SMD shares across clients is the service around the seats. Figure 6 shows:

1. **Three client seats side by side** (client A, client B, client C), each marked as theirs alone.
2. **Inside each seat, five parts of its own:** the machine (one per client), the storage volume (one per client), memory (this client's only), the audit record (this client's only), and connection credentials (which stay on this machine and are never in a shared store). A seat will not start if pointed at another client's storage.
3. **Beside each seat, its own ops workspace,** one per client, with logged access.
4. **Between each seat and SMD, three arrows:** health every minute and an audit copy daily go up; releases and configuration come down.
5. **Below the seats, one SMD Services layer,** shared across clients: the Client portal, SMD's console, the pager and hourly triage, releases and configuration, and the audit archive, each kept apart by client.
6. **The caption:** no two clients share a machine, a storage volume, connection credentials, memory or an audit record.

- **Never shared:** the machine, its storage volume, connection credentials, memory, the audit record, and the Operator's mailbox (in the client's own mail tenant where the client has one, otherwise a mailbox SMD hosts for that seat alone).
- **Shared, and kept apart by client:** the Client portal, SMD's console, the pager and hourly triage, releases and configuration, and the daily audit archive.
- **What one client's Operator learns never benefits another client.** That is a term of the service agreement and a property of the layout.

## Data and audit record: where the data lives

Explainer view: #trust

View 7, data and audit record: where the data lives, and what the audit record is. These are the statements our service agreement makes. They are worded to be exactly true. Figure 7 maps where each record lives and who reads it:

1. **The agent reads, the broker writes.** The audit record lives on the client's seat, one entry per action, chained. The agent can read it and cannot rewrite it. Only the broker writes it.
2. **The client can see it.** An administrator looks up the audit record for any reference in the Client portal and downloads it.
3. **A fingerprint leaves every minute.** The fingerprint of the newest audit entry is sent to SMD's console and kept there.
4. **The whole record is checked daily.** A daily verifier pulls each seat's audit record once a day.
5. **The check has an outside anchor.** The last fingerprint held in SMD's console must be present in the audit record that was pulled. A break pages SMD.
6. **A dated copy is kept.** The verifier writes each day's copy to the daily audit archive, which refuses overwrites.
7. **SMD reads a notice per request.** Request notices land in SMD's operations mailbox, one per staff request, and SMD operations is paged when a reply is missing. Each notice carries who asked, the subject, the request's own words and the first reply.

Tamper-evident, not tamper-proof: entries are chained, not signed. Every read SMD's console makes of a seat is itself logged.

| Kind of data                        | Where it lives                                                                                                                                                   |
| ----------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| The client's business files         | In the client's own systems. Read through granted connections while a task runs. SMD does not warehouse copies.                                                  |
| The Operator's email                | In the client's own mail tenant where the client has one, under the client's retention and controls; otherwise a mailbox SMD hosts for that seat.                |
| Memory                              | On the client's dedicated machine. Owned by the client. Exported on exit.                                                                                        |
| Audit record                        | On the client's dedicated machine, looked up by reference in the portal. A fingerprint of its newest entry leaves the machine every minute and is checked daily. |
| Connection credentials              | On the client's machine only. The ones that can send are held by the broker, where the agent cannot read them.                                                   |
| Settings, staff names, billing      | SMD's console and its listed sub-processors.                                                                                                                     |
| Task content sent for AI processing | Processed by the model provider for the task and not used to train models.                                                                                       |

### The audit record

- One audit entry per action: one for each tool call.
- Each entry is chained to the one before it, so a change shows.
- The agent can read it and cannot rewrite it.
- It holds what was done, by which skill, under which limit, and the outcome. It does not hold the text of the client's documents.
- Say "tamper-evident." Do not say "tamper-proof."

### What SMD can see

- Health, settings, and the audit record of actions.
- A notice per staff request: who asked, the subject, the request's own words and the first reply.
- Client work itself is handled only by AI agents: the client's Operator, and SMD's agents in a workspace sealed to that client, where work is kept for up to 30 days. No person at SMD reads client files.

### What never happens

- SMD does not warehouse copies of the client's files. Work SMD's agents do on a client's documents stays in that client's own ops workspace for up to 30 days.
- Send credentials never reach the agent.
- Client content is not used to train models.

### Standard terms

These are what the standard agreement states. Each client's signed agreement governs.

- Security incident notice within 24 hours.
- On exit: the audit record and memory are exported within 14 days and everything is destroyed within 30, confirmed in writing.
- 30 days' notice before a sub-processor changes.
- No uptime percentage and no service credits. SMD holds no SOC 2 or ISO 27001 of its own, and says so.

## What managed means

Explainer view: #service

The service around the Operator runs in five phases. Each ends on proof, not on a calendar date.

| Phase   | What happens                                                                                                                                       | What must be true to move on                                                     |
| ------- | -------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| Prepare | SMD names the client's principal and system administrators and lists what is needed.                                                               | The client record.                                                               |
| Explore | A working session on how the business runs. The client sets the limit for each kind of action.                                                     | A written configuration the client has signed off.                               |
| Realize | SMD builds missing skills and connections, sets up the client's machine, and rehearses on practice data it owns, including hostile cases.          | A rehearsal with no limit broken.                                                |
| Deploy  | The client grants access to each real system. Areas of work come on one at a time.                                                                 | The client confirms each area on its own work.                                   |
| Run     | Health checked every minute, work checked every hour, releases rolled out seat by seat with a rollback point. Staff state how they like work done. | Billing starts only after SMD says testing is complete and the client starts it. |

- **Usage is included.** Work whose cost grows with volume carries a stated allowance. Beyond it, SMD quotes first and starts only on written acceptance.
- **New kinds of work have a path.** SMD may do the first instances as a trial, measures them, then proposes terms in writing.
- **The client can always stop it:** pause in the portal, or cut its access in the client's own systems.
- **AI output can be wrong.** The client's people review it before relying on it. The product is built so that review is the normal path.

Pricing is set per engagement and is not covered here.

## What runs today, and what SMD sets up

Explainer view: #edges

A technical buyer will test these. Describe each as a capability of the Operator. When the buyer asks what is live today, say which of these a client is running.

### Running for a client today

- One isolated machine per client.
- Limits enforced at every tool call; unwritten means refused.
- Drafts for a person, and sends to listed staff, the client's customers and vendors.
- "Ask first" approvals on commitments, and sending as a staff member on that person's approval.
- Chained audit record with daily verification.
- Email through a mailbox in the client's own tenant.
- Queued jobs with proof gate, review and read-back.
- Pause in the portal.
- Pager, hourly work check, request notices.
- One industry delivered in depth.

### Built, set up per client

- "Ask first" approvals on sends.
- The level dial in the portal.
- Client-run connections, team access and a compliance view.
- Chat and voice-note channels.
- The connector that lets staff reach it from their AI assistant.
- Self-serve purchase.
- More than one persona per seat.

### Configured per engagement

SMD sets these up, and builds what they need, when a client's engagement calls for them.

- Texting, and team chat platforms such as Slack and Teams.
- Mailboxes in other mail systems.
- Industry-specific skills for the other industry packs.
- A memory view for the client.
- Memory inferred from behavior (today it remembers what it is told and shown).
- Signed audit entries.

Industry packs are starting configurations for onboarding. The running product is the same for every business: nothing in it branches on industry.

## The same model, in one industry

Explainer view: #example

The first industry delivered in depth is a law practice. The system of record is the firm's practice-management system. Staff email requests, and the queued jobs produce medical chronologies and first drafts for an attorney to judge, every chronology entry cited to the page it came from. Communications to a court or to opposing counsel are always prepared for a person to send, and nothing touching deadlines or money runs on its own. Swap the system of record, the skills and the limits, and the seven views above describe any other business.
