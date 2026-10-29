---
title: Operating Cadence
section: engineering
order: 11
owner: engineering
review_every: 90
last_reviewed: "2026-10-29"
summary: How a single working session runs - start, end, the heartbeat, and when to stop and escalate
sources:
  - label: CLAUDE.md - Session Start
    href: https://github.com/venturecrane/ss-console/blob/main/CLAUDE.md
  - label: Enterprise Rules (CLAUDE.md) - Escalation triggers
    href: https://github.com/venturecrane/ss-console/blob/main/CLAUDE.md
  - label: Memory audit (reachability, thresholds, wiring receipt)
    href: .claude/hooks/memory-audit.mjs
  - label: Trap-recurrence counter
    href: .claude/hooks/trap-recurrence.mjs
  - label: Situational memory (triggers, matching, fire log)
    href: .claude/hooks/lib/situational-memory.mjs
  - label: Reply contract (Stop hook)
    href: .claude/hooks/reply-contract.mjs
  - label: Gmail draft gate
    href: .claude/hooks/situational-gate.mjs
  - label: Prompt-stage recall
    href: .claude/hooks/situational-recall.mjs
  - label: Memory-recurrence counter
    href: .claude/hooks/memory-recurrence.mjs
  - label: Memory trigger test (history sweep)
    href: .claude/hooks/memory-trigger-test.mjs
---

This page covers how an AI agent's working session runs: how it starts, how it hands
off, when it must stop and escalate, and how agents remember across sessions. An
administrator needs it when an agent's report sends you here, or when an agent stops
with a `BLOCKED:` message and you want to know why it stopped.

## What this page is

This page describes an AI agent's working session, not the administrator's
operating rhythm; the administrator's week is in `/admin/playbook/administrators-week`.

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
directives, and where the last session left off. The `/sos` skill wraps this;
since 2026-09-17 `/sos` and `/eos` are SMD's own skills
(`.claude/skills/sos/SKILL.md`, `.claude/skills/eos/SKILL.md`, pinned by
`tests/smd-session-skills.test.ts`), so enterprise updates no longer reach them.

## Inside the session: four mechanical checks

`CLAUDE.md` (Session Mechanics) adds four checks, built after the 2026-08-01 review
of fourteen sessions. Each one produces or checks an artifact rather than asking an
agent to remember:

1. **Reply contract.** A Stop hook (`.claude/hooks/reply-contract.mjs`) bounces a
   reply over 25 prose lines that does not lead with `MISSION:` / `STATUS:` /
   `DID:` / `NEXT:`, where STATUS is one of `OK`, `BLOCKED`, `DECISION-NEEDED`
   or `DEFECT-FOUND`. At any length it also bounces a reply that weighs a cost
   against a client's fee, or that matches a saved memory's trigger (see "Lessons
   delivered when they apply" below). Every rule that fires lands in one bounce,
   and each reply is bounced at most once.
2. **Mission and board.** Once the session's focus is set, the agent records it
   as a winning line, the outcome the business or a client experiences and the
   task that gets there, with
   `.claude/bin/mission set "<outcome>, via <task>" --focus <issue or branch>`.
   The per-turn primer re-injects it with the working stance attached (own your
   calls; reversible work moves after one stated risk; client, paid or seat work
   proves the whole path first; side work off the line goes to another session)
   and shows every live peer session's mission, so two sessions do not build the
   same thing.
3. **Premise gate.** A hook (`.claude/hooks/plan-premise-gate.mjs`) refuses to
   let a plan leave plan mode without a `## Premises` table whose every row
   carries evidence.
4. **Fresh doctrine.** The per-turn primer (`.claude/hooks/reflex-primer.sh`)
   serves the operating laws from `origin/main`, labeled with commit and age, so
   a law merged to main reaches every running session on its next turn.

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

A long-running session can be kept alive by the `/heartbeat` skill (an enterprise
skill synced from crane-console into `.agents/skills/heartbeat/`), which updates
the session's last-heartbeat time in the crane context service so a session busy
on a long task is not timed out as idle (its stated timeout is 45 minutes).
`/sos`, `/update` and `/eos` refresh the heartbeat themselves. This is a session
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

The schedule is data in the crane context service, not a file in this repo, so
the list below is a probe, not a contract (`crane_schedule` with action `items`,
probed 2026-10-21). One item is SMD's own: **Code Review (ss)**, on a roughly
30-day interval (its last completion and next due date are 30 days apart). SMD
no longer keeps an executive summary: the Captain retired it on 2026-10-22, its
note was archived, and its cadence item is removed by crane-console migration
0056, so neither appears in the start-of-session briefing. The rest of the list is enterprise-wide
items shared with the other ventures (context refresh, skill audit, docs audit,
memory audit, the weekly verify-ledger audit, fleet and secrets checks). Re-run
the probe before relying on the list; it changes without a commit here.

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
carries the venture's accumulated judgment. (The Operator's own memory, which
serves clients, is a different system; see `/admin/playbook/knowledge-memory`.)

Durable memory is tiered, because the index loads into every session and cannot
grow without bound. `MEMORY.md` is the always-on layer and carries two
thresholds: a hard read limit of 24,985 bytes (24.4 KiB), past which the index is
truncated, and a recommended target of 17,510 bytes (17.1 KiB) that the harness
asks you to compact under. Headroom is measured against the target, because that
is the number that should change a decision. The design puts depth one hop away,
in sub-indexes grouped by theme, and moves retired memories to an `attic/`
directory rather than deleting them; the audit below follows both. A memory reached only through a sub-index still loads: the tier model
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

