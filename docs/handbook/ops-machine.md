---
title: Client Ops Workspaces
section: running
order: 24
owner: captain
review_every: 30
last_reviewed: "2026-10-21"
summary: Where client documents live (one Fly workspace per client, never a laptop), how to open one with a single command, how work is handed over from a laptop, what each workspace can reach, how long documents are kept, and smd-ops, SMD's own machine.
sources:
  - label: operator/ops-machine/workspaces.json - the list of client workspaces
    href: https://github.com/venturecrane/ss-console/blob/main/operator/ops-machine/workspaces.json
  - label: operator/ops-machine/bin/ops-workspace - create, update, status, destroy
    href: https://github.com/venturecrane/ss-console/blob/main/operator/ops-machine/bin/ops-workspace
  - label: operator/ops-machine/laptop/client-data-guard.mjs - the laptop tripwire
    href: https://github.com/venturecrane/ss-console/blob/main/operator/ops-machine/laptop/client-data-guard.mjs
  - label: operator/ops-machine/bin/ops-sweep - retention and health, run daily
    href: https://github.com/venturecrane/ss-console/blob/main/operator/ops-machine/bin/ops-sweep
  - label: .github/workflows/ops-workspace-sweep.yml - the daily sweep
    href: https://github.com/venturecrane/ss-console/blob/main/.github/workflows/ops-workspace-sweep.yml
  - label: operator/ops-machine/laptop-offboard.py - removing verified copies from a laptop
    href: https://github.com/venturecrane/ss-console/blob/main/operator/ops-machine/laptop-offboard.py
  - label: operator/ops-machine/TRIAGE.md - the smd-ops triage agent's standing orders
    href: https://github.com/venturecrane/ss-console/blob/main/operator/ops-machine/TRIAGE.md
  - label: operator/ops-machine/bin/ops-triage - the hourly triage run
    href: https://github.com/venturecrane/ss-console/blob/main/operator/ops-machine/bin/ops-triage
  - label: operator/ops-machine/crontab - smd-ops's schedule
    href: https://github.com/venturecrane/ss-console/blob/main/operator/ops-machine/crontab
  - label: operator/ops-machine/bin/ops-idle-stop - the idle stop
    href: https://github.com/venturecrane/ss-console/blob/main/operator/ops-machine/bin/ops-idle-stop
---

A client's documents (matter files, medical records, and everything made from them) are opened only on that client's own locked-down machine in the cloud, its ops workspace, never on a laptop. This page says what a workspace is, who opens one and how, how long documents are kept, and what `smd-ops`, SMD's own operations machine, does.

Who acts: **an agent** creates, updates, sweeps and destroys workspaces and moves files off laptops; **the Captain** opens a workspace and works in it, because today every workspace runs Claude on the Captain's own login ([Continuity](/admin/playbook/continuity)); **you** need only know the rule, notice when a healthchecks.io notice names an `ops-<slug>` workspace or `smd-ops-triage`, and ask an agent to restore it.

## The line

Client documents are opened only in that client's ops workspace, never on a laptop (Captain, 2026-10-17). Client documents means a client's matter files and medical records, the chronologies, demands and reviews made from them, and the manual work done on them before an Operator lane exists.

Everything else stays on the laptops: product development, seat work, rebuilds, deploys, proofs, letters and the engagement record (the `engagements` repo). None of that needs a client's files.

Why: by 2026-10-16 the laptops held 81 GB of chronology data, proof runs and client deliverables on mac23 and 7 GB on m16, accumulated by agents running the tools there. The A&P data terms describe matter content as read in memory during a task and not retained by SMD.

## One workspace per client

Each client with document work has its own Fly app, `ops-<slug>` (A&P: `ops-ashton-price`), listed in `operator/ops-machine/workspaces.json`. Each workspace:

- holds only that client's documents, on its own encrypted volume (`/home/ops`, client work under `~/work`);
- reaches only that client's seat: its Fly token is a deploy token for `hermes-<slug>` alone, and its selftest proves another app refuses it;
- runs the tools on that client's own Anthropic key, while Claude itself runs on the Captain's Max login;
- has no Infisical identity, so nothing else in the vault is reachable from it;
- stops itself after an hour with nobody attached and nothing running, and costs nothing while stopped.

An agent runs `bin/ops-workspace create <slug>` from a laptop signed in to the Captain's Fly, Infisical and GitHub accounts (the slug must first be added to `workspaces.json` in a reviewed change): app, volume, secrets, image, health check. `update` rebuilds the image and re-mints its tokens; `status` shows its state and selftest; `destroy` deletes it and everything on it. Decommissioning a client destroys its workspace with its seat.

## Opening one, and handing work over

On a laptop the Captain types one command: `ops ap` (or `ops` to list the workspaces). It starts the workspace if it is stopped and puts him in its Claude session. It runs on the Captain's own logins, so today no one else can open one; giving an administrator their own logins is the decided remedy on [Continuity](/admin/playbook/continuity).

When a laptop agent gets a request that would pull client documents, it does not start it. It runs `ops-handoff <client> "<task>" --reply-to <its session name>` and tells the Captain what that printed (switch to the new task window, or type `ops <client>`); the task opens in front of him there. Every laptop session carries the rule (Law 15 in the operating doctrine), and a user-level hook (`client-data-guard.mjs`) stops the common commands and prints the same instruction.

