---
title: Secrets & Access
section: engineering
order: 9
owner: engineering
review_every: 90
last_reviewed: "2026-10-21"
summary: Where credentials live, how they rotate, and the rules for handling them. Pointers and procedures only, never values.
sources:
  - label: CLAUDE.md (Enterprise Rules, Contact Addresses)
    href: https://github.com/venturecrane/ss-console/blob/main/CLAUDE.md
  - label: wrangler.toml (Worker secrets list)
    href: https://github.com/venturecrane/ss-console/blob/main/wrangler.toml
  - label: operator/bin/seat-probe.sh
    href: https://github.com/venturecrane/ss-console/blob/main/operator/bin/seat-probe.sh
  - label: tests/seat-probe-env-leak.test.ts
    href: https://github.com/venturecrane/ss-console/blob/main/tests/seat-probe-env-leak.test.ts
  - label: operator/bin/rotate-machine-credential.sh
    href: https://github.com/venturecrane/ss-console/blob/main/operator/bin/rotate-machine-credential.sh
  - label: src/lib/portal/operator/entitlement-change.ts
    href: https://github.com/venturecrane/ss-console/blob/main/src/lib/portal/operator/entitlement-change.ts
  - label: ADR 0010 (per-customer OAuth token storage)
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0010-per-customer-oauth-token-storage.md
  - label: ADR 0042 (Operator credential custody)
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0042-operator-credential-custody.md
---

Where SMD's credentials are kept, how one is rotated, and the rules every agent follows
when handling them. An administrator needs it when an agent's report sends you here,
usually because a key was rotated, found stale, or exposed.

This page is a map and a set of rules. It deliberately contains **no secret values, no
working credential-recovery commands, and no vault contents**. A handbook that listed those
would be a recon document. To actually read or rotate a secret, follow the procedure here
against the real tools, signed in as the Captain.

## The cardinal rule

**Never echo a secret value.** Transcripts persist in `~/.claude/` and are sent to API
providers. Verify a secret's **value** works (not just that the key exists), but do it
through tooling that does not print the value into the conversation. The enterprise
provides two crane MCP tools for exactly this:

- `crane_secret_check` - confirms a key is present at an Infisical path without returning
  its value (key names only).
- `crane_secret_set` - writes a secret to Infisical by reading it server-side from the
  clipboard or a local file, so the value never enters the transcript.

Raw `infisical secrets set` from Bash is blocked for this reason; pasting a value into chat
is prohibited.

### Process-listing flags are exfiltration primitives on a seat

`pgrep -a`, `pgrep -af`, `ps e`, and `ps auxe` print a process's command line or
environment. On an Operator seat both carry live credentials, so these are never
debugging conveniences there.

