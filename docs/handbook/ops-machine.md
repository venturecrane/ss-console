---
title: The Ops Machine
section: operations
order: 12
summary: smd-ops, SMD's own operations machine on Fly. Where client operations run instead of the Captain's laptops, how to connect, what lives there, how its keys work, and the break-glass path.
sources:
  - label: operator/ops-machine/Dockerfile - the image and why it exists
    href: https://github.com/venturecrane/ss-console/blob/main/operator/ops-machine/Dockerfile
  - label: operator/ops-machine/fly.toml - the Machine (no public services, encrypted volume)
    href: https://github.com/venturecrane/ss-console/blob/main/operator/ops-machine/fly.toml
  - label: operator/ops-machine/TRIAGE.md - the triage agent's standing orders
    href: https://github.com/venturecrane/ss-console/blob/main/operator/ops-machine/TRIAGE.md
  - label: operator/ops-machine/laptop-offboard.sh - taking client operations off a laptop
    href: https://github.com/venturecrane/ss-console/blob/main/operator/ops-machine/laptop-offboard.sh
---

## Why it exists

Until 2026-10-16 every client operation (seat rebuilds, seat probes, chronology runs, the fixes agents made) ran from the Captain's laptops. That put every client seat's production keys and copies of client files on two personal machines, and it contradicted the A&P data terms, which describe matter content as read in memory during a task and not retained by SMD. The Captain's decision: one SMD-owned machine on Fly does that work, and the laptops go back to product development.

## What it is

- **Fly app `smd-ops`**, one Machine in `sjc`, shared-cpu-2x with 4 GB. No public IP and no services, so nothing outside SMD's private Fly network can reach it, and Fly never auto-stops it.
- **An encrypted 150 GB volume** mounted as `/home/ops`, the only place anything client-related is written. Archived laptop data lives under `/home/ops/archive/`.
- **The `ops` user** runs every session and scheduled job. Root is used only by the entrypoint, to prepare the volume and start cron.
- **Tools pinned** to the versions the laptop ran: Claude Code, flyctl, the Infisical CLI, gh, Node, Python with uv, and the crane CLI.

## How to connect

From either laptop, type `crane ss`. It opens the client-ops session on the ops machine, or rejoins it if it is already running (a tmux session named `main` that survives closing the laptop). `crane ss brokery` opens a second, separately named session. `crane ss --local` runs on the laptop as before, for product work. Every other venture is untouched.

How it works: `operator/ops-machine/laptop/install.sh` gives each laptop a Fly token that reaches smd-ops only (refused at every client seat), installs `~/.smd-ops/ops-connect`, and adds a `crane` shell function to `~/.zshrc`. On the Machine, `bin/ops-session` starts `crane ss` with the Machine's own Infisical identity. A `claude` shim first on its PATH removes the generic `ANTHROPIC_API_KEY` that rides in with the `/ss` secrets, so every Claude there, interactive or unattended, uses the Captain's Max login.

## How its keys work

| Access | How | Expires |
| --- | --- | --- |
| Claude | The Captain's Max login, done on the Machine (`claude`, then `/login`) | Renew when Claude asks |
| GitHub | The Captain's `gh auth login` on the Machine, with `read:packages` and `read:org` | Until revoked |
| Production secrets | Its own read-only Infisical identity `smd-ops` (`~/.infisical-ua`); `ss-env <command>` runs a command with `/ss` prod loaded | Client secret: 90 days |
| Client seats | One Fly deploy token per seat app, joined in `~/.fly-tokens`; each reaches only its own app | 30 days |
| Email | Resend (team@) for reports; AgentMail for any inbox the Machine needs | Per vendor |

When a key expires, `ops-selftest` on the Machine shows which one, and the matching step is re-run from a laptop that still has the Captain's own logins.

## The triage agent

`bin/ops-triage` runs from cron as `ops`. Each run evaluates work health the same way the hourly email does. When there is a new listed item, it starts Claude (capped at $5 a run, read-only tools only) to investigate each item under `TRIAGE.md`. It then emails team@ one report: handled, needs you, and fixes to approve. Quiet hours send nothing. The first version changes nothing on its own.

## Break-glass

If the Machine is down when a client needs something, the Captain logs a laptop back into Fly (`fly auth login`) and works from there as before. A laptop used this way gets offboarded again afterwards (`laptop-offboard.sh`).

## Moving data onto it, and off a laptop

`move-to-ops.sh` and `move-by-folder.sh` copy laptop folders onto the Machine and prove every file arrived with a sha256 manifest checked on the Machine. A folder is recorded as moved only when every file verifies. `laptop-offboard.sh` deletes only what is recorded as verified, plus engagements clones with no unsaved work. It then logs the laptop out of Fly and reads the filesystem back to prove each path is gone. Run it without `--execute` first to see the list.
