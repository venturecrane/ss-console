# Jobs

This document explains how the Operator handles work that is too large or too costly for a chat reply: it runs it as a queued job. It covers how a job differs from a reply, each stage from request to delivery, the states a job can be in, and what the client is told when a job stops.

Labels used here, as defined in [INDEX.md](INDEX.md): running for a client today; built, set up per client; configured per engagement. Queued jobs, with their proof gate, review, read-back and reply watch, are running for a client today.

## Job versus chat turn

A chat turn is the agent answering a message in the conversation. A queued job is a piece of work the Operator hands off to run on its own.

|                   | Chat turn                                                                | Queued job                                                                                                              |
| ----------------- | ------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------- |
| Time              | Must answer within a normal reply's time                                 | Runs unattended. Each kind of queued job has its own time limit, measured in hours                                      |
| Who does the work | The agent, in the conversation                                           | A separate process on the seat, under its own system user, with a memory limit and only the settings it needs           |
| Rule              | Large volumes of work and multi-step work are not allowed in a chat turn | The work follows fixed stages written in code. The stages call the model provider through one controlled, metered route |

The runner handles one queue per kind of queued job and runs one job at a time for each kind.

## Kinds of job

There are 7 kinds of queued job.

- One is general in shape: a staff request for named records, answered with files, with an optional SMD review hold.
- Six serve the law-firm industry pack: a medical chronology, a demand, document drafting, a litigation status report, watching a negotiation, and reviewing a case file.

A further kind of work runs through the same broker without a queue: a note that SMD approves and that is sent as part of a digest.

Any other kind of long or costly work a client needs becomes a new kind of queued job, configured per engagement on the same stages. A separate, more general engine for long-running agent work is also built, set up per client.

## From request to delivery

1. **Submit.** Following its skill, the agent submits the job. The requester, the original message and the request's own words are taken from the opening email, not supplied by the agent. The broker checks the request, the allowance for the billing period, that the requester is entitled to ask for this kind of work, and that the same work is not already running.
2. **Acknowledge.** The requester gets a short note that the request was received and that a reply will come in the same thread. The note gives no timing. If the broker refuses the job, the requester gets the broker's reason instead.
3. **Proof gate.** A kind of queued job that has never been proven on this seat does not run. It stays in the queue with a note, and SMD is paged. A proof record that cannot be read counts as no proof.
4. **Proof run.** To prove a kind of job, SMD runs the real pipeline on the client's real data and stops before the one stage that writes to the client's system. During the run, the connector refuses every action that is not a read. Passing the run is necessary but not enough: a person reviews what it produced before the kind of job is accepted on that seat. A proof run uses real model calls.
5. **Estimate.** Before the first stage that costs money, the job estimates its cost from the size of the material it has to read. If the estimate is over the cost limit, the job is held and SMD is paged.
6. **Run.** The stages run in a fixed order. A saved state file skips finished stages if the job restarts, so no stage is paid for or filed twice. Before every paid model call, the job checks its per-job cost limit, the monthly cost limit for that kind of job, and whether SMD has stopped it. Over a cost limit, the job holds and SMD is paged.
7. **Review.** Code checks the output against its sources. Where judgement is needed, a second model reviews it independently and looks for faults. If the review finds a problem from a fixed list of must-fix problems, the job is held, nothing is filed, and SMD is paged. It is released only by a fresh run after a fix, or by SMD through a privileged, audited step.
8. **File.** The job's write stage saves the result to the client's system of record. Each kind of write is proven separately.
9. **Read back.** Each filed file is found in the client's system, downloaded, and compared byte for byte, by cryptographic fingerprint, with what was built. A mismatch fails the job and SMD is paged.
10. **Claim check.** A job can only record that it delivered if it carries proof: the read-back result, the files or rows written, the review status and the review's cost. Without it the job is recorded as failed.
11. **Reply.** The job wakes the agent once to report completion (with up to 5 attempts). The broker, not the agent, decides who receives the reply: the person who asked. Some kinds of job send fixed wording written in code. If the reply has not appeared in the sent folder 20 minutes later, SMD is paged.

## Job states

Most kinds of job move through these states: submitted or queued, running, held, delivered, failed. Delivered is final. Whether a held job can resume depends on the kind of job. The runner also has two other final states: stopped by SMD, and observed.

There is no tool the agent can use to stop, resume or accept a job. Those are SMD actions.

## Why a job stops

A job can stop for any of these reasons:

- its kind has not been proven on this seat
- the review found a must-fix problem
- it reached its per-job cost limit, the monthly cost limit for its kind, or the billing-period allowance
- the seat was paused (the job resumes by itself when the pause is lifted)
- it had no proof of delivery
- the read-back did not match
- the client's inputs for that kind of job could not be used
- infrastructure retries ran out (it retries up to 4 times in place)
- SMD stopped it

## What the client hears when a job stops

When a job stops, the requester hears nothing until the work is delivered. SMD is paged once and resolves the stop, and the reply goes out when the job delivers.

The one exception is a held demand: the requester is told it has not been drafted yet and that nothing is needed from them.

If a person writes again in the thread of a held job, SMD receives a request notice with their words, because the Operator cannot record a decision on a held job itself.

An hourly health check watches job outcomes and flags runs that failed, were held or stalled. It covers the six kinds of job that serve the law-firm pack.

## Rehearsals and proof runs

These are two different things.

- **Rehearsal.** Each kind of job has a rehearsal that replays saved inputs through the job's code checks and compares the result with a written answer key. It makes no model calls and writes nothing. It costs nothing to run.
- **Proof run.** As described above, this runs everything for real except the final write to the client's system, so it does use paid model calls.