`operator/bin/seat-probe.sh` reaches the seat by re-execing the probe as
`runuser -- env -i ${ENVV} ...`, which puts the gateway's whole environment on the wrapper's
own argv. That is by design and it is how the probe gets the credentials it needs. It also
means the wrapper matches loose process patterns. On 2026-08-10 a probe ran
`pgrep -af establish_intake`, matched its own wrapper, and printed
`ANTHROPIC_API_KEY`, the Smokeball client id and secret, and more into a session transcript
(ss#2218, P1).

Match on a pattern that cannot match the wrapper, and print pids only. The script carries
the same warning at the line where the env is assembled.

The probe's environment is an allowlist: `env -i` starts it empty, and it receives only the
keys the hermes-uid gateway process holds. The ssh session inherits the Machine's PID 1
environment, root-only secrets included, and naming those secrets one at a time failed
twice (the Graph send credential in #2879, the account-wide R2 keys in #2902, each found by
boot smoke on a live seat). The named list survives only as a second filter on the copy.

### `env` with no program prints the environment

`env NAME=VALUE ...` with no program after the assignments prints its whole environment,
which inside `seat-probe.sh` is the gateway's environ, every secret value included. On
2026-10-18 a probe passed one command string ending in a base64 payload (`...==`); the
wrapper quoted it onto the `env -i ${ENVV} ...` line as one escaped word containing `=`,
`env` read it as an assignment, and the gateway environment went to stdout and into the
agent transcript.

The probe command now never reaches `env` as a bare word. The wrapper runs
`env -i ${ENVV} ... /bin/sh -c "$CMD"`, so `env` always has a program. The command travels
base64-encoded, so quotes, `=`, pipes and redirection arrive intact, and both the caller
and the seat refuse an empty command before `env` runs. One argument is a shell string
(pipes and `>` work); several arguments are run as that argv. Any new wrapper that ends in
`env ... <caller-supplied words>` has the same flaw: put a fixed program after the
assignments. Test: `tests/seat-probe-env-leak.test.ts` runs the wrapper's real remote side
against a fake environ holding a canary secret.

## Where secrets live

| Store | Holds | Notes |
|---|---|---|
| **Infisical** (`/ss` path, `prod` env) | The canonical source for SMD secrets - Clerk, Anthropic, ElevenLabs, AgentMail, Google, billing, and the rest | Rotate here first, then push to the runtime store |
| **Cloudflare Workers secrets** | The web app's runtime secrets, bound to the `ss-web` Worker | On Workers, secrets persist across `wrangler deploy` runs (unlike the old Pages `[vars]` trap). The expected names are listed in the secrets comment block of `wrangler.toml`. Set one key at a time; never bulk-export the `/ss` path into `wrangler secret bulk` (see [Deployment & Release](/admin/playbook/deployment-release)) |
| **Fly.io Machine secrets / volume** | Per-customer Operator secrets (staged by `operator/bin/provision-customer.sh`, plus the per-seat `MACHINE_HEARTBEAT_KEY`) and OAuth tokens (ADR 0010) | Per-customer isolation. Tokens stored on the Fly volume; a stale volume copy is a known failure mode (see [Disaster Recovery & Runbooks](/admin/playbook/disaster-recovery)) |
| **`.dev.vars`** (gitignored) | Local dev values, pulled from Infisical | Never committed |

The build-time public keys (anything `PUBLIC_*`, the `*_BASE_URL` vars) are configuration,
not secrets, and are set in the build/deploy environment - see
[Deployment & Release](/admin/playbook/deployment-release).

## Rotation procedure (shape, not commands)

1. Update the value in Infisical (`/ss`, the right env) - via `crane_secret_set` so it never
   hits the transcript.
2. Verify presence with `crane_secret_check`, and verify the value actually works against the
   downstream service before relying on it (a present key can still be stale or revoked).
3. Push to the runtime store, one key at a time:
   - **Web app:** set the one changed key on the `ss-web` Worker with `wrangler secret put`,
     the value piped from Infisical rather than typed. Never bulk-export `/ss` into
     `wrangler secret bulk`; it re-sets every key under the path, including ones retired
     from the Worker.
   - **A seat's Machine credential:** `operator/bin/rotate-machine-credential.sh <slug>`
     mints a fresh per-seat key, records its hash in the console's `machine_credentials`
     table first (the previous key stays valid for 24 hours), then sets it as the seat's
     `MACHINE_HEARTBEAT_KEY` Fly secret. Setting it restarts the Machine at once and the
     script runs no busy gate, so the agent first checks the seat has no job running or
     held, then reads the heartbeat back. Running it is agent work (Captain, 2026-09-11);
     a Captain "not now" on that seat stands until lifted.
   - **Other Fly-side secrets:** reprovision the seat (`operator/bin/reprovision.sh <slug>`),
     which stages them and deploys behind the busy gate.
   - **A client's OAuth token:** the client re-consents in their own browser; SMD does not
     hold the secret (ADR 0010, ADR 0042).
4. Confirm the running service picked up the new value (fresh process, live check), not just
   that the store was written.

There is no separate rotation runbook in `docs/runbooks/`; this procedure and the scripts it
names are the procedure.

## Access boundaries

- **Contact identities.** Operational alerts go to `team@smd.services`; the Captain is
  `scott@smd.services`. Each Operator seat receives email at its own AgentMail inbox
  (`<slug>@agentmail.to` unless its `customer.yaml` authors `inbox_address`; see
  `operator/workspace_broker/agentmail_auth.py`). Its `scope.inbound_allow_from` roster
  decides which senders it treats as authorized; mail from anyone else is handled as
  untrusted content behind the inbound trust boundary (ADR 0027). The `smdurgan@venturecrane.com` address that can appear in session
  context belongs to a different venture and must never be used in SMD code, config, or
  content.
- **Admin vs portal.** Session cookies are per-host with no shared domain; an admin cookie
  cannot leak to the portal and vice versa (see [The Website](/admin/playbook/the-website)).
- **Operator isolation.** Each customer's Operator runs on its own Fly Machine with its own
  tokens (ADR 0007, ADR 0010); a credential for one customer never reaches another.
- **Never grant or change access controls casually.** Modifying auth flows, sharing
  permissions, or access controls is a Captain decision, not an agent one.
- **Client-reachable controls carry no git credential.** The entitlement dial
  (#2003 Q7) and the pause control both ride the console-proxy bearer
  (`OPERATOR_MCP_WEBHOOK_SECRET`-derived, per customer) to the Machine's gate;
  neither path holds a GitHub token. The Machine clamps every entitlement set
  to the authored `exposure_ceiling` itself, so even a compromised console
  credential cannot raise the Operator above the letter commitment. (The
  fine-grained `OPERATOR_CONFIG_PR_TOKEN` this section previously described
  belonged to the superseded PR-based delivery leg and was never minted.)

## Related

- [Deployment & Release](/admin/playbook/deployment-release) - how secrets reach the running services
- [Disaster Recovery & Runbooks](/admin/playbook/disaster-recovery) - recovering a lost or stale credential
- [Integrations & Tooling](/admin/playbook/integrations-tooling) - what each external service is and why it needs a key
