# Complete Code Review - 2026-04-17

Scope: full repository review of SMD Services, with extra attention on client-facing AI slop, fabricated commitments, tenant scoping, test quality, deployment hygiene, and Golden Path readiness.

Reviewer: Codex

## Verdict

Overall grade: C

The project is healthier than the previous 2026-04-16 review in some areas: `npm run verify` passes, `react` is now declared, worker dry-run checks exist in CI, and several prior Pattern A/B strings have been removed. The remaining issues are still serious. The highest-risk problems are not syntax or build failures. They are product integrity issues: client-facing pages still synthesize promises and line items from non-authoritative fields, portal data access still relies too much on entity-only scoping, and tests often lock in implementation strings instead of verifying behavior.

This is the exact shape of AI slop in a production codebase: plausible copy, TODO-backed fallbacks, and brittle tests that make unfinished work look complete.

## Scorecard

| Area                            | Grade | Notes                                                                                                        |
| ------------------------------- | ----- | ------------------------------------------------------------------------------------------------------------ |
| Client-facing content integrity | D     | Multiple live portal and SOW paths still fabricate or infer commitments.                                     |
| Tenant isolation                | C     | User lookup improved, but entity-only portal DAL and time-entry DAL still leave avoidable cross-tenant risk. |
| Test quality                    | C-    | Good volume, weak assertions. Several tests bless unsafe implementation details.                             |
| Build and CI                    | B-    | `npm run verify` passes, but warning noise and generated artifacts hide real problems.                       |
| Deployment hygiene              | C     | Social-listening worker verifies but does not deploy. Runtime binding/config warnings remain.                |
| Dependency/security hygiene     | C     | Audit fails on moderate dev-tool advisories with no current fix.                                             |
| Documentation and agent context | C-    | Agent guidance is partly stale and still contains old client-facing template commitments.                    |
| Architecture maintainability    | C     | Large route files mix data access, business rules, UI, and copy.                                             |

## Verification Run

Commands run:

```bash
npm audit --audit-level=moderate
npm run verify
```

Results:

- `npm run verify` passed.
- Tests: 40 files, 39 passed, 1 skipped; 1152 passed, 2 skipped.
- `npm audit --audit-level=moderate` failed with 5 moderate advisories through the `yaml` package in `@astrojs/check`/language-server tooling. No fix is currently available from npm audit.
- Build and typecheck produce warnings that should not be ignored: generated `coverage/` and `.claude/worktrees/` files are included in checks, the Cloudflare adapter warns about the expected `SESSION` binding, and `src/pages/404.astro` reads request headers while prerendering.

## Critical Findings

### 1. Portal quotes still render derived deliverables from line items

File: `src/pages/portal/quotes/[id].astro:77-93`

The portal quote page prefers authored deliverables, then falls back to line items:

```ts
const deliverables: DeliverableRow[] =
  authoredDeliverables.length > 0
    ? authoredDeliverables
    : lineItems.map((item) => ({
        title: getProblemLabel(item.problem),
        body: item.description ?? '',
      }))
```

This is still Pattern B. Line items are pricing/problem data, not authored client-facing deliverables. The inline comment says the fallback is temporary, but the page is live and client-facing. The test suite also blesses the fallback at `tests/quotes-authored-content.test.ts:439-443`, so CI currently protects the unsafe behavior.

Required fix:

- Remove the fallback from client-visible quote rendering.
- Backfill existing quotes with authored deliverables or mark legacy quotes with an explicit non-client-rendering state.
- Replace the test with a negative assertion that client-visible quote pages never derive deliverables from line items.

### 2. Signed quote next-step copy is synthesized from `scope_summary`

File: `src/pages/portal/quotes/[id].astro:150-157`

The signed-state next step uses authored `next_touchpoint_label` when present, but otherwise builds a new client-facing promise from `engagement.scope_summary`:

```ts
engagement?.scope_summary ? `Kickoff next: ${engagement.scope_summary}.` : null
```

`scope_summary` is not an authored next-step field. This turns a summary into operational commitment copy. That is Pattern A/B hybrid behavior.

Required fix:

- Render only explicit, reviewed next-step copy.
- If `next_touchpoint_label` is missing, render nothing or an explicit internal/admin state. Do not compose a promise from `scope_summary`.

### 3. Portal dashboard fabricates next-check-in promises

File: `src/pages/portal/index.astro:272-277` and `src/pages/portal/index.astro:403-416`

When no authored touchpoint exists, the portal promises that the consultant will reach out:

