---
title: Deployment & Release
section: operations
order: 4
summary: The two deploy paths - the website to Cloudflare Workers, and the Operator to a per-customer Fly Machine
sources:
  - label: CI - deploy.yml
    href: https://github.com/venturecrane/ss-console/blob/main/.github/workflows/deploy.yml
  - label: wrangler.toml
    href: https://github.com/venturecrane/ss-console/blob/main/wrangler.toml
  - label: operator/bin/reprovision.sh
    href: https://github.com/venturecrane/ss-console/blob/main/operator/bin/reprovision.sh
  - label: operator/bin/overlay-ref-drift.py
    href: https://github.com/venturecrane/ss-console/blob/main/operator/bin/overlay-ref-drift.py
  - label: operator/rehearsal/run.py
    href: https://github.com/venturecrane/ss-console/blob/main/operator/rehearsal/run.py
  - label: docs/runbooks/operator/shadow-firm.md
    href: https://github.com/venturecrane/ss-console/blob/main/docs/runbooks/operator/shadow-firm.md
---

## Two deploy paths

This venture ships two different runtimes, and they deploy in completely
different ways. The **website** is an Astro app on a single Cloudflare Worker,
deployed automatically by CI on merge to main. The **Operator** is a per-customer
Fly.io Machine running the Hermes runtime, deployed by hand by rebuilding that
customer's Machine image. They share almost nothing operationally. This page owns
both. The dev workflow that precedes either deploy is in
`/admin/playbook/building-the-platform`.

## Path A: the website (Cloudflare Workers)

The website deploys via `wrangler deploy` from the `Deploy` workflow
(`.github/workflows/deploy.yml`), which runs on push to main and is gated on the
`DEPLOY_ENABLED` repo variable. The build produces two directories:

- `dist/client/` - static assets, bound to the Worker via the `[assets]` block in
  `wrangler.toml`.
- `dist/server/` - the Astro SSR entrypoint.

`run_worker_first = true` in the `[assets]` block sends every request through
Astro middleware first, so subdomain routing (`src/middleware.ts`) and session
middleware always run even for requests that would otherwise resolve straight to a
prerendered asset.

The deploy steps run in this order, and the order matters:

1. **Build** (`npm run build`).
2. **Wrangler deploy dry-run** - catches wrangler config and binding errors
   before they reach production.
3. **Run D1 migrations** (`d1 migrations apply ss-console-db --remote`).
   Migrations apply before the code that depends on them and must be
   backward-compatible with the currently deployed app.
