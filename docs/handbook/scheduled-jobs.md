---
title: Scheduled Jobs
section: engineering
order: 16
owner: engineering
review_every: 90
last_reviewed: "2026-10-21"
summary: Every job that runs on a schedule, wherever it runs, generated from the tree so it cannot fall out of date.
sources:
  - label: GitHub workflows
    href: .github/workflows
  - label: Worker cron triggers
    href: workers/
  - label: Ops machine crontab
    href: operator/ops-machine/crontab
  - label: The generator
    href: scripts/lib/handbook-inventories.ts
---

Everything that runs on its own clock, in three places: GitHub Actions workflows, Cloudflare Worker cron triggers, and the crontab on SMD's ops machine (`smd-ops`). What each one tells a person, and who acts, is on [The Administrator's Week](/admin/playbook/administrators-week); this page is the complete list behind it.

Each schedule is a cron expression read in the time zone shown: GitHub and Cloudflare run in UTC, the ops machine in Phoenix time (UTC-7 all year). Each client seat also runs its own scheduled routines, authored per client in its `customer.yaml`; those are not listed here ([Clients & Commitments](/admin/playbook/clients-and-commitments)).

<!-- BEGIN GENERATED: scheduled jobs. Regenerate with `npm run handbook:inventories -- --write`; tests/handbook-integrity.test.ts fails while it is stale. -->

| Runs on | Job | Schedule (cron) | Time zone |
|---|---|---|---|
| GitHub Actions | `.github/workflows/audit-chain-verify.yml` | `23 4 * * *` | UTC |
| GitHub Actions | `.github/workflows/control-probes.yml` | `41 6 * * *` | UTC |
| GitHub Actions | `.github/workflows/cron-slot-watchdog.yml` | `23 7 * * *` | UTC |
| GitHub Actions | `.github/workflows/customer-config-reconcile.yml` | `41 9 * * *` | UTC |
| GitHub Actions | `.github/workflows/handbook-review.yml` | `29 14 * * 1` | UTC |
| GitHub Actions | `.github/workflows/hermes-release-watch.yml` | `17 14 * * 1` | UTC |
| GitHub Actions | `.github/workflows/obligation-reconcile.yml` | `53 7 * * *` | UTC |
| GitHub Actions | `.github/workflows/ops-workspace-sweep.yml` | `17 13 * * *` | UTC |
| GitHub Actions | `.github/workflows/security.yml` | `0 13 * * *` | UTC |
| GitHub Actions | `.github/workflows/terminal-state-reconcile.yml` | `41 6 * * *` | UTC |
| GitHub Actions | `.github/workflows/unaudited-send-reconcile.yml` | `17 13 * * *` | UTC |
| GitHub Actions | `.github/workflows/work-health.yml` | `23 * * * *` | UTC |
| Cloudflare Worker | `workers/cost-anomaly` | `0 3 * * *` | UTC |
| Cloudflare Worker | `workers/cost-telemetry` | `0 2 * * *` | UTC |
| Cloudflare Worker | `workers/fleet-alerts` | `*/2 * * * *` | UTC |
| Cloudflare Worker | `workers/fleet-alerts` | `11 15 * * 1` | UTC |
| Ops machine (smd-ops) | `/home/ops/bin/ops-triage` | `47 * * * *` | America/Phoenix |
| Ops machine (smd-ops) | `find /home/ops/archive -mindepth 3 -path '/home/ops/archive/*/trans...` | `23 3 * * *` | America/Phoenix |

<!-- END GENERATED: scheduled jobs -->
