# The Managed Service

This document explains what SMD does to set up, run, change and retire a client's Operator, and who does what.

Labels used here, as defined in [INDEX.md](INDEX.md): running for a client today; built, set up per client; configured per engagement. Every capability described is one SMD can set up for a client.

## Onboarding phases

Onboarding has five phases. Each phase ends on proof, not on a calendar date. SMD does not promise a fixed length for any phase.

| Phase   | What happens                                                                                                                                                                                                                                   | Who                                                      | What closes it                                                 |
| ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- | -------------------------------------------------------------- |
| Prepare | Handoff, identify the principal and the system administrators, prepare the list of what is needed                                                                                                                                              | An SMD specialist                                        | The client record                                              |
| Explore | A workshop on how the client works today; setting the limit for each kind of action (a kind of action with no limit written is refused); authoring the Operator's role; a register of connections; assembling and validating the configuration | The client decides, SMD records, a build agent assembles | A validated configuration and a process model the client signs |
| Realize | Build any missing connections and skills; set up the isolated seat; set the cost limits for queued jobs; rehearse on a practice copy SMD owns, including hostile test cases                                                                    | A build agent; an SMD principal approves the first start | A rehearsal report with no safety-line violations              |
| Deploy  | Authorize the Operator into each real system with the client's IT, check each one with a live read, then turn areas on one at a time                                                                                                           | The client administrator with SMD                        | The client's confirmation on their own work turns each area on |
| Run     | The client corrects the Operator directly. Limit settings widen only where the client opens them and the check for that setting passes                                                                                                         | Client, Operator and SMD                                 | The audit record                                               |

Billing starts only when SMD states in writing that testing is complete and the client's principal starts the subscription in the Client portal. Nobody at SMD can start it.

## Releases

- A seat is updated through four separately shipped parts: the seat image (built once for all seats), the skills version (applied without a restart), the jobs bundle (sent only to seats that run jobs), and the configuration.
- Changes build automatically once merged, and a controller rolls them out in rings. There is no fixed release schedule.
- Ring order: a release twin of the paying client's seat first, then the paying client's seat, then SMD's own seats.
- Each seat's release follows the same sequence: check for a hold, preflight, wait if a job is running (never release mid-job), take a storage volume snapshot as a rollback point, release, then verify with the seat's own self-test. A real failure rolls the seat back and halts the rollout.
- Every change passes seven required checks in a merge queue. Changes to the running seat also need a recorded verification.

## Monitoring

| Function                 | How it works                                                                                                                                                                                                                                                                                                          | Today                      |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------- |
| Heartbeat                | Each seat reports every 60 seconds. A seat silent for 300 seconds turns red, and SMD is paged by email to its operations address.                                                                                                                                                                                     | Running for a client today |
| No-answer check          | A request that has had no answer for 30 minutes pages SMD.                                                                                                                                                                                                                                                            | Running for a client today |
| Hourly work-health check | Reads each seat's job and request records, SMD's console and mailbox sign-in health. It flags a job that is stuck, held, failed, or delivered only after a person stepped in. A read-only agent investigates and emails only what needs a person. It covers the six kinds of queued job that serve the law-firm pack. | Running for a client today |
| Nightly checks           | Audit chain verification; control probes that plant a violation to prove each control still refuses; checks for jobs left in an unfinished state; scheduled-task watchdog; configuration reconcile; a check for any send without an audit entry; and the obligations reconcile.                                       | Running for a client today |
| Cost watching            | Model usage is collected nightly per seat; anomalies are flagged; SMD's console shows per-seat usage and what drives it. The client does not see cost.                                                                                                                                                                | Running for a client today |

## Incidents and backups

- Incidents use a severity ladder. SMD does not publish an uptime percentage and does not offer service credits.
- At the highest severity, the seat is paused and the routines involved are stepped down until the fix is merged and the incident has become a rehearsal case.
- The standard agreement states that the client is notified within 24 hours when its data or access is affected.
- A storage volume snapshot is taken before every release, and the audit record is archived daily. Some recovery procedures, including restoring SMD's console database, are stated as not yet written.

## Change requests

A client can ask for a change three ways:

- The "Request a change" row in the Client portal. It is emailed to SMD and tracked until acknowledged, resolved or declined.
- An email to the Operator, which sends SMD a request notice.
- An operations request by email, carrying a tag that lapses after seven days.

Who decides:

- An SMD administrator approves fixes to agreed behaviour and adds kinds of queued job that have been proven.
- An SMD principal decides anything that lets a routine act on its own for the first time, changes scope or terms, or adds a new commitment.

## Obligations SMD tracks

SMD keeps one register of everything it owes each client. Most entries are created automatically each night from open work, paging state and change requests. A promise made in a letter is captured with an exact quote that must match the letter. An entry closes as kept only with a recorded check. The register is internal; clients do not have a view of it.

## Offboarding

Decommissioning is one procedure, run first as a dry run and then for real. It removes the machine, storage, workspace, stores, mailbox, monitoring and console records; builds a signed evidence packet; refuses to run while the register shows anything still owed; and re-checks every layer at the end. It is built, set up per client: it has run on one of SMD's own seats, not yet for a client.

## Who does what

| Activity                                                 | Client principal | Client staff | SMD administrator   | SMD principal                  | The Operator       | Automation                          |
| -------------------------------------------------------- | ---------------- | ------------ | ------------------- | ------------------------------ | ------------------ | ----------------------------------- |
| Set how far each routine goes, within the agreed ceiling | Decides          |              |                     | Signs any raise of the ceiling | Enforces           | Records                             |
| Authorize connections                                    | Does it          |              | Guides              |                                |                    | Checks with a live read             |
| Approve mail sent in a staff member's name               |                  | That person  |                     |                                | Sends on approval  | Enforced by the sending service     |
| Start, pay, cancel                                       | Only they can    |              |                     | Sets terms                     |                    | Billing                             |
| Accept a new kind of queued job                          | Asks             | Asks         | Accepts after proof |                                | Holds the job      | An agent runs the proof             |
| Clear a seat that stopped itself                         |                  |              | Non-cost stops      | Cost stops                     | Stops itself       |                                     |
| Detect problems                                          | Reports          | Reports      | Answers pages       |                                | Reports shortfalls | Pager, hourly check, nightly checks |
| Fix, release, rotate credentials                         |                  |              | Directs             |                                |                    | Build agents, by reviewed change    |
