<!--
Enterprise PR template. Every section is required unless explicitly marked
optional. Reviewers: do not approve a PR that leaves the "Acceptance
criteria status" or "Linked issue" sections blank or templated. AC
auto-ticking on merge is documented at:
https://github.com/venturecrane/crane-console/blob/main/docs/runbooks/ac-tick-workflow-rollout.md
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
unblocks any TODO-in-source CI check — without this section filled in, that
check will fail.
-->

- **AC:** _(verbatim text)_
  - **Why deferred:** _(scope, dependency, infra gap, etc.)_
  - **Tracked in:** #NNN

## Test plan

- [ ] `npm run verify` passes
- [ ] _(feature-specific manual verification)_

## Security Checklist

<!--
Pause and check each item. Self-attestation is the floor; the Semgrep gate
and Secret Detection workflow are the belt-and-suspenders.
-->

- [ ] No secrets in code or comments
- [ ] No PII exposed in frontend responses
- [ ] Input validation on new endpoints
- [ ] Parameterized queries for any SQL (use `.bind()`)
- [ ] Auth required on new endpoints
- [ ] No internal IDs that enable enumeration

## Feature Impact

<!--
Does this PR remove, disable, or change any existing user-facing
functionality?

Write "None" if no existing features are affected.
Write "Authorized — #{directive}" if the Captain approved the removal/change.
If neither applies, STOP — fetch `crane_doc('global', 'guardrails.md')`.
-->

**Feature impact:** None

## Deployment Notes

<!-- Schema migrations, env vars, secret rotations, manual ops? Leave blank if standard deploy. -->

🤖 Generated with [Claude Code](https://claude.com/claude-code)
