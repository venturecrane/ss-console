# Anatomy of a Seat

This document describes what a seat is made of: the machine, the software on it, the control layer, where it keeps its data, where its configuration comes from, and what SMD authors for each client.

Maturity labels used here: **In production** (built and used on a paying client's seat), **Built, not yet in client use** (merged code that no paying client's seat uses yet), **Specified, not built** (designed, not built).

## The machine

Each client seat is its own dedicated machine. There is no shared runtime between clients. In production.

- **Size.** The seat configuration sets the machine's region and size. Seats today use one of three memory tiers: 2 GB, 4 GB or 8 GB, with one, two or four shared processor cores. A seat that runs heavy queued jobs can also set a separate memory limit for each kind of job.
- **Storage.** Each seat has one 10 GB storage volume of its own.
- **Region.** Set per seat in its configuration.
- **Who changes it.** SMD, by a reviewed change followed by a rebuild of the seat.

## The software image

Every seat runs the same software image. The image holds three things.

- **An open-source agent runtime.** It runs the model loop, skills, memory files, the tool registry, schedules and the channel connections. It runs unmodified. The whole fleet is pinned to one exact version, and the build fails if the version does not match.
- **SMD's control layer.** This is where SMD's rules live. It is built as plug-ins to the runtime and must not modify the runtime's core. The version a seat reports is the SMD release it was built from.
- **Connectors.** Connectors to client systems are built into the shared image, each in its own isolated environment. A connector does nothing until a seat's configuration binds it. Connectors are not installed separately for each client.

SMD changes the image by a reviewed change, then builds and releases it to seats in order.

## Separate system users

Three separate system users run on each seat: one for the agent, one for the capability broker, and one for queued jobs. Because they are separate, the agent cannot read the broker's send credentials and cannot rewrite the audit record. In production.

## Start-up and self-checks

- **Start-up.** A privileged start-up step fetches and validates the seat's configuration, sets file ownership, gives the send credentials to the broker, runs boot checks, and then starts the agent as the less privileged user.
- **Boot checks.** The seat may only bind its own storage area. If its configuration points anywhere else, the seat refuses to start. The authored rules on disk cannot be written by the agent. In production.
- **Liveness supervisor.** If the agent's process hangs, a supervisor stops it so the machine is replaced cleanly. Restarts are counted and bounded.
- **Self-test.** A list of checks runs at boot and every hour. The result is reported in the seat's heartbeat.

## The control layer by capability group

The control layer has 29 plug-ins. They fall into five groups.

| Group                 | What it does                                                                                                                                                                                                                                                                                                                                                                               |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Governance            | Checks every tool call against the client's written limits and the outbound content gates. Tells the agent who the sender is and what standing they have. Fences off untrusted content so it cannot pose as instructions. Writes audit entries. Proves that the runtime's checkpoints actually fire.                                                                                       |
| Channels and delivery | Routes inbound messages and records where they came from. Sends replies only to the recorded sender. Sends calendar invitations through the audited send path. Handles attachments without the agent holding the bytes or the credentials. Transcribes voice notes from listed senders. Answers through connected assistant apps. Provides brokered access to a client's Google Workspace. |
| Memory and authoring  | Keeps per-person working preferences. Keeps a ledger of corrections from the client's administrators. Sets voice and output shape through conversation. Keeps records inside authored folders.                                                                                                                                                                                             |
| Work execution        | Runs queued jobs and lets the agent submit and check them. Applies a final check on drafted documents. Keeps an escalation ledger. Reads the web through a sealed reader. Attributes model usage to each person. Tracks failures per connector.                                                                                                                                            |
| Multi-person seats    | Supports seats shared by several people with different roles. These do nothing on a seat that does not author more than one person.                                                                                                                                                                                                                                                        |

## Broker, gate, ledger, jobs and schedules

| Component         | What it does                                                                                                                                                                                                                                                                                                                                       | Maturity      |
| ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------- |
| Capability broker | A separate process under its own user. It holds the send credentials, and grants single-use permission for each call. It is the only writer to the audit record. It decides who a reply goes to and enforces the participant fence (see [request-lifecycle.md](request-lifecycle.md)).                                                             | In production |
| Inbound gate      | The seat's only public web address. It verifies that each incoming message is signed by the sending service, then passes it to the agent on the machine. It also hosts the sign-in callback for connected accounts, the read endpoint SMD's console uses, the endpoints for changing a routine's limit setting and for pausing, and the heartbeat. | In production |
| Audit record      | One audit entry per tool call, each chained to the one before by a cryptographic hash. The broker is the only writer. There is no way to update or delete an entry. The agent can read it only.                                                                                                                                                    | In production |
| Jobs runner       | A background service with one queue per kind of queued job, running one job at a time per kind. Each job runs as its own process under the jobs user, with a memory limit and only the settings it needs. See [jobs.md](jobs.md).                                                                                                                  | In production |
| Schedules         | Routines that run on a schedule are authored in the seat configuration and loaded into the runtime's scheduler at every boot. A check that runs first can decide not to wake the agent at all when there is nothing to do.                                                                                                                         | In production |