The detector runs at every session start and, by design, says nothing when the
store is healthy - which left it with the same hole it was built to close: a
clean run and a hook that never fired produce identical output, namely none. So
each hook run now stamps `.memory-audit-receipt.json` beside the store, and
`memory-audit --wiring` answers whether the hook is firing. The receipt alone
would be circular (a hook that stops running also stops updating its own
receipt), so the check compares it against the session transcripts the harness
writes whether or not any hook runs: a session that started after the last
receipt is a session the hook did not serve. A manual run deliberately does not
stamp - proof a human can mint by running the tool shows the tool works, not
that the wiring fires.

Reachability is only half the question. The other half is whether the always-on
tier changes what an agent does, and `MEMORY.md` pre-registers that experiment
itself: on or after 2026-09-23, check whether any of the traps it lists still
fired. `.claude/bin/trap-recurrence` is the instrument. The review is not on the
venture schedule: `crane_schedule` lists only Code Review for SS (see the
schedule section above), so the review happens only when a session
runs the instrument.

What it counts is the distinction the decision turns on. An ENCOUNTER is the
trap condition appearing in tool output; a STUMBLE is an encounter where the
documented remedy did not follow. A pull request left BEHIND by a sibling merge
is not a failure - one an agent answered by re-running `gh pr merge` is. Stumbles
above zero justify a delivery hook; zero with encounters above zero means the
tier is doing its job. The counter reports its own blind spots on every run, and
its rules were narrowed by running them over thirty-two sessions of history:
the first draft scored a thousand false stumbles on one rule and made another
incapable of ever passing, either of which would have handed the review a
predetermined answer. It reads every transcript directory of this repo, the
canonical one and one per worktree; until 2026-10-29 it read only the canonical
one, which held 57 of the 112 sessions in its window.

### Lessons delivered when they apply

Reachable is still not seen. On 2026-10-29 an agent set a cost "against the
firm's monthly fee" for the third time, although the Captain's correction was
saved in a memory: it sat one hop from `MEMORY.md`, and nothing in that session
followed the pointer. So a memory may carry its own trigger, written in its
frontmatter as single-quoted regexes: `trigger_reply` (tested against each
sentence of the agent's reply), `trigger_prompt` (tested against the Captain's
prompt), `trigger_draft` (tested only against a Gmail draft, for rules about
what a client reads, such as "we", never "I checked", that a chat reply to the
Captain legitimately breaks), `trigger_since` (when counting starts), and
`trigger_example` / `trigger_counter` (sentences that must and must not match).
Three surfaces read
them, quote the memory's own text, and say nothing otherwise:

- the reply contract (Stop hook) bounces a matching reply once;
- `.claude/hooks/situational-gate.mjs` (before a Gmail draft, reply, send or
  forward) refuses a matching draft once, because client sentences travel there
  and the Stop hook never sees a draft's body;
- `.claude/hooks/situational-recall.mjs`, run by the per-turn primer, shows a
  matching memory before the agent starts, once per session.

A Stop hook runs after the reply is on screen, so the reply stage corrects the
agent in the same turn rather than hiding the sentence; the prompt and draft
stages are the ones that prevent it. Triggers live in the private memory store,
not in this public repo, so no CI test sees the live ones. The memory audit is
their test: at every session start it runs each memory's examples and counters
against its live triggers and names any trigger it refuses (one that matches
everything, nests a quantifier, or is not single-quoted), and it counts the
feedback memories that record a repeat correction and still carry no trigger.

A trigger that matches ordinary work stops agents constantly, and an agent
stopped constantly learns to route around the stop, which kills the lesson. So
a trigger is tested against history before it goes live:
`.claude/bin/memory-trigger-test <memory file>` runs it over every past reply,
Captain prompt and Gmail draft and prints the hits to read by eye. Each hit that
is not the mistake the memory names is a false positive: narrow the trigger and
add that sentence as a `trigger_counter`. The first fee trigger matched 53 past
replies; after narrowing it matched 14, nine of them real.

`.claude/bin/memory-recurrence` is the measure, and `/eos` runs it. Per
triggered memory it counts STUMBLES (replies whose words match), BOUNCED (caught
by a hook, matched by sentence hash against the fire log), ESCAPED (the rest:
the Captain read them), and the headline, Captain re-corrections: prompts that
raise the memory's subject in correction language. The last does not depend on
the reply trigger, so a paraphrase the trigger misses still shows up as the
Captain repeating himself. A lesson is working when re-corrections stay at zero.

## Escalation triggers - mandatory stop points

The cadence has hard stops. An agent does not churn on a blocker; it escalates.
These triggers are enforcement, not suggestion - they come from a post-mortem
where an agent churned for 10-plus hours on symptoms instead of escalating
(global `team-workflow.md`, Escalation Triggers, fetched with
`crane_doc('global', 'team-workflow.md')`; restated in `CLAUDE.md` Enterprise
Rules):

| Condition | Action |
| --- | --- |
| Credential not found in 2 minutes | Stop. Ask the Captain. Do not guess or hunt. |
| Same error 3 times (different approaches) | Stop. Escalate: "I've tried X, Y, Z - all failed. Need a different approach." |
| Network/TLS errors from a container | Stop. "Can't test from this environment." Do not try 12 curl variations. |
| Wrong repo or venture twice | Stop the session. Investigate why the context is wrong; do not just fix and continue. |
| Blocked more than 30 minutes on one problem | Time box expired. Escalate or pivot. Activity is not progress. |

Escalating is not filing. Per `CLAUDE.md` (Scope discipline), work found
mid-task gets done in the session when it is within reach; an issue is filed
only when the work is blocked on something that does not yet exist or needs a
decision only the Captain can make.

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