```ts
;`${consultantFirst} will reach out to schedule the next check-in.`
```

and:

```astro
{consultantFirst} will reach out to schedule the next touchpoint.
```

These are client-facing future behavior promises with no authored engagement data behind them.

Required fix:

- Remove both fallbacks.
- Require `next_touchpoint_label` or `next_touchpoint_at` for any next-step copy.
- If no next touchpoint is authored, render a neutral status only.

### 4. Invoice detail page invents invoice line-item descriptions

File: `src/pages/portal/invoices/[id].astro:84-97`

If no invoice line items exist, the page displays:

```ts
description: invoice.description ?? engagement?.scope_summary ?? 'Engagement work'
```

`Engagement work` is fabricated invoice content. `scope_summary` is also not necessarily authored as invoice line-item copy. This is especially risky because invoices are payment-facing records.

Required fix:

- Require invoice line items before an invoice can be sent.
- Remove the fallback row from client-visible rendering.
- Add a send gate and a behavior test that a sent invoice without line items fails before client exposure.

### 5. Invoice payment CTA can point to a nonexistent endpoint

File: `src/pages/portal/invoices/[id].astro:113-120`, rendered at `src/pages/portal/invoices/[id].astro:324-334` and `src/pages/portal/invoices/[id].astro:506-520`

When `stripe_hosted_url` is absent or `#dev-mode`, the page falls back to:

```ts
const payHref = stripeUrl ?? `/api/invoices/${invoice.id}/pay`
```

No matching route exists. A repo search only finds the TODO and the fallback. Tests at `tests/invoices.test.ts:495-499` only check that `payHref` exists, not that the link works.

Required fix:

- Do not render a payment CTA unless a real hosted payment URL exists, or implement the server route behind the CTA.
- Add a route existence or integration test for the payment CTA path.

## High Findings

### 6. SOW template still contains hardcoded post-signing and stabilization commitments

File: `src/lib/pdf/sow-template.tsx:538-553` and `src/lib/pdf/sow-template.tsx:590-594`

The PDF still hardcodes future behavior:

- Start date confirmation after deposit clears.
- Stabilization period after handoff.
- Deposit invoice after signing.
- Kickoff date confirmation after deposit clears.

The previous fixed-duration phrases were removed, but the template still contains universal commitments. Per `CLAUDE.md:47-66`, post-signing promises must come from authored engagement data or an explicitly reviewed source file.

Required fix:

- Move SOW terms and next-step copy into authored SOW fields, or document a Captain-approved template decision that these commitments apply to every engagement.
- Update tests to reject generic post-signing promise templates unless those terms are sourced from reviewed SOW data.

### 7. SOW design documentation still preserves old fixed-timeframe commitments

File: `docs/templates/sow-template.md:299-303` and `docs/templates/sow-template.md:331-333`

The template documentation still says:

- Confirm start date within 1 business day.
- A 2-week stabilization period follows handoff.
- Confirm kickoff date within one business day.

This conflicts with current code and the no-fabricated-content rule. Because agents use repo docs as context, stale template docs can reintroduce bad commitments.

Required fix:

- Update the template docs to match the current content standard.
- Add a docs scan for fixed timeframes in client-facing templates.

### 8. Portal entity resolution fetches client entity without org scope

File: `src/lib/portal/session.ts:33-45`

The user lookup is scoped by `userId` and `orgId`, but the entity fetch is not:

```ts
SELECT * FROM entities WHERE id = ?
```

If `users.entity_id` is ever stale, migrated incorrectly, or compromised, the portal can resolve an entity outside the session org. The comment says org scope prevents cross-org access, but the second query does not enforce that.

Required fix:

- Change the entity query to `WHERE id = ? AND org_id = ?`.
- Add a regression test with a user whose `entity_id` points to a different org.

### 9. Portal quote and invoice DAL intentionally avoids org scoping

Files:

- `src/lib/db/quotes.ts:410-445`
- `src/lib/db/invoices.ts:298-334`
- `tests/portal-quotes.test.ts:16-45`

The DAL comments explicitly say portal functions are scoped by `entity_id (NOT org_id)`. The tests assert that `orgId` is not accepted or used. Entity IDs are expected to be unique, but this is a weak tenant boundary for a multi-tenant admin/portal system.

Required fix:

- Pass `session.orgId` into portal quote and invoice DAL functions.
- Scope by both `entity_id` and `org_id`.
- Replace tests that prohibit `orgId` with tests that require it.

### 10. Portal dashboard follow-up queries drop org scope after finding the engagement

