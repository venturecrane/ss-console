# Program brief: logic in code (read this first, then your workstream brief)

Repo: /Users/scottdurgan/dev/ss-console (venturecrane/ss-console). You work in YOUR OWN git worktree (the harness created it; confirm with `pwd && git rev-parse --show-toplevel`; never cd into the primary checkout, never edit files outside your worktree). Branch from `origin/main` (fetch first; Law 10: your snapshot decays, re-probe before trusting it).

## Winning line

The Operator (SMD's AI employee on the Ashton & Price law-firm seat) can no longer misstate a fact it already had right: a matter number, a dollar figure, a date, a status or a count reaches firm staff either copied by code or not at all. Your workstream is one mechanism of six. Product work; never name a client in code, tests, fixtures or commit messages (synthetic data only; "A&P"/"ashton-price" may appear only where config already names the seat).

## Risk class

Reversible repo work. You edit code, tests and skill text, run suites, commit, push, open a PR. You NEVER: touch a seat (no `fly`, no `seat-probe`, no `ops-handoff`), run anything paid, merge, release, edit `customer.yaml` of a live seat beyond adding a validator-accepted key with no value, or change doctrine files (`docs/doctrine/**`, `.claude/hooks/**`). Those are the team lead's.

## Where the design lives

- The approved plan: /Users/scottdurgan/.claude/plans/nifty-napping-planet.md (your workstream section is the contract; the Premises table carries file:line evidence for every fact you will rely on).
- The audit it executes: /Users/scottdurgan/dev/ss-console/.claude/worktrees/audit-model-as-logic/docs/reviews/model-as-logic-audit-2026-11-04.md (and `-agent-reports.md` beside it).
- The repo rules: /Users/scottdurgan/dev/ss-console/CLAUDE.md and /Users/scottdurgan/dev/ss-console/operator/CLAUDE.md (read both; the Operator CLAUDE.md auto-loads under operator/).

## Engineering standard

Correct, professional, root cause. No quick fixes, no TODO deferrals, no "follow-up issue". Every new rule gets a test that fails without it (run the mutation once and say so in the PR). Parse external inputs, never cast. No module-level mutable state in Workers. File 500 / function 75 / complexity 15 ceilings (`operator/contracts/operator-function-size.json`, pyright baseline `operator/contracts/operator-pyright-baseline.json`: ratchets must not grow). `ruff check` and `ruff format --check` clean (ruff config in `operator/ruff.toml`). No em dashes anywhere you write (code comments, docs, skills, commit messages, PR body). No "coming soon", no placeholder copy. Skill text that firm staff might see follows the Tone rules in CLAUDE.md.

## Suites you must run before pushing (fresh venv; deps are stale, build them)

- Connector: `cd operator/connectors/smokeball && python -m pytest tests -q` (about 1,420 today).
- Overlay: `cd operator/hermes-smd-overlay && python -m pytest tests -q` (about 7,300).
- Broker: `cd operator/workspace_broker && python -m pytest tests -q`.
- Substrate/safety: `cd operator && python -m pytest tests safety_substrate -q` where your change touches shared contracts.
- Root: `npm ci` then `npm run verify` (the repo's merge gate; includes forbidden-strings, doctrine integrity, handbook integrity, customer-yaml parity).
  Run whichever your files touch, AND `npm run verify`. Report exact counts in the PR.

## Commit and PR

Conventional commit, one subject line that names what the FIRM experiences, body with Why / What changed / Tests (counts) / Seat artifacts (image, skills or jobs bundle: say which path lists you touched per `.github/operator-artifact-paths.txt` and `.github/operator-bundle-paths.txt`) / Acceptance criteria table with `(runtime)` rows left UNMET (the lead proves them on seats and ticks them with crane_verify ids; never tick a runtime row yourself). End the PR body with the attribution lines your harness gives you. Use the Edit/Write tools for files; `git add` specific paths; plain `git commit -m` (multi -m flags, no heredoc); `git push -u origin <branch>`; `gh pr create --title ... --body-file <file>`. If the harness refuses the push or the PR, do not retry in other forms: commit locally, and report the branch name plus the exact `git push` and `gh pr create` commands for the lead to run.

## Your scratch files

Write your own scratch files (PR bodies, notes) under `/tmp/<your-workstream-name>/`, never under the lead's scratchpad directory (the one these briefs live in): a sibling agent overwrote the lead's `pr-body.md` there on 2026-11-04.

## When done

Report in one message: the PR URL (or the branch and commands), the test counts, the mutation you ran, what you could not do and why. No stopping-point offers; finish or say why.
