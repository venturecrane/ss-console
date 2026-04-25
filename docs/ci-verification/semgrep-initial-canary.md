# Semgrep Initial Canary Verification

**Date:** 2026-04-25
**PR:** #575 (chore/security-semgrep-ci-gate)
**Captain concern addressed:** "Make sure this actually gets implemented correctly and doesn't end up being some theatre we only discover down the road."

This doc captures the pre-merge evidence that the Semgrep CI gate actually catches findings, not just runs and passes. It survives squash-merge as permanent proof the gate was real at installation time.

## Canary file

`scripts/semgrep-canary.ts` was committed to the draft PR with three deliberate `detect-child-process` findings — `execSync` and `spawn` calls where an argument traces back to a function parameter. All three are exact matches for rules in the pinned pack combination.

Canary content (removed before merge):

```typescript
import { execSync, spawn } from 'child_process'

export function canaryChildProcessExec(userName: string): string {
  return execSync(`echo hello ${userName}`).toString()
}

export function canaryChildProcessSpawn(cmd: string): void {
  spawn(cmd)
}

export function canaryExecThird(venture: string): void {
  execSync(`gh repo list ${venture}`)
}
```

## CI run — with canary (RED, as expected)

**Run:** https://github.com/venturecrane/ss-console/actions/runs/24942226215

**Static Analysis (Semgrep) job:** FAILED (expected)

Findings (3 total, 3 blocking — all canary):

```
❯❯❱ javascript.lang.security.detect-child-process.detect-child-process
        Blocking — scripts/semgrep-canary.ts (canaryChildProcessExec)

❯❯❱ javascript.lang.security.detect-child-process.detect-child-process
        Blocking — scripts/semgrep-canary.ts (canaryChildProcessSpawn)

❯❯❱ javascript.lang.security.detect-child-process.detect-child-process
        Blocking — scripts/semgrep-canary.ts (canaryExecThird)
```

Semgrep scan metadata: `Rules run: 126`, `Targets scanned: 549`.

**nosemgrep Justification Audit job:** PASSED — no nosemgrep annotations in codebase.
**TypeScript Validation job:** PASSED.
**Secret Detection job:** PASSED.
**npm audit job:** PASSED.

**Summary job:** FAILED (aggregated as expected — the semgrep job's failure propagates through `needs`).

## Pre-existing findings discovered

Pre-flight local scan (run before commit): **0 findings** — codebase was clean before this PR. CI confirmed the same: only the 3 canary findings, zero pre-existing.

## CI run — canary removed (GREEN)

**Run:** https://github.com/venturecrane/ss-console/actions/runs/24942295516 — all 5 security checks pass (npm audit, Secret Detection, TypeScript, Semgrep, nosemgrep Audit); Security Summary aggregates green.

## Ruleset application

Applied post-merge: `gh api --method POST /repos/venturecrane/ss-console/rulesets --input ~/dev/crane-console/config/github-ruleset-main-protection.json`

**Ruleset ID:** PLACEHOLDER_RULESET_ID
