---
title: Operating Cadence
section: operations
order: 2
summary: How a single working session runs - start, end, the heartbeat, and when to stop and escalate
sources:
  - label: CLAUDE.md - Session Start
    href: https://github.com/venturecrane/ss-console/blob/main/CLAUDE.md
  - label: Team Workflow (global) - Escalation Triggers
    href: crane_doc('global', 'team-workflow.md')
  - label: Enterprise Rules (CLAUDE.md) - Escalation triggers
    href: https://github.com/venturecrane/ss-console/blob/main/CLAUDE.md
---

## What this page is

This is how a working session actually runs, start to finish. The operating model
(who decides versus who executes) lives in `/admin/playbook/operating-model`; this
page is the rhythm an agent follows inside a session. It is the same shape every
session so that work is resumable: any session can be picked up by the next.

## Start of Session

Every session begins the same way, per `CLAUDE.md` (Session Start):

1. Call the `crane_preflight` MCP tool (no arguments). This validates the
   environment is ready.
2. Call the `crane_sos` MCP tool with `venture: "ss"`. This creates a session,
   loads the venture documentation and directives, and establishes the handoff
   context from the previous session.

The result is a session that starts warm: it knows the venture, the active
directives, and where the last session left off. The `/sos` skill wraps this.

## End of Session

A session ends by writing a handoff, not by going quiet. The `crane_handoff` MCP
tool (the `/eos` skill) records an end-of-session summary with a status
(`in_progress`, `blocked`, or `done`) and a written summary of what was completed
and what is still open. The next `/sos` reads it back.

Handoffs are also a mid-session tool, not only an end-of-session one. The team
workflow (global `team-workflow.md`, "Conversation Checkpointing") directs an
agent to checkpoint proactively on long sessions - exceeding roughly 30 minutes of
active work, or at a milestone like a PR created or a feature completed - by
calling `crane_handoff(status: "in_progress")`. This creates a resumable snapshot
so a context compaction or a crash does not lose the thread.

## The heartbeat

A long-running session can be kept alive by the `/heartbeat` skill, which keeps
the session from going idle while a long operation completes. This is a session
liveness tool, distinct from the fleet-health monitoring described in
`/admin/playbook/operating-model` (that monitors the running Operator Machines;
the heartbeat keeps a working session warm).

## The schedule and planned-events cadence

Recurring venture activities and planned events are tracked through the cadence
engine (`crane_schedule`). It produces a briefing of what is due, records
completions, and links planned events to the calendar. This is how recurring
obligations (audits, reviews, follow-ups) surface at the right time rather than
relying on memory. The `/calendar-sync` skill keeps the planned events and the
calendar aligned.

> TODO(why): The specific cadence items SMD runs (which audits, on what
> interval) are driven by `crane_schedule` data, not by a file I could read in
> this repo. I verified the mechanism (the `crane_schedule` tool and its
> list/complete/planned-event actions) but not the populated schedule.

## The memory system

The fleet remembers across sessions on two layers:

- **Per-session handoffs** (`crane_handoff`) - the resumable snapshot of where a
  session left off, described above. This is short-horizon continuity.
- **Durable memory** - facts that persist across all sessions. Agents write
  single-fact memory files (who the Captain is, standing feedback on how to work,
  ongoing project state, external references) and index them so they load each
  session. This is long-horizon continuity: a lesson learned once does not have
  to be relearned.

The two are complementary. The handoff carries this week's work; durable memory
carries the venture's accumulated judgment. Captain dismissal of a memory is a
physical delete (the same discipline as Operator memory dismissal, see
`/admin/playbook/knowledge-memory`).

Durable memory is tiered, because the index loads into every session and cannot
grow without bound. `MEMORY.md` is the always-on layer and carries two
thresholds: a hard read limit of 24,985 bytes (24.4 KiB), past which the index is
truncated, and a recommended target of 17,510 bytes (17.1 KiB) that the harness
asks you to compact under. Headroom is measured against the target, because that
is the number that should change a decision. Depth lives one hop away, in sub-indexes grouped
by theme, and retired memories move to an `attic/` directory rather than being
deleted. A memory reached only through a sub-index still loads: the tier model
works because following the pointer is the rule (see Law 2 in
`docs/doctrine/agent-operating-doctrine.md`).

That structure has one failure mode, and it is silent. Compacting the index by
deleting rows instead of moving them to a sub-index leaves the files on disk and
reachable from nothing, which means they never load again and no session notices.
On 2026-09-09 that had happened to 60 files, some of them standing Captain
directives. `.claude/bin/memory-audit` is the detector: it walks reachability
from `MEMORY.md` through every sub-index, and reports memories that no index
reaches, index rows that point at nothing, and the remaining headroom against
the target. It exits non-zero on a lost or dangling memory, so the loss is a
state a session can see rather than something discovered weeks later, and it
warns without failing when the index is over the target, because a soft cue that
breaks the build gets switched off.

## Escalation triggers - mandatory stop points

The cadence has hard stops. An agent does not churn on a blocker; it escalates.
These triggers are enforcement, not suggestion - they come from a post-mortem
where an agent churned for 10-plus hours on symptoms instead of escalating
(global `team-workflow.md`, Escalation Triggers; restated in `CLAUDE.md`
Enterprise Rules):

| Condition | Action |
| --- | --- |
| Credential not found in 2 minutes | Stop. File an issue. Ask the Captain. Do not guess or hunt. |
| Same error 3 times (different approaches) | Stop. Escalate: "I've tried X, Y, Z - all failed. Need a different approach." |
| Network/TLS errors from a container | Stop. "Can't test from this environment." Do not try 12 curl variations. |
| Wrong repo or venture twice | Stop the session. Investigate why the context is wrong; do not just fix and continue. |
| Blocked more than 30 minutes on one problem | Time box expired. Escalate or pivot. Activity is not progress. |

The escalation format is fixed so the Captain can act on it fast:

```
BLOCKED: [brief description]
TRIED:   [what was attempted]
NEED:    [what would unblock - a decision, a credential, a different environment]
```

The anti-patterns these triggers kill: "let me try one more variation" after
three failures, testing a partial flow and declaring success, and lots of tool
calls with little progress. The rule under all of them is that activity is not
the same as outcome.