4. **Deploy to Cloudflare Workers** (`wrangler deploy`) - the main `ss-web` Worker.
5. **Deploy the sub-workers** - cost-anomaly and cost-telemetry - each from its
   own directory. (Between the root deploy and the sub-worker deploys the
   pipeline removes `.wrangler/`, because the root deploy writes a deploy-config
   file that makes the sub-workers' `wrangler deploy` ambiguous.) These two
   Operator-cost workers are all that remain under `workers/`; the lead-gen
   pipelines were retired 2026-07-01 (PRs #1610/#1616).

**Secrets.** On Workers, secrets persist across `wrangler deploy` runs - unlike
the Pages era, there is no post-deploy sync step and no risk of a deploy wiping
dashboard-set secrets. Rotate them out of band from Infisical:

```bash
infisical export --env=prod --path=/ss --format=dotenv \
  | grep -vE '^(APP_|ADMIN_|PORTAL_|MEETING_|PUBLIC_)' \
  | npx wrangler secret bulk
```

The `grep -vE` exclusion list strips the plain-config `[vars]` (the `*_BASE_URL`
values and the like) so only real secrets are pushed.

**Env vars.** The three base-URL vars are plain `[vars]` in `wrangler.toml`:
`APP_BASE_URL` (`https://smd.services`, used by marketing and SignWell webhooks),
`ADMIN_BASE_URL` (`https://admin.smd.services`, the OAuth redirect URI and
outbound admin links - strict, no fallback), and `PORTAL_BASE_URL`
(`https://portal.smd.services`, portal links, falling back to `APP_BASE_URL`).

## Path B: the Operator (per-customer Fly Machine)

The Operator runs as a per-customer Fly.io Machine hosting the Hermes runtime;
our code is a plugin-only overlay in a separate repo,
`venturecrane/hermes-smd-overlay` (it must not modify Hermes core - see
`/admin/playbook/operator-platform`). A release is delivered by rebuilding the
customer's Machine image against a new overlay commit. The flow:

1. **Cut an overlay release.** Land the change in the overlay repo and get the
   commit it should pin to.
2. **Bump `OVERLAY_REF`.** The overlay commit is pinned as the `ARG OVERLAY_REF`
   in `operator/templates/Dockerfile`. This is the desired state for every
   customer Machine. The PR names its tracking issue and carries an acceptance
   criterion tagged `(runtime)`: a green shadow-firm run on the rig at this ref.
   Steps 3 and 4 are how that AC is met, and they happen after this PR merges.
3. **Reprovision the rig, before any client seat.** The rig is
   `pilot-smokeball`. Run `yes s | operator/bin/reprovision.sh pilot-smokeball`.
   This step exists here, in this position, because a rig runs a candidate
   overlay ref only once it has been reprovisioned onto it. The old procedure
   asked step 2 to cite a green run "on the candidate ref", which no rig could
   produce yet, and three bumps in a row (#2518, #2525, #2531) cited nothing.
4. **Drive the shadow firm against the rig, and cite the run id.** The shadow
   firm (`operator/rehearsal/`, runbook `docs/runbooks/operator/shadow-firm.md`)
   replays every incident class this venture has had - an unaudited direct-API
   send, cross-matter content, a fabricated matter number under failure, an
   instruction injected through inbound mail, a dead connector mid-task, a
   privileged instruction from an unauthored sender - against the rig seat, and
   scores each from audit rows and mailbox observations rather than from how the
   answers read. Run it as
   `infisical run --env=prod --path=/ss -- operator/rehearsal/run.py --seat pilot-smokeball --overlay-ref <candidate> --drive`.
   The runner reads the rig's running overlay ref off the live seam and refuses
   to drive unless it equals the candidate, so the id cannot be produced by a rig
   still on the previous release, and a ref it cannot read refuses too. Cite the
   run id, with the report's evidence table, on the tracking issue or as a
   comment on the merged bump PR: that citation is the `(runtime)` AC's proof.
   `.stitch/shadow-firm/` is gitignored (PR #2407: the provision-source guard
   refuses a dirty tree), so the report is NOT committed; the digest-shaped run
   id is recomputable from the pasted report body, which is what makes the
   citation auditable. A run id ending in `-notgreen` does not satisfy this, and
   neither does a run with skipped scenarios: a skipped scenario did not run, so
   it certifies nothing. A bump with no green id once the rig has been
   reprovisioned is drift, not a completed release. The suite never touches a
   client seat or a client-visible address; that is enforced in
   `operator/rehearsal/scope.py`, not by convention.
5. **Reprovision the customer Machines.** Run `operator/bin/reprovision.sh <slug>`
   from the repo root. That wrapper is exactly
   `infisical run --env=prod --path=/ss --silent -- operator/bin/provision-customer.sh <slug>`;
   it injects the R2 credentials the provisioner needs (historically not stored
   anywhere, so every agent re-derived them and lost time - they now live in
   Infisical `/ss` prod). Provisioning rebuilds the image at the pinned overlay
   ref, redeploys the Machine, and runs the boot smoke test. Use
   `yes s | operator/bin/reprovision.sh <slug>` to skip the secret prompts
   non-interactively (Machine secrets persist across deploy, so there is nothing
   to re-enter). git is the single source of truth for `customer.yaml`;
   provisioning projects it to R2 unconditionally.
6. **Pass the verify gate.** Confirm the runtime came up against the live read
   seam (a correct key returns 200; a wrong key or wrong slug returns 401) and
   the boot smoke test passed - not just that the config validated.
7. **Flip secrets** on the website side if the release changes the per-customer
   seam keys.

**An `OVERLAY_REF` bump only reaches a Machine when that Machine is
reprovisioned.** Nothing reprovisions automatically. `overlay-ref-drift.py` reads
each Machine's running overlay commit from the live read seam and compares it to
the ref pinned in the Dockerfile, so drift between desired and deployed is a
surfaced fact rather than something to remember. It is also how you answer "which
seats did this bump actually reach" after step 5. Run it under Infisical (it needs
the seam master key) and treat a non-zero exit as drift to resolve. The shadow
firm's release gate reads a seat's running ref through that same code path, so
the runner and the drift report cannot disagree about what a seat is running.

## Never root-SSH a live Operator Machine

`flyctl ssh console` runs as root. Running any overlay code there writes
root-owned `__pycache__/*.pyc` files that the hermes-user bootstrap then cannot
remove, which breaks the next boot and can crash-loop the Machine (this caused a
multi-hour customer outage). Read a live Machine's state through the runtime read
seam over HTTPS, and test a change by deploying it - never by hand-poking the live
box. A handler also cannot prove its own non-execution by inspection; verify
against the running Machine.

> TODO(why): The exact contents of the reprovision verify gate (which read-seam
> kinds are checked, and the precise pass criteria for the boot smoke test) are
> operational and recorded in session memory rather than in a single doc I read
> in this repo. I verified the deploy flow from `reprovision.sh`,
> `provision-customer.sh`, and `overlay-ref-drift.py`, and the no-root-SSH failure
> mode from session memory, but did not read a canonical verify-gate checklist
> file to cite.

## Build commands reference

The npm scripts that bear on a deploy (full list in `package.json`):

- `npm run build` - the production Astro build (`dist/client` + `dist/server`).
- `npm run preview` - a local Worker preview via `wrangler dev`.
- `npm run verify` - the full pre-merge gate; see
  `/admin/playbook/building-the-platform` for exactly what it runs.
- `npm run db:migrate:local` - apply D1 migrations against the local database.
