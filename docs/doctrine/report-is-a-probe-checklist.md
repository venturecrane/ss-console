# The Report Is a Probe (Law 14)

**Registry entry:** `docs/doctrine/agent-operating-doctrine.md`, Law 14. This page is the review-time mechanism that entry points to. Read it before shipping, and answer it in the PR, for any module that emits a manifest, a completion record, a wired or unwired map, a `*_deleted` or `*_done` boolean, or a "nothing to do" exit.

## Why it exists

The 2026-09-10 code review found four new mediums in one eleven-commit window, and they were one shape. Each was a program that acted on the world and then reported on the result from the statement it ran rather than from the world afterwards:

- `operator/bin/lib/decommission_backends.py` returned `fleet_status_row_deleted: True` after a `DELETE` whose result set is empty by construction. A deleted row and a matched-nothing produced the same manifest.
- The substrate required check's conformance test pinned the test files, not the fixture modules those tests execute. A fixture-only PR merged reporting "No substrate paths changed".
- `src/pages/api/admin/clients/[id]/operator-price.ts` wrote the retainer price and the payment rail as two awaits with no batch. A failed second write left the row half-updated behind one generic error.
- `--allow-unwired` on the decommission CLI said DEV/FIXTURE ONLY in its help text and nothing enforced it. A live run with the flag destroyed every wired backend while printing that it "does NOT fully decommission the customer".

All four had passing tests. What each lacked was the read-back. This is Law 12 (a check that cannot fail has measured nothing) applied to shipped code instead of an agent's own verification, and it is the same failure class as Law 9 (the deliverable is the act) and the gone-means-gone rule in CLAUDE.md, one level down: a program's report is a claim about the world, and a claim is earned by observing, never by intending.

## The five questions

Answer each in one sentence in the PR body, under a heading "Report is a probe", for every module the PR adds or changes that emits a report of the kind above. "Not applicable" is an answer only when the module emits nothing of that kind.

1. **Does it read the world back after acting?** After the DELETE, the SELECT. After the write, the read. After the deploy, the probe. If the report is composed from the arguments the module was called with or the statements it issued, it is an echo, and the fix is a read.
2. **What would the report say if the action had matched nothing?** Run the module against a target that does not exist, or a row already gone, or a flag already set. If the report is identical to the success case, it cannot fail, and by Law 12 it has measured nothing. The counting-D1 fake in `operator/bin/tests/test_decommission_backends.py` is the model: the test asserts what the manifest says when the DELETE matched zero rows.
3. **Does every flag or mode whose name or help text says "only" have a guard?** `--allow-unwired` said DEV/FIXTURE ONLY. `--dry-run` says dry. A test-only path says test. Each of those is a promise the code must keep with a refusal, not with prose. Name the guard and the test that trips it.
4. **Does the CI gate's own conformance test cover the inputs its tests execute, not just the tests?** A gate that lists test files knows which tests exist, not what they read or run. If a test opens a fixture, executes a script, or imports a module from a directory the gate's trigger list does not name, a change to that input merges without the gate running. The conformance test walks the inputs; it does not stop at the test file.
5. **Can the report be produced without the action having run?** If a manifest can be assembled by a code path that never reached the destructive step, the manifest is decoration. Every field that claims an effect is populated from the observation of that effect, and a field that could not be observed says so (`table_present: false`, `rows_remaining: unknown`), never `true`.

## What "yes" looks like

The observability backend after the 2026-09-10 wave: it counts the rows for the slug before the DELETE and after, reports `rows_deleted` as an integer and `rows_remaining` as the read-back count, sets the `_deleted` boolean only when the remaining count is zero, and reports a missing table as absent rather than raising or assuming. The manifest changes when the world does not.

## Relationship to the other laws

- **Law 9** (the deliverable is the act) is this rule for a PR's own status table.
- **Law 12** (a check that cannot fail) is this rule for the checks an agent runs on its own work.
- **Gone means gone** (CLAUDE.md) is this rule for removals: a negative probe per runtime layer, recorded through `crane_verify`, never the diff that deleted the artifact from git.

They share one sentence. Intent is not observation.
