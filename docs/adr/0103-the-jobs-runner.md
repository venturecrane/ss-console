---
title: The jobs runner
date: 2026-11-04
status: accepted
captain: Scott Durgan
related-adr: docs/adr/0100-one-operator-core-and-authored-capabilities.md, docs/adr/0050-operator-task-execution-framework.md, docs/adr/0102-matter-files-on-a-staff-reply.md, docs/adr/0024-hermes-consumption-and-update-cadence.md
enforced_by:
  - operator/runners/jobs/tests/test_runner_core_imports_no_lane.py
  - operator/runners/jobs/tests/test_daemon_drain.py
  - operator/hermes-smd-overlay/tests/test_bundle_installer.py
  - operator/bin/tests/test_fleet_bundles.py
  - operator/templates/tests/test_selftest.py
  - tests/operator-entrypoint.test.ts
  - tests/jobs-volume-old-path-allowlist.test.ts
---

# ADR 0103: The jobs runner

- **Status:** Accepted (2026-11-04)
- **Decider:** Captain (the ask: the runner is named for what it is, and designed as a job runner in general, not for the job it was first built to run); mechanism by the agent
- **Amends:** ADR 0100 (the jobs bundle's layout and the installer's rule), ADR 0024 item 5 (the quiesce file's run dir, `/run/smd-jobs/quiesce` from R2), ADR 0102 amendment 4 (the page index's path, `/opt/data/jobs/page-index` from R2)

## Context

Every Operator job lane (demand, drafting, litigation status, case file, negotiation, note, records and the medical chronology) runs under one root daemon on the seat. That daemon, its package, its command line, its venv, its uid, its env contract, its volume directory and its overlay plugin were all named `medchron`: the package was born on 2026-08-29 as the chronology runner, the drafting lane reused its daemon on 2026-10-07, and five more lanes followed. Nobody renamed it at the second lane. Worse than the name, the chronology lane was not a lane module: it was the daemon's base class, with the chronology's job envelope, firm config, coverage and allowance logic inside the core every other lane inherited, and the runner's model client imported the chronology firm config.

On 2026-11-04, reading the post-mortem of the 11-02 records request, the Captain asked what "the medchron runner tests" had to do with the records lane, learned that they were the jobs runner's tests, and ordered the naming fixed before anything else: "we should have realized we needed a job runner when we first built the medchron runner, named it accordingly, and designed it as a job runner in general vs building it only to run a specific job."

## Decision

1. **The runner is `jobs`.** Package `jobs` at `operator/runners/jobs/`, command `jobs`, daemon `jobs.daemon`, venv `/opt/jobs`, uid and group `jobs` (ids unchanged: 10002 and 995, so nothing on any volume changes owner), env contract `JOBS_*` (the runner's) and `SMD_JOBS_*`, overlay plugin `hermes-smd-lanes` (every lane's submit tools), broker client `JobsBrokerClient`, self-test ids `jobs-*`, requirements `operator/requirements/jobs.txt`. `medchron` names only the medical chronology product: its lane key, its subpackage `jobs/medchron/`, its firm config `medchron-firm.yaml`, its broker verbs and tables, its audit action types, its D1 `job_lane` value, and its admin page. A reader of a seat, a log, a heartbeat, a command line or this repository sees one name for the runner.

2. **The runner core imports no lane.** `jobs.daemon`, `jobs.lanes`, `jobs.child`, `jobs.llm`, `jobs.budget`, `jobs.ledger`, `jobs.limits` and the proof machinery import nothing from `jobs.medchron` or any lane package; the chronology lane is `jobs/medchron/lane.py`, a `Daemon` subclass like the other seven (demand, drafting, litigation status, negotiation, note, case file, records), and `jobs.lanes.LANES` lists all eight. The model client takes the fields it needs through a small protocol (`jobs/firm_levers.py`) rather than the chronology's firm config. `test_runner_core_imports_no_lane.py` fails the build if the core grows a lane import again. The daemon's `main` starts every lane the same way and keeps a main-thread supervisor loop that only joins, so signal handling and the stop exit stay where the process can receive them; `test_daemon_drain.py` sends SIGTERM to a daemon with every lane started and asserts each drains within its deadline.

3. **A seat never runs an image with a bundle of another layout.** The jobs bundle's tarball layout is numbered (`BundleSpec.layout`, 2 from this change: root `jobs/` mounted at `/opt/jobs`), the number is written into each bundle's manifest, and CI publishes `fleet/bundles/jobs/layout-<n>/latest.json` beside the seat pointers. At boot the installer compares the seat pointer's layout with the image's; on a mismatch it installs the latest bundle of the image's layout instead of the pointer, otherwise it behaves as ADR 0100 says, so the release tick's pointer rollback keeps working. The tick's bundle step writes a seat's pointer without a restart when the seat already reports that version. Before this rule a new image on an old pointer booted with no runner at all (since ADR 0100 W5 the image carries none), failed its self-test, was rolled back, and the bundle stayed deferred for ever: image and bundle could not change names together.

