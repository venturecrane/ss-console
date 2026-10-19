---
title: Deployment & Release
section: operations
order: 4
summary: The two deploy paths - the website to Cloudflare Workers, and the Operator as one image rolled out to every seat in rings
sources:
  - label: CI - deploy.yml
    href: https://github.com/venturecrane/ss-console/blob/main/.github/workflows/deploy.yml
  - label: wrangler.toml
    href: https://github.com/venturecrane/ss-console/blob/main/wrangler.toml
  - label: operator/bin/reprovision.sh
    href: https://github.com/venturecrane/ss-console/blob/main/operator/bin/reprovision.sh
  - label: operator/bin/fleet (the release CLI; code in operator/bin/lib/fleet/)
    href: https://github.com/venturecrane/ss-console/blob/main/operator/bin/lib/fleet/cli.py
  - label: operator/fleet/rings.yaml
    href: https://github.com/venturecrane/ss-console/blob/main/operator/fleet/rings.yaml
  - label: operator/bin/lib/render-fly-toml.sh
    href: https://github.com/venturecrane/ss-console/blob/main/operator/bin/lib/render-fly-toml.sh
  - label: scripts/ci-publish-skills.sh
    href: https://github.com/venturecrane/ss-console/blob/main/scripts/ci-publish-skills.sh
  - label: ADR 0024 (Hermes consumption; amended for fleet release)
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0024-hermes-consumption-and-update-cadence.md
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
Fly.io Machine running the Hermes runtime; one image is built per release and
rolled out to every seat in rings by `operator/bin/fleet`, and skills ship as
their own release through the same rings. They share almost nothing operationally. This page owns
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

## Merge gates, and why no human approval is required

Every change to main goes through a pull request, and the gate is mechanical. The
legacy branch protection requires the verify workflow (`Typecheck, Lint, Format,
Test`), the repository ruleset requires `Security Summary`, `substrate`, and
`Runtime ACs carry a verification ID` (the Law 9 gate, required since
2026-09-25; it resolves every cited `vfy_` id against the verify ledger and
fails closed when the ledger cannot answer), and branches must be up to date
with main, so one merge lands at a time.

Required approving reviews are zero on both, and that is a decision rather than an
oversight. The venture is one Captain plus agent sessions: the agents open and
merge the pull requests, and the Captain is not a reviewer (the `/ship` contract
merges, confirms, and reports). A human-approval requirement would either stall
every merge on the Captain or be satisfied by the same session that wrote the
change, which is no review at all. The substitute is evidence rather than
eyeballs: runtime claims carry `crane_verify` ids, the runtime-AC gate refuses an
acceptance criterion marked met without one, and the doctrine's "report is a
probe" law makes a program's own completion claim insufficient. Admin enforcement
is also off on purpose, so the Captain can bypass a stuck required check during an
incident. The periodic code reviews under `docs/reviews/` grade this as an
accepted gap each time, so it stays visible; revisit when a second human
maintainer exists.

## Path B: the Operator (one image, rolled out in rings)

The Operator runs as a per-customer Fly.io Machine hosting the Hermes runtime;
our code is a plugin-only overlay in a separate repo,
`venturecrane/hermes-smd-overlay` (it must not modify Hermes core - see
`/admin/playbook/operator-platform`). Since 2026-10-19 a release is built ONCE
and rolled out to every seat by `operator/bin/fleet`; nothing builds per seat
(ADR 0024, amended). A release is a pair: an **image** (one digest in the Fly
registry app `hermes-fleet`, which has no Machines) and a **skills version** (a
content-addressed manifest in R2). Overlay and code changes are image releases;
a skill-only change is a skills release and restarts nothing.

Run every `fleet` command from the repo root under Infisical, so R2 is
reachable: `infisical run --env=prod --path=/ss --silent -- operator/bin/fleet <command>`.

### An image release

1. **Cut an overlay release** and land it in the overlay repo.
2. **Bump `OVERLAY_REF`.** The overlay commit is pinned as `ARG OVERLAY_REF` in
   `operator/templates/Dockerfile`, beside the Hermes pin (`ARG
   HERMES_UPSTREAM_TAG` / `HERMES_UPSTREAM_SHA`, the one Hermes pin for the
   fleet; `customer.yaml` no longer carries one). The PR names its tracking
   issue and carries a `(runtime)` acceptance criterion: a green shadow-firm
   run on the rig at this ref.
