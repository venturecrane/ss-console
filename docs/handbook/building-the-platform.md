---
title: Building the Platform
section: operations
order: 3
summary: The dev workflow, the verify gate, worktree isolation, and the coding standards every change ships under
sources:
  - label: Team Workflow (global) - Story Lifecycle, DoD
    href: crane_doc('global', 'team-workflow.md')
  - label: Coding Standards (global)
    href: crane_doc('global', 'coding-standards.md')
  - label: CI - verify.yml
    href: https://github.com/venturecrane/ss-console/blob/main/.github/workflows/verify.yml
  - label: package.json - npm scripts
    href: https://github.com/venturecrane/ss-console/blob/main/package.json
---

## The rule that governs everything

All changes ship through PRs. Never push to main. This is the first Enterprise
Rule in `CLAUDE.md` and the spine of the team workflow (global
`team-workflow.md`): branch from main, open a PR, pass CI, verify, merge on a
Captain directive. A direct push to main bypasses CI, the verify gate, and review
in one move, so it is prohibited even for a one-line change. This page owns how a
change moves from a branch to a merge. The two deploy paths that run after a merge
- the website and the Operator - are owned by `/admin/playbook/deployment-release`.
The trust controls a change is reviewed against are in `/admin/playbook/security-trust`.

## The dev workflow

1. **Branch from main.** A feature branch per change.
2. **Implement, commit, push.** Small focused commits.
3. **Open a PR** that links its issue (`Closes #NNN`) and fills the template:
   summary, how to test, screenshots for UI.
4. **CI runs.** The `Verify` workflow runs on every PR regardless of base branch
   (see below). The PR cannot merge red.
5. **QA against the preview**, not production, checking each acceptance criterion.
6. **Merge on a Captain directive**, then the issue closes with `status:done`.

`status:verified` means QA passed and the PR is still open; `status:done` means
merged and deployed. The Definition of Done (global `team-workflow.md`) requires
the PR merged, every acceptance criterion verified, no open P0/P1 bug linked, and
the change deployed to production - not merely merged.

## What `npm run verify` actually runs

`npm run verify` is the full local gate. From `package.json`, it chains, in
order, and stops on the first failure:

1. `npm run typecheck` - TypeScript validation via `astro check`.
2. `npm run typecheck:workers` - typecheck each sub-worker under `workers/*`
   that has its own `package.json`.
3. `npm run format:check` - Prettier in check mode.
4. `npm run lint` - ESLint over the repo.
5. `npm run build` - the production Astro build.
6. `npm run test` - the Vitest suite (`vitest run`).
7. `npm run test:workers` - each sub-worker's own Vitest suite.

CI mirrors this. `verify.yml` runs Typecheck, Typecheck Workers, Format check,
Lint, Build, Test, then Test Workers, then a dry-run wrangler build of each
sub-worker. Two steps in that file carry scars worth knowing: the workflow runs
on every pull request with no `branches` filter, because a `branches: [main]`
filter (removed 2026-06-08) matched the PR's base, so a stacked PR based on a
feature branch silently skipped the whole suite; and the `Test Workers` step was
added 2026-06-12 after worker suites ran locally under `npm run verify` but
never in CI.

There is a separate workflow, `operator-substrate.yml`, that runs the Operator's
Python pytest suites. It triggers only on changes under `operator/**` (and a few
named contract files), and it does not run as part of `npm run verify`. A
`customer.yaml` change with a new top-level block can pass `npm run verify` and
still turn main red on the substrate workflow - so Operator config changes get
the pytest suite run locally before merge (`cd operator && python3 -m pytest ...`).

## Worktree isolation for parallel sessions

Parallel sessions do not share a working tree. Each runs in its own git worktree -
an isolated checkout on its own branch - so independent work does not collide.
Since 2026-07-06 this is enforced, not conventional: the primary checkout is
read-only for agent sessions. A `PreToolUse` hook (`.claude/hooks/worktree-guard.mjs`,
wired in the checked-in `.claude/settings.json`) rejects `Edit`/`Write`/`NotebookEdit`
calls targeting the primary tree; paths under `.claude/` are exempt because the
worktrees themselves live there. A `SessionStart` hook (`.claude/hooks/sync-primary.sh`)
fast-forwards a clean primary checkout to `origin/main` at session start, so the
primary tree can no longer drift stale the way it did before the guard (87 commits
behind, 46 dirty paths of already-merged residue).

The guard also closes an older failure mode: Bash runs in the worktree, but
`Write` and `Edit` follow the absolute path given, so a parent-repo absolute path
used to land the file in the parent while tests ran green against the untouched
worktree. Those writes are now rejected instead of silently landing.
`tests/worktree-guard.test.ts` pins the blocked and exempt paths; the Captain-only
escape hatch is `SS_ALLOW_PRIMARY_WRITES=1`.

