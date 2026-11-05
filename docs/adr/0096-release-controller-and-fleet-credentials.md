---
title: The release controller and its fleet credentials
date: 2026-10-23
status: accepted
captain: Scott Durgan
supersedes: the "CI holds no Fly token" clause of the 2026-10-19 amendment to 0024-hermes-consumption-and-update-cadence.md
amends: 0024-hermes-consumption-and-update-cadence.md
related-adr: docs/adr/0024-hermes-consumption-and-update-cadence.md, docs/adr/0054-machine-hosted-oauth-callback.md, docs/adr/0095-one-repository-for-the-seat-runtime.md, docs/adr/0007-per-customer-machine-isolation.md
enforced_by:
  - .github/workflows/release-tick.yml
  - .github/workflows/deploy.yml
  - operator/bin/lib/fleet/tick.py
  - operator/bin/lib/fleet/skills.py
  - operator/bin/lib/fleet/fly.py
  - operator/bin/lib/fleet/ledger.py
  - operator/bin/tests/test_fleet_tick.py
  - operator/bin/tests/test_fleet_skills_tick.py
  - operator/bin/tests/test_fleet_release.py
  - operator/bin/tests/test_ci_coverage_conformance.py
  - scripts/lib/seat-registries.ts
---

# ADR 0096: The release controller and its fleet credentials

## Context

Since 2026-10-19 a seat image is built once and rolled to every seat in rings
by `operator/bin/fleet` (ADR 0024, amended). The roll was one long blocking run
on a laptop: it held a Fly login, ssh and the private engagements checkout, it
read holds once per run, it had no wait between rings, and a rerun after a halt
deployed again to seats already done. Every release therefore needed a person
at a laptop for the length of the rollout. At six seats that is an afternoon; at
tens of seats it is the bottleneck on everything the Operator ships.

Two Wave 1 pieces removed what tied the roll to a laptop. The seat runs its
own self-test in the image and reports the verdict on its heartbeat (Wave 1,
live on five seats, vfy_01M5NGDSEQ5A8SMWC96RSE0Z3C), so the fleet no longer
needs ssh and the engagements checkout to judge a seat. The overlay lives in
this repo (ADR 0095), so the image a merge produces is the merge's own commit.

What remained was where the controller runs and what credential it holds. The
2026-10-19 amendment to ADR 0024 says "CI holds no Fly token", with no reason
given. ADR 0054 does give a reason, for a different surface: an org-wide Fly
credential on a public surface (the `ss-web` Worker) turns a compromise of that
surface into control of every client's Machine, the cross-customer chokepoint
ADR 0007 exists to prevent.

## Decision

1. **The release controller runs in GitHub Actions.** `deploy.yml` job
   `build-seat-image` runs `fleet build` at the merged commit, only when the
   tree differs from the newest recorded image release under a path the image
   can carry (`.github/operator-substrate-paths.txt` minus its `.github/`
   lines). It diffs against the last BUILT commit, not the push range, so a
   substrate merge is not lost when a docs merge lands before its build runs.
   The build-source guard's refusal of a commit main has already moved past
   (exit 4, `git rev-list HEAD..origin/main` non-empty) is a clean skip: the
   newer merge's own run builds the newer tree.
   `release-tick.yml` runs `fleet release latest --tick` every 15 minutes (at
   7, 22, 37 and 52 past the hour), when the Deploy workflow completes, and on
   dispatch, in one concurrency group that never cancels a running tick.

   **Amended 2026-11-04: the tick starts on publish, and seat artifacts wait
   only for the migrations.** The tick used to start when the whole Deploy run
   completed, so a skill edit waited behind the Cloudflare web build and every
   other job, the image build included. Now Deploy's D1
   migrations are their own first job, `migrate`, and `publish-skills`,
   `build-seat-image` and `publish-bundle` need it alone, never the web
   `deploy` job (the two config projections moved to it too: they need D1 and
   R2, not the Worker). `publish-skills` starts a tick (`gh workflow run
release-tick.yml`) the moment its candidate is published, and
   `build-seat-image` does the same after a build. The cron is every 5 minutes,
   as the backstop; the trigger on Deploy's completion was removed on 2026-11-05
   (it only replaced pending runs and left cancelled ones behind). Every dispatch
   is skipped while a tick is already waiting (`queued`, or `pending` on the
   concurrency lane): that lane keeps one running and one pending run, and a
   further dispatch would only replace the pending one and leave a cancelled
   run behind.