4. **The rename ships as two releases.** R1 (this change) renames everything in code and keeps the volume directory and the run dir at their old values (`JOBS_DATA_DIR=/opt/data/medchron`, `JOBS_RUN_DIR=/run/smd-medchron`), so nothing on a volume changes and the release stays forward-compatible: a failing seat rolls back on its own. R2 is the volume move alone, built not-forward-compatible behind holds: one `mv /opt/data/medchron /opt/data/jobs` at boot (same filesystem), `/run/smd-jobs` bound to it, and `/run/smd-medchron` bound to the same tree for one release so a job held before the move finds the paths its envelope stored; no symlink is left in `/opt/data`, which the agent uid owns; the boot chown sweep prunes both names; a seat moves only with zero held and zero queued jobs, probed as root. The chronology lane's own directories under the run dir keep their unprefixed names `queue/` and `jobs/` until a later volume release, because held chronology jobs store those paths.

5. **The second instance of anything is the moment to name and design the general thing.** When a second lane, a second connector, a second seat type or a second client reuses a thing built for the first, the reuse is a design decision, taken then: the general name, the general shape, and a test that keeps the specific out of the general. "It works, we will rename it later" is how this runner carried the wrong name for two months and the wrong structure into every lane.

## Consequences

- One name on every surface; the chronology is one lane of eight, structurally and by name. The chronology runs on a lane thread like the others; `main` holds only the supervisor, so a daemon whose every lane thread ends without a stop now exits for the entrypoint's respawn instead of blocking forever.
- The layout rule removes a class of failed release: anything that renames what the image and the bundle share.
- Two releases instead of one: an extra CI cycle, bought for automatic rollback on the 650-file change.
- Open after R2: renaming the chronology lane's `queue/` and `jobs/` directories to `medchron-queue/` and `medchron-jobs/`, which needs a held-job path rewrite; and the broker's `medchron_*` module names, which are the lane's and stay.

## Verification

R1: the unit suites, the import-graph test, the drain test, the installer layout tests, the self-test id parity, then the release tick to smd-staging (read back: daemon as `jobs`, `/opt/jobs` mounted, `jobs-*` checks green, a chronology and a records lane-proof dry-run claimed by the renamed daemon), pilot-smokeball (a lane-proof to delivery of its proof row), then the client seat behind the busy gate with every count unchanged. R2: zero held and queued jobs per seat, staging first, counts equal to the pre-move probe, both run dirs bound, a held fixture job resumes. Each read-back is a `crane_verify` row cited in the release PR.

## Amendment (2026-11-06): R2 as shipped

R2 was rebuilt on the tree of 2026-11-06 and ships with decision 4 unchanged and five additions.

1. **An old tree of directories only is removed, not refused.** When both names exist, the boot counts every entry under `/opt/data/medchron` that is not a directory. Zero means an older image booted after the move and made its directories without writing into them: the old tree is removed with `rmdir`, which cannot delete a file, and the boot goes on. Anything else is two trees: no job lane boots, one `JOBS VOLUME REFUSED` line is logged and the self-test fails until a root hand leaves exactly one. The trees are still never merged.
2. **The move is counted, and the count is checked for as long as the seat lives.** The boot that moves the tree writes the regular-file count before and after the rename to `/opt/data/jobs/volume-epoch.state` (a seat with nothing to move records that once; a second move after a hand rollback records again). `seat-checks.sh` fails the self-test, at boot and every hour, unless `/opt/data/jobs` is a real directory, `/opt/data/medchron` holds no file, both run dirs are bind mounts of that tree, and the two counts are equal.
3. **The old path cannot come back unnoticed.** `tests/jobs-volume-old-path-allowlist.test.ts` fails when a tracked file outside a short, reasoned list names `/opt/data/medchron` or `/run/smd-medchron`, and when a listed file no longer does. The list is what removing the one-release bind must empty.
4. **CI decides not-forward-compatible against the last built commit.** `deploy.yml` builds an image `--not-volume-forward-compatible` when `operator/fleet/volume-epoch` differs from the file at the commit the last image was built from (no file is epoch 1; no readable last build is treated as a difference). The merge's own parent is the wrong base: a merge whose build is skipped because main moved is built by a later merge.
5. **The fleet's seat-side tools read the run dir the seat has.** The busy probe, the quiesce writer and `lane-proof.sh pull` ship from the newest tooling to every seat, including the seat a rollout is about to move and every held seat until its turn. They use `/run/smd-jobs` and, only when it is absent, `/run/smd-medchron`. Reading the new name alone on a seat not yet moved finds no lane and no running job, and the gate that exists to stop a restart mid-job would call that seat idle.

The rollback sentence of decision 4 is narrower than it read. The R1 image prunes both names from its chown sweep, so it does not hand the moved tree to the agent; what it does is make a fresh, empty `/opt/data/medchron` and run every lane on it. That is why the rollback stays a hand's work: the tree is renamed back before the old image boots.
