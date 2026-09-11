---
title: Secrets & Access
section: operations
order: 5
summary: Where credentials live, how they rotate, and the rules for handling them. Pointers and procedures only, never values.
sources:
  - label: CLAUDE.md (Deployment, Secrets)
    href: https://github.com/venturecrane/ss-console/blob/main/CLAUDE.md
  - label: secrets module (crane_doc)
    href: https://github.com/venturecrane/ss-console/blob/main/CLAUDE.md
---

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
`runuser -- env ${ENVV} ...`, which puts the gateway's whole environment on the wrapper's
own argv. That is by design and it is how the probe gets the credentials it needs. It also
means the wrapper matches loose process patterns. On 2026-08-10 a probe ran
`pgrep -af establish_intake`, matched its own wrapper, and printed
`ANTHROPIC_API_KEY`, the Smokeball client id and secret, and more into a session transcript
(ss#2218, P1).

Match on a pattern that cannot match the wrapper, and print pids only. The script carries
the same warning at the line where the env is assembled.

## Where secrets live

| Store | Holds | Notes |
|---|---|---|
| **Infisical** (`/ss` path, `prod` env) | The canonical source for SMD secrets - Clerk, Anthropic, ElevenLabs, AgentMail, Google, billing, and the rest | Rotate here first, then push to the runtime store |
| **Cloudflare Workers secrets** | The web app's runtime secrets, bound to the `ss-web` Worker | On Workers, secrets persist across `wrangler deploy` runs (unlike the old Pages `[vars]` trap). Bulk-rotate from Infisical with `wrangler secret bulk` |
| **Fly.io Machine env / volume** | Per-customer Operator secrets and OAuth tokens (ADR 0010) | Per-customer isolation. Tokens stored on the Fly volume; a stale volume copy is a known failure mode (see [Disaster Recovery & Runbooks](/admin/playbook/disaster-recovery)) |
| **`.dev.vars`** (gitignored) | Local dev values, pulled from Infisical | Never committed |

The build-time public keys (anything `PUBLIC_*`, the `*_BASE_URL` vars) are configuration,
not secrets, and are set in the build/deploy environment - see
[Deployment & Release](/admin/playbook/deployment-release).

## Rotation procedure (shape, not commands)

1. Update the value in Infisical (`/ss`, the right env) - via `crane_secret_set` so it never
   hits the transcript.
2. Verify presence with `crane_secret_check`, and verify the value actually works against the
   downstream service before relying on it (a present key can still be stale or revoked).
3. Push to the runtime store: Workers secrets for the web app (the Infisical export to
   `wrangler secret bulk` pattern in `CLAUDE.md`), or reprovision the Operator Machine for
   Fly-side secrets.
4. Confirm the running service picked up the new value (fresh process, live check), not just
   that the store was written.

> TODO(why): There is no single written secrets-rotation runbook in `docs/` yet; this
> procedure is synthesized from CLAUDE.md (Deployment) and the crane secret tooling. If a
> formal runbook is desired, it should live in `docs/runbooks/` and be linked here.

## Access boundaries

- **Contact identities.** Operational alerts go to `team@smd.services`; the Captain is
  `scott@smd.services`; the Operator's allow-list-gated inbound email channel is
  `crane@smd.services`. The `smdurgan@venturecrane.com` address that can appear in session
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
