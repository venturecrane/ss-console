---
name: code-review
description: Standing-state code audit for SMD Services across ss-console, including the Operator overlay at operator/hermes-smd-overlay/. Proves each finding with a re-runnable probe, then fixes what it finds in the same session and gets the fixes onto the seats.
---

# /code-review - Codebase Audit (SMD Services)

> **Forked from crane-console on 2026-10-17.** This is SMD's own copy and no
> longer receives enterprise updates; improvements originate here.
> The fork point is commit `9d4f3b8c` (crane `.claude/commands/code-review.md`,
> v2); everything after it is ours. It lives in `.claude/skills/` for the
> same reason `/sos` and `/eos` do: `crane ss` re-syncs `.claude/commands/` on
> every launch, and nothing in the crane toolchain writes here. If `/code-review`
> runs crane's copy instead, follow the fallback ladder in
> `.claude/skills/sos/SKILL.md`.
>
> **Why it was forked (Captain, 2026-10-17).** Two gaps in the inherited skill:
>
> 1. **It reviewed only the repo it ran in.** The Operator's runtime half, the
>    overlay (then the separate repo `venturecrane/hermes-smd-overlay`, since
>    ADR 0095 `operator/hermes-smd-overlay/` in this repo: the trust gate, approvals, the
>    brake, the inbound router, the record-store fence), had never been reviewed,
>    and a probe that searched only ss-console carried the brake (F1) as unwired
>    through two reviews while it was live in the overlay.
> 2. **It ended in a list.** Its last step asked before acting, so every run
>    produced recommendations of a subset to fix. The venture's rule is that
>    work worth doing is done now (Law 13). This skill ends with the fixes
>    merged and running on the seats.

> **Invocation:** As your first action, call `crane_skill_invoked(skill_name: "code-review")`. This is non-blocking — if the call fails, log the warning and continue. Usage data drives `/skill-audit`.

Produces **a probe script, a short report, and the fixes**. Not a graded essay, and not a backlog.

## Scope: the one code repo the venture runs

Review `venturecrane/ss-console` in your worktree at `origin/main`. Since ADR 0095 (2026-10-22) it holds all of the venture's code, in two areas the review partitions separately:

| Area | Path | What it is |
| --- | --- | --- |
| Site, portal, admin, Operator control plane | everything outside `operator/hermes-smd-overlay/` | the Worker, the admin console, the portal, and the Operator's build and config side (`operator/`: broker, medchron lanes, connectors, skills, fleet) |
| The overlay | `operator/hermes-smd-overlay/` | the Operator and hosted agent runtime on every seat: the trust gate, approvals, the brake, the inbound router, the record-store fence |

`venturecrane/engagements` is client material, not code, and is out of scope (Law 15). The standalone `venturecrane/hermes-smd-overlay` repo is locked and carries only hotfixes for a seat still held on a pre-fold image; its history is in this repo at `refs/heads/overlay-history`.

What a seat runs is the release it reports. A finding fixed on `origin/main` is still live on a seat until `fleet release` carries it there; `fleet status` shows which release each seat runs.

**A probe looks where the code runs.** A claim about a runtime control (a gate, a fence, the brake, a send path) must be checked in the overlay as well as in ss-console. "No caller in ss-console" is not "unwired". That exact mistake carried F1 through two reviews.

## Arguments

```
/code-review [focus] [--full]
```

- `focus` — optional path or repo name to narrow the diff pass.
- `--full` — re-derive the invariant set from scratch rather than reusing the previous probe.

## The things you produce

1. `docs/reviews/claims-<YYYY-MM-DD>.probe.sh` — every fact, as a re-runnable command. Overlay claims run against `operator/hermes-smd-overlay/` in the same checkout.
2. `docs/reviews/claims-<YYYY-MM-DD>.md` — **≤60 non-blank lines.** The probe proves; the report explains.
3. **The fix PRs**, merged, and the seats rebuilt (Steps 9 and 10).

**You may produce no findings.** If the previous probe holds and the diff passes turn up nothing above the floor, say so in three lines and stop. A review that always finds something is a review that invents things.

---

## Step 1 — Run the previous probe FIRST

Find the most recent `docs/reviews/claims-*.probe.sh` and run it. This is the carry-forward, and it is what stops the same finding being rediscovered every run.

| Field | Values | Meaning |
| --- | --- | --- |
| `state` | `OPEN` \| `FIXED` | Was the defect fixed, or merely recorded? |
| `direction` | `exact` \| `at-most` \| `at-least` | Which way is improvement? |

| Result | `state: FIXED` | `state: OPEN` |
| --- | --- | --- |
| Holds | **Closed.** Skip it. | **`STILL-OPEN`.** Carry it, and fix it this run. |
| Drifts | **Regression.** Report loudly, fix it this run. | Improvement or change — re-measure and restate. |

A carried `STILL-OPEN` is first **re-checked against its instrument**: before fixing, confirm the probe measures the defect and not a proxy. N15 and F1 were both carried for weeks by probes that read correct code as broken.

`direction` exists because string equality reports improvement as drift. Never assert a raw line number. `state` exists because `HOLDS` on "0 security headers found" means still broken.

## Step 2 — Two passes per repo, with different epistemics

**Do not read a large fraction of the corpus and form impressions.** That is the sampling that made v1 vary.

