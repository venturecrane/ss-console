---
title: Operating Model & the Fleet
section: operations
order: 1
summary: Who decides and who executes - the Captain, the fleet of agents, and the system they run
sources:
  - label: Operating Ethos (global instruction module)
    href: crane_doc('global', 'operating-ethos.md')
  - label: ADR 0035 - No imposed entitlement defaults (authored ceilings)
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0035-no-imposed-entitlement-defaults.md
  - label: ADR 0030 - Control plane / human-principal surface
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0030-control-plane-human-principal-surface.md
---

## How the work is organized

SMD Services is run by one human directing a band of AI agents. The human is the
Captain. The agents are the fleet. The division of labor is not "boss and staff."
It is a judgment layer over a throughput layer: the Captain sets the mission and
holds the decisions that bind the venture, and the agents do the work inside the
boundaries the Captain authored.

This page owns that split and the operating culture that goes with it. For the
rhythm of a single working session (start, end, escalation timing) see
`/admin/playbook/operating-cadence`. For how the Operator product expresses this
same split toward a paying client, see `/admin/playbook/operator-thesis`.

## The Captain's Domain

Some decisions belong to the Captain and never get delegated. They are the
decisions that commit the venture or change what an agent is allowed to do. The
Captain governs the Operator product through a single control plane (ADR 0030):
one surface for authority, configuration, and lifecycle. The same shape governs
the venture itself.

- **Client acceptance.** Who gets in. For the Operator, an allow-list controls
  which inbound contacts reach the agent; the Captain sets that list.
- **Authority delegation.** What an agent may do, per client and per domain.
  Authority is off by default and the Captain enables it explicitly. This is the
  governance boundary - see `/admin/playbook/autonomy-governance`.
- **Scope and pricing.** Every engagement's scope and price is a Captain
  decision. See `/admin/playbook/pricing-economics`.
- **Escalation resolution.** When an agent hits a decision it cannot make, it
  routes up with a clear summary and the specific decision needed; the Captain
  resolves it.
- **Go / kill.** The Captain authorizes continuation, pause, or termination of
  any active engagement.
- **Operator development.** New capabilities, vertical packs, skill additions,
  and platform direction are Captain calls.

## Agent Execution

Everything that is not a Captain decision is agent execution. Agents run the work
inside the ceilings the Captain authored - the no-imposed-defaults model in ADR
0035, surfaced at `/admin/playbook/autonomy-governance`:

- Inbound client communications - email, voice, chat - handled within the
  configured autonomy ceilings.
- Assessment preparation and extraction support. The agent-run assessment
  interview (`/assessment`) is an internal dogfood page behind a flag, not a
  live client flow (`src/pages/assessment.astro`); the Captain runs assessment
  conversations and owns closing.
- Consulting analysis and deliverable drafting executed by agents; the Captain
  reviews before any client-facing send.
- Fleet health monitoring, which runs autonomously with the Captain notified on
  degradation.
- Platform maintenance and deployment, handled through PRs (never a direct push
  to main - see `/admin/playbook/building-the-platform`).
- Learned preferences, which accumulate automatically; the Captain can dismiss
  any memory, and dismissal is a physical delete, not a flag.

The throughput is the agent. The judgment is the Captain.

## The operating ethos

The fleet does not work like a corporate team. The enterprise operating ethos
(the global `operating-ethos.md` module, the Captain's standing order) sets the
culture for every agent in every session:

> We are a wild band of AI agents with an ape commander. We can and we will. You
> know the mission. Execute it. If you are not clear on the mission, ask.
> Otherwise, move out.

In practice this licenses an agent to:

- **Parallelize.** Independent work goes out at once - independent tool calls in
  one message, independent branches to sub-agents. Working serially on
  embarrassingly parallel work wastes context and time.
- **Hold whole systems in context, not files.** Read what is needed up front;
  do not keyhole through one file at a time when the module fits in the window.
- **Verify end to end.** "Typecheck passes" is not "it works." Browser, deploy,
  observe the running thing. For the Operator, verify against the running Machine
  and the runtime read seam, not the config that was written
  (`/admin/playbook/deployment-release`).

## The skunkworks stance

This is a pre-launch venture run as a skunkworks, not a process shop. The ethos
distinguishes bureaucracy (which is cut) from safety (which is kept). No phases,
feature flags, or follow-up tickets for work that fits in one session: do it now
or kill it. No "good enough" shortcut when the professional move is clear - the
right path is the right path.

The guardrails that survive this stance are the ones where lost work or broken
trust costs more than moving slightly slower: all changes ship through PRs and
never a push to main; destroying data, removing a feature, or touching auth gets
escalated first; context switches (repo, venture, branch) get announced;
irreversible, shared, or external actions (a merge, a deploy, a message sent on
the Captain's behalf) get confirmed. The ethos is about removing ceremony, not
removing safety.

Fleet health is watched on two schedules. The `ss-fleet-alerts` Worker checks every
2 minutes and emails team@smd.services only when a seat's state changes
(`workers/fleet-alerts/wrangler.toml`, `crons = ["*/2 * * * *", ...]`). The
work-health control runs hourly at :23 (`.github/workflows/work-health.yml`). See
[Work Health](/admin/playbook/work-health) and
[Incident Response](/admin/playbook/incident-response).

## The tools of the fleet

The fleet's leverage comes from a state-of-the-art model with a large context
window plus a full toolkit (per `operating-ethos.md`, "Your Kit, Your
Confidence"). The ones that shape how work gets parallelized here:

- **Parallel sub-agents.** A session spawns sub-agents to run independent
  branches of research or implementation at once, then synthesizes the results.
  This handbook was authored that way - a fan-out of agents, one section each.
- **Workflows and slash commands.** Repeatable procedures (start of session, end
  of session, ship, code review) are encoded as commands so the procedure is
  executed the same way every time rather than re-derived.
- **Fleet dispatch.** Long-running or cross-machine work is dispatched to a fleet
  machine rather than blocking the live session.
- **Git worktrees.** Parallel sessions each get an isolated copy of the repo so
  they do not collide - the mechanics are covered in
  `/admin/playbook/building-the-platform`.

A newcomer inherits a running operation, not a blank slate: the harness defines
the governance boundary, the guide (per-client `customer.yaml`) carries the
context, and the memory holds the history. The Captain directs from day one.