2. **A tick is resumable and advances at most one ring**
   (`operator/bin/lib/fleet/tick.py`). It takes the fleet lease (another holder
   means wait, exit 5), reads each seat's latest rollout of this release, and:
   a seat whose latest is `rolled_back` or `failed` is a standing halt (exit 6)
   until a person acts (amended 2026-10-30: a `tooling_error` stalls only its
   own seat, which is retried, and no longer holds later rings back); otherwise it rolls the remaining
   seats of the first ring that has any, but only after the ring before it has
   baked. A seat already `done` on the release is never redeployed, in a tick
   or in a laptop rerun; `--only` still redeploys on request. Holds are re-read
   before every seat, in every mode, so a hold set mid-release stops the next
   seat it covers.

   **Amended 2026-11-05: a halt that was only a lane-input refusal clears
   itself.** A `failed` row whose detail is nothing but lane refusals (the
   preflight's "the lane's firm-input schema changed; re-stage") deployed
   nothing, so on every tick the controller asks whether the declarations now
   on main (`operator/fleet/lane-inputs-accepted.yaml`) cover every refused
   lane for that release and that seat's current image
   (`preflight.refused_lanes`, `PreflightChecker.refusal_now_declared`, the
   `retriable` hook of `plan_tick`). When they do, the seat is pending again
   and its ring rolls it, the preflight passing with the declaration's note.
   Any other failure, and a rollback, stand for a person exactly as before.
   Why: on 2026-11-05 #3774 moved every lane's digest by one import line, a
   hand `--only` release marked the client seat `failed`, and the declaration
   merged eleven minutes later (#3775) could not move the halted release;
   the tick said "a person must act" for a call a person had already made
   and merged. The `--accept-lane-inputs` flag is still never passed by a tick.

   **Amended 2026-10-26: promotion across releases in flight.** As first built,
   `latest` meant only the newest release, so every merge restarted the
   controller at ring 0; five merges in four hours left the pilot three hours
   stale and the client seat untouched by the controller. A `latest` tick now
   weighs every image release still in flight, newest first, and each ring
   takes the newest release that has baked on the ring before it. A seat done
   on a newer release counts as done for an older one, so promotion never moves
   a seat backwards. A halt on the newest release still stops everything; a
   halt on an older, superseded one only skips that release. An older release
   gives way when a newer one is already done on the ring before and so is at
   most one bake away, which spares the ring a second restart minutes later; it
   gives way for at most one bake, so a run of merges cannot starve it. A tick
   moves every ring that has a release ready (one release per ring), not only
   the first: with one ring per tick the newest release, ready for ring 0 after
   every merge, took every tick while the client ring waited.

   **Amended 2026-10-28: the controller rolls skills releases too.** CI published
   a candidate skills release on every merge touching `operator/skills/**`, and
   only a person running `fleet release --skills <version>` ever rolled one: five
   candidates published after `skl-202610272001-04e1b619` reached no seat, and
   `fleet status` showed `drift [skills]`. A `latest` tick now steps the skills
   releases after the image releases, with the same promotion, bake, holds,
   standing halt and lease; a skills seat is a pointer write and a heartbeat
   read-back, with no restart and no busy gate. One rule is added: a seat takes
   a skills release only once it runs an image built from the skills' commit or
   a later commit on main (`deferrals` in `operator/bin/lib/fleet/skills.py`).
   The capability gate checks only what a skill names, not a changed argument or
   behaviour, and every skills merge also builds an image, so skills follow the
   image ring by ring. A seat whose image is older, or not built from main, is
   deferred: not pending, never halted, and the release stays incomplete. A seat
   whose heartbeat reports a newer skills version is deferred too, so a seat
   pointed by hand at something newer is never moved back. An image step that
   refuses or errors does not stop the skills step; the run reports the image
   error after it. A tick for a named image release steps that image only.

   **Amended 2026-11-04: a run that advanced a ring starts the next run.** A
   `--tick` run that exits 0 (progress) dispatches the next run itself
   (`release-tick.yml`, step `Start the next ring`), with the same
   queued-or-pending guard as Deploy's dispatches; a run that waited (5),
   halted, was a dry run or was an urgent one-seat run starts nothing. Corrected
   2026-11-05 after the first measured run: one run already advances EVERY ring
   that is ready (with no bake, staging then ashton-price then the rest, one
   release per ring), so the chain matters when a run ENDS with a release still
   in flight, which is now the designed case: a `latest` run re-reads the
   releases after each ring and, when a newer image has been published, ends
   there with progress so the run it starts takes the newer release from ring 0. Before this, run 41958879617 carried a76ce77a through all six seats
   (03:02 to 03:35Z) while 6e384840's image, published at 03:08:58Z, waited for
   the lane; staging took it at 03:42Z.

   **Amended 2026-11-05: the skills and bundle steps hand off too.** The
   hand-off above was the image step's alone. Run 41982023842 handed its image
   off correctly, then carried the jobs bundle `bnd-1991f54c` through every
   ring (06:57 to 07:22Z, busy-gate waits included) while `6db0f014`'s image,
   built 07:05:11Z, waited 18 minutes for the lane. Now every step of a
   `latest` run asks, between waves and after each ring, whether an image
   newer than the newest at its start has been recorded
   (`artifact_tick.newer_image_since`, `run_promoting` `yield_when`); when one
   has, the step ends with progress and the run it starts takes that image
   from ring 0. Skills and bundles follow it on that run's later steps.

   **Amended 2026-11-05: client rings roll in waves.** Designed for 10 to 20
   paying seats: ring 0 is staging and the last ring is SMD's own seats, one at
   a time; every ring between is a client ring and rolls its seats in waves of
   `max_parallel` (five, `scripts/lib/seat-registries.ts` CLIENT_RING_PARALLEL,
   written into rings.yaml). Twenty seats serially at four to ten minutes each
   is a two-hour release; in waves of five it is four waves. Staging's self-test
   has already passed the image, every seat still judges itself and rolls itself
   back, and a failure halts the rings after it, so a wave is the blast radius.
   Between waves the run asks whether a newer image has landed and, if so, leaves
   the rest of the ring for the run it starts.

3. **The bake.** Ring N+1 starts only when every unheld seat of ring N is
   `done` on the release, at least one is, the newest of their `finished_at`
   is at least that ring's `bake_minutes` old, and no `selftest_failing`,
   `image_drift` or `rollout_failed` alarm is open on those seats.
   `bake_minutes` defaults to 10 (30 until 2026-10-26; at six seats the wait cost more than it caught); a seat may author `bake_minutes` in its
   `seat.yaml`, and a ring bakes for the longest value any of its seats
   authors, rendered into `operator/fleet/rings.yaml` by
   `scripts/gen-seat-registries.ts`. (`rollout_failed` is event-shaped and
   never `open`; the halt it reports is read from the rollout rows themselves,
   which is the standing-halt rule above.) A fully held ring holds every ring
   after it: nothing has proved the release there.

4. **The seat's self-test decides; there is no ssh boot smoke in a tick.** A
   tick verifies with `--verify selftest` and no fallback: a self-test that
   gives no verdict in its window is a `tooling_error`, which stalls that seat
   and pages through the existing `rollout_failed` pager once it repeats, and a rollback is re-verified by
   the self-test the rollback image runs at boot. A release whose image does
   not carry the self-test is refused by a tick. The laptop path keeps its
   current behaviour (`--verify bash` by default, with the fallback), and
   `--no-ssh-fallback` gives a person the tick's verification. The busy gate's
   ssh probe is not the boot smoke and still runs in a tick: it is what keeps a
   running client job from being restarted, and it runs on the seat's own
   token (an app-scoped token can `fly ssh console`, as
   `ops-workspace-sweep.yml` has done since 2026-10-17).

5. **Credentials: two app-scoped macaroons per call, never an org token.** The
   protected GitHub environment `fleet-release` (deployable from `main` only)
   holds one Fly deploy token per seat app, `FLY_DEPLOY_TOKEN__<SLUG>` (the slug
   upper-cased, `-` to `_`), and one for the registry app,
   `FLY_REGISTRY_TOKEN`. The fleet CLI puts the right credential on each call
   (`FlyTokens` in `operator/bin/lib/fleet/fly.py`): a call against
   `hermes-fleet` (the build and push, the artifact read) carries the registry
   token alone; a call against `hermes-<slug>` carries the seat token and the
   registry token comma-joined in one `FLY_API_TOKEN`, which flyctl accepts.
   The pair is the measured minimum. Probe F1 (2026-10-23, on `smd-staging`):
   the seat token alone cannot deploy `registry.fly.io/hermes-fleet:<label>`
   ("Not authorized to access this imageref"); the seat token plus the
   registry token deploys it; and that pair is still refused on another seat
   app, so a leaked pair reaches one seat and the registry, not the fleet. In a
   GitHub run a missing token ends that seat as a `tooling_error` naming the
   variable; it never falls back to another login. No token value is ever in an
   argv, a log line or an error.

   This supersedes the ADR 0024 amendment's "CI holds no Fly token", which
   carried no reasoning. It keeps ADR 0054's rule, which carried the real one:
   there is still no org-wide Fly credential anywhere, and nothing on a public
   surface holds a Fly credential at all. The environment is readable only by
   workflows on `main`, and `main` changes only through the merge queue's
   required checks.

6. **Client seats have no human approval step.** The client ring is protected
   by the bake after the ring before it, by the busy gate (a running client job
   makes the seat wait, `waiting_idle`, and a later tick picks it up), and by
   holds, which any session can set and every tick re-reads per seat. A tick
   never passes `--force-mid-job` and never passes `--accept-lane-inputs`: a
   lane schema change is refused at preflight, the tick halts and pages, and a
   person re-stages. `operator/bin/tests/test_ci_coverage_conformance.py` pins
   both. A required approval would put a person back on every release, which is
   the cost this record removes, and would be satisfied by whoever was awake
   rather than by evidence. _Amended 2026-11-04:_ the person's call can also be
   merged once, in `operator/fleet/lane-inputs-accepted.yaml` (the commit the
   schema modules changed at, the lanes, who, why); the preflight reads it from
   the checkout, and a release built at or after that commit passes a seat
   whose image is from before it with a note. Added when ADR 0103 renamed the
   runner's package and every lane's digest changed in name only; the tick
   still passes no flag, and a schema that accepts different inputs is still
   re-staged. _Amended 2026-11-05:_ the digest now only says when to look. A
   lane whose module digest differs is put to the release's own validator:
   the seat's staged marker, read from the vault, through `validate()` from
   the runner tree at the release's commit
   (`operator/bin/lib/fleet/lane_validate.py`). It passes with a note when
   the inputs fit, is refused when they do not, and falls back to the refusal
   above when the question cannot be asked. A second harmless edit (#3774's
   one import line) had refused every seat the day after the first; a
   declaration is now for the case the validator cannot judge, which is a
   change to what a pinned file means.

7. **Every judged seat writes a verify-ledger row.** After a seat ends `done`,
   `rolled_back`, `failed` or `tooling_error`, the controller POSTs a
   `live_state` row to crane-context's verify ledger (`POST /verify`, the
   relay key CI already holds as `CRANE_RELAY_KEY`), whose `command` re-reads
   the seat's heartbeat row. The id lands in `fleet_rollouts.detail`; a write
   that fails is a note there (`verify row: not written (...)`) and never fails
   the release. So a release is proven by the pipeline, not by a person
   remembering to record it.

8. **Off until its credentials are in place.** Both jobs that enter the
   `fleet-release` environment (`tick` in `release-tick.yml`, and
   `build-seat-image` in `deploy.yml`) run only when the repository variable
   `FLEET_RELEASE_ENABLED` is `true`. The gate is the job's own `if:`, which
   GitHub evaluates before it enters the environment, so a disabled run never
   touches the environment and never makes GitHub auto-create it without its
   branch policy; a `disabled` job in `release-tick.yml` says why the run did
   nothing. The variable is set only after the environment exists with its
   main-only deployment branch policy and all seven Fly tokens (six seats and
   the registry), and setting it is followed at once by a dry-run dispatch of
   `release-tick.yml` (`dry_run: true`) whose log shows the plan it would take.
   The conformance test pins the gate on both jobs.

9. **The laptop path stays, as the manual fallback.** `fleet release <id>`,
   `--only`, `fleet hold`, `fleet build` all work as before on a laptop's own
   Fly login. They are how a person finishes a halted release, fixes one seat,
   or ships a hotfix lane; they share the leases with the controller, so the two
   never deploy one seat at once.

## Consequences

- A merge that changes the image is on staging within one tick of its build,
  and on the client seat after three bakes, with no laptop.
- The controller's blast radius is the set of per-app tokens in one protected
  environment, rotated per app. Adding a seat adds one token and one env line in
  `release-tick.yml`; the conformance test fails until both exist.
- Not proven by this record: that the registry token alone can run the remote
  builder and list apps for `fleet build` in CI. The first CI build is that
  proof; until it runs green, `fleet build` from a laptop remains the path.