Isolation stops trees colliding; it does not stop a session being briefed on a
world that has since moved. On 2026-07-31 five concurrent sessions merged 22 PRs
in 36 hours, and each of them repeatedly discovered it had acted on something no
longer true: a tree snapshot listing thirty-one dirty paths that were clean
twenty minutes later, a worktree list that disagreed with itself three minutes
apart. The sharpest case was `sync-primary.sh` itself, which fast-forwards the
primary at one session's startup without reinstalling, so a major-version
lockfile change invalidated the dependency tree of sessions already running
there, hours after their briefing had correctly reported it current.

Session-start detection cannot reach this, because session start is exactly when
every answer is still right. So `.claude/hooks/reflex-primer.sh` re-checks on
every turn and states the decay: whether `origin/main` has moved, whether the
working tree changed against a per-session baseline, and whether installed
packages content-differ from `package-lock.json`. It gates the dependency check
on mtime but speaks only on content, because `git stash`, `git restore`, and
rebases all rewrite the lockfile without changing it, and a warning that is
sometimes wrong and always loud teaches agents to skim past the laws above it.
`.claude/hooks/session-peers.sh` adds the other half at startup: which sibling
worktrees are live, which are locked by a process that has since died, and what
landed on main ahead of this checkout. Both are covered by
`tests/staleness-detection.test.ts`, whose load-bearing assertions are not that
the signals fire but that every doctrine line still reaches stdout and the exit
code is still zero on every failure path. This is Law 10 in
`docs/doctrine/agent-operating-doctrine.md`.

The same discipline applies to the code we ship, not only to what an agent reads.
A program that acts on the world and then reports on the result has two ways to
write that report: from the statement it ran, or from the world after it ran.
The 2026-09-10 code review found four new defects in one window that were all
the first kind: a decommission manifest that said a row was deleted whether or
not it was, a required check whose conformance test covered the test files but
not the fixtures those tests execute, a pricing route that wrote two halves and
could not say which landed, and a flag whose help text said "dev only" while
nothing enforced it. So any module that emits a manifest, a completion record, a
wired map, or a done flag reads the world back after acting, and the review asks
what the report would say if the action had matched nothing. This is Law 14 in
the same registry; the five questions a reviewer answers are in
`docs/doctrine/report-is-a-probe-checklist.md`.

## The portable coding standards

Every change is written to the enterprise coding standards (global
`coding-standards.md`). The highest-leverage rules a contributor here will hit:

- **Parse, don't cast.** At every trust boundary - HTTP bodies, webhook
  payloads, D1/KV JSON columns, external API responses, cursor tokens - parse the
  input with a schema (Zod), never `JSON.parse(x) as T`. TypeScript types are
  compile-time only; unvalidated external data propagates wrong values until
  something crashes far from the source. This is review-enforced and called the
  most important rule in the document.
- **No floating Promises.** Every Promise is `await`ed, `return`ed, passed to
  `ctx.waitUntil()`, or explicitly `void`ed with a comment. In Workers the
  isolate can terminate before an unawaited Promise resolves, so the write never
  lands. Lint-enforced at error.
- **No request-scoped state in module scope (Workers).** Module-level `let`/`const`
  hold only immutable init-time values (parsed config, compiled regexes, reusable
  clients). Workers reuse isolates across requests, so per-request data in module
  scope leaks between requests. All per-request state flows through function
  parameters. Review-enforced.
- **The ceilings: 500 / 75 / 15.** Files cap at 500 lines, functions at 75,
  cyclomatic complexity at 15 (also depth 4, params 5). The driver is agent
  context arithmetic, not aesthetics: a file plus its surrounding context plus a
  generation buffer must fit a comfortable working window. Lint-enforced; test
  files exempt. Split at a cohesion boundary, not mid-function, when approaching
  the cap.

Other mechanically enforced rules from the same document: no `any` in production
source (use `unknown` and narrow), throw `Error` instances not literals, `===`
always, preserve `cause` on caught errors, `assertNever` on switch defaults over
a union, and named exports only (defaults allowed only in framework positions like
`*.astro` and the Worker entry).

## The merge gates

Beyond CI, this repo carries domain-specific merge gates that block a PR on
policy, not just on tests. They enforce the no-fabricated-client-facing-content
policy in `CLAUDE.md`:

- `scope-deferred-todo.yml` - blocks a TODO-deferred acceptance criterion that
  lacks the `scope-deferred` label.
- `unmet-ac-on-close.yml` - reopens an issue closed with unchecked acceptance
  criteria.
- `ui-drift-audit.yml` - the source-level UI anti-pattern audit.
- `security.yml` - the security review workflow (see `/admin/playbook/security-trust`).

These exist because a violation of the content policy is a P0 in this venture, so
the policy is enforced in CI rather than left to review memory.
