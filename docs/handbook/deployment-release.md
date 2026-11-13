---
title: Deployment & Release
section: engineering
order: 8
owner: engineering
review_every: 90
last_reviewed: "2026-10-23"
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
  - label: operator/bin/lib/fleet/busy.py (the busy gate)
    href: https://github.com/venturecrane/ss-console/blob/main/operator/bin/lib/fleet/busy.py
  - label: operator/bin/lib/fleet/smoke.py (verify classification)
    href: https://github.com/venturecrane/ss-console/blob/main/operator/bin/lib/fleet/smoke.py
  - label: operator/bin/boot-smoke-test.sh
    href: https://github.com/venturecrane/ss-console/blob/main/operator/bin/boot-smoke-test.sh
  - label: operator/templates/seat-checks.sh (the one check list)
    href: https://github.com/venturecrane/ss-console/blob/main/operator/templates/seat-checks.sh
  - label: operator/bin/lib/boot_harness/cli.py (the boot harness)
    href: https://github.com/venturecrane/ss-console/blob/main/operator/bin/lib/boot_harness/cli.py
  - label: operator/templates/selftest.sh (the in-image self-test)
    href: https://github.com/venturecrane/ss-console/blob/main/operator/templates/selftest.sh
  - label: scripts/compile-seat-spec.ts (the seat-spec compiler and image guard)
    href: https://github.com/venturecrane/ss-console/blob/main/scripts/compile-seat-spec.ts
  - label: scripts/ci-publish-customer-configs.sh (the one writer of a seat's config)
    href: https://github.com/venturecrane/ss-console/blob/main/scripts/ci-publish-customer-configs.sh
  - label: operator/hermes-smd-overlay/config_applier/spec_verifier.py (the seat's verifier)
    href: https://github.com/venturecrane/ss-console/blob/main/operator/hermes-smd-overlay/config_applier/spec_verifier.py
  - label: ADR 0095 (one repository for the seat runtime)
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0095-one-repository-for-the-seat-runtime.md
  - label: operator/templates/Dockerfile
    href: https://github.com/venturecrane/ss-console/blob/main/operator/templates/Dockerfile
  - label: operator/rehearsal/run.py
    href: https://github.com/venturecrane/ss-console/blob/main/operator/rehearsal/run.py
  - label: docs/runbooks/operator/shadow-firm.md
    href: https://github.com/venturecrane/ss-console/blob/main/docs/runbooks/operator/shadow-firm.md
  - label: CI - release-tick.yml (the release controller)
    href: https://github.com/venturecrane/ss-console/blob/main/.github/workflows/release-tick.yml
  - label: operator/bin/lib/fleet/tick.py (one tick, the bake)
    href: https://github.com/venturecrane/ss-console/blob/main/operator/bin/lib/fleet/tick.py
  - label: operator/bin/lib/fleet/skills.py (a skills release, and when a seat may take one)
    href: https://github.com/venturecrane/ss-console/blob/main/operator/bin/lib/fleet/skills.py
  - label: operator/bin/lib/fleet/bundles.py (a jobs bundle release, and which seats take one)
    href: https://github.com/venturecrane/ss-console/blob/main/operator/bin/lib/fleet/bundles.py
  - label: operator/bin/lib/fleet/bundle_align.py (the image step points a lane seat at its bundle before it boots)
    href: https://github.com/venturecrane/ss-console/blob/main/operator/bin/lib/fleet/bundle_align.py
  - label: operator/templates/jobs-bundle-commit-probe.py (the self-test refuses a bundle older than the image)
    href: https://github.com/venturecrane/ss-console/blob/main/operator/templates/jobs-bundle-commit-probe.py
  - label: operator/bin/lib/fleet/ordering.py (when a seat may take a skills or bundle release)
    href: https://github.com/venturecrane/ss-console/blob/main/operator/bin/lib/fleet/ordering.py
  - label: scripts/ci-publish-bundle.sh
    href: https://github.com/venturecrane/ss-console/blob/main/scripts/ci-publish-bundle.sh
  - label: ADR 0096 (the release controller and its fleet credentials)
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0096-release-controller-and-fleet-credentials.md
---

How code reaches production: the website through CI on every merge, and the
Operator through a release built once and walked across the seats in rings. An
administrator needs it when an agent's report sends you here, for example to
explain why a seat was skipped, held, or rolled back.

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

- `dist/client/` - static assets.
- `dist/server/` - the Astro SSR entrypoint (`entry.mjs`) and the
  adapter-generated `dist/server/wrangler.json`, which is the config `wrangler
  deploy` actually consumes. `wrangler.toml` declares no `main`; the
  `@astrojs/cloudflare` adapter writes it, and points the `ASSETS` binding at
  `../client`.

`run_worker_first = true` (in `wrangler.toml`'s `[assets]` block, carried into
the generated config) sends every request through
Astro middleware first, so subdomain routing (`src/middleware.ts`) and session
middleware always run even for requests that would otherwise resolve straight to a
prerendered asset.

The first job, **`migrate`**, runs the D1 migrations
(`d1 migrations apply ss-console-db --remote`). Migrations apply before the
code that depends on them and must be backward-compatible with the currently
deployed app. Every other job waits for it. The `deploy` job then runs these
steps in this order, and the order matters:

1. **Build** (`npm run build`).
2. **Wrangler deploy dry-run** - catches wrangler config and binding errors
   before they reach production.
3. **Deploy to Cloudflare Workers** (`wrangler deploy`) - the main `ss-web` Worker.
4. **Deploy the sub-workers** - cost-anomaly, cost-telemetry, and fleet-alerts
   (the `ss-fleet-alerts` pager) - each from its own directory
   (`.github/workflows/deploy.yml`). (Between the root deploy and the sub-worker
   deploys the pipeline removes `.wrangler/`, because the root deploy writes a
   deploy-config file that makes the sub-workers' `wrangler deploy` ambiguous.)
   These three are all that remain under `workers/`; the lead-gen pipelines
   were retired 2026-07-01 (PRs #1610/#1616).

Five more jobs need only `migrate`, never the web build, so they run beside
`deploy` as soon as the migrations land, kept separate so one failing does not
stop the others. A seat artifact therefore never waits for the Cloudflare
deploy:

- **`sync-customer-configs`** (`scripts/ci-sync-customer-configs.sh`) projects
  every changed `customer.yaml` into the D1 `customer_configs` table the portal
  reads. A slug that has never been projected is skipped with a warning; the
  first projection stays a deliberate, manual act.
- **`publish-customer-configs`** (`scripts/ci-publish-customer-configs.sh`)
  publishes every changed `customer.yaml` to the R2 object the seat reads. It
  never creates an object for a seat nobody provisioned, and it refuses a
  config the seat's running image and skills cannot run (see "A skills
  release" below).
- **`publish-skills`** (`scripts/ci-publish-skills.sh`) publishes a candidate
  skills release (see "A skills release" below), then starts the release tick.
- **`build-seat-image`** runs `fleet build` when the merge changed the seat
  image (see "The release controller" below), then starts the release tick.
- **`publish-bundle`** (`scripts/ci-publish-bundle.sh`) builds and publishes a
  candidate jobs bundle after `build-seat-image` (see "A jobs bundle release"
  below).

A separate `ss-web-staging` Worker is declared under `[env.staging]` in
`wrangler.toml`, with its own staging D1, R2 and KV resources; the Deploy
workflow does not deploy it.

**Secrets.** On Workers, secrets persist across `wrangler deploy` runs - unlike
the Pages era, there is no post-deploy sync step and no risk of a deploy wiping
dashboard-set secrets. Rotation goes through the procedure on
[Secrets & Access](/admin/playbook/secrets-access): update the one value in
Infisical, verify it works, push it to the runtime store, and confirm the running
service picked it up. Do not pipe a bulk export of the whole Infisical path into
`wrangler secret bulk`, the pattern older comments in `wrangler.toml` and
`.github/workflows/deploy.yml` still show: the export carries every key under
`/ss`, so a key already retired from the Worker but still present in Infisical
is set on the Worker again.

**Env vars.** The three base-URL vars are plain `[vars]` in `wrangler.toml`:
`APP_BASE_URL` (`https://smd.services`, used by marketing and SignWell webhooks),
`ADMIN_BASE_URL` (`https://admin.smd.services`, the OAuth redirect URI and
outbound admin links - strict, no fallback), and `PORTAL_BASE_URL`
(`https://portal.smd.services`, portal links, falling back to `APP_BASE_URL`).

## Merge gates, and why no human approval is required

Every change to main goes through a pull request, and the gate is mechanical. The
`Main Branch Protection` ruleset (id 15555219) requires seven checks: the verify workflow
(`Typecheck, Lint, Format, Test`), `Security Summary`, `substrate`,
`runner` (the jobs runner's suite, its own parallel job of
`.github/workflows/operator-substrate.yml` since 2026-11-04, run on four
workers), `overlay` (the seat runtime's ruff, pytest and lockfile checks, a job of
`.github/workflows/operator-substrate.yml`, required from the day the overlay
fold merged; ADR 0095), and
`Runtime ACs carry a verification ID` (the Law 9 gate, required since
2026-09-25; it resolves every cited `vfy_` id against the verify ledger and
fails closed when the ledger cannot answer). A second overlay job,
`overlay-e2e`, drives the overlay through the real, pinned Hermes (a temp seat
translated from the hosted template, one scripted model call per overlay tool
dispatched by Hermes' own registry, a stub broker) and is required too, since
2026-10-23; it is also the instrument for a Hermes promotion, run by hand with a
candidate sha. The Engineering Appendix page "Building the platform" describes
it.

**A Hermes promotion does not merge until the boot harness passes on its
candidate image.** `overlay-e2e` drives the agent directly, with no entrypoint,
no gateway and no plugin enable, so it cannot see a boot failure: the first
v0.22.5 image built, passed every check above, and then crash-looped
smd-staging because Hermes built its own environment under `/opt/data` at boot
and that environment had no `yaml`, `aiohttp` or overlay packages.
`operator/bin/boot-harness run --image <candidate>` boots the image whole, with
its real entrypoint and bootstrap, on a throwaway Machine in the
`hermes-boot-harness` app (no seat's app), on a staging-derived config that
reaches nobody, with per-run dummy secrets and R2 pointed at a dead local port.
It prints one PASS/FAIL line per act a seat must perform (gateway ready, overlay
activation and sub-plugins, webhook adapter, the in-image self-test, the Hermes
pin, no runtime environment built at boot, a gateway-origin audit row, a managed
cron firing, a clean boot log, and the boot-path audit items) and exits non-zero
on any failure. `--postmortem` keeps a dying boot's Machine up so its state can
still be read. The harness is calibrated against an image every seat runs green
and one that broke staging; both captures are its test fixtures
(`operator/bin/tests/test_boot_harness.py`). Candidate images for it are pushed
to the harness app's own registry, never to `hermes-fleet`.

Main merges through a **merge queue** (since 2026-10-21). A PR whose checks are
green enters the queue (`gh pr merge <n>`, which `/ship` runs; the queue sets
the squash itself and refuses `--squash` and `--delete-branch`); the
queue tests it on top of main plus every PR ahead of it, with the same seven
checks run on the `merge_group` event, running only the suites the group's
changed paths touch (`merge_group.base_sha`), and merges it when they pass.
Until 2026-11-04 a queue run carried no base, so every suite ran on every
queued PR (21 minutes); a push to main still carries none and runs everything,
the gate before the image build. Nobody
rebases onto a moving main by hand. Before the queue, the legacy branch
protection required branches to be up to date with main, and on a day with five
sessions merging each PR re-ran its checks once for every merge that landed
ahead of it. The four workflows behind those checks (`substrate`, `runner`,
`overlay` and `overlay-e2e` are four jobs of one workflow) carry a `merge_group` trigger for this reason; a
required check without one leaves every queued PR waiting at "Expected".

Required approving reviews are zero on both, and that is a decision rather than an
oversight. The venture is one Captain plus agent sessions: the agents open and
merge the pull requests, and the Captain is not a reviewer (the `/ship` contract
merges, confirms, and reports). A human-approval requirement would either stall
every merge on the Captain or be satisfied by the same session that wrote the
change, which is no review at all. The substitute is evidence rather than
eyeballs: runtime claims carry `crane_verify` ids, the runtime-AC gate refuses an
acceptance criterion marked met without one, and the doctrine's "report is a
probe" law makes a program's own completion claim insufficient. The ruleset has
no bypass actors, so none of its seven checks can be bypassed by anyone. (Until
2026-10-21 the verify check lived on the legacy branch protection, where admin
enforcement was off, so an administrator could merge past it; moving it into the
ruleset with the queue closed that.) The periodic code reviews under
`docs/reviews/` grade zero required reviews as an accepted gap each time, so it
stays visible; revisit when a second human maintainer exists.

## Path B: the Operator (one image, rolled out in rings)

The Operator runs as a per-customer Fly.io Machine hosting the Hermes runtime;
our code is a plugin-only overlay at `operator/hermes-smd-overlay/` in this
repo (it must not modify Hermes core - see
`/admin/playbook/operator-platform`). Until 2026-10-22 the overlay was the
separate repo `venturecrane/hermes-smd-overlay`, pinned by `ARG OVERLAY_REF`;
ADR 0095 folded it in, so the image is built from one tree and there is no
overlay pin to bump. Since 2026-10-19 a release is built ONCE
and rolled out to every seat by `operator/bin/fleet`; nothing builds per seat
(ADR 0024, amended). A release is a pair: an **image** (one digest in the Fly
registry app `hermes-fleet`, which has no Machines) and a **skills version** (a
content-addressed manifest in R2). Overlay and code changes are image releases;
a skill-only change is a skills release and restarts nothing.

Since 2026-10-23 an image release needs no laptop (ADR 0096): a merge that
changes the image is built by CI and rolled through the rings by a scheduled
controller, described under "The release controller" below. Since 2026-10-28 the
same controller rolls skills releases too. The steps that
follow are what that controller runs, and they remain the manual path: a person
finishes a halted release, fixes one seat or ships a hotfix lane with them.

Run every `fleet` command from the repo root under Infisical, so R2 is
reachable: `infisical run --env=prod --path=/ss --silent -- operator/bin/fleet <command>`.

### An image release

1. **Merge the change.** A runtime change is one ordinary PR, whether it
   touches `operator/hermes-smd-overlay/`, the rest of `operator/`, or both
   (ADR 0095). The image is built from the local tree: the Dockerfile copies
   `operator/hermes-smd-overlay/` in, installs it into the Hermes venv without
   dependencies (the third-party ones are hash-locked in
   `operator/requirements/hermes-overlay.txt`), and copies it a second time to
   `/app/overlay-pack`, which `bootstrap.sh` refreshes onto the volume on every
   boot. Nothing is fetched from git at build or at boot. The one pin left is
   Hermes (`ARG HERMES_UPSTREAM_TAG` / `HERMES_UPSTREAM_SHA` in
   `operator/templates/Dockerfile`, with `HERMES_REF` holding the full pin
   string for provenance; `customer.yaml` no longer carries one). A PR that
   changes seat behavior names its tracking issue and carries a `(runtime)`
   acceptance criterion: a green shadow-firm run on the rig running a release
   built from it. What a change does and why goes in the commit message and
   the PR, never as a dated comment in a file body:
   `tests/no-history-in-file-bodies.test.ts` fails a PR that writes it there.
   The overlay's own CI is the `overlay` job of
   `.github/workflows/operator-substrate.yml` (ruff, the overlay pytest suite
   against the pinned Hermes, and a no-diff check of
   `operator/requirements/compile.sh`).
2. **Build once:** `fleet build [--notes "..."]`. It refuses a tree that is
   dirty or behind origin/main (`operator/bin/lib/build-source-guard.sh`,
   the same guard a from-source provision runs), builds on Fly's remote builder
   with the label `rel-<ss-console sha8>-<yyyymmddHHMM UTC>` and the release id
   `img-<yyyymmddHHMM UTC>-<ss-console sha8>`, records
   the release in D1 `fleet_releases` (its `overlay_ref` column holds the
   ss-console commit, which the seat's heartbeat must echo), and uploads the image's own
   `image-manifest.json`, `image-capabilities.json` and required-env list (read
   out of the pushed image over the registry's HTTP API by `ImageExtractor` in
   `operator/bin/lib/fleet/build.py`; it deliberately does not shell out to
   go-containerregistry's `crane` image tool, because on SMD laptops the name
   `crane` is the venturecrane session launcher) plus the lane-input schema digests to
   R2 `fleet/images/<digest>/`. If the image cannot be read, the release is
   recorded without capabilities: image releases still work, but a skills
   release to that image is refused; `fleet artifacts <release-id>` re-reads
   them later. The apt, npm, uv and Hermes layers are cached
   across releases (the overlay copy and the commit build-arg come after
   them), so an overlay-only release rebuilds only the tail. It
   prints the release id. Flags: `--declare-secret NAME` when the release
   needs a newly staged secret, `--not-volume-forward-compatible` when the new
   image changes the volume in a way the old image cannot read (a failing seat
   then halts instead of rolling back). The build also records the volume
   epoch of the tree it built, the integer in `operator/fleet/volume-epoch`
   (no file is epoch 1), on the release (`fleet_releases.volume_epoch`,
   migration 0150).
3. **Release, twin first:** `fleet release <release-id>` walks the rings, and
   ring 0 is each client's **release twin**: the seat whose `seat.yaml` says
   `purpose: release-twin` and names its client in `twin_of` (ADR 0107). A
   client seat with a twin takes a release only after its twin is done on that
   same release, on a laptop run as in the controller: `--only <client>` is
   refused until `--only <twin>` is done, and a full run whose twin was held or
   failed records the client `waiting_twin` and leaves it alone. The demo seat
   (`pilot-smokeball`, `purpose: demo`, [The Demo Seat](/admin/playbook/demo-seat))
   gates nothing and is not a step before the client. Rings live in `operator/fleet/rings.yaml`: 0
   `ap-staging` (the twin of `ashton-price`), 1 `ashton-price`, 2
   `pilot-smokeball` `brokery` `captain-mark` `smd-guide` `smd-office` (the last ring
   always one at a time), with a `twins:` block naming each client's twin. The
   client seat has been ring 1 since 2026-10-30; the reason is under "The
   release controller" below. That file is generated from each seat's
   `purpose:`, `twin_of:` and `ring:` in `operator/customers/<slug>/seat.yaml`
   (`npm run gen:seat-registries`); a twin is ring 0 by derivation, and a seat
   in no ring would join the last. The twin of `ashton-price` is `ap-staging`,
   an exact copy of the client seat on SMD's Smokeball staging tenant
   (`twin.yaml` declares every difference). A
   seat authored with `fly_app: null` (`ashton-price-marketing` until the firm
   creates its mailbox) is in no ring at all, in the generator and in
   `rings.py` alike: it has no app to release to. `--parallel N` widens a ring; `--dry-run` runs every
   check and touches nothing.

**Rolling back** a seat or the fleet is `fleet release <previous img id>`:
the earlier image is still in the registry and its release row is still in
D1, so going back is a release like any other.

For each seat, in order, every step recorded in D1 `fleet_rollouts`:

- **Already done.** A full run skips a seat whose latest rollout of this
  release is `done`, so rerunning a release after a halt resumes it instead of
  deploying the finished seats again. `--only` still redeploys on request.
- **Hold.** A seat in `fleet_holds` (`fleet hold <slug> --reason "..." --until
"..."`, takes effect at once, no PR; `fleet unhold <slug>` lifts it) is
  skipped as `skipped_held`. Holds are read again before every seat, so a hold
  set while a release is walking stops the next seat it covers. `--until` is
  the condition that ends the hold, in words, and is recorded with it. The hold
  records the Claude session that set it (its worktree name and mission line,
  from the session board) or the GitHub run, and `fleet status` lists every
  hold with who, when and why. On 2026-10-30 ashton-price sat held while
  several sessions waited for it to lift, and the hold named only the laptop
  login, so nobody could say whose it was.
- **Preflight.** The release's required env must all be on the seat (secret
  names or rendered `[env]`); a staged secret the release does not declare is
  refused (`fly deploy` would apply it); a lane whose schema module changed
  since the seat's current image, for a lane the seat authors, is put to the
  release's own validator: the seat's staged marker file is read from the
  vault and run through `validate()` from the runner at the release's commit
  (`operator/bin/lib/fleet/lane_validate.py`, 2026-11-05). It passes: the lane
  passes with a note. It finds problems, or it cannot be asked: the lane is
  refused until
  its inputs are re-staged (`operator/bin/reprovision.sh <slug>`, then rerun
  with `--accept-lane-inputs`), or until a person declares in the repo that the
  staged inputs still fit: an entry in `operator/fleet/lane-inputs-accepted.yaml`
  naming the commit the schema modules changed at, the lanes, who and why. A
  release built at or after that commit then passes a seat whose image is from
  before it with a note, and the tick rolls it. The declaration is for a schema
  module that changed in name only, as on 2026-11-04 when ADR 0103 renamed the
  runner's package; a schema that accepts different inputs is re-staged.
- **Busy gate, read live on the seat.** The probe runs as root through `fly
  machine exec`, an HTTPS call with no WireGuard tunnel; until 2026-10-26 it
  used ssh, and a fresh tunnel per GitHub run lost two of three probes. A running job (a live job child, or a lane
  heartbeat naming one), pending wakes, a heartbeat gone stale (over 5 minutes) while its daemon
  lives, a managed cron firing within 6 minutes (or fired within the last 2),
  a gateway with turns in flight, or a store the probe could not read means busy. The gateway is
  asked live, by its own `active-work` control verb (from a child that drops to the gateway's uid,
  since the socket accepts no other); the `active_agents` number in `gateway_state.json` is read
  only from a gateway that does not answer, because Hermes writes that number only when a chat
  turn starts or ends, and a stale one held ap-staging for 45 minutes on 2026-11-11. The program
  ships zlib-compressed, because exec refuses a command over its size cap
  (`operator/bin/lib/fleet/busy.py`; the cron window is that narrow so a seat
  with 15-minute slot crons still has an idle gap). Busy writes the quiesce marker (`/run/smd-jobs/quiesce`, root-owned,
  expires on its own) so no new job is claimed, and waits for idle, up to
  `--max-idle-wait` (30 minutes). Still busy: the seat is skipped as
  `waiting_idle`, never deployed mid-job. Queued work is not busy; the queue is
  on the volume and survives the restart. `--force-mid-job` skips the gate, and
  says so loudly. A probe or quiesce call that loses its connection to Fly is
  retried, a poll apart, up to 3 times in a row; a connection that stays down,
  or a probe that ran and failed, halts the seat as a tooling error.
- **Record and snapshot.** The seat's current image and deployed fly.toml are
  recorded as its rollback target, and the volume is snapshotted. A release
  built with `--not-volume-forward-compatible` halts a failing seat instead of
  rolling it back.
- **Never onto an older volume layout** (`operator/bin/lib/fleet/volume_epoch.py`,
  2026-11-07). A failing seat is not rolled back when its rollback image's
  volume epoch is lower than the release's: it halts on the failing image
  with its snapshot kept, as `failed`, through the same `rollout_failed` pager
  as the flag above. This covers what the flag cannot: a LATER release failing
  on a seat that already moved, and a seat that skipped the move's own release
  and took a later one. A release whose epoch is lower than the image the seat
  runs now is refused before anything is touched. A release row without an
  epoch (built before migration 0150), and an image no release row names,
  count as epoch 1.
- **Jobs bundle pointer, before the deploy** (a seat that authors a job lane,
  while `BUNDLE_RELEASE_ENABLED` is `true`; `operator/bin/lib/fleet/bundle_align.py`).
  A seat reads its bundle pointer only at boot, so the pointer is written at
  the newest bundle the new image qualifies for and read back before the image
  boots. Until 2026-11-07 the image went first and the bundle step after, and a
  client seat ran rel-b4d97f03 with the previous bundle for ten minutes. A
  pointer that cannot be written or read back stops that seat before its
  deploy (`tooling_error`, retried next tick; the other seats go on). When the
  bundle the image needs is not published yet, the tick defers the seat. A
  rollback, and a deploy that decided nothing, put the previous pointer back
  before anything else. The bundle step that follows then finds the seat
  already on the version and writes its pointer with no restart, so a release
  restarts a lane seat once. Skills are not pointed ahead: a seat applies them
  live, so they would run on the old image.
- **Deploy** `fly deploy --image <release> --config <rendered>`. The rendered
  toml (`operator/bin/lib/render-fly-toml.sh`, the renderer provisioning uses)
  sets `kill_signal = "SIGTERM"` and `kill_timeout = 300`, so the entrypoint
  supervisor drains running jobs to a checkpoint before the gateway stops.
- **Verify.** The boot smoke test (`operator/bin/boot-smoke-test.sh`, pinned to
  the release's Hermes commit, 45-minute budget), then a heartbeat read-back
  from D1 `fleet_status`: within 6 minutes the seat must report the release
  image and overlay (`operator/bin/lib/fleet/smoke.py`). What the smoke test
  checks is under "What the verify step checks" below. With
  `--verify selftest` the seat's own self-test decides instead (the same check
  list, run on the Machine, read back from the heartbeat); see "The self-test
  as the verifier" below. The default is `--verify selftest` (since
  2026-10-26); `--verify bash` keeps the boot smoke as the judge. With
  `--no-ssh-fallback` as well, a self-test that gives no verdict is a tooling
  error instead of a cue to run the boot smoke, and a rollback is re-verified by
  the self-test; that is how the controller verifies.
- **Classify.** Only a real seat failure (a smoke check that ran and said no, a
  Machine that never started, a heartbeat naming another image) rolls the seat
  back to its recorded image and toml, re-smokes it, and HALTS the rollout.
  Seats not reached are recorded `halted`. A tooling error (Fly or the self-test
  decided nothing: ssh with no verdict, Fly agent or wrangler errors) or a
  timeout leaves the seat untouched and stalls only that seat: the rollout goes
  on to the next one. A `fly deploy` that fails without saying the release is
  unhealthy is tried once more a minute later, and what Fly printed is kept in
  the rollout's detail (lines naming a credential are dropped).
- **Complete.** After any real run, a release whose latest rollout on every
  ringed seat is `done` is marked `completed_last_ring_at`. That is read from
  the `fleet_rollouts` records, not from one run: a release repaired seat by
  seat with `--only` completes when its last seat is done, and a seat left
  `waiting_idle`, held or `halted` keeps it incomplete.

**Leases.** Two scopes, one D1 table (`fleet_lease`, 2-hour TTL, an expired
holder is taken over). A full `fleet release` holds the fleet lease while it
walks the rings, so a second full rollout is refused, naming the holder. Every
seat deploy holds that seat's lease (`seat:<slug>`): a rollout's, an `--only`,
and a reprovision alike. So `--only <seat>` runs beside a full rollout of the
others, and two deploys never land on one seat at once: a full rollout records
a seat someone else is deploying as `waiting_idle` and moves on; an `--only`
against it is refused. Exit codes: 0 success; 1 a seat failed or was refused,
or the rollout halted; 2 usage; 3 a tooling error (nothing decided); 4 refused
before anything ran (lease held, unknown release, source guard). A tick adds 5
(waiting) and 6 (a standing halt), below.

### The release controller (no laptop)

Two workflows run the release (ADR 0096).

- **Build on merge.** The `build-seat-image` job of `deploy.yml` runs
  `fleet build` at the merged commit when the tree differs from the newest
  recorded image release under an image path (the
  `.github/operator-substrate-paths.txt` list without its `.github/` lines and
  without the artifact paths in `.github/operator-artifact-paths.txt`, today
  `operator/skills/**` and the jobs bundle's runner and Smokeball connector:
  `substrate-paths-changed.py --image`). It
  compares with the last BUILT commit, so a runtime merge followed quickly by a
  docs merge is still built. When main has already moved past the commit, the
  build-source guard refuses it and the job ends green: the newer merge's own
  run builds the newer tree. A build that loses the builder's stream is tried
  twice more, a minute and two minutes later, before the run fails.
- **Roll by tick.** `release-tick.yml` runs `fleet release latest --tick` when
  Deploy's `publish-skills` job has published and when `build-seat-image` has
  built (each starts a run the moment its artifact exists, not when the whole
  Deploy run ends), after every run that advanced a ring (that run starts the
  next one, so rings follow each other in minutes), every 5 minutes as the
  backstop, and on manual dispatch (with an optional release id and a dry run
  that records nothing). A dispatch is skipped while a run is already waiting,
  so starts never pile up as cancelled runs; the old trigger on Deploy's
  completion is gone for the same reason (2026-11-05).
  Ticks never overlap. One tick takes the fleet lease, reads every seat's
  rollout of the image releases still in flight from D1, and advances every
  ring that is ready, one release per ring (`operator/bin/lib/fleet/tick.py`).
  A client ring rolls one seat first, alone, as a canary; once it is done,
  every other seat of the ring rolls at once, up to `max_parallel` (twenty, set
  in `scripts/lib/seat-registries.ts` and written into
  `operator/fleet/rings.yaml`). Twenty client seats cost about two seat
  restarts after staging, not four waves or a two-hour line, and a defect only
  a client seat shows stops at the canary. Between waves the run asks whether a
  newer image has landed and hands off to the next run if so. Each ring takes the newest
  release that has baked on the ring before it: the newest goes as far as its
  own bakes allow, and while it waits, an older release that already passed the
  earlier rings keeps moving, so a run of merges never starves the later rings.
  An older release gives way, for at most one bake, to a newer one already done
  on the ring before, so a ring is not restarted twice in a few minutes. One
  tick moves every ring that has a release ready, so staging taking each new
  merge never keeps the client ring waiting. Every step of a run (image,
  skills, jobs bundle) ends early when a newer image is published mid-step,
  so the run it starts can take that image from staging (2026-11-05).
  Within a run, paying clients' seats come first for everything: the image,
  the skills and the jobs bundle each step the rings before the last ring,
  and only then does SMD's own last ring take them (2026-11-05, after a
  client's jobs bundle waited 29 minutes while the image was deployed to our
  own four seats).
  A seat already on a newer release is never moved back to an older one:
  - a seat whose latest rollout is `rolled_back` or `failed` is a **standing
    halt**: nothing moves until a person acts, and the `rollout_failed` pager
    has already mailed. Finish it from a laptop with
    `fleet release <id> --only <seat>`, or hold the seat. One halt clears
    itself: a `failed` row that was only a lane-input refusal ("the lane's
    firm-input schema changed; re-stage") deployed nothing, so each tick asks
    whether `operator/fleet/lane-inputs-accepted.yaml` on main now declares
    every refused lane for that release and that seat; when it does, the seat
    is pending again and its ring rolls it. Merging the declaration is the
    whole fix (2026-11-05, ADR 0096 amended).
  - a seat that is **stalled** (`tooling_error`, or `waiting_idle` because it
    was busy) in a ring where another seat is done does not hold the rings
    after it back: the next ring bakes on the done seats, and the stalled seat
    is tried again on a tick when the front of the release has nothing to do.
    A ring with no seat done stays the front, and its stalled seats are retried
    there. Before 2026-10-30 a `tooling_error` was a standing halt: on
    2026-10-29 one Fly deploy that failed without a reason, first on
    smd-staging and then on brokery, kept every later seat, the client's
    included, off merged fixes for hours.
  - a client seat whose **release twin** is not done on this release, by the
    twin's own rollout row, is not pending: the tick names it as waiting on its
    twin (`twin_deferrals`, ADR 0107). A twin done only on a newer release does
    not count, and a twin with no row, held, stalled or failed keeps its client
    waiting. On 2026-10-30 releases `9bd7caa3` and `f835d1d9` reached
    `ashton-price` with no `smd-staging` rollout row; this is the gate that was
    missing.
  - otherwise the remaining seats of the first unfinished ring deploy, after
    **the bake**: every unheld seat of the ring before it done on the release
    for that ring's `bake_minutes` (10 by default; a seat may author
    `bake_minutes` in its `seat.yaml`, and the ring takes the longest, rendered
    into `operator/fleet/rings.yaml`), and no `selftest_failing` or
    `image_drift` alarm open on those seats. A ring whose every seat is held
    holds every ring after it.
  - each seat is verified by its own self-test, with no ssh boot smoke: no
    verdict is a tooling error that stalls that seat. The busy gate still
    probes the seat, waiting up to 10 minutes; a seat still busy is left
    `waiting_idle` and a later tick picks it up. A tick never passes
    `--force-mid-job` or `--accept-lane-inputs`, so a lane schema change the
    seat's staged inputs do not pass halts
    the tick at preflight until a person re-stages the seat or merges the
    call in `operator/fleet/lane-inputs-accepted.yaml`. A lane that is
    new on the release (the seat's current image recorded other lanes'
    digests but not this one) passes with a note: no old-schema input exists.
  - every judged seat writes a `live_state` row to the verify ledger and its id
    lands in the rollout's detail (`verify row: vfy_...`); a failed write is a
    note there, never a failed release.

  - **skills releases** take the same step in the same run, after the image
    step: the newest skills release published from main and not yet complete,
    with older ones promoted exactly as older images are. A seat takes one only
    once its image has the skills' commit (see "A skills release" above). An
    image step that refuses or errors does not stop the skills step; the run
    then reports the image error. A run dispatched with an explicit image
    release id steps that image only.
  - **jobs bundle releases** take the same step after the skills step, on rings
    holding only the seats that author a job lane (see "A jobs bundle release"
    below), and only while the repository variable `BUNDLE_RELEASE_ENABLED` is
    `true`. A step that refuses or errors does not stop the later steps.

  A tick exits 0 on progress, 5 when it is waiting (a bake running, the lease
  held by a laptop run, nothing to do) and 6 on a standing halt; all three are
  green runs. 1 (this tick halted the release), 3 and 4 fail the run. With
  both kinds stepped, a halt in either wins, then a standing halt, then
  progress.

There is no approval step before the client ring. Since 2026-10-30 the client
seat is ring 1, straight after its release twin (`ap-staging`) with no bake on
either, and SMD's other seats follow it. Since 2026-11-10 the client seat also
waits for its twin to be done on each release (ADR 0107), so a release that
skipped the twin never reaches the client: with one client, a fix waited about an hour behind four
rings of bakes, and the bakes protected nothing a seat's own self-test and
automatic rollback did not. So a merged fix reaches ashton-price in the same
run that takes staging, straight after staging's self-test is green, about 20
minutes after the image is built on a normal day (staging 8, the busy gate up to
10). When a newer image lands while a run is still rolling an older one, the run
ends after the seat it is on and the run it starts takes the newer image from
staging; an older image is never carried through SMD's own seats while the
client waits. Staging's
self-test, the busy gate and holds protect it; a required approval would put a
person back on every release. When a second client signs, revisit the order.

**An urgent fix for one seat skips the queue.** Merge the fix, let Deploy build
the image (about 6 minutes), then run `release-tick` by hand (Actions, Run
workflow; it works from a phone) and pick the seat. That release goes to the one
seat now, with no ring order and no bake. For a client seat with a release twin,
pick the twin first and then the client: the client is refused, naming the
twin, until the twin is done on that release (ADR 0107). The seat's self-test still judges it,
a failure still rolls it back, a hold is still honored, and a running client job
is still waited for, up to 30 minutes. It runs in its own lane per seat, so it
never queues behind a tick, and the tick later skips the seat as done.

**Credentials.** The protected GitHub environment `fleet-release` (deployable
from `main` only) holds one Fly deploy token per seat app,
`FLY_DEPLOY_TOKEN__<SLUG>` (upper case, `-` as `_`), and `FLY_REGISTRY_TOKEN`
for the `hermes-fleet` registry app. There is no org-wide Fly token (ADR 0054).
The fleet CLI chooses per call: the registry token alone for the build, and for
a seat both tokens comma-joined, because a seat token alone cannot pull the
registry app's image. A run missing a seat's token ends that seat as a tooling
error naming the variable; on a laptop nothing is injected and `fly auth login`
is used as before. Adding a seat means minting its token into the environment
and adding its line to `release-tick.yml`; the conformance test fails until
both are there.

**Turning it on.** Both controller jobs run only when the repository variable
`FLEET_RELEASE_ENABLED` is `true`; until then a tick run reports "Release
controller not enabled" and the build job is skipped, and neither enters the
environment (a job's `if:` is checked first, so GitHub never auto-creates
`fleet-release` without its branch policy). Set the variable only after the
`fleet-release` environment exists with its main-only deployment branch policy
and every Fly token (one per seat app in `release-tick.yml`, seven today, and
the registry token). Then immediately dispatch `release-tick.yml` with
`dry_run: true` and read the plan in its log before the first scheduled tick
acts.

**A failed rollout pages.** The fleet-alerts Worker reads `fleet_rollouts` every
2 minutes and emails team@smd.services once per new `failed` or `rolled_back`
row, and per `tooling_error` row that is the seat's second on the same release
(one that the next tick's retry fixed is not worth a page), with the release,
the detail, and the `--only` command
that finishes it (`workers/fleet-alerts/src/rollout-failed.ts`, migration
0133). On 2026-10-20 a preflight refusal on captain-mark sat unread for a day.

**Drift pages.** Every 2 minutes the same Worker compares what each seat
reports on its heartbeat (`image_ref`, `config_digest`, `skills_version` in
`fleet_status`) with what was authored for it, and opens one alert per seat and
kind of drift, with one recovery notice when the seat converges
(`workers/fleet-alerts/src/drift.ts`, migration 0135):

- `image_drift`: the seat runs a release older than the newest one that
  completed its last ring, although its rollout of that release finished
  `done`. A seat the release has not reached yet is a rollout still to run, and
  a seat on a newer release is a rollout in progress; neither pages. The fix is
  `operator/bin/fleet release <release> --only <seat>`.
- `image_unknown`: the seat runs an image that no `fleet_releases` row carries,
  so something deployed it outside the release system.
- `config_drift`: the seat's `config_digest` is not the sha256 of R2
  `vaults/<slug>/customer.yaml`, or R2 holds no such object. The seat hashes
  exactly the bytes it fetched from that object, so the two differ only when a
  live apply was refused, a rebuild-class change is waiting for a reprovision,
  or the seat cannot reach R2.
- `skills_drift`: the seat's `skills_version` is not the version
  `vaults/<slug>/skills-pointer.json` names, or the seat reports skills with no
  pointer. No pointer and no version is a seat that has never had a skills
  release, and does not page.

A mismatch within 15 minutes of the R2 write (`DRIFT_GRACE_SECONDS`) waits,
because the appliers are still picking it up. A seat whose heartbeat is red
holds every drift signal: its row describes the past, and the heartbeat alert
already covers it.

### Hotfix lanes

Three lanes, depending on what the seat runs and what main holds.

- **A seat on a folded image** (built after ADR 0095): an ordinary PR, then
  `fleet build` and `fleet release <id> --only <seat>` (or the rings). There is
  nothing else to update.
- **`ashton-price` while it is held on a pre-fold image.** That image was built
  from the standalone overlay at `8c6f4b86`, so a fix for it cannot come from
  main without also moving the seat onto the folded build. Land the overlay fix
  on a hotfix branch off `8c6f4b86` in the locked `venturecrane/hermes-smd-overlay`
  (unlock `main`, land, relock), bump the pin on the ss-console branch
  `release/pre-fold`, build that branch with
  `SS_ALLOW_DIVERGENT_SOURCE=1 operator/bin/fleet build` (the build-source guard
  otherwise refuses a tree that is not origin/main), and release it with
  `fleet release <id> --only ashton-price`. Record the lane in the release
  notes. The lane closes when `ashton-price` runs a folded image; the standalone
  repo is then archived.
- **A seat on a folded image while main holds a change that seat must not get
  yet** (a promotion under a `fleet hold`, say). The fix lands on main as an
  ordinary PR first. Then branch `hotfix/<what>` off the commit the seat's image
  was built from (`fleet status` names the release), cherry-pick the squash
  commit, push the branch, and build it with
  `SS_ALLOW_DIVERGENT_SOURCE=1 operator/bin/fleet build`. That image is never
  `latest` (`_image_release` in `operator/bin/lib/fleet/cli.py` takes only
  images built from main), so the controller cannot roll it to other rings.
  With the seat idle (`fleet busy <seat>`), release it in one chained command
  so the hold always comes back, even when the release fails:
  `fleet unhold <seat> && fleet release <id> --only <seat>; fleet hold <seat> --reason "<the hold's reason>" --until "<its lift-when, as fleet status shows it>"`.
  Read back `fleet status` (the hotfix image, a green heartbeat, held again)
  and record the lane in the release notes. The lane closes at the held
  change's promotion, and that promotion must use a main build that contains
  the fix: promoting an image built before the fix merged would take the fix
  back off the seat.

### The overlay cutover

Seats move to folded images seat by seat in ring order: `smd-staging`,
`pilot-smokeball`, then `scott`, `brokery` and `captain-mark`, and
`ashton-price` last. The fold changes what the heartbeat's `version` field
reports (the ss-console commit instead of an overlay commit), so the
read-back compares it with the ss-console commit the release recorded.

### A skills release

A merge touching `operator/skills/**` (or the retired-skills list,
`operator/templates/retired-skills.txt`) makes the deploy workflow's
`publish-skills` job (`scripts/ci-publish-skills.sh`) upload the skill files to
R2 `fleet/skills/objects/<sha256>` and a candidate manifest
`fleet/skills/<yyyymmddHHMM>-<sha8>.json` that records each file, the retired
skill names, and what each skill NEEDS from its image (cited paths, tools,
broker verbs, action classes), and records the release in D1
`fleet_releases` (kind `skills`) when it can reach D1. The version is
`<yyyymmddHHMM UTC>-<commit sha8>`. It writes no seat pointer itself. The
release controller rolls it: every `release-tick` run (`fleet release latest
--tick`) steps the skills releases after the image releases, with the same
rings, bake, holds, standing halt and lease (see "The release controller"
below). `fleet release --skills <version>` walks the same rings by hand. Per
seat it checks the needs against that seat's image (`image-capabilities.json`
for the digest its heartbeat reports; an unknown image is refused), writes
`vaults/<slug>/skills-pointer.json`, and waits up to 3 minutes for the
heartbeat to report the version. No restart, no busy gate: open sessions keep
their prompt, new turns read the new files. A seat that never confirms gets its
previous pointer back and the rollout halts. **Rolling a skill back is a
skills release of the previous version** (`fleet release --skills
<previous>`); `rollback-skill.sh` is gone.

**Skills follow the image.** The controller gives a seat a skills release
published at commit S only once that seat's image commit is base(S) or
descends from it, where base(S) is the last commit at or before S that changed
an image path (`operator/bin/lib/fleet/ordering.py`, the same image path list
`build-seat-image` uses). A skill is written against the code in the image, and
the capability check sees only what a skill names, not a changed argument or
behaviour. Since ADR 0100 W3 `operator/skills/**` is an artifact path, not an
image path: a skills-only merge builds no image, and its skills go to every
seat already on the newest image before it. A merge that changes skills and
image code together waits for its own image. A seat whose image is older waits;
it never halts, and the tick log says which seat waits and for what. A seat on a
hotfix image counts when its commit descends from base(S), and a seat whose
heartbeat already reports a newer skills version is never moved back.

### A jobs bundle release

The job runner (`/opt/jobs`, with its pricing table) and the Smokeball
connector venv (`/opt/connectors/smokeball`) also ship as a versioned artifact,
the jobs bundle (ADR 0100 W3), so a change to them reaches only the seats that
run jobs. A merge touching a path in `.github/operator-bundle-paths.txt` makes
the deploy workflow's `publish-bundle` job (`scripts/ci-publish-bundle.sh`)
build the two venvs inside the seat image this merge built (else the newest
completed image built from main), at the same paths, with
`operator/templates/jobs-runtime.sh`, the script the Dockerfile itself runs. It
uploads `fleet/bundles/jobs/<version>.tar.zst`, reads it back, then the manifest
`fleet/bundles/jobs/<version>.json` (name, version, sha256, size, source
commit, base image, Python version), and records `fleet_releases` kind
`bundle`, release id `bnd-<version>`. It writes no seat pointer. Since the cut
(2026-10-31) the runner, the Smokeball connector and their two requirement pins
are artifact paths: a change to them builds no image and restarts only the seats
that author a job lane. The connector SDK, `jobs-runtime.sh` and the pricing
table stay image paths as well, because the image builds its other connectors
from them and the overlay prices from the table. The image keeps a seed copy of
the runner and connector, frozen at its own build. A seat whose pointed-at
bundle fails to verify or import runs the version it ran before (the installer
keeps it, N-1), and only with none on disk the image's seed.

**A seat never runs an image with a bundle of another layout (ADR 0103).**
The bundle's tarball layout is numbered (`layout` in its manifest; 1 was the
`medchron/` root at `/opt/medchron`, 2 is `jobs/` at `/opt/jobs`), and every
publish also writes `fleet/bundles/jobs/layout-<n>/latest.json`. At boot the
installer compares the seat pointer's layout with the image's own: on a
mismatch it installs the latest bundle of the image's layout instead of the
pointer; otherwise it behaves as above, so the controller's pointer rollback
keeps working. The controller writes a seat's pointer without a restart when
the seat already reports that version. Before this rule an image whose
expected layout differed from the pointed-at bundle booted with no runner,
failed its self-test, was rolled back, and the bundle stayed deferred: the
rename of the runner (2026-11-04) could not have shipped.

**A seat never runs a bundle older than its image (2026-11-07).** Each image
bakes its bundle floor, `/app/bundle-floor`: the last commit at or before the
image's commit that changed a path in `.github/operator-bundle-paths.txt`, and
that commit's number (`git rev-list --count`, via `fleet bundle-floor`). Each
manifest carries its own commit number (`ss_console_seq`, `fleet commit-seq`).
The boot self-test check `jobs-bundle-built-for-image` fails when the mounted
bundle was built from a commit before the floor, with one line naming both
commits, and after an image deploy that failure rolls the seat back like any
other failed self-test. A bundle that lags the image because the image's commit
changed no bundle path is built at or after the floor and passes; so does a
bundle newer than the image. The tick picks the bundle by the same rule, with
git: built at or after the floor, and needing no newer image than this one.

**The runner rename and its hand rollback (ADR 0103).** R1, the code rename,
is forward-compatible and rolls back on its own: a failing seat returns to
the previous image, whose installer runs the old-layout bundle it still holds
on disk. R2, the one-time move of `/opt/data/medchron` to `/opt/data/jobs`, is
volume epoch 2 and built `--not-volume-forward-compatible`, so a failing seat
halts on the new image with the volume snapshot the halt recorded. Every later
release records epoch 2 too, so a moved seat that fails a later release halts
the same way instead of rolling back onto an epoch-1 image, and R2 can roll in
rings like any release, without holds or a hand `--only` per seat. The recovery is by hand:
`fleet hold` the seat; start a Machine on the previous image with the
entrypoint overridden and run `mv /opt/data/jobs /opt/data/medchron` before
the old image boots (the R1 image leaves the moved tree alone, but it makes a
fresh, empty `/opt/data/medchron` and runs every lane on that, with no queue,
state or cache; an image older than R1 would also chown the moved tree to the
agent uid); restore the bundle pointer; or restore the volume snapshot. There
is no separate rehearsal: the release twin (then smd-staging) took R2 first, and a halt there stops
the release before any client seat, which is where this recovery would first
be used.

What the R2 image does at boot, and what holds it afterwards
(`operator/templates/entrypoint-lanes.sh`, the `jobs-volume` block): it renames
the tree once, binds it at `/run/smd-jobs` and, for one release, at
`/run/smd-medchron`, and writes the regular-file count before and after the
move to `/opt/data/jobs/volume-epoch.state`. When both names exist it never
merges them. An old tree that holds directories only (what an R1 boot leaves
when its lanes never started) is removed with `rmdir` and the boot goes on; an
old tree that holds any file means no job lane boots, one `JOBS VOLUME REFUSED`
line is logged, and the self-test fails until a root hand leaves exactly one
tree. The self-test asserts the result at every boot and every hour
(`operator/templates/seat-checks.sh`): `jobs-volume-old-tree-holds-no-files`,
`jobs-volume-tree-present`, `jobs-volume-run-dirs-bound` and
`jobs-volume-move-counts-equal`. `tests/jobs-volume-old-path-allowlist.test.ts`
fails the build when a file outside its short list names the old tree again.
Until every seat is moved, the busy probe, the quiesce writer and
`lane-proof.sh pull` read `/run/smd-jobs` and, where a seat does not have it
yet, `/run/smd-medchron` (`operator/bin/lib/fleet/remote_busy_probe.py`).

The release controller steps bundle releases after skills, ring by ring with
the same bake, holds, halt and lease, but only on the seats whose
`customer.yaml` authors a job lane (`operator/hermes-smd-overlay/shared/capabilities.py`).
Other seats are not in the bundle rings at all: they are never touched and never
keep a release incomplete. A seat reads its pointer only at boot, so per seat
the controller waits for the seat to be idle (the image rollout's busy gate),
writes `vaults/<slug>/bundles/jobs-pointer.json`, restarts the Machine
(`fly machine restart`), and waits up to 15 minutes for a heartbeat reporting
`bundles.jobs` at the version, then, on an image that carries it, the boot
self-test passing. Anything else puts the previous pointer back (or removes it),
restarts the seat again behind the busy gate, and records it `rolled_back`
(which halts the release) or `tooling_error` (retried).

The tick's bundle step is off unless the repository variable
`BUNDLE_RELEASE_ENABLED` is `true`; off, it logs "bundle releases not enabled"
and rolls nothing, while CI still publishes candidates. To send one bundle to
one seat, run release-tick by hand with the seat and `artifact: bundle`
(`fleet release --bundle latest --only <seat>`): the same lease, busy gate,
read-back and rollback, with no ring order and no bake, whatever the switch
says, and only to a seat that authors a job lane. A client seat with a twin
takes a bundle only after its twin is done on that bundle, here as in the tick
(ADR 0107), so a bundle goes to ap-staging first, then ashton-price. `fleet status` shows
what each seat runs in its bundles column: a version, `image` when the image's
own copy runs, `-` when the seat reports none.

A seat's config reaches R2 by one writer, the deploy job's publisher
(`scripts/ci-publish-customer-configs.sh`, job `publish-customer-configs`). On
every push to main it compiles each seat's config into a **signed seat spec**
(`scripts/compile-seat-spec.ts`): one canonical description of what the seat is
configured to run (the `customer.yaml` digest, the lane-proofs seed, each lane's
firm-input manifest digest, the skills release) and what that requires of the
seat's image. It writes `vaults/<slug>/customer.yaml`, the spec at
`fleet/specs/<slug>/<sha256>.json`, and last the pointer
`vaults/<slug>/spec-pointer.json`, which carries an Ed25519 signature over the
spec's bytes. The signing key is a secret of the protected `seat-spec` GitHub
environment and of nothing else, read only when the repository variable
`SEAT_SPEC_ENABLED` is `true`; without it the publisher still publishes
`customer.yaml` and skips the spec with a warning. Seats whose `customer.yaml`
did not change in the push are converged too, so a push range that was never
processed is healed by the next one.

The compiler refuses a config the seat's running image cannot run: a skill,
pin or pre_run neither the image's manifest nor its skills release carries, or
a capability (path, tool, broker verb, action class) an enabled skill needs and
the image lacks. A refusal leaves the object as it is, because the seat
re-fetches `customer.yaml` on every boot and the next restart would adopt it
(the 2026-10-13 outage, ADR 0093). A lane whose image validates firm inputs
against a schema other than this checkout's refuses the spec only. A refusal
fails the job for a seat the push changed and is a warning for any other seat.

The nightly reconciler (`scripts/ci-reconcile-r2-customer-configs.sh`, run by
`.github/workflows/customer-config-reconcile.yml`) is read-only. It compares
every seat's `customer.yaml` bytes and signed spec with what main compiles and
pages on a mismatch: DRIFT and SPEC findings on a dated issue (SPEC only once
`SEAT_SPEC_ENABLED` is `true`, since before then no signed spec is expected) (the next push to
main converges them), REFUSED on one standing issue titled "R2 seat config
refused by the image guard" that each nightly run comments on.

Provisioning (`operator/bin/provision-customer.sh` step 2,
`operator/bin/lib/stage-seat-spec.sh`) uploads through the same compiler with
`--provisioning`: it records the config's requirements without checking them,
because it ships the image that carries them in the same run. It signs and
publishes the spec only when `SEAT_SPEC_SIGNING_KEY` is set; otherwise the next
push to main does.

A firm's lane inputs are not part of the spec's bytes: the spec names each
staged lane's manifest key and pins the schema module its inputs are validated
against, never the inputs themselves (ADR 0094, amendment 2026-11-05). They
publish on a merge to the engagements repo's main through
`operator/bin/lib/publish-firm-inputs.sh`, validated against the runner the
seat runs, and provisioning stages them through the same script
([Changing a Client's Operator](/admin/playbook/changing-a-clients-operator)).

On the seat, the root `config_applier` loop runs a spec verifier
(`config_applier/spec_verifier.py`) in **shadow mode**: it verifies the
pointer's signature against `/app/seat-spec.pub`, baked into the image, and the
spec's requirements against that image, writes its verdict to
`/run/smd-spec/state.json`, and the heartbeat carries it as `spec`. The seat
still applies `customer.yaml` exactly as before. The fleet-alerts worker pages
`spec_unverified` on a verdict that fails, or when a seat still holds an older
spec than the published pointer 15 minutes after it was written. A published
config reaches the seat on its next boot; fields the seat applies live are
picked up sooner by the ADR 0044 poller.

### Driving the shadow firm (a runtime change's `(runtime)` proof)

The shadow firm (`operator/rehearsal/`, runbook `docs/runbooks/operator/shadow-firm.md`)
replays every incident class this venture has had - an unaudited direct-API
send, cross-matter content, a fabricated matter number under failure, an
instruction injected through inbound mail, a dead connector mid-task, a
privileged instruction from an unauthored sender - against the rig seat, and
scores each from audit rows and mailbox observations rather than from how the
answers read. Once the rig runs the release, run
`infisical run --env=prod --path=/ss -- operator/rehearsal/run.py --seat pilot-smokeball --overlay-ref <candidate> --drive`,
where the candidate is the ss-console commit the release was built from (since
ADR 0095 that is the overlay version a seat reports).
The runner reads the rig's running overlay ref off the live seam and refuses to
drive unless it equals the candidate, and a ref it cannot read refuses too.
Cite the run id, with the report's evidence table, on the tracking issue or the
merged PR. `.stitch/shadow-firm/` is gitignored (the build-source guard
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
  `fleet releases` lists releases. The overlay ref column is the ss-console
  commit each seat's image was built from; image drift is how a seat behind
  the fleet shows (the separate `overlay-ref-drift.py` was removed with the
  fold, ADR 0095).
- **A new seat** (no Machine yet): `operator/bin/reprovision.sh <slug>` runs
  `infisical run --env=prod --path=/ss --silent -- operator/bin/fleet
  with-seat-lease <slug> -- operator/bin/provision-customer.sh <slug>` between
  a busy gate and a quiesce clear (below). Provisioning deploys the newest
  release that completed its last ring (`fleet latest-image`, chosen in
  `operator/bin/lib/select-release-image.sh`); `--build` builds the checkout
  from source instead, behind the same build-source guard, for a bring-up
  before any release exists. It ends by running the boot smoke test. The repo
  is the single source of truth for `customer.yaml`; provisioning projects it
  to R2 unconditionally. `yes s |` answers "skip" to every secret paste prompt
  (Machine secrets persist across deploy). A seat with no Machine yet reads
  as idle to the busy gate (`fleet busy` prints "no Machine yet"), so a first
  provision needs no `FLEET_FORCE_MID_JOB=1`; a Machine listing that fails is
  still a tooling error and still refuses.
- **Before a new seat's first provision**, five things must already exist; when
  they do not, they surface one failed run at a time (learned standing up a
  seat end to end on 2026-11-10):
  1. **The Fly app, authored.** `fly apps create hermes-<slug> --org personal`
     (the same command provisioning step 4 runs; it has to exist first so its
     deploy token can be minted), then a merged PR that sets `fly_app:` in
     `operator/customers/<slug>/seat.yaml` and drops `unprovisioned_reason`,
     with the registries regenerated (`npm run gen:seat-registries`:
     `operator/fleet/rings.yaml` and the generated files under
     `src/lib/operator/`; `tests/seat-inventory.test.ts` fails while one is
     stale).
  2. **The release tick can reach it.** The seat's option and its
     `FLY_DEPLOY_TOKEN__<SLUG>` line in `.github/workflows/release-tick.yml`,
     the token itself minted straight into the `fleet-release` environment
     (piped, never printed), and the Fly token count under "Turning it on"
     below updated. The conformance test fails until the workflow lines are
     there.
  3. **A first `customer_configs` row.** CI never creates one, and
     provisioning refuses without it (`provision-customer.sh`, the
     `machine_credentials` check). Emit it with `npx tsx
     scripts/project-customer-config.ts <slug> <entity_id> --actor=<email>`,
     then apply the file to remote D1 by hand (the script never writes
     remote; its header gives the command). See
     [Customer Lifecycle](/admin/playbook/customer-lifecycle).
  4. **For a seat that binds the AgentMail adapter:** its inbox, that inbox's
     webhook to `https://hermes-<slug>.fly.dev/webhooks/agentmail`, and in
     Infisical `/ss` prod the per-seat `AGENTMAIL_API_KEY__<SLUG>` and
     `AGENTMAIL_SEND_API_KEY__<SLUG>` (minted by
     `operator/bin/mint-agentmail-keys.py`, never pasted) and
     `WEBHOOK_SECRET_AGENTMAIL__<SLUG>`, the signing secret of that webhook.
     The webhook secret has no fallback: provisioning refuses without it
     (`operator/bin/lib/stage-agentmail.sh`).
  5. **A clone of the engagements repository**, at `~/dev/engagements` or
     wherever `SS_ENGAGEMENTS_DIR` points: provisioning refuses when it is
     missing (`operator/bin/lib/firm-config-currency.sh`). Run
     `reprovision.sh` from a clean checkout of current main; with `--build`,
     a dirty tree or one behind origin/main is refused
     (`operator/bin/lib/build-source-guard.sh`).
- **Reprovisioning an existing seat** re-applies that seat's config and secrets
  on the image its Machine runs now (`fleet seat-image <slug>`, read from Fly).
  It never moves the seat's software and never depends on any other seat:
  moving a seat to a newer image is `fleet release`. It runs the same busy gate
  first (`fleet busy <slug> --wait`), refuses a seat still busy
  (`FLEET_FORCE_MID_JOB=1` overrides), holds the seat's lease for the deploy
  (refused, exit 4, while a rollout is deploying that seat), and clears its
  quiesce marker afterwards (`fleet unquiesce <slug>`).
- **Flip secrets** on the website side if a release changes the per-customer
  seam keys.

### Provisioning ap-staging, the exact twin of ashton-price

The winning line: every change is rehearsed on an exact twin of the A&P seat
before A&P sees it. This is done: `ap-staging` has been ashton-price's release
twin since the flip (#3972), and `smd-staging`, the twin before it (not an
exact copy: no Smokeball connector, a different persona), was decommissioned
on 2026-11-11. The steps below are the record of how the twin was stood up,
kept because a new exact twin for a future client follows the same path; to
reprovision `ap-staging` itself run `operator/bin/reprovision-staging.sh
ap-staging`. Where a step names `smd-staging` it describes the state at the
time. `ap-staging` is the exact copy: `operator/customers/ap-staging/customer.yaml` equals A&P's except the paths
`operator/customers/ap-staging/twin.yaml` declares (an identity, a tenant, a
mailbox, a person, a vault, never a behaviour), held there by
`tests/twin-drift.test.ts`. It is authored `purpose: internal` with
`fly_app: null` because a client has at most one release twin (ADR 0107), and
`smd-staging` stays the twin until `ap-staging` is provisioned and green. Every
step below is an agent's work, done end to end the way `smd-guide` was stood up
on 2026-11-10, and names the probe that shows it held. The one act only the
Captain can do is step 8's sign-in to SMD's Smokeball staging tenant (an agent
never enters a password). Steps 3 to 7 write to Fly, D1 and the secret stores,
which an agent session in auto mode may be refused; a refused step is surfaced
to the Captain with the exact action, never worked around. Values
never pass through a terminal or a transcript: each copy below reads one
Infisical name into a file that `crane_secret_set` (source `file`) writes under
the new name and deletes.

1. **The firm inputs in engagements.** For each lane A&P runs, author
   `operator/customers/ap-staging/<lane>/` in `venturecrane/engagements`
   (medchron `firm.yaml`; demand, drafting, litigation, casefile and records
   `<lane>-firm.yaml` with what each pins), in A&P's shape with fictional
   content: pilot-smokeball's fictional exemplars where engagements has them,
   never an A&P document (Law 15). Merge to engagements main.
   Probe: `git -C ~/dev/engagements show origin/main:operator/customers/ap-staging/medchron/firm.yaml >/dev/null && echo ok`,
   once per lane. The medchron config is what the self-test count turns on: A&P
   runs 101 checks, smd-staging 92, and the 9 between them are the medchron
   firm-config, token, controls and gate checks `seat-checks.sh` runs only
   where `lane_authored medchron` holds. With it staged `ap-staging` runs A&P's
   101; without it, 92.
2. **The staging tenant's ids (the PENDING markers).** `ap-staging` binds
   SMD's Smokeball staging tenant, the one pilot-smokeball is connected to. Read,
   through pilot-smokeball's connection and with no write, the id of the
   matter numbered `OPS-OPERATOR-LIBRARY` (the twin keeps A&P's library number)
   and the id of the tenant's staff row, then replace the four nil-GUID matter
   ids and `vendor_invoice_intake.staff_id` in `ap-staging/customer.yaml` by PR.
   `operator_matter.client_contact_id` is already the tenant's SMD Operations
   contact pilot-smokeball authors.
   Probe: `npx tsx scripts/refuse-pending-config.ts --slug ap-staging --config operator/customers/ap-staging/customer.yaml`
   prints nothing about customer.yaml (it still names `fly_app: null` until
   step 4).
3. **The Fly app and its volume.** `fly apps create hermes-ap-staging --org
   personal` (a new app: Fly cannot rename `hermes-smd-staging`). The twin is
   sized like smd-staging, not A&P (2 shared CPUs, 4 GB, a 10 GB volume,
   declared in `twin.yaml`): it proves each release boots and passes its checks
   and never runs A&P's heavy jobs. Provisioning creates the 10 GB volume.
   Probe: `fly volumes list -a hermes-ap-staging` after step 7 shows one
   `hermes_state`, 10GB, sjc.
4. **The app, authored, and the release tick.** One PR: `fly_app:
   hermes-ap-staging` in `ap-staging/seat.yaml` with `unprovisioned_reason`
   removed (purpose stays `internal`, ring 2, so it takes releases after A&P
   until the flip), `npm run gen:seat-registries`, and the seat's option and
   `FLY_DEPLOY_TOKEN__AP_STAGING` line in `.github/workflows/release-tick.yml`
   (the name `seat_token_env_name` in `operator/bin/lib/fleet/fly.py` derives).
   Mint that deploy token straight into the `fleet-release` environment, piped,
   never printed (a new app's deploy token is necessarily new).
   Probe: `tests/seat-inventory.test.ts` and the release-tick conformance test
   pass in the PR's CI; `gh secret list --env fleet-release | grep FLY_DEPLOY_TOKEN__AP_STAGING`.
5. **The first `customer_configs` row.** `npx tsx scripts/project-customer-config.ts
   ap-staging <entity_id> --actor=<email>` with smd-staging's entity (both roll
   up to smd-services), applied to remote D1 by the command its header gives.
   Probe: `SELECT slug, entity_id FROM customer_configs WHERE slug IN ('ap-staging','smd-staging')`
   returns both rows on one entity.
6. **The per-seat Infisical names, values reused.** In `/ss` prod, each new
   NAME holding a value `/ss` already holds (no new keys):
   `MSGRAPH_CLIENT_SECRET__AP_STAGING`, `MSGRAPH_SEND_CLIENT_ID__AP_STAGING` and
   `MSGRAPH_SEND_CLIENT_SECRET__AP_STAGING` from the `__SMD_STAGING` names (the
   same lab apps and mailbox); `ANTHROPIC_API_KEY__AP_STAGING` from
   `ANTHROPIC_API_KEY` (no `__SMD_STAGING` key exists to copy; an Anthropic
   workspace of the twin's own would be a new key, the Captain's call).
   Deliberately NOT created: anything `__ASHTON_PRICE` copied, any
   `RECORDS_VENDOR_*__AP_STAGING` (a records vendor token is a real firm's
   account; the records-order tools then say the vendor is not connected,
   which is the twin's stated difference), and `SMOKEBALL_STAGING_*` (SMD's
   sandbox app, already in `/ss`, kept by the wrapper as the `sandbox` custody
   class).
   Probe: `crane_secret_check` on `/ss` prod for the four names reports them
   present, and the dry run `REPROVISION_STAGING_DRY_RUN=1 operator/bin/reprovision-staging.sh --pre-flip ap-staging`
   lists every `__ASHTON_PRICE` name, `SMOKEBALL_PROD_*`, and none of
   `SMOKEBALL_STAGING_*` or `ANTHROPIC_API_KEY__AP_STAGING`.
7. **Provision, pre-flip.** From a clean checkout of current main, with the
   engagements clone current: `operator/bin/reprovision-staging.sh --pre-flip ap-staging`.
   It refuses any seat that is not `internal` with a `twin.yaml` naming a client
   seat, blanks the declared client's customer-owned secrets and the global
   ones, keeps the twin's own per-seat names (the new app has no earlier
   Machine secret to fall back on), substitutes the isolated Google credential,
   and runs `provision-customer.sh ap-staging`, ending in the boot smoke.
   (`operator/bin/reprovision.sh ap-staging` refuses: it injects `/ss`
   un-isolated.)
   Probe: the boot smoke's last line, then `fleet status` shows `ap-staging`
   on the newest completed release with a fresh heartbeat and a self-test of
   101 checks, 0 failed (92 if step 1's medchron config is missing). The
   staff-voice spec alert (`spec_control_broken:staff.voice`) is expected
   until the twin's own voice is established, as it was on A&P.
8. **Smokeball consent, into SMD's staging tenant.** First the Captain adds
   `https://hermes-ap-staging.fly.dev/oauth/smokeball/callback` to the callback
   URLs on the Staging tab of the SMD Operator app in the Smokeball Developer
   Console (console.smokeball.com), and clicks Save, not Publish (Publish is a
   review submission). Without it Smokeball's sign-in page shows only "An error
   was encountered with the requested page" (observed 2026-11-10). Every new seat
   that binds Smokeball needs its own callback registered the same way. Then: `infisical run --env=prod
   --path=/ss -- bin/connect-smokeball.sh ap-staging` prints the authorize URL
   for the staging app (it reads `environment: staging`); sign in to SMD's
   staging tenant, never A&P's, and Allow.
   Probe: the next hourly `connector-auth-check` verdict on the seat is
   connected (SMD's work-health email), and a read-only matter list through the
   twin returns the staging tenant's matters, including `OPS-OPERATOR-LIBRARY`.
9. **Lane proofs on the twin.** `ap-staging/lane-proofs.yaml` starts empty
   (a proof is evidence from its own seat), so each lane's first job waits and
   pages SMD. Prove and accept each lane A&P runs, on the twin, with
   `operator/bin/lane-proof.sh ap-staging dry-run|accept <job id>`, and record
   them in `lane-proofs.yaml` by PR. Prove negotiation-watch first: its cron
   submits a job three times each weekday.
   Probe: `lane-proofs.yaml` names every lane A&P's does.
10. **The lab mailbox overlap.** Until step 12, `smd-staging` and `ap-staging`
    both poll `operator@smdopslab.onmicrosoft.com`, so a lab message is
    answered by both seats. Keep lab mail to the probes above in that window.
11. **The flip.** One PR: in `ap-staging/seat.yaml` `purpose: release-twin`,
    `twin_of: ashton-price`, the `ring:` line removed; in
    `smd-staging/seat.yaml` a purpose other than `release-twin`; the
    `smd-staging` entry removed from `STATED_GAPS` in `tests/twin-drift.test.ts`;
    `npm run gen:seat-registries`; the release-tick bundle order note
    updated. After it, `reprovision-staging.sh ap-staging` (no flag) is the
    reprovision path.
    Probe: `operator/fleet/rings.yaml` reads ring 0 `[ap-staging]` and
    `twins: ashton-price: ap-staging`; the next release's rollout rows show
    `ap-staging` done before `ashton-price` takes it.
12. **Decommission smd-staging** (done 2026-11-11). `infisical run --env=prod --path=/ss --
    operator/bin/decommission-customer.sh smd-staging` (a dry run: the survey
    of every layer `operator/bin/lib/decommission_inventory.py` names), then
    `--live --confirm-slug smd-staging`, then the repo edits its residue
    report prints (the tombstone rename and the registries). Gone means gone: a
    negative probe per runtime layer, each recorded with `crane_verify`:
    `fly apps list` has no `hermes-smd-staging`; R2 lists nothing under
    `vaults/smd-staging/` and no `ss-operator-smd-staging-skills` bucket;
    D1 `customer_configs`,
    `fleet_holds` and the other slug-keyed tables return no `smd-staging` row;
    `crane_secret_check` on `/ss` prod finds no `__SMD_STAGING` name
    (after the `MSGRAPH_*__SMD_STAGING` names are deleted; their values live on
    under `__AP_STAGING`); `gh secret list --env fleet-release` has no
    `FLY_DEPLOY_TOKEN__SMD_STAGING`; and `fleet status` and the release-tick
    workflow no longer name it.

## Never root-SSH a live Operator Machine

`flyctl ssh console` runs as root. Running any overlay code there writes
root-owned `__pycache__/*.pyc` files that the hermes-user bootstrap then cannot
remove; that crash-looped a Machine's boot in the ss-console#1285 outage.
`operator/templates/bootstrap.sh` now moves such directories aside instead of
deleting them (`replace_dir_tolerant`), so that one failure no longer stops a
boot, but a root session on a live seat is still the wrong tool. Read a live Machine's state through the runtime read
seam over HTTPS, and test a change by deploying it - never by hand-poking the live
box. The `fleet` busy probe and quiesce writer are the sanctioned exceptions:
each is a stdlib-only program fed to the image's system `python3 -I -` on
stdin (no overlay import, no `__pycache__` written), the probe only reads, and
the writer touches only the root-owned `/run/smd-jobs/quiesce` marker. A handler also cannot prove its own non-execution by inspection; verify
against the running Machine.

## What the verify step checks

The checks themselves live in one list, `operator/templates/seat-checks.sh`,
which two transports run: the boot smoke test over ssh from a laptop, and the
seat's own self-test inside the image (`operator/templates/selftest.sh`, below).
A change to what a seat must pass is an edit to that list, and both get it.

The boot smoke test (`operator/bin/boot-smoke-test.sh`) is the verify gate for
both a fleet release and a provision. It is a smoke test, not an end-to-end
test: it proves the seat's startup chain came up, and makes no real agent turn,
LLM call or connector write. Its checks, by the step numbers in the script: the
Machine reached `started` and is the size `customer.yaml` authored (1, 1b);
`customer.yaml` is present, root-owned and agent-read-only (5); profiles exist,
with no unauthored profile homes and no retired or omitted repo skills on the
volume, and staged scripts are current (6 to 6e); the overlay plugins are
installed and the running Hermes is the pinned Hermes (7, 7b); the curator and
customer-disabled skills are off, and the matter-mixing read fence
discriminates (8 to 8c); the audit ledger is broker-owned and not
agent-writable, agent state stays writable, and the broker and gateway are
supervised with a beating loop and a watchdog budget sized to the seat (9 to
11d); governance files are root-owned, the account-wide R2 key is stripped from
the live agent, and the identifier-gate rollback lever is not set (12 to 13b);
the chronology runner is present, idle, capped and fenced, and its registered
gates refuse planted violations (14, 14b). Steps 2 to 4 (Postgres, Redis,
Honcho) are deferred with ADR 0016.

Pass means every check passed. A failed check is recorded and the run
continues, so the summary lists every failure together (ss#2487), and the exit
code is non-zero if any failed. A warning (the first is the identifier-gate
lever) is listed but never fails the run. Only a failed precondition (the
Machine never started, or the pin cannot be parsed) stops the run early. The
fleet reads that output into the four classes above
(`operator/bin/lib/fleet/smoke.py`), and only `fail` rolls a seat back.

Two expectation sources differ by transport, and the list asks each transport
for them rather than knowing either. Whether a seat authors a runner lane
(medchron, demand, drafting, litigation, casefile): over ssh, the engagements checkout,
as provisioning reads it; in the image, whether the live `customer.yaml`
enables the lane's skill. The Hermes pin the running Hermes must equal: over
ssh, the release's pin or this checkout's Dockerfile; in the image, the pin the
image was built at (`SMD_HERMES_UPSTREAM_SHA`), and the fleet then compares the
self-test's reported Hermes with the release's. The retired-skill list is read
on the Machine (`/app/retired-skills.txt`) by both, and an empty list fails.

### The in-image self-test

Every seat image from this change on runs the list itself, as root:
`entrypoint.sh` forks `/app/selftest.sh --supervise` (after the credential
strips, under `env -i`, at low priority), which runs once the gateway
supervisor arms (a boot run) and then hourly (hourly runs). Results go to
`/run/smd-selftest/results.json` (0644, written by rename), and the gate ships
the verdict on every heartbeat as `selftest`, stored in `fleet_status.selftest_json`
(migration 0137). Two classes are judged by run kind: a lane-is-idle check and
a check that asks a vendor (AgentMail, Microsoft Graph) FAIL at boot and WARN
hourly, because a job may be running and vendor weather must not page every
seat every hour. A results file older than three hours, or none on a Machine up
longer than that, ships as the failed check `selftest-loop-dead`; the
supervisor restarts a crashed loop with backoff and records
`selftest-crashlooping` as a warning. The fleet-alerts worker pages
`selftest_failing` from the verdict (Work Health).

### The self-test as the verifier

`fleet release <id> --verify selftest` verifies each seat from its heartbeat:
first the two things only the fleet can see (the Machine started, on the guest
size `customer.yaml` authors), then up to 20 minutes for a self-test verdict
that is the RELEASE's run: finished after the deploy, on the release image and
the release's code version, so a late green row from the previous image can
never pass. A failed check or the wrong Hermes is a `fail` and rolls the seat
back. No verdict in the window falls back to the boot smoke, which then decides,
and both are recorded in `fleet_rollouts.detail`. An image whose
`image-capabilities.json` lacks `selftest` is always verified by the boot
smoke, and a rollback's re-smoke always uses the boot smoke.

Under `--verify bash` the boot smoke decides, and the self-test's verdict as of
that moment is appended to the detail (`selftest: pass ...`,
`selftest: fail ...`, or `selftest: not yet`), at no extra wait. That was the
default until 2026-10-26. The cross-check release
(`img-202610232220-4118de5f`) ran it across every ring: the two verdicts agreed
on every seat but the pilot, where the self-test was right (it offered demand
letters with no demand inputs, and the boot smoke had skipped the check). Both
paths now judge lanes the same way (#3448), so the default is `selftest`.

`fleet selftest --plant retired-skills-absent smd-staging` re-proves the alert
path after any change to the self-test: it plants one real violation as root,
asks the seat's loop for two runs (through its root-only trigger), waits for
`selftest_failing` to open, clears the violation (always, even after a timeout),
and waits for the alert to resolve. It refuses any seat but a release twin (`seat.yaml` `purpose: release-twin`, ADR 0107; `ap-staging` today).

## Build commands reference

The npm scripts that bear on a deploy (full list in `package.json`):

- `npm run build` - the production Astro build (`dist/client` + `dist/server`).
- `npm run preview` - a local Worker preview via `wrangler dev`.
- `npm run verify` - the full pre-merge gate, run by CI (the pre-push hook runs only the scrub and secret-detector tests); see
  `/admin/playbook/building-the-platform` for exactly what it runs.
- `npm run db:migrate:local` - apply D1 migrations against the local database.