3. **Build once:** `fleet build [--notes "..."]`. It refuses a tree that is
   dirty, behind origin/main, or whose `OVERLAY_REF` disagrees with
   `operator/contracts/overlay-pairs.json` (`operator/bin/lib/build-source-guard.sh`,
   the same guard a from-source provision runs), builds on Fly's remote builder
   with the label `rel-<ss-console sha8>-<overlay sha8>-<utc minute>`, records
   the release in D1 `fleet_releases`, and uploads the image's own
   `image-manifest.json`, `image-capabilities.json` and required-env list (read
   out of the pushed image with `crane`) plus the lane-input schema digests to
   R2 `fleet/images/<digest>/`. The apt, npm, uv and Hermes layers are cached
   across releases (the overlay and commit build-args are declared just before
   their first use), so an overlay-only release rebuilds only the tail. It
   prints the release id. Flags: `--declare-secret NAME` when the release
   needs a newly staged secret, `--not-volume-forward-compatible` when the new
   image changes the volume in a way the old image cannot read (a failing seat
   then halts instead of rolling back).
4. **Prove it on the rig, then release:** `fleet release <release-id> --only
   pilot-smokeball`, drive the shadow firm against the rig and cite the run id
   (below), then `fleet release <release-id>` for the rings. Rings live in
   `operator/fleet/rings.yaml`: 0 `smd-staging`, 1 `pilot-smokeball`, 2 `scott`
   `brokery` `captain-mark`, 3 `ashton-price` (always one at a time); a seat in
   no ring joins the last. `--parallel N` widens a ring; `--dry-run` runs every
   check and touches nothing.

For each seat, in order, every step recorded in D1 `fleet_rollouts`:

- **Hold.** A seat in `fleet_holds` (`fleet hold <slug> --reason "..."`, takes
  effect at once, no PR) is skipped as `skipped_held`.
- **Preflight.** The release's required env must all be on the seat (secret
  names or rendered `[env]`); a staged secret the release does not declare is
  refused (`fly deploy` would apply it); a lane whose firm-input schema changed
  since the seat's current image, for a lane the seat authors, is refused until
  its inputs are re-staged (`operator/bin/reprovision.sh <slug>`, then rerun
  with `--accept-lane-inputs`).
- **Busy gate, read live over ssh.** A running job (a live job child, or a lane
  heartbeat naming one), pending wakes, a heartbeat gone stale while its daemon
  lives, or a managed cron firing within 10 minutes (or fired within 5) means
  busy. Busy writes the quiesce marker (`/run/smd-medchron/quiesce`, root-owned,
  expires on its own) so no new job is claimed, and waits for idle, up to
  `--max-idle-wait` (30 minutes). Still busy: the seat is skipped as
  `waiting_idle`, never deployed mid-job. Queued work is not busy; the queue is
  on the volume and survives the restart. `--force-mid-job` skips the gate, and
  says so loudly.
- **Record and snapshot.** The seat's current image and deployed fly.toml are
  recorded as its rollback target, and the volume is snapshotted.
- **Deploy** `fly deploy --image <release> --config <rendered>`. The rendered
  toml (`operator/bin/lib/render-fly-toml.sh`, the renderer provisioning uses)
  sets `kill_signal = "SIGTERM"` and `kill_timeout = 300`, so the entrypoint
  supervisor drains running jobs to a checkpoint before the gateway stops.
- **Verify.** The boot smoke test (`operator/bin/boot-smoke-test.sh`, pinned to
  the release's Hermes commit), then a heartbeat read-back from D1
  `fleet_status`: within 6 minutes the seat must report the release image and
  overlay.
- **Classify.** Only a real seat failure (a smoke check that ran and said no, a
  Machine that never started, a heartbeat naming another image) rolls the seat
  back to its recorded image and toml, re-smokes it, and HALTS the rollout. A
  tooling error (ssh with no verdict, Fly agent or wrangler errors) or a timeout
  halts without touching the seat. Seats not reached are recorded `halted`.
  A rollout that halts nowhere marks the release `completed_last_ring_at`.

One rollout runs at a time: `fleet release` takes the D1 `fleet_lease` (2-hour
TTL, renewed per seat) and a second one is refused, naming the holder.

### A skills release

A merge touching `operator/skills/**` makes the deploy workflow's
`publish-skills` job (`scripts/ci-publish-skills.sh`) upload the skill files to
R2 `fleet/skills/objects/<sha256>` and a candidate manifest
`fleet/skills/<yyyymmddHHMM>-<sha8>.json` that records each file, the retired
skill names, and what each skill NEEDS from its image (cited paths, tools,
broker verbs, action classes). It writes no seat pointer; nothing changes on a
seat until `fleet release --skills <version>` walks the same rings. Per seat it
checks the needs against that seat's image (`image-capabilities.json` for the
digest its heartbeat reports; an unknown image is refused), writes
`vaults/<slug>/skills-pointer.json`, and waits up to 3 minutes for the
heartbeat to report the version. No restart, no busy gate: open sessions keep
their prompt, new turns read the new files. A seat that never confirms gets its
previous pointer back and the rollout halts. **Rolling a skill back is a
skills release of the previous version** (`fleet release --skills
<previous>`); `rollback-skill.sh` is gone.

