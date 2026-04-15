# Branch Protection Setup

Required status checks to enforce on `main`. Repo-admin access required.

**Settings URL:** https://github.com/venturecrane/ss-console/settings/branches

## Steps

1. Go to the URL above.
2. Under "Branch protection rules", click **Add rule** (or edit the existing `main` rule if one exists).
3. Set **Branch name pattern** to `main`.
4. Check **Require status checks to pass before merging**.
5. Check **Require branches to be up to date before merging**.
6. In the search box, find and check the following status checks:

   | Check name                             | Workflow                  |
   | -------------------------------------- | ------------------------- |
   | `Block undeferred TODO(#NNN) patterns` | `scope-deferred-todo.yml` |
   | `Typecheck, Lint, Format, Test`        | CI workflow               |
   | `npm audit (high+)`                    | audit workflow            |

7. Click **Save changes**.

## Note on check visibility

A status check only appears in the dropdown after the workflow has run at least once on a PR targeting `main`. The `Block undeferred TODO(#NNN) patterns` check became available after PR #379 merged — it should appear in the search now.

If `Typecheck, Lint, Format, Test` or `npm audit (high+)` are already required, no action needed for those rows.
