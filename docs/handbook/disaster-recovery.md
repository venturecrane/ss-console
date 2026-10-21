---
title: Disaster Recovery & Runbooks
section: operations
order: 8
summary: What breaks, how to recover, and when to stop and escalate. The known failure modes and the runbooks that exist.
sources:
  - label: Operator runbooks
    href: https://github.com/venturecrane/ss-console/tree/main/docs/runbooks/operator
  - label: CLAUDE.md (Enterprise Rules, Deployment)
    href: https://github.com/venturecrane/ss-console/blob/main/CLAUDE.md
---

This is the recovery map: the failure modes we have actually hit, how each was resolved,
and where the formal runbooks live. It names procedures, never secret values or working
exploit commands.

## Escalation triggers (stop and ask)

Before any recovery turns into thrashing, the enterprise rule is to **stop and escalate**
when any of these fire:

- A credential cannot be found within 2 minutes.
- The same error recurs 3 times.
- You are blocked for more than 30 minutes.

Escalations go to the Captain with a clear summary and the decision needed. When in doubt,
stop and escalate.

## Existing runbooks

The written runbooks live in `docs/runbooks/` in two subtrees: `operator/` (23 files plus
an `incidents/` folder of incident notes) and `hosted-agent/` (2 files). Each runbook's
first heading states its job; grouped by when you would reach for it:

| When | Runbook (`docs/runbooks/...`) | Covers |
|---|---|---|
| Standing up a client | `operator/implementation-playbook.md` | The end-to-end method for taking a client from signed to live |
| | `operator/first-boot.md` | Standing up a client's Operator Machine for the first time |
| | `operator/keyless-build-handoff.md` | Building a seat without client keys, injecting them at handoff |
| | `operator/ms-graph-azure-ad-setup.md` | Microsoft Graph app registration for a client's Microsoft tenant |
| | `operator/voice-distillation.md` | Distilling a firm's voice for its Operator |
| | `hosted-agent/concierge.md`, `hosted-agent/couples-setup.md` | Standing up a Hosted Agent seat, and the couples role |
| Changing what a seat does | `operator/routine-lifecycle.md` | Defining, changing, adding and retiring a routine |
| | `operator/enable-gate-checklist.md` | The checks before a routine is promoted to a higher level |
| | `operator/lane-proof.md` | A firm asked for a kind of job its seat has never done |
| | `operator/curator-supervised-consolidation.md` | Consolidating a seat's skill inventory safely |
| SMD acting through a seat | `operator/smd-note.md`, `operator/staged-send.md` | Having the Operator tell a colleague something; sending SMD's exact words and files from the seat |
| | `operator/ops-close-by-smd.md`, `operator/medchron-deliver-by-smd.md` | Closing a request SMD already answered; recording a chronology SMD filed by hand |
| Something is wrong | `operator/sticky-stop-clear.md` | Seeing, investigating and resuming a stopped seat |
| | `operator/msgraph-dead-letter.md` | Microsoft Graph dead letters: what pages, how to replay |
| | `operator/hold-ledger-remediation.md` | Releasing held work that never resumed |
| | `operator/agentmail-vendor-ask-deliverability.md` | Email deliverability controls the AgentMail vendor holds |
| Upgrades and rehearsal | `operator/hermes-v0.18-upgrade-plan.md`, `hermes-v0.20-upgrade-plan.md`, `hermes-v0.21-upgrade-plan.md` | The staged Hermes runtime upgrades, including the defaults diff and rollback rehearsal to repeat at every bump |
| | `operator/shadow-firm.md` | The standing adversarial rehearsal against a fake firm |
| | `operator/cost-telemetry-enable.md` | Turning on the cost telemetry pipeline |

Incident notes go in `docs/runbooks/operator/incidents/` (see
[Incident Response](/admin/playbook/incident-response)).

## Known failure modes and recovery

These are recurring incidents captured from real sessions. Treat them as the seed of a
fuller runbook set.

### Operator Machine crash-loop after a hand-poke

Running overlay code on a live Operator Machine as root (via `flyctl ssh console`) writes
root-owned `__pycache__/*.pyc` files that break the hermes-user bootstrap's `rm -rf`,
crash-looping the Machine. This caused a multi-hour customer outage.

**Recovery / prevention:** never root-SSH a live Machine to test or inspect. Read Operator
state through the runtime read seam (HTTPS), and test changes by deploying a new image and
reprovisioning, not by editing the box. See [Deployment & Release](/admin/playbook/deployment-release).

### Stale OAuth token on the Fly volume

A recurring 401 from a connector is usually a **stale token copy on the Fly volume**, not a
revoked credential. Verify the token in Infisical is valid and refreshable before asking
the Captain to recreate it - recreating a working token wastes a rotation and can mask the
real (volume) problem.

> TODO(why): The decrypt/verify and self-heal reseed procedure for connector tokens is
> documented in session memory but not yet in a `docs/runbooks/` file. It should be written
> up and linked here.

### Lost or stranded API key

Keys created Fly-only (written to a Machine env but never persisted to Infisical) have been
"lost" when the Machine was rebuilt. The fix and the rule: **vault every key in Infisical
(`/ss`) at creation**, so a Machine rebuild never loses it. See
[Secrets & Access](/admin/playbook/secrets-access).

### Bad deploy

The web app deploys as a single Cloudflare Worker; a bad release is rolled forward with a
corrected PR through the normal deploy pipeline. An Operator release goes out through
`fleet release` ring by ring, and a seat that fails its boot smoke test or heartbeat
read-back is rolled back automatically to its recorded image and config, and the rollout
halts (see [Deployment & Release](/admin/playbook/deployment-release)).

> TODO(why): There is still no written rollback runbook for the web Worker and no
> documented backup and restore procedure for the D1 database. These are real gaps for a
> successor and should be authored. Recovery for the web app today relies on roll-forward
> through the deploy pipeline.

## Authorization note

Operational actions on live seats (reprovisioning after a merged change, key rotation,
secret sets, Machine restarts) are agent work: the agent does them when they are right for
the venture, reads the result back, and reports what changed (Captain correction,
2026-09-11, replacing the earlier "wait for authorization" rule). Two limits hold: a
Captain "not now" on a specific action stands until lifted, and a question such as "can you
run that?" is a question, not a go.

## Related

- [Deployment & Release](/admin/playbook/deployment-release) - the deploy and reprovision paths
- [Secrets & Access](/admin/playbook/secrets-access) - credential handling and rotation
- [Operating Cadence](/admin/playbook/operating-cadence) - the escalation triggers in context
- [Operator Platform Architecture](/admin/playbook/operator-platform) - the per-customer Machine model