The config publisher (`scripts/ci-publish-customer-configs.sh`) checks every
`customer.yaml` against what the seat runs NOW: its current image's manifest
plus its current skills release. A config naming a skill, pin or pre_run
neither carries is refused.

### Driving the shadow firm (the bump's `(runtime)` proof)

The shadow firm (`operator/rehearsal/`, runbook `docs/runbooks/operator/shadow-firm.md`)
replays every incident class this venture has had - an unaudited direct-API
send, cross-matter content, a fabricated matter number under failure, an
instruction injected through inbound mail, a dead connector mid-task, a
privileged instruction from an unauthored sender - against the rig seat, and
scores each from audit rows and mailbox observations rather than from how the
answers read. Once the rig runs the release, run
`infisical run --env=prod --path=/ss -- operator/rehearsal/run.py --seat pilot-smokeball --overlay-ref <candidate> --drive`.
The runner reads the rig's running overlay ref off the live seam and refuses to
drive unless it equals the candidate, and a ref it cannot read refuses too.
Cite the run id, with the report's evidence table, on the tracking issue or the
merged bump PR. `.stitch/shadow-firm/` is gitignored (the build-source guard
refuses a dirty tree), so the report is NOT committed; the digest-shaped run id
is recomputable from the pasted report body. A run id ending in `-notgreen`
does not satisfy the AC, and neither does a run with skipped scenarios. The
suite never touches a client seat or a client-visible address
(`operator/rehearsal/scope.py`).

### Status, provisioning, reprovisioning

- `fleet status [--json]` shows each seat's ring, hold, image, overlay ref,
  skills version, config digest, jobs (running / queued / wakes pending),
  heartbeat age, last rollout state, drift against the newest completed
  release, and `id-lever` (`SET` when the incident-only
  `SMD_IDENTIFIER_GATE_MODE` rollback is a Fly secret on the seat, `?` when the
  secret list could not be read; ADR 0093). Boot smoke warns on the same lever.
  `fleet releases` lists releases. `overlay-ref-drift.py` still reads
  each seat's running overlay off the live seam.
- **A new seat:** `operator/bin/reprovision.sh <slug>` (exactly `infisical run
  --env=prod --path=/ss --silent -- operator/bin/provision-customer.sh <slug>`)
  deploys the newest release that completed its last ring (`fleet
  latest-image`); `--build` builds the checkout from source instead, for a
  bring-up before any release exists. git is the single source of truth for
  `customer.yaml`; provisioning projects it to R2 unconditionally. `yes s |`
  skips the secret prompts (Machine secrets persist across deploy).
- **Reprovisioning an existing seat** runs the same busy gate first (`fleet busy
  <slug> --wait`), refuses a seat still busy (`FLEET_FORCE_MID_JOB=1` overrides),
  and clears its quiesce marker afterwards (`fleet unquiesce <slug>`).
- **Flip secrets** on the website side if a release changes the per-customer
  seam keys.

## Never root-SSH a live Operator Machine

`flyctl ssh console` runs as root. Running any overlay code there writes
root-owned `__pycache__/*.pyc` files that the hermes-user bootstrap then cannot
remove, which breaks the next boot and can crash-loop the Machine (this caused a
multi-hour customer outage). Read a live Machine's state through the runtime read
seam over HTTPS, and test a change by deploying it - never by hand-poking the live
box. The `fleet` busy probe and quiesce writer are the sanctioned exceptions:
each is a stdlib-only program fed to the image's system `python3 -I -` on
stdin (no overlay import, no `__pycache__` written), the probe only reads, and
the writer touches only the root-owned `/run/smd-medchron/quiesce` marker. A handler also cannot prove its own non-execution by inspection; verify
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