The outcome comes back the same way. A handed-over task's session writes what it did and found to `~/outbox/` on the workspace (the verdict, never document content), and the laptop session reads it with `ops-handoff <client> --results`, so the Captain does not copy results between windows.

While the task is in progress, the two sessions talk directly over Remote Control with SendMessage. The workspace session starts the conversation, not the laptop. The laptop agent hands over with `--reply-to <its own session name>` (the name `ListAgents` prints as "This session is ..."); the workspace session's first act is to message that session with its own address, and it sends its verdict there when done. It works in that direction because the workspace session cannot be found by name. It is launched as `ops-<alias>` (`ops-ap`), but a session started with a task is renamed to an auto-generated title, so on 2026-10-22 the laptop saw an offline `ops-ap` and a live session called "Inbox task ... handoff". Both ends need Remote Control on and the same claude.ai account: a laptop session that runs `/login` drops Remote Control until `/remote-control` is run again. The Captain still approves every seat command and write in the workspace window. The channel carries instructions and verdicts only; record text, names and amounts stay on the workspace.

A handoff to a workspace that already has a session running opens at once, in a new window of it (tmux window `task`), so the Captain switches windows rather than re-running `ops`. Before 2026-10-22 a second handoff sat in the inbox until the next `ops`, because a running session never rereads it.

Claude on a workspace always runs through the shim `/opt/ops/bin/claude`, which keeps the client's Anthropic key away from Claude itself (Claude runs on the Captain's Max login; only the tools bill the client's key). On 2026-10-19 Claude's own auto-updater put a copy into `~/.npm-global`, ahead of the shim on PATH, and a session ran on A&P's key. The shim now turns the updater off, the last line of `.bashrc` puts the shim first on every boot, and the selftest fails if `claude` resolves anywhere else.

The first time on a new workspace, `ops` walks the Captain through his GitHub sign-in and Claude's own `/login`, then sets up the repos and tools.

## How long documents are kept

Delivered work is kept 30 days after delivery, then deleted (Captain, 2026-10-17). A piece of work is marked delivered with `ops-deliver <dir>`. The daily sweep deletes it 30 days later and keeps a ledger of what it held: a hash and path per file, no content, so coverage checks still work. Work not marked delivered is never deleted. A long job runs under `ops-job <command>` so the idle stop leaves the Machine running until it finishes.

## Monitoring

Once a day (`ops-workspace-sweep`) GitHub starts each workspace, runs retention and the selftest over SSH, and pings that workspace's healthchecks.io check (`ops-<slug>`, daily, 6 hours grace). A failed or missing run pages. The sweep also fails 30 days before any of the workspace's tokens expire, naming the command that renews them.

## The honest limits

- The tripwire is a habit-breaker, not a lock. It is installed per laptop by `operator/ops-machine/laptop/install.sh`; an agent can phrase a command it does not recognise; the Captain's own logins reach everything.
- The Captain's Claude and GitHub logins on a workspace reach what they reach anywhere: his mail, his repos.
- The engagements repo (letters, notes, dossiers) is the relationship record and stays on the laptops. It must not hold client documents; its CI refuses document files under `operator/customers/` outside an allowlist of SMD-authored material.
- Break-glass: if a workspace is down and a client is waiting, a laptop session can run the tools with `SS_ALLOW_CLIENT_DATA_ON_LAPTOP="<reason>"` (audited). The laptop is offboarded again afterwards.

## Removing copies from a laptop

This is agent work. `move-to-ops.sh` and `move-by-folder.sh` copy laptop folders onto a workspace and prove every file arrived with a sha256 manifest checked on the Machine. `laptop-offboard.py` deletes a laptop file only if it is listed in a copy's manifest and still hashes to the copied value, so anything changed since the copy stays. It never deletes a whole folder, the agent memory, or `~/dev`, and it reads back that every deleted file is gone. Run it without `--execute` first to see the counts.

## smd-ops, SMD's own machine

`smd-ops` is not a client workspace. It runs SMD's own operations: the hourly work-health triage agent (`bin/ops-triage`, at :47 Phoenix time per its `crontab`, standing orders in `TRIAGE.md`), which runs the work-health evaluation, investigates each new item with read-only tools, and emails team@ one report only when something new was found ([Work Health](/admin/playbook/work-health)). It may not change code, deploy, restart anything or message a client. Its silence is caught by the healthchecks.io check `smd-ops-triage` (hourly, 30 minutes grace). It holds the Captain's Max login, a read-only Infisical identity for `/ss` prod (`ss-env <command>`), and per-seat Fly deploy tokens for reading seats. Connect with `fly ssh console -a smd-ops -u ops`. It holds no client documents. It does keep the Claude transcripts archived from the laptops (`/home/ops/archive/<host>/transcripts`), because they mix clients and ventures and belong to no single client workspace. A daily job in its `crontab` deletes each transcript 30 days after it was last written, the same rule `cleanupPeriodDays: 30` applies on the laptops; the agent memory copies in that tree are kept.