## Where a seat keeps its data

All of a seat's working data lives on the seat's own storage volume. Each seat has its own local database files. There is no shared per-client database in the cloud: one was designed early on and never built.

| Store             | What it holds                                        | Notes                                                                                                |
| ----------------- | ---------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Profile home      | The worker's identity, configuration and skills      | Rebuilt from authored configuration at boot                                                          |
| Flat-file memory  | Notes the runtime keeps                              | Switched off on seats shared by several people                                                       |
| Agent-state store | Learned preferences                                  | Written by the agent through its tools                                                               |
| Limit settings    | A client's changes to a routine's limit setting      | Always held at or below the authored ceiling, checked when written and when read. Survives a rebuild |
| Stop state        | Pause and safety-stop state                          |                                                                                                      |
| Request index     | One entry per request                                | Message text is cleared after 7 days; rows are deleted after 30 days                                 |
| Job state         | Queued jobs and their working folders                | A job's working folder is deleted 72 hours after the job ends                                        |
| Job caches        | Extracted text and per-job state reused between runs | Survive a rebuild by design. Removed on request for a given record                                   |

## Where configuration comes from

| Source                      | What it is                                                                                                                                                                                                                  | Who writes it                                                                                                                                                | Maturity      |
| --------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------- |
| The seat configuration file | The seat's authored configuration                                                                                                                                                                                           | SMD only, by a reviewed change. No path in the Client portal or SMD's console writes it. It is published to the seat automatically when the change is merged | In production |
| Live configuration applier  | A privileged process on the seat that checks for a new configuration every 15 seconds. It applies a change live only if every changed setting is on its allow-list. Otherwise the change waits for the next boot or release | In production                                                                                                                                                |
| Signed seat specification   | A signed, compiled copy of the configuration that the seat verifies and reports on                                                                                                                                          | Built, not yet in client use (it is checked and reported, but not yet what the seat applies)                                                                 |
| Console copy                | A read copy of each seat's configuration and its history, kept for SMD's console                                                                                                                                            | In production                                                                                                                                                |
| Runtime overrides           | A routine's limit setting (never above its ceiling), pause, and voice and format specifications                                                                                                                             | Client administrators, where the agreement allows. In production                                                                                             |
| Job inputs                  | Per-job limits, model choice, house style and examples                                                                                                                                                                      | SMD, in a private repository, published to the seat                                                                                                          | In production |
| Skills release              | A versioned bundle of skills, swapped in without a restart                                                                                                                                                                  | SMD                                                                                                                                                          | In production |

Settings that can change live include: what each routine may do on its own; whether a skill is on, how it starts and its settings; the roster of people and administrators; escalation routing; inbound triggers; output-class settings. Settings that never change live include: the industry label, the model, memory settings, the seat's identity, its region, its connectors, the worker's identity, and the people on a shared seat. If one setting in a change cannot be applied live, the whole change waits.

## Secrets and monitoring

- **Infrastructure secrets.** SMD keeps them in a secrets vault and places them on the seat when it is provisioned. Each secret is classified as SMD's or the client's. An unclassified secret stops provisioning.
- **Send credentials.** These are available only at start-up. They are handed to the broker in files only the broker can read, then removed before any agent process starts. The agent holds read-only connection credentials.
- **Credentials never sit in a configuration file.** The client can provide a connection credential through a write-only field.
- **Heartbeat.** Every 60 seconds the seat reports 44 health fields to SMD's console. It also checks in with an outside service that pages SMD if the seat goes silent. A seat that has not reported for 5 minutes shows red.
- **Pager.** A background service reviews heartbeats every 2 minutes and pages SMD, by email to its operations address, when something is wrong.

## What SMD authors for each client

SMD authors each seat. The client describes the work; SMD writes it down as configuration, reviews it, and publishes it. Every seat starts from a preset. Two validators check the configuration: one before the change is merged and one on the seat. Both must agree.

## The seat configuration file

The seat configuration file is the main authored artifact. One per seat. Its main parts are:

