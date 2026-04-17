# Code Review: SMD Services

**Date:** 2026-04-16
**Reviewer:** Claude Code (automated)
**Scope:** Full codebase
**Mode:** Full (Phase 1, Claude-only)
**Models Used:** Claude
**Golden Path Tier:** 1 (default; not explicitly listed in compliance dashboard)

## Summary

**Overall Grade: C** (improved from D on 2026-04-07)

The codebase has strong bones — middleware, three-subdomain routing, webhook security, and multi-tenant API patterns are correctly implemented and tested. Prior-review security fixes (#172 cross-org authz, #173 canonical base URLs) are real, verified end-to-end, and holding. Test count grew from 839 to 1,120. The regression lies in compliance: CLAUDE.md documents six Pattern A/B violations by file and line as P0, and all six are still present in current code. Two existing test cases actively assert the prohibited strings as correct, which means the intended remediation will produce test failures that look like regressions.

## Scorecard

| Dimension     | Grade | Trend     |
| ------------- | ----- | --------- |
| Architecture  | B     | stable    |
| Security      | D     | stable    |
| Code Quality  | D     | regressed |
| Testing       | B     | regressed |
| Dependencies  | B     | improved  |
| Documentation | B     | improved  |
| Golden Path   | B     | improved  |

## Detailed Findings

### 1. Architecture

Findings:

1. [high] `src/pages/portal/invoices/[id].astro:72` — Engagement lookup `FROM engagements WHERE id = ?` omits `org_id`. The `invoice.engagement_id` is already loaded scoped to the portal client, so practical exploitability is low in the normal path, but the pattern is wrong. Fix: add `AND org_id = ?` using `client.org_id`.
2. [high] `src/pages/portal/index.astro:83-86` — Active-engagement query uses `WHERE entity_id = ?` only. Fix: add `AND org_id = ?`.
3. [medium] `src/pages/admin/entities/[id]/quotes/[quoteId].astro` (1004 lines), `src/pages/admin/engagements/[id].astro` (909 lines), `src/pages/admin/entities/[id].astro` (811 lines) — mix data loading, business rules, and rendering. Fix: extract queries to `src/lib/db/`; keep pages presentational.
4. [medium] `src/pages/portal/engagement/index.astro:82-88` — inline cast `(engagement as { consultant_name?: string | null } | null)` instead of using the typed `listEngagements` DAL. Fix: switch to `listEngagements(db, orgId, entityId)`.
5. [low] `src/lib/db/milestones.ts:69-74` — `listMilestones` takes only `engagementId`, no `org_id`. Callers have already scoped, but the function is not safe on untrusted input. Fix: add `orgId` param and join via engagements.

Grade: **B**
Rationale: Structure is coherent (middleware + subdomains + DAL are correct). 15 files exceed 500 lines, but the admin detail pages mixing concerns is the main architectural cost. No architectural regression since 2026-04-07.

### 2. Security

Findings:

1. [critical] `src/pages/api/admin/engagements/[id]/milestones.ts:75-76` — `getMilestone(env.DB, milestoneId)` has no `org_id` predicate. Endpoint validates the engagement ID is org-scoped but then calls `getMilestone` by ID alone, and `deleteMilestone`/`updateMilestone` do the same. Defense-in-depth failure: the only guard is an `engagement_id` comparison, not tenant scoping. Fix: add `org_id` to the `milestones` schema (or join through engagements); scope every milestone mutation by it.
2. [high] `src/lib/portal/session.ts:33` — `getPortalClient` queries `FROM users WHERE id = ? AND role = 'client'` with no `org_id`. Fix: accept `orgId` and add `AND org_id = ?`.
3. [medium] `src/pages/portal/invoices/[id].astro:72` — engagement lookup missing `org_id` (also listed under Architecture).
4. [verified fixed] `#172` — `src/pages/api/admin/resend-invitation.ts:57-61` scopes SELECT and UPDATE to `org_id` and `role = 'client'`. `tests/admin/resend-invitation.cross-org.test.ts` exercises this end-to-end.
5. [verified fixed] `#173` — `requirePortalBaseUrl`, `buildPortalUrl`, `buildAdminUrl`, `requireAppBaseUrl` from `src/lib/config/app-url.ts` used in `resend-invitation.ts:102`, `invoices/[id].ts:125`, `follow-ups/[id].ts:99`, `sow/service.ts:220`. No `request.headers.get('host')` or `new URL(request.url).origin` patterns found in auth/outbound-link paths.
6. [low] `src/pages/api/webhooks/signwell.ts` — HMAC-SHA256 verification with constant-time comparison; 300s replay window; no payload action before hash check. No issues.
7. [low] No hardcoded API keys or bearer tokens in `src/`. `sk-test-123` in `assessment-to-quote.test.ts:143` is a test fixture.

Grade: **D**
Rationale: Per rubric, any critical finding lands at D or F. The milestone gap is on a mutation endpoint; mitigated by the engagement-ownership check but not resolved. Prior-review authz/URL fixes are real.

### 3. Code Quality

Findings:

1. [critical — Pattern A] `src/lib/portal/states.ts:143` — fallback `"We'll reach out to schedule kickoff."`. Test `tests/portal-states.test.ts:107` asserts this string is present — the test is enforcing the violation. Fix: fallback to `null`; widen `ProposalSurface.next` to `string | null`; render nothing when null; flip the test assertion.
2. [critical — Pattern A] `src/pages/portal/quotes/[id].astro:77` — `const startWindowText = 'Work begins within two weeks of signing.'`. Fix: delete the variable entirely; any timeline surface must come from authored engagement data.
3. [critical — Pattern A] `src/components/portal/ConsultantBlock.astro:138,154` — `"Replies within 1 business day."` in both SMS and mailto branches. Fix: remove from both.
4. [critical — Pattern A] `src/lib/pdf/sow-template.tsx:539,593` — `"within 1 business day of receiving the deposit"` (Term 2) and `"within one business day"` (Next Steps). Although the SOW is contractual, these strings are baked into the universal template, not authored per engagement. Fix: move to authored fields on the engagement/SOW.
5. [critical — Pattern A] `src/lib/pdf/sow-template.tsx:550` — `"A 2-week stabilization period follows the final handoff."` Test `tests/sow-template.test.ts:219` asserts `toContain('2-week stabilization period')`. Fix: remove the string; flip the test.
6. [critical — Pattern B] `src/pages/portal/invoices/[id].astro:202` — `const consultantFirstName = consultantName ? ... : 'Scott'`. A named-person fallback rendered to clients when engagement has no consultant set. Fix: return `null`; hide consultant affordances.
7. [critical — Pattern B] `src/pages/portal/quotes/[id].astro:200` and `src/pages/portal/index.astro:306` — `consultantFirst ?? 'Scott'` passed to `PortalHeader`. Fix: accept `string | null`; hide consultant UI when null.
8. [critical — Pattern B] `src/pages/portal/engagement/index.astro:85-88` — `?? 'Scott'` fallback. Same fix as above.
9. [high] `src/pages/portal/quotes/[id].astro:157` — `We'll ${engagement.next_touchpoint_label.toLowerCase()}.` prepends `"We'll "` to authored data; fragile composition. Fix: render authored label directly.
10. [clean] No `as any`, `@ts-ignore`, or `@ts-expect-error` found in `src/`.

Grade: **D**
Rationale: Six critical CLAUDE.md compliance violations in current code, explicitly catalogued in CLAUDE.md as P0 from the 2026-04-15 audit. Two tests enforce the violations as correct. Regressed from B.

### 4. Testing

Findings:

1. [high] `tests/sow-template.test.ts:219` — asserts the prohibited `"2-week stabilization period"` string exists. Fix: flip to `not.toContain` after the template fix.
2. [high] `tests/portal-states.test.ts:107` — asserts `"kickoff"` fallback string present. Fix: assert `surface.next === null` after states.ts fix.
3. [high] `vitest.config.ts` — no `coverage.thresholds` block. Fix: add a baseline (e.g., 60/60/60) and fail the build if not met.
4. [medium] `tests/sow-render.test.ts` — 2 tests skipped. Fix: un-skip or justify with a comment.
5. [medium] No cross-org test for `getMilestone` / milestones endpoint. Fix: parallel to `tests/admin/resend-invitation.cross-org.test.ts`.
6. [positive] Test count grew from 839 → 1120 across 36 files. Webhook handler test (`src/lib/webhooks/signwell-handler.test.ts`, 533 lines) is thorough.

Grade: **B**
Rationale: Strong breadth but two tests actively enforce prohibited content, no coverage threshold, and SOW render tests are skipped.

### 5. Dependencies

Findings:

1. [medium] `npm audit` — 5 moderate advisories via `yaml` → `yaml-language-server` → `volar-service-yaml` → `@astrojs/language-server` → `@astrojs/check` (GHSA-48c2-rrv3-qjmp, deep-nesting DoS). All devDependencies (typecheck tooling). Fix: accept or pin a hotfix once upstream ships; `npm audit fix` would roll `@astrojs/check` back a major.
2. [resolved] Prior high-severity advisories from #174 are gone. Audit shows zero high or critical.
3. [medium] `workers/job-monitor/package.json` pins `wrangler: ^4.0.0`; root is `^4.78.0`. Fix: align to `^4.78.0`.
4. [low] `tsconfig.json` pins `@cloudflare/workers-types` to `2023-07-01`. Fix: bump to the 2025 vintage.
5. [low] No `eval` / `new Function` in `workers/` source. Runtime compatibility clean.

Grade: **B**
Rationale: Moderate devDep findings only; runtime dep graph is clean.

### 6. Documentation

Findings:

1. [high] `CLAUDE.md` lists five Pattern A violations with file:line as P0 from the 2026-04-15 audit. Code still contains them. This is an enforcement gap, not a documentation gap.
2. [medium] No API documentation for `src/pages/api/**`. Endpoints like `POST /api/admin/engagements/:id/milestones` carry multiple action modes with no single reference. Fix: add `docs/api/` or OpenAPI.
3. [low] `docs/adr/decision-stack.md` exists and is current (29 decisions, up-to-date Quick Reference).
4. [low] `src/pages/portal/engagement/index.astro` lacks a header reference to the portal-UX brief. Fix: link if one exists.

Grade: **B**
Rationale: CLAUDE.md and ADRs are strong; API reference is the main gap.

### 7. Golden Path Compliance (Tier 1)

Findings:

1. [verified] `CLAUDE.md` exists and is comprehensive.
2. [verified] `tsconfig.json` extends `astro/tsconfigs/strict`; no TS escape hatches in `src/`.
3. [verified] `eslint.config.js` (flat) with `eslint-plugin-astro` + `typescript-eslint`; `npm run verify` runs lint.
4. [verified] `.github/workflows/scope-deferred-todo.yml` and `unmet-ac-on-close.yml` are active merge/close gates.
5. [medium] `tsconfig.json` excludes `workers/`. No CI step runs worker typecheck. Fix: add `workers/job-monitor` typecheck to `verify.yml`.
6. [verified] No hardcoded secrets in `src/`.

Grade: **B**
Rationale: Process controls solid; worker CI gap is the one non-critical miss.

## Model Convergence

N/A — Phase 1 Claude-only.

## Trend Analysis (vs 2026-04-07)

- Overall: D → **C** (improved)
- Architecture: B → B (stable)
- Security: D → D (stable; two big fixes, one new gap)
- Code Quality: B → D (regressed; compliance violations)
- Testing: A → B (regressed; no threshold, enforcing-violation tests)
- Dependencies: D → B (improved; prior high advisories resolved)
- Documentation: C → B (improved)
- Golden Path: C → B (improved)

**Prior-review issues:**

- #172 (cross-org authz) — verified fixed
- #173 (canonical APP_BASE_URL) — verified fixed
- #174 (dep advisories) — resolved for high/critical; 5 moderate devDep remain

**3 of 3 prior findings resolved.**

## File Manifest Summary

- `src/` ~35,286 LOC across `.ts/.tsx/.astro` (excluding tests)
- 1,924 `.ts`, 53 `.astro`, 2 `.tsx`, 23 `.sql`, 21 `.toml`
- 15 files > 500 lines (top: `quotes/[quoteId].astro` 1004, `engagements/[id].astro` 909, `sow/service.ts` 857)
- 36 test files, 1,120 tests
- CI: `deploy.yml`, `verify.yml`, `security.yml`, `scope-deferred-todo.yml`, `unmet-ac-on-close.yml`

## Raw Model Output

Full Claude agent output is captured in-session; findings above are the synthesized form. No Codex/Gemini runs (Phase 1).
