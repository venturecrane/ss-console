---
title: Building the Platform
section: engineering
order: 10
owner: engineering
review_every: 90
last_reviewed: "2026-10-21"
summary: The dev workflow, the verify gate, worktree isolation, and the coding standards every change ships under
sources:
  - label: CI - verify.yml
    href: https://github.com/venturecrane/ss-console/blob/main/.github/workflows/verify.yml
  - label: package.json - npm scripts
    href: https://github.com/venturecrane/ss-console/blob/main/package.json
  - label: ESLint config - the enforced coding rules
    href: https://github.com/venturecrane/ss-console/blob/main/eslint.config.js
---

This page covers how a code change gets from an idea to a merge: the PR workflow, the checks
that must pass, how parallel agent sessions are kept apart, and the coding rules. An
administrator needs it when an agent's report sends you here, for example to explain why a
PR cannot merge.

## The rule that governs everything

All changes ship through PRs. Never push to main. This is the first Enterprise
Rule in `CLAUDE.md` and the spine of the team workflow (global
`team-workflow.md`): branch from main, open a PR, pass CI, verify, merge. The
Captain is not the per-PR reviewer: `/ship` merges once the required checks are
green, without waiting for a per-PR directive. The two global modules this page
cites, `team-workflow.md` and `coding-standards.md`, are enterprise docs fetched
with `crane_doc('global', '<module>')`, not files in this repo. A direct push to main bypasses CI, the verify gate, and review
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
   (see below). Its job is a required check, so the PR cannot merge red; the
   full required set is under "The merge gates" below.
5. **QA against the preview**, not production, checking each acceptance criterion.
6. **Merge** once the required checks are green (`/ship` does this without a
   per-PR Captain directive), then the issue closes with `status:done`.

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
5. `npm run deadcode` - `knip --production`, which fails on unused files,
   exports, and dependencies in production code.
6. `npm run build` - the production Astro build.
7. `npm run test` - the Vitest suite (`vitest run`).
8. `npm run test:workers` - each sub-worker's own Vitest suite.

CI mirrors this. `verify.yml` runs Typecheck, Typecheck Workers, Format check,
Lint, Dead code, Build, a check that the bundle carries no inlined secret, Test,
then Test Workers, then a dry-run wrangler build of each
sub-worker. Two steps in that file carry scars worth knowing: the workflow runs
on every pull request with no `branches` filter, because a `branches: [main]`
filter (removed 2026-06-08) matched the PR's base, so a stacked PR based on a
feature branch silently skipped the whole suite; and the `Test Workers` step was
added 2026-06-12 after worker suites ran locally under `npm run verify` but
never in CI.

There is a separate workflow, `operator-substrate.yml`, that runs the Operator's
Python pytest suites, and it does not run as part of `npm run verify`. Its
`substrate` job runs on every PR with no path filter, because it is a required
check and a path-filtered required check would strand skipped PRs at "Expected";
a detect step decides from `.github/operator-substrate-paths.txt` whether the
suites need to run. A `customer.yaml` change with a new top-level block can pass
`npm run verify` and still fail `substrate` - so Operator config changes get the
pytest suite run locally before merge (`cd operator && python3 -m pytest ...`).

The Operator tree is one Python project (`operator/pyproject.toml`). Its CLIs
(`operator/bin/*.py`, `operator/rehearsal/run.py`) import `bin.lib.*`,
`adapter.*`, `workspace_broker.*` and `rehearsal.*` as packages and no longer
put the tree on `sys.path` themselves, so a laptop that runs them needs the
project installed once: `python3 -m pip install -e operator`. CI's substrate
job and the scheduled workflows that run those CLIs do the same install; a bare
local `pytest` gets the tree root from `pythonpath = .` in `operator/pytest.ini`.