| Part                       | What it sets                                                                                                                                                                                                                                                           |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Identity                   | The seat's identifier and the client's name                                                                                                                                                                                                                            |
| Seat kind                  | Whether it is a client seat, a proving seat, a sandbox, an internal seat or a pre-production seat, and which product it is                                                                                                                                             |
| Industry label             | Which pack it started from. Nothing at runtime depends on it                                                                                                                                                                                                           |
| Machine and models         | Region, machine size, the model, and the model used for escalation                                                                                                                                                                                                     |
| Users                      | People with access to the Client portal, with the role principal, staff or compliance                                                                                                                                                                                  |
| Worker identity            | The worker's name, tone, signature, whom it may send as, what each kind of action may do (its limits and the ceiling on them), its skills and how each starts (by request, on a schedule, or on an event), and its schedules. Every seat today has one worker identity |
| Connectors                 | Which kind of capability goes to which client system, and who holds the credential (the client or SMD)                                                                                                                                                                 |
| Google Workspace access    | Brokered access mode, permissions and managed mailboxes, where used                                                                                                                                                                                                    |
| Scope                      | Who the worker answers (the roster), who its administrators are, listed outside contacts (the client's customers, vendors, staff), and who it may send as                                                                                                              |
| Escalation                 | Who is paged for urgent problems and failures                                                                                                                                                                                                                          |
| Send policy                | Rate limits on replies and release of held replies                                                                                                                                                                                                                     |
| Safety                     | SMD's daily cost limit and daily limits on how often it can be woken and how much it can search                                                                                                                                                                        |
| Authority                  | Which control areas the client may operate in the Client portal. The client can be given configuration, limit settings, connectors, pause, memory, people access, compliance and observability. Provisioning and cost are never handed over                            |
| Connected assistant access | Whether the client can reach the worker through a connected assistant app, and for how long access lasts (1 to 90 days)                                                                                                                                                |
| Output classes             | For each kind of output, whether a voice or format specification is expected                                                                                                                                                                                           |
| Working preferences        | Authored per-person preferences                                                                                                                                                                                                                                        |
| Inbound triggers           | Which events from client systems start which skill, with exclusions and cooldowns                                                                                                                                                                                      |
| Other parts                | Records folders, staff mailbox reads, who may receive files, start-up steps, routine names, observe-or-enforce switches, business hours, digests, pause, health reporting, compliance view opt-in, memory isolation and retention, and multi-person settings           |

Parts that belong to a connector are refused unless the seat binds that connector.

## The routine grid

The routine grid lists each routine the client's agreement names, in the agreement's own words and order. In production. Each row holds:

- the routine's name, the agreement section, and the agreement's own words for where it starts and how far it may go
- the skills that carry it out
- a starting setting and a ceiling, each exactly one of three limit settings. A start above the ceiling is refused. There is no default.
- how each setting is actually enforced

The three limit settings, in the words the client sees:

| Setting           | Client-facing words   | What it means in code                                                                                 |
| ----------------- | --------------------- | ----------------------------------------------------------------------------------------------------- |
| Flag only         | "Surfaces it"         | No authored permission to send. This is a rule the skill follows and audit checks, not a gate in code |
| Prepare and route | "Prepares it for you" | Sends become drafts for a person to review                                                            |
| Auto-handle       | "Handles it"          | Sends may go out on their own, after every gate passes                                                |

## Other authored artifacts and how each reaches the seat

| Artifact                                                                | Who authors it                                                                       | How it reaches the seat                                                                                                                               | Maturity                     |
| ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------- |
| Seat configuration, routine grid, seat record, list of proven job kinds | SMD, by reviewed change                                                              | Published when merged. Applied live within about 15 seconds if the changed settings are on the allow-list; otherwise at the next boot or release      | In production                |
| Job inputs (limits, models, house style, examples)                      | SMD, in a private repository                                                         | Published when merged; picked up live or at the next boot                                                                                             | In production                |
| Voice and format specifications per kind of output                      | A client administrator, by instructing the Operator in conversation                  | Installed on the seat by a privileged process, read-only to the agent                                                                                 | In production                |
| Per-person preferences                                                  | Any listed person, for their own work                                                | Installed on the seat; no gate reads them                                                                                                             | In production                |
| Accepted document library (the client's blank house templates)          | A client administrator accepts each one. A file that contains client text is refused | Installed on the seat                                                                                                                                 | Built, not yet in client use |
| Skills                                                                  | SMD                                                                                  | One shared library; a seat selects skills in its configuration. There is no per-client skill folder. Delivered by a skills release, without a restart | In production                |
| A routine's limit setting                                               | The client's principal, where the agreement hands over limit settings                | From the Client portal to the seat's gate; takes effect on the next tool call; never above the grid's ceiling                                         | Built, not yet in client use |
| Pause and resume                                                        | The client's principal, where the agreement hands over pause                         | From the Client portal to the seat; recorded only once the seat confirms it                                                                           | In production                |
| Connection credentials and other secrets                                | SMD at provisioning, or the client through a write-only field                        | Kept in the hosting platform's secret store; never in a configuration file                                                                            | In production                |

A client's change to a routine's limit setting or a pause is a runtime override. It does not rewrite the seat configuration file. Voice is an authored specification per kind of output; the Operator does not learn voice from samples of past mail.