File: `src/pages/portal/index.astro:91-132`

The active engagement query scopes by `entity_id` and `org_id`, but follow-up invoice and milestone queries use only `engagement_id`. The quote list uses only `entity_id`.

Required fix:

- Add `AND org_id = ?` to invoice, milestone, and quote queries.
- Bind `session.orgId` in every portal query, not just the first one.

### 11. Time-entry DAL can update and delete by raw ID

Files:

- `src/lib/db/time-entries.ts:53-89`
- `src/lib/db/time-entries.ts:131-197`
- `src/pages/api/admin/time-entries/[id].ts:43-87`

The API verifies the parent engagement belongs to the admin org before update/delete, which reduces practical risk. The DAL still exposes unsafe primitives:

- `getTimeEntry(db, id)` with no org scope.
- `updateTimeEntry(db, id, data)` with `UPDATE time_entries SET ... WHERE id = ?`.
- `deleteTimeEntry(db, id)` with `DELETE FROM time_entries WHERE id = ?`.
- `recalculateActualHours(db, engagementId)` with no org scope on the engagement update.

Required fix:

- Thread `orgId` through every time-entry DAL function.
- Scope reads, writes, deletes, and actual-hours recalculation by org.
- Replace string-inspection tests in `tests/time-entries.test.ts` with behavior tests that prove cross-org IDs cannot be changed.

### 12. Turnstile configuration can silently disable bot protection or break forms

Files:

- `wrangler.toml:31-36`
- `src/pages/book.astro:10`
- `src/pages/book.astro:508-606`
- `src/lib/booking/turnstile.ts:35-43`
- `src/pages/api/booking/reserve.ts:56-65`
- `src/pages/api/intake.ts:39-47`

`PUBLIC_TURNSTILE_SITE_KEY` is set to an empty string in `wrangler.toml`. The client skips the widget when that value is empty. The server skips verification only when `TURNSTILE_SECRET_KEY` is missing; if the secret is present and the public key is empty, submissions fail with missing token. If both are absent, production can accept submissions without Turnstile.

Required fix:

- Make Turnstile configuration explicit per environment.
- Fail startup/build or render an admin-visible configuration error when one key exists without the other.
- Do not let production silently bypass verification because a secret is missing.

## Medium Findings

### 13. Social-listening worker is verified but not deployed

Files:

- `.github/workflows/verify.yml:53-63`
- `.github/workflows/deploy.yml:54-85`

CI dry-run builds four workers, including `workers/social-listening`. Deployment only deploys job-monitor, new-business, and review-mining. If social-listening is a real worker, changes can pass CI and never reach production.

Required fix:

- Add install and deploy steps for `workers/social-listening`, or remove it from verify if it is intentionally dormant.

### 14. Lint/typecheck include generated and worktree artifacts

Files:

- `eslint.config.js:18-29`
- `eslint.config.js:44-46`
- `tsconfig.json:6`

`npm run verify` passes but emits warning noise from generated `coverage/` files and `.claude/worktrees/`. Real source warnings are mixed into generated-output warnings. `no-unused-vars` and `no-explicit-any` are warnings, not errors.

Required fix:

- Ignore `coverage/**` and `.claude/worktrees/**` in lint, format, and typecheck.
- After the noise is removed, promote unused vars and explicit `any` to errors for source files.

### 15. SOW PDF rendering tests are skipped

File: `tests/sow-render.test.ts:58-68`

Both SOW PDF render tests are skipped. The comment says build and live deployment validate rendering, but there is no automated test asserting a valid PDF for contractual documents.

Required fix:

- Add a compatible render smoke test, even if it runs in a narrower environment than full Vitest.
- At minimum, cover the API route that returns the PDF bytes and assert PDF shape, response type, and non-empty content.

### 16. Tests frequently inspect source strings instead of behavior

Examples:

- `tests/portal-quotes.test.ts:16-45` asserts unsafe lack of `orgId`.
- `tests/quotes-authored-content.test.ts:439-443` asserts a fallback exists instead of proving it is safe.
- `tests/invoices.test.ts:495-499` checks that `payHref` exists, not that it points to a real route.
- `tests/time-entries.test.ts:36-80` checks substrings in DAL source instead of behavior.

Required fix:

- Replace source-text tests with route/DAL behavior tests.
- For client-facing content, test rendered output and provenance.
- For tenant scoping, use cross-org fixture rows and assert denial.

### 17. Large route files concentrate business logic and copy

Largest examples:

