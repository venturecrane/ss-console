<!--
PR template enforces #377 process change. Every section is required unless
explicitly marked optional. Reviewers: do not approve a PR that leaves the
"Acceptance criteria status" or "Linked issue" sections blank or templated.
-->

## Summary

<!-- 2-3 sentences. What changed and why. -->

## Linked issue

<!--
Use one of:
  Closes #NNN     — this PR fully resolves the issue
  Refs #NNN       — this PR is partial; the issue stays open
  None            — only for chores with no associated issue (rare; explain)
-->

Closes #

## Acceptance criteria status

<!--
For each acceptance criterion in the linked issue, state which commit/file
satisfies it OR mark it deferred with `scope-deferred` label + rationale below.

Do not skip ACs you didn't touch — list them all and mark them as already-met,
N/A, or deferred. Reviewers approve based on this table.
-->

| AC (verbatim from issue) | Status               | Evidence                         |
| ------------------------ | -------------------- | -------------------------------- |
|                          | met / deferred / n/a | commit / file:line / explanation |

## Deferred ACs (required if `scope-deferred` label is set)

<!--
Only fill this section if you are deferring one or more ACs. Each deferred AC
needs a rationale and a follow-on issue. The `scope-deferred` label is what
unblocks the TODO-in-source CI check (#377 Move 1) — without this section
filled in, that check will fail.
-->

- **AC:** _(verbatim text)_
  - **Why deferred:** _(scope, dependency, infra gap, etc.)_
  - **Tracked in:** #NNN

## Test plan

- [ ] `npm run verify` passes
- [ ] _(feature-specific manual verification)_

🤖 Generated with [Claude Code](https://claude.com/claude-code)