The seat runtime overlay, `operator/hermes-smd-overlay/`, is a separate Python
project inside that tree (its own `pyproject.toml` and `uv.lock`), moved in from
the standalone `venturecrane/hermes-smd-overlay` repo on 2026-10-22 (ADR 0095).
The directory keeps the plugin's name on purpose: pytest's package walk stops at
a directory whose name is not a Python identifier, so the overlay's tests
resolve their imports (`shared`, `bootstrap`, `plugins`) from the overlay root
exactly as they did in the standalone repo, which relied on the same stop. CI runs it as the `overlay` job of `operator-substrate.yml`: ruff
check and format on the overlay tree, the overlay pytest suite on Python 3.14
(the Hermes venv's interpreter since the v0.22.5 promotion) against the pinned Hermes (fetched shallow from `NousResearch/hermes-agent` at
the Dockerfile's `ARG HERMES_UPSTREAM_SHA`), and a no-diff run of
`operator/requirements/compile.sh`. The overlay tests read this repo's contract
fixtures in place, so they run in CI with nothing copied across. To run the
suite locally:

```bash
cd operator/hermes-smd-overlay
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -e '.[dev]' ruamel.yaml==0.18.16 psutil==7.2.2
HERMES_AGENT_SRC=<a NousResearch/hermes-agent checkout at ARG HERMES_UPSTREAM_SHA> \
  .venv/bin/python -m pytest -q
```

### The real-dispatch harness (`overlay-e2e`)

The overlay suite tests the seat runtime's parts. The `overlay-e2e` job of
`operator-substrate.yml` runs them together, through the real Hermes, the way a
seat does: `operator/hermes-smd-overlay/tests_e2e/` translates
`operator/customers/_hosted-template/customer.yaml` into a temporary seat with
the real `bootstrap.translate`, lets Hermes' own plugin loader discover the
overlay, builds the agent the way the seat's job worker builds it, and then, for
every tool the overlay registers, has a scripted model call that tool once and
lets Hermes' own registry dispatch it. Only the edges are stand-ins: the model is
a local server speaking the Anthropic Messages API (the adapter every seat
runs), with a second pass over Hermes' default chat-completions path, and the
capability broker is a stub socket that records what reaches it.

What it catches before merge:

- a tool handler Hermes cannot call. This is the 2026-08-13 incident
  (`docs/runbooks/operator/incidents/2026-08-13-send-tool-dispatch-shape.md`),
  when the only send tool raised `TypeError` on every call. A committed copy of
  that handler lives in `tests_e2e/_regression/`, and one test asserts the
  harness still fails on it, so "the harness still bites" is checked on every
  run;
- a broker-backed tool that is called but sends the broker an empty or partial
  payload, the second half of the same incident;
- a gate refusing a tool it should not, or a tool that is supposed to be refused
  starting to go through (the refused set is frozen, with the reason each one
  must give);
- the overlay's `pre_tool_call` gates registering in a different order from
  `plugin.yaml`, a banned send reaching the broker, a finished turn missing from
  the audit ledger, or one of the hook-probe plugin's six hooks not firing.

What it does not cover, printed in the job's summary on every run: the gateway
process and its platforms (so `pre_gateway_dispatch` and anything that depends
on a verified sender from an inbound email), MCP connectors, the broker's own
logic, the non-gateway tool wall, and the session-title side call. The temporary
seat starts from a scrubbed environment and refuses any network connection that
is not to its own local stand-ins, so it cannot pick up or send a real
credential. It takes well under a minute once Hermes is installed; the job's
budget is five.

The same job is how a Hermes upgrade is tried before the pin moves. Run the
`operator-substrate` workflow by hand (Actions, then "Run workflow") and put the
candidate's full 40-character commit in `hermes_sha`; left blank, it uses the
Dockerfile's `ARG HERMES_UPSTREAM_SHA`. The job summary names the commit it ran
against, so a promotion PR can cite that run. Whether `overlay-e2e` is a
required check is read from the ruleset, as described under "The merge gates"
below.

To run the harness locally, with a Hermes checkout at the pin (or a candidate):

```bash
cd operator/hermes-smd-overlay
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -e '<hermes checkout>[mcp,anthropic]'
uv pip install --python .venv/bin/python -e '.[e2e]'
HERMES_AGENT_SRC=<hermes checkout> .venv/bin/python -m pytest tests_e2e -x -q --timeout=240
```

Without `HERMES_AGENT_SRC` the harness skips locally and refuses to run in CI.
`tests_e2e/` is outside the overlay's `testpaths`, so a plain `pytest` in that
directory never collects it.

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
  input with a schema (Zod) or into `unknown` narrowed field by field
  (`parseJsonRecord` and `isRecord` in `src/lib/api/helpers.ts`,
  `readLineItemsExact` in `src/lib/db/quote-content.ts`), never
  `JSON.parse(x) as T`. TypeScript types are compile-time only; unvalidated
  external data propagates wrong values until something crashes far from the
  source. It is called the most important rule in the document, and since
  2026-09-25 it is lint-enforced at error under `src/`: a `no-restricted-syntax`
  guard in `eslint.config.js` refuses `JSON.parse(...) as T`,
  `(await r.json()) as T` and `r.json<T>()`.
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
  generation buffer must fit a comfortable working window. Lint-enforced at error
  in `eslint.config.js`; test files are exempt, and the line ceiling alone is
  lifted for the dev component galleries (`**/pages/dev/**/*.astro`) and the
  `customer.yaml` type module (`src/lib/operator/customer-yaml/types.ts`). Split at a cohesion boundary, not mid-function, when approaching
  the cap.

Other mechanically enforced rules from the same document: no `any` in production
source (use `unknown` and narrow), throw `Error` instances not literals, `===`
always (`== null` is the one allowed loose comparison), preserve `cause` on caught
errors, exhaustive `switch` over a union (`switch-exhaustiveness-check`), and named
exports only (defaults allowed only in framework positions like
`*.astro` and the Worker entry).

## The merge gates

Five checks are required to merge to main, all in the `Main Branch Protection`
ruleset: the `Verify` job (`Typecheck, Lint, Format, Test`), `Security Summary`
(`security.yml`), `substrate` and `overlay` (both jobs of
`operator-substrate.yml`; `overlay` joined the required list the day the
overlay fold merged, ADR 0095), and `Runtime ACs carry
a verification ID` (`runtime-ac-proof.yml`, which refuses to mark an acceptance
criterion tagged `(runtime)` as met without a `crane_verify` ID in the PR's
Evidence column). The same ruleset puts main behind a merge queue (2026-10-21):
a green PR is queued and the queue re-runs the five checks on the `merge_group`
event against main plus the PRs ahead of it, so every one of those workflows
must keep its `merge_group` trigger. Probe the ruleset when checking what is
required (`gh api repos/venturecrane/ss-console/rules/branches/main`), and
confirm the legacy surface stays empty
(`gh api repos/venturecrane/ss-console/branches/main/protection` returns 404
once it is retired); a check on either surface is enforced.

Beyond those, this repo carries domain-specific gates that act on policy, not
just on tests. They enforce the no-fabricated-client-facing-content policy in
`CLAUDE.md`:

- `scope-deferred-todo.yml` - fails a PR whose diff adds a `TODO(#NNN)` (a
  deferred acceptance criterion) unless the PR carries the `scope-deferred` label
  and a `## Deferred ACs` section in its description.
- `unmet-ac-on-close.yml` - reopens an issue closed with unchecked acceptance
  criteria.
- `ui-drift-audit.yml` - the source-level UI anti-pattern audit, run on PRs that
  touch `.astro`, `.tsx`, `.jsx` or `.css` files, against thresholds in
  `.ui-drift.json`.
- `security.yml` - the security review workflow (see `/admin/playbook/security-trust`).

These exist because a violation of the content policy is a P0 in this venture, so
the policy is enforced in CI rather than left to review memory.