**Diff pass** — bug-class findings on code changed since the last review, per area (`git log <last-review-sha>..HEAD`, once for the overlay path and once for the rest; the overlay's pre-fold history is at `refs/heads/overlay-history`). Exhaustive over that diff. **The first review after the fold is a standing-state review of the overlay's whole source**, partitioned by path, because no earlier review read it as part of this repo.

**Invariant pass** — `grep -c`-shaped checks across each whole tree. Absence findings are always full-scope.

## Step 3 — The bar for a finding

**A finding carries a command, or it is not a finding.** No command → `## Judgment`, no severity.

Score every finding 0-100 with this rubric verbatim:

- **0** — false positive under light scrutiny.
- **25** — might be real; could not verify.
- **50** — verified real, but a nitpick or rare in practice.
- **75** — verified, will be hit in practice, the existing approach is insufficient.
- **100** — confirmed, will happen frequently, evidence directly supports it.

**Discard below 80.** Discard false positives, pedantic nitpicks, anything a linter or typechecker catches, and clearly intentional changes. Pre-existing issues and issues on unmodified lines are **not** discarded: this skill audits the standing state.

## Step 4 — The absence lane

An absence finding needs all four: the claim with its scope, the exact search (`wc -l`, never `head`), exhaustive over that scope, and a positive control showing the instrument can return non-zero (a sibling positive, a planted fixture, or an instrument self-test). No positive control → `## Judgment`.

## Step 5 — Fan-out, and asserting delivery

State your fan-out before you start: diff agents **partitioned by path within each area** (sized so each agent can read its partition exhaustively, typically ≤15K changed lines), plus the invariant pass. Agents do not spawn sub-agents; a sub-agent's report bypasses its parent and the parent waits forever (2026-10-17).

**Every agent delivers by calling `SendMessage` to `main`.** After the fan-out, name every dispatched pass and whether it returned. Never write the report while a pass is outstanding.

**Re-run every finding's probe command yourself before it enters the report.** An agent's report is a claim.

## Step 6 — Write the probe and the report

Report sections, in order: **Still open**, **New findings** (≥80, with commands), **Closed this run**, **Judgment**, **Coverage** (per area: passes run, passes returned, files read in full vs scanned). **No letter grades. No dimension scorecard.**

## Step 7 — Verify your own output, arithmetically

```bash
wc -l docs/reviews/claims-<date>.md                         # must be <= 60
grep -cE '^\| [A-F][+-]? ' docs/reviews/claims-<date>.md    # must be 0
bash docs/reviews/claims-<date>.probe.sh; echo "exit=$?"    # must run
```

Paste the output into the report verbatim. **Prove the probe can fail:** tamper one FIXED target and one OPEN target, confirm REGRESSED and CHANGED, restore.

## Step 8 — Record

`crane_note` with tag `code-review`, venture-scoped: still-open count, new findings, closed count, probe path. Then `crane_schedule(action: "complete", name: "code-review-ss", ...)`.

## Step 9 — Fix everything, now

This step is not optional and does not wait for a go-ahead. Every finding ≥80 and every `STILL-OPEN` claim is fixed in this session.

- **Group findings by the files they touch**, one Opus agent per group, each in its own worktree (`isolation: "worktree"`), overlay fixes included. Groups that touch disjoint files run in parallel.
- **Each agent, per defect:** confirm the probe output, fix the root cause, add a regression test that **fails on the old code** (revert locally, see red, restore), re-run the probe, run that package's suites, open one PR per group, wait for CI, merge it.
- **Agents never deploy, reprovision, touch a seat, send email, or write to a client system.** Code and tests only.
- **A defect that turns out not to be real** is reported with evidence, and its probe is restated, never "fixed".
- **A runtime fix is one PR.** An overlay change and the ss-console code or contract it pairs with land in the same PR; there is no pin to bump (ADR 0095). It reaches seats only through `fleet build` and `fleet release` (Step 10).
- **Only two things are not fixed now:** work blocked on something that does not exist yet, and a decision that is genuinely the Captain's (strategy, commitments, spend, a client's posture, removing a feature). For those, bring the Captain one sentence of stakes, the options, and your pick. File an issue only when neither of those can resolve it in the session.
- **Check for peer overlap first.** If a live peer session owns the area (the board shows mission lines), send it the finding and let it fix; record it as theirs in the report.

When the PRs merge, flip each claim in the probe to `FIXED` with the value measured on main after the merge, so a returning defect reads REGRESSED. Give new claims ids that do not collide with older ones in the carried probe.

## Step 10 — Done means the seats run it

A fix is done when the seats run it (Law 9).

1. Build the release with `operator/bin/fleet build` from a worktree detached at the merged `origin/main` (copy `.infisical.json`, run `npm ci`, use a fresh engagements clone for `SS_ENGAGEMENTS_DIR`). The overlay is built from that same tree.
2. **Never kill a running job:** check each seat's `/run/smd-medchron` for `*.pid` files (with a positive control, e.g. the `*-heartbeat.json` files) before rebuilding.
3. **Pilot first** (`fleet release <id> --only pilot-smokeball`), then the rings (`fleet release <id>`) once the pilot's boot smoke is green. Seats another session owns are coordinated with it, not rebuilt over it.
4. Read the fixed code back from the running seat and record it with `crane_verify`.

Seat rebuilds and `fly ssh` reads need the session out of auto mode; if the classifier refuses them, say so plainly and ask the Captain to switch modes.

---

## Notes

- **This is the standing-state audit.** The per-PR gate is the official `code-review` plugin, which reviews one diff. They are complementary.
- **The report's 60-line limit is hard**, enforced by the probe's self-check. If the findings do not fit, the probe file carries them.