- `src/pages/admin/entities/[id]/quotes/[quoteId].astro` - 1004 lines
- `src/pages/admin/engagements/[id].astro` - 909 lines
- `src/pages/book.astro` - 842 lines
- `src/pages/admin/entities/[id].astro` - 811 lines
- `src/pages/portal/quotes/[id].astro` - 603 lines
- `src/pages/portal/invoices/[id].astro` - 550 lines

These files mix SQL, authorization assumptions, view-model assembly, client copy, UI, and inline browser scripts. That structure makes fabricated copy and inconsistent scoping more likely.

Required fix:

- Move portal/admin view-model construction into testable library modules.
- Keep Astro pages focused on rendering.
- Centralize client-facing copy resolvers with provenance requirements.

### 18. Agent guidance is stale enough to mislead future agents

File: `CLAUDE.md:17-28`

The file still says this is not a product codebase and that there is no app to build yet. The repository now contains a real Astro app with admin, portal, booking, quotes, invoices, SOW generation, auth, D1, KV, R2, and Workers.

Required fix:

- Update agent guidance to describe the actual application surface.
- Keep the no-fabricated-content rules, but remove stale repo-purpose statements.

### 19. Cloudflare session binding warning remains unresolved

Files:

- `wrangler.toml:58-61`
- `src/middleware.ts:103-107`

The app code uses `env.SESSIONS`, and `wrangler.toml` binds `SESSIONS`. Build output warns that the Cloudflare adapter expects a `SESSION` binding for Astro sessions. This might be benign if Astro sessions are not used, but the warning appears on every build and can hide real deployment warnings.

Required fix:

- Confirm whether Astro adapter sessions are enabled or needed.
- Either configure the adapter to stop requesting `SESSION`, or add the expected binding intentionally.

### 20. `404.astro` reads request headers while prerendering

Build warning:

```text
Astro.request.headers was used when rendering the route src/pages/404.astro.
```

Required fix:

- If 404 needs request headers, make it server-rendered.
- If it does not, remove the request-header dependency.

## Low Findings

### 21. Root Cloudflare worker types are pinned to an old date

File: `tsconfig.json:3-5`

The root type config still uses `@cloudflare/workers-types/2023-07-01` even though `package.json` installs `@cloudflare/workers-types` version `^4.20260417.1`.

Required fix:

- Use the current package-provided types path unless there is a documented reason to pin.

### 22. Open issues appear stale after fixes

Observed issue state:

- Issue #409 is still open, but `package.json:23-33` now declares `react` and `npm run verify` passes.
- Issue #399 is still open, but the milestone DAL appears to have been improved since the prior review.
- Issue #404 remains partially open because worker checks and Wrangler versions improved, but root worker types remain stale.

Required fix:

- Reconcile open code-review issues against current code.
- Close fixed issues with evidence and split remaining acceptance criteria into smaller issues.

## Existing Strengths

- The repo has a single `npm run verify` Golden Path that runs typecheck, worker typecheck, format check, lint, build, and tests.
- The test suite is broad enough to catch many regressions once assertions are made behavior-first.
- Prior audit fixes did remove several fixed-duration and generic consultant fallbacks.
- Auth middleware consistently attaches sessions and renews sessions through a shared helper.
- D1 queries are parameterized in the reviewed surfaces.

## Recommended Fix Order

1. Remove all remaining client-facing fabricated content in quotes, invoices, portal dashboard, and SOW PDF.
2. Add org scoping to portal client resolution, quote/invoice DAL, dashboard queries, invoice line items, and time-entry DAL.
3. Replace tests that bless unsafe source strings with behavior tests for provenance and tenant isolation.
4. Fix the broken invoice payment CTA and Turnstile production config behavior.
5. Clean lint/typecheck inputs so generated artifacts and old worktrees do not hide real warnings.
6. Reconcile deployment CI with actual worker inventory.
7. Update stale agent guidance and SOW template documentation.

## Issue Coverage Notes

Likely overlaps with existing issues:

- #398: Pattern A/B violations.
- #399: DAL tenant scoping.
- #404: platform hygiene.
- #409: React dependency, likely fixed in code.
- #362: invoice payment endpoint TODO, referenced from source.

New or not cleanly covered:

- Turnstile production config mismatch.
- Social-listening worker verified but not deployed.
- Tests that explicitly protect unsafe no-org-scope and fallback behaviors.
- Stale agent guidance saying this is not an app codebase.

I did not create VCMS notes or GitHub issues during this review. Repo guidance requires explicit approval for VCMS writes, and issue creation should be done after deciding whether to update existing issues or split new ones.
