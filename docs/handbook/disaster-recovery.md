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

The written runbooks today are Operator-focused and live in `docs/runbooks/operator/`:

| Runbook | Covers |
|---|---|
| `first-boot.md` | Standing up a new Operator Machine for the first time |
| `curator-supervised-consolidation.md` | Safely consolidating an Operator's skill inventory |
| `ms-graph-azure-ad-setup.md` | Microsoft Graph / Azure AD OAuth setup for document storage |
| `hermes-v0.18-upgrade-plan.md` | The first deliberate Hermes fleet promotion (v0.14.0 to v0.18.0, July 2026): hook-surface diff, staged rollout, bless, release-watch |
| `hermes-v0.20-upgrade-plan.md` | The second promotion (v0.18.0 to v0.20.4, August 2026): the staged procedure to repeat at every pin bump, including the defaults-diff step and the rollback rehearsal |

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
corrected PR through the normal deploy pipeline. The Operator rolls back by pinning the
prior overlay reference and reprovisioning.

> TODO(why): There is no written rollback runbook (web or Operator) in `docs/runbooks/`
> yet, and no documented backup/restore procedure for the D1 database. These are real gaps
> for a zero-context successor and should be authored. Recovery today relies on the deploy
> pipeline (roll-forward) and overlay-ref pinning (Operator), described in
> [Deployment & Release](/admin/playbook/deployment-release).

## Authorization note

Reprovisioning a live Operator Machine is destructive to its current state and **requires
explicit Captain authorization in the moment** - it is never run on an agent's own
initiative.

## Related

- [Deployment & Release](/admin/playbook/deployment-release) - the deploy and reprovision paths
- [Secrets & Access](/admin/playbook/secrets-access) - credential handling and rotation
- [Operating Cadence](/admin/playbook/operating-cadence) - the escalation triggers in context
- [Operator Platform Architecture](/admin/playbook/operator-platform) - the per-customer Machine model
