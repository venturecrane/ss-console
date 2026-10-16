# Offboard a laptop from client operations

For a Claude Code session running ON the laptop being offboarded (it reads the
laptop's own disk, which nothing else can). mac23 was done this way on
2026-10-16; m16 is next. Run from a worktree of ss-console at origin/main.

## 1. Inventory (read-only)

List client material on this laptop, names and sizes only, never contents:

- `~/smd-medchron-data`, `~/.claude/handoffs`
- `~/.claude/projects/-Users-*-dev-ss-console*` (session transcripts)
- `/private/tmp/claude-*` folders named for matters (`r20*`, `proof-*`, `lp-*`,
  `litproof*`, `ap-inputs`, `plead`, `rehearsal`, `replay-job`) and engagements clones
- `~/Desktop`, `~/Downloads`: files named for A&P matters, staff or claimants
- `~/smd-*`, anything else under `~` named for a client or matter

Show the Captain the list with counts and sizes before moving anything.

## 2. Move, verified

`H=$(hostname -s)`. For each big tree, run
`operator/ops-machine/move-by-folder.sh <base> $H/<name> [list-file]`.
For single folders, run `operator/ops-machine/move-to-ops.sh <base> $H/<name> <rel-path>`
and append the absolute path to `~/.smd-ops-verified.txt` only after it prints
`VERIFIED`. Paths starting with `-` go in list files prefixed `./`.

## 3. Offboard

Run `operator/ops-machine/laptop-offboard.sh` (dry run), show the Captain the
list, then `laptop-offboard.sh --execute`. It deletes only verified paths and
clean engagements clones, logs out of Fly, and reads the filesystem back.
Record the result with `crane_verify`.

Not in scope here: product repos under `~/dev`, the agent memory, and personal
files that are not client material.
