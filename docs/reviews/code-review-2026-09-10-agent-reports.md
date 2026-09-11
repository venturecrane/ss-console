---
title: Code Review 2026-09-10 Agent Reports
---

# Code Review 2026-09-10: Agent Reports

The four agent reports behind [code-review-2026-09-10.md](./code-review-2026-09-10.md), preserved verbatim. Each agent wrote its report to disk before returning. The orchestrator re-probed every claim it graded on; where an orchestrator number differs, the main report says so. These files are the raw evidence and are not edited for style, so they may carry markers the main report avoids.



<!-- ===== agent: arch-src ===== -->

## Review: Architecture + Code Quality (TypeScript side)

**Agent:** src-arch-quality
**Scope:** `src/`, `workers/`, `scripts/`, `.claude/hooks`, and `tests/` where it bears on those two dimensions. `operator/` (Python) belongs to a sibling agent; `operator/*.ts` (16 files) is touched here only where the dead-export gate reaches it.
**Commit:** 1da55373 (== origin/main at review start). All probes run at that commit, most from the isolated worktree `.claude/worktrees/code-review-2026-09-10` (verified `git log --oneline -1` → 1da55373, clean); `npm run lint`, `npm run deadcode` and `npx vitest run` were run from the primary checkout at the identical commit because the worktree has no `node_modules`.
**Previous review:** docs/reviews/code-review-2026-09-09.md — Architecture C, Code Quality C.

---

## Headline

The window (11 commits) landed two changes aimed squarely at this dimension pair, and both worked. `#2728` moved the dead-export gate to `knip --production` and made the `@public` waiver a checked artifact; `#2732` deleted 30 files, plus the exports and tests only they consumed. **Both carried Code Quality HIGHs are closed at the gate layer, not merely at the symptom layer**, and I could not find an unwaived dead export by an independent method. Architecture moved less: of ten carried `src/`-side findings, one is closed, three shrank by one or two units, and six are unchanged.

---

## 1. Architecture

### 1.1 Carried findings, re-probed at HEAD

| # | Carried finding (2026-09-09) | Verdict at HEAD | Instrument |
|---|---|---|---|
| A | Dead admin OAuth callback + middleware exemption (`src/middleware.ts:120`) | **OPEN-CHANGED** (scope corrected — smaller than implied) | see 1.2 |
| B | Two helper modules registered as public endpoints | **OPEN-UNCHANGED** | see 1.3 |
| C | `src/lib/db/` reaching up into stripe / follow-ups / sow / booking (4 edges) | **OPEN-UNCHANGED, and one new edge of a different kind** | see 1.4 |
| D | Raw SQL in 17 route files | **OPEN-CHANGED**: 16 files | `grep -rln "DB.prepare(\|db.prepare(" src/pages` |
| E | Operator domain split (`src/lib/operator` vs `src/lib/portal/operator`), 27 edges one way / 1 back | **OPEN-CHANGED**: 24 one way, 1 back | see 1.5 |
| F | `promotion-cards` feature with no importer | **CLOSED** | `ls src/lib/portal/operator/promotion-cards.ts` → No such file; `find src -name '*promotion*'` → empty (deleted by #2732) |
| G | Two incompatible API error-body shapes under one key | **OPEN-UNCHANGED, now measured** | see 1.6 |
| H | `reserve.ts` / `heartbeat.ts` are the domain layer; three lib files at exactly 499 logical lines | **OPEN-UNCHANGED (routes); WORSE (the 499 cluster)** | see 1.7 |
| I | `workers/fleet-alerts` deploys with no CI build gate | **OPEN-UNCHANGED** | `grep -ln fleet-alerts .github/workflows/*.yml` → `deploy.yml` only |
| J | Six untracked `workers/` directories are build residue | **OPEN-UNCHANGED, and confirmed not a repo defect** | `ls workers/` in the worktree returns exactly `cost-anomaly cost-telemetry fleet-alerts`; the primary checkout returns nine. Absent from git, present only on this machine. |

### 1.2 Findings

1. **[MEDIUM] `src/pages/api/oauth/callback.ts` is a 231-line route with no initiator, and `src/middleware.ts:129` carries a rewrite exemption for it.** Nothing in the repo issues an OAuth state whose redirect URI is `${ADMIN_BASE_URL}/api/oauth/callback`: the sole production caller of `issueOAuthState` is `src/pages/portal/products/operator/oauth/[connector]/index.ts`, whose consent flow returns to its own portal-side callback. The route's own header documents two of its steps as unfinished ("v1 is a no-op pending Hermes Machine control plane wiring", "writer is a no-op until #891"). Its last functional commit is #936, its own creation. Because `/api/oauth` matches neither `isAdminRoute` (`/admin`) nor `isAdminApiRoute` (`/api/admin`), `enforceAdminAuth` never runs on it (`src/middleware.ts:174-184`); the route's only gate is its own `reviewerMatches(locals.session, reviewer_id)` check at `:190`.
   **Correction to the carried finding's implied scope:** the supporting `src/lib/oauth/` modules are *not* dead. `getOAuthProvider`, `getDefaultTokenStore` and `emitAuditEvent` each have a second, live caller in the portal callback (`grep -rn` for each: two call sites, one admin one portal). The dead surface is one route file plus one middleware clause, not the `oauth/` subtree.
   Instrument: `grep -rn "issueOAuthState" src` (one production caller); `git log --oneline -3 -- src/pages/api/oauth/callback.ts`; read of `src/middleware.ts:120-136,174-184` and `src/pages/api/oauth/callback.ts:1-231`.
   Falsifier: any production file constructing an authorize URL with the admin redirect URI, or an `issueOAuthState` call outside the portal route.
   Recommendation: delete the route and the `!pathname.startsWith('/api/oauth')` clause together, or open the initiator. Leaving it is an unauthenticated token-exchange endpoint kept warm for a flow nobody starts.

2. **[MEDIUM] Exactly two files under `src/pages/api` export no HTTP verb, and both are libraries.** `src/pages/api/booking/confirmation-emails.ts` (`sendConfirmationEmails` plus three interfaces) and `src/pages/api/booking/reserve-helpers.ts` (`createGoogleCalendarEvent`, `buildEventDescription`, `formatSlotLabelLong`, `parseOptionalInt`, and a re-export of three `src/lib/api/helpers` functions). Astro's file-based routing registers every file under `src/pages`, so both occupy public URLs. `reserve-helpers.ts:98` re-exporting `trimString, isValidEmail, jsonResponse` from `src/lib/api/helpers` is the tell: the file exists to preserve an import surface, which is what `src/lib/` is for.
   Instrument: a loop over `find src/pages/api -name '*.ts'` testing each for `^export (const|async function) (GET|POST|PUT|PATCH|DELETE|ALL|prerender)`; exactly these two miss.
   Falsifier: an Astro route-exclusion config, or a `prerender` export making them non-routes. Neither exists (`astro.config.mjs` has no route filter).
   Recommendation: move both to `src/lib/booking/`; update the three importers.

3. **[MEDIUM] `src/lib/db/` still calls upward on four edges, and now also owns pricing policy that the Stripe layer imports back down.** The four upward edges are unchanged and are all `import` statements, not comments: `db/integrations.ts:12 → ../booking/encryption`, `db/quotes.ts:14 → ../sow/store`, `db/engagements.ts:8 → ../follow-ups/scheduler`, `db/milestones.ts:11 → ../stripe/client` (`createStripeInvoice`, `sendStripeInvoice`). New in this window: `src/lib/stripe/subscriptions.ts:42` imports `CARD_FEE_LINE_DESCRIPTION` and `cardProcessingFeeCents` from `../db/invoices`. So the data-access layer both calls the Stripe client and authors the 3% card-fee rule that Stripe reads back. A reader looking for "where is the card fee defined" has no reason to look in the invoices data module.
   Instrument: `grep -rn "^import .*from '\.\./\(stripe\|follow-ups\|sow\|booking\)" src/lib/db --include='*.ts'`; `grep -rn 'CARD_FEE\|cardProcessingFeeCents' src/lib`.
   Recommendation: the fee constant and its arithmetic belong in `src/lib/stripe/` (or a `src/lib/pricing/`), imported by `db/invoices.ts` if the invoice writer needs the label.

4. **[MEDIUM] The Operator domain is still two directories with a back-edge.** `src/lib/portal/operator` (40 files) reaches into `src/lib/operator` (55 files) on 24 import edges; `src/lib/operator/entitlement-compiler.ts:56` imports back from `../portal/operator/config-governance`. The direction of the single back-edge is the problem: the entitlement compiler, which is the lower layer, depends on a governance table that lives in the portal-presentation half. Two further sites document the coupling in comments rather than code (`runtime-read.ts:51`, `customer-yaml/sections-persona-skills.ts:159`).
   Instrument: import-line greps in both directions (counts above); `find ... -name '*.ts' | wc -l` for both trees.
   Falsifier: the back-edge being a `import type` (erased at compile time). It is not — `config-governance` is imported for values.
   Recommendation: move `VERTICAL_FLOORS` and its accessors down into `src/lib/operator/`, leaving `portal/operator/` as presentation only. That is one file move and it removes the only cycle-shaped edge.

5. **[MEDIUM] One API error key carries two incompatible vocabularies, and the canonical helper is used in 29 of 98 route files.** Under the same `error` key, `src/pages/api` returns 36 distinct snake_case machine codes (`entity_not_found`, `invalid_action_class`, `already_cancelled`, `calendar_sync_failed`, …) and 33 distinct human sentences (`'Unauthorized'`, `'Assessment is temporarily unavailable.'`, `'Your assessment session is no longer valid. Please restart.'`). A caller cannot tell a parseable code from prose meant for a person. Inside the prose set the same condition has several spellings: `'Invalid JSON.'` and `'Invalid JSON'`; three phrasings of rate-limiting (`'Too many requests, please try again later.'`, `'Too many requests. Please slow down.'`, `'Too many submissions. Please try again later.'`). `errorResponse` (`src/lib/api/helpers.ts:52`, the shape declared canonical by the 2026-07-02 review) is imported by 29 of 98 API route files.
   Instrument: `grep -rhoE "error: '[a-z][a-z0-9_]*'"` and `grep -rhoE "error: '[A-Z][^']*'"` over `src/pages/api` + `src/middleware.ts`, `sort -u`; `grep -rln errorResponse src/pages/api | wc -l`.
   Falsifier: an object-form body (`error: { code, message }`) reconciling the two. `grep -rn "error: {" src/pages/api` returns nothing, so no such reconciliation exists.
   Recommendation: pick one — machine code in `error`, prose in a sibling `message` — and extend the existing `no-restricted-syntax` guard in `eslint.config.js:198` to require `errorResponse`.

6. **[MEDIUM] Two route files are still the domain layer.** `src/pages/api/internal/heartbeat.ts` (419 logical / 730 raw) holds 16 functions, of which 12 are field parsers (`parseGatewaySupervisorState`, `parseConnectorsJson`, `parseSendRefusalsJson`, …), plus `upsertFleetStatus` issuing a raw upsert at `:680` and a second raw read at `:568`. There is still no `fleet_status` module under `src/lib/db/` (`ls src/lib/db/` — 27 files, none for fleet status). `src/pages/api/booking/reserve.ts` (456 logical / 561 raw) holds 12 functions including `commitBookingToDb` (`:252`), `syncGoogleCalendarAndPromote` (`:344`), `seedScheduleSidecars` and `mintManageToken`. `src/pages/api/webhooks/stripe.ts` delegating to `src/lib/webhooks/*-handler.ts` remains the shape both should follow.
   Instrument: function-declaration greps in both files; `ls src/lib/db/`.
   Recommendation: `src/lib/db/fleet-status.ts` for the heartbeat parsers and upsert; `src/lib/booking/commit.ts` for the reserve transaction.

7. **[MEDIUM] Zero files exceed the 500-line ceiling, and six sit between 494 and 499.** Counting the way `eslint.config.js:19` does (`max-lines`, `skipBlankLines`, `skipComments`), the top of the tree is: `src/lib/sow/service-finalize.ts` 499, `src/pages/portal/products/operator/[instance]/settings/index.astro` 499, `src/lib/operator/customer-yaml/types.ts` 497 (1,422 raw), `src/lib/db/quotes.ts` 494, `src/lib/pdf/sow-template.tsx` 494, `src/lib/webhooks/stripe-subscription-handler.ts` 494. Twelve files exceed 450; none exceeds 500. Last review found three at exactly 499; the cluster has grown to six within six lines of the ceiling, and the settings page moved from 498 to 499 during the window. The ceiling is being met by trimming, not by decomposition, and a ratchet nobody can cross is not the same as a tree nobody needs to.
   Instrument: my own comment/blank-stripping counter (`scratchpad/loc.mjs`, block-comment state machine) over `src/`, `scripts/`, the three `workers/*/src`; cross-checked against `npm run lint`, which reports 0 errors and therefore 0 `max-lines` violations.
   Recommendation: `customer-yaml/types.ts` at 1,422 raw lines is a pure type module and either deserves a reasoned per-file exemption or a split by section; the other five deserve extraction, not trimming.

8. **[MEDIUM, NEW] `src/pages/api/admin/clients/[id]/operator-price.ts:50-51` writes the retainer price and the payment rail as two independent non-atomic statements.** The handler does `await setOperatorPrice(...)` then, conditionally, `await setOperatorPaymentMethod(...)`. Each of those is itself a read-modify-write (`src/lib/db/services.ts:243` reads the existing service, then `:245` updates; `:279-285` the same), with no `db.batch()` and no transaction anywhere in the path. If the second write fails, the row carries the new price and the old rail — and the rail is what decides whether the 3% card-processing fee line is added to checkout and to every monthly invoice (`src/lib/stripe/subscriptions.ts:56-57,174-175`). The caller sees `?error=server` and cannot tell which half landed. This is new code from `#2730`, on the money path.
   Instrument: read of the route (61 lines, whole file) and of `src/lib/db/services.ts:237-289`; `grep -rn 'batch(' src/lib/db/services.ts` returns nothing.
   Falsifier: a `db.batch([...])` or a compensating re-read in either function. Neither is present.
   Recommendation: one `db.batch()` covering both `UPDATE services` statements, or a single statement setting both columns.

9. **[LOW, NEW] `.claude/hooks/trap-recurrence.mjs` is a Captain CLI that no hook registers, and knip cannot tell the difference.** `.claude/settings.json` registers ten scripts; the directory holds eleven plus `lib/`. The eleventh is reached through the wrapper `.claude/bin/trap-recurrence`, which is legitimate and documented (it answers the traps block's pre-registered 2026-09-23 review). The structural point is that `knip.jsonc` declares `.claude/hooks/*.mjs!` as an entry pattern, so a hook module that later stops being registered — a real regression, since an unregistered hook silently stops enforcing — is invisible to every gate in the repo.
   Instrument: `grep -o 'hooks/[a-z-]*\.\(mjs\|sh\)' .claude/settings.json | sort -u` (10) versus `ls .claude/hooks/` (11 + lib); `cat .claude/bin/trap-recurrence`.
   Recommendation: move the two Captain CLIs (`trap-recurrence.mjs`, and `memory-audit.mjs` is registered so it stays) to `.claude/bin/lib/`, and add a test asserting every `.claude/hooks/*.{mjs,sh}` appears in `settings.json`. That test would also catch an unregistered guard, which is the failure that matters.

10. **[LOW] `workers/fleet-alerts` still has no dry-run build in CI, and the window changed its dependencies.** `.github/workflows/verify.yml:95-99` dry-run builds cost-anomaly and cost-telemetry under a comment whose stated rationale ("own wrangler configs + live consumers") applies identically to fleet-alerts, which is the heartbeat-red / HARD_STOP pager. `deploy.yml` is the only workflow naming it. This window bumped `workers/fleet-alerts/package.json` and its lockfile (245 lines) and edited `src/conditions.ts`, so the untested-config surface was actively touched.
    Instrument: `grep -ln fleet-alerts .github/workflows/*.yml`; read of `verify.yml:78-99`; `git diff --stat 0a3722a3..1da55373 -- workers`.
    Recommendation: a third dry-run step. One line.

11. **[POSITIVE, verified] The new admin pricing route is the layering the venture asks for, and the middleware fix is exemplary.** `src/pages/api/admin/clients/[id]/operator-price.ts` is 61 lines: gate (`requireAdminSession`), parse (`parsePrice`), delegate to `src/lib/db/services`, redirect. No SQL, no business rule, org-scoped on every call. Independently, `src/middleware.ts:96-99` now hands the session renewal to `waitUntil` and reports the failure through `captureError`, with a comment naming the review that found it — the carried Code Quality item, closed at the mechanism and documented at the site.

### Architecture grade: **C (stable, top of band)**

Rubric line: *"3+ files over 500 lines OR unclear boundaries OR mixed concerns in route handlers."* The first clause no longer holds — zero files exceed 500. The other two do: four upward `db/` edges plus a pricing rule in the data layer, a 24-edge Operator split with a back-edge, two vocabularies under one error key, and two route files that are the domain layer. Not D: nothing regressed, boundaries are discernible and mostly followed, the dead-feature cluster was actually deleted rather than annotated. Not B: the same six boundary findings have now survived three reviews, and the one new architectural defect in the window is a non-atomic write on the money path.

---

## 2. Code Quality

### 2.1 Carried findings, re-probed at HEAD

| # | Carried finding | Verdict | Instrument |
|---|---|---|---|
| 1 | `@public` waiver unverified prose; 3 of 15 false, 6 resting on source-text greps | **CLOSED** | see 2.2 §1 |
| 2 | 39 exports with no non-test referrer; gate reports zero | **CLOSED** | see 2.2 §2 |
| 4 | Ten knip categories `off`; `types` disabled on a false rationale | **CLOSED** | `knip.jsonc` rules block: `exports`/`nsExports`/`types`/`nsTypes` = `error`, the other ten = `warn` |
| 5 | Byte-identical helper duplication in `src/` | **OPEN-UNCHANGED, sharpened** | see 2.2 §3 |
| 6 | `captureError` 3/156; `middleware.ts:90` swallows and may never run | **HALF CLOSED** | see 2.2 §4 |
| 7 | `yaml` is a phantom dependency | **CLOSED** | `node -e` on package.json → `devDeps: ['yaml']`; 27 importers now resolve against a declared dep |
| 9 | Lint at exactly 13 of 13 warnings, no ratchet since 08-09 | **CHANGED, and slack opened** | see 2.2 §5 |
| 12 | `capabilities/conformance.ts:17` names a nonexistent `runConformanceSuite` | **CLOSED** | `ls src/lib/operator/capabilities/` → `types.ts` only; `grep -rn runConformanceSuite src tests` → empty |
| 13 | Type discipline strong (POSITIVE) | **RE-VERIFIED, stronger** | see 2.2 §7 |

### 2.2 Findings

1. **[POSITIVE, verified] The `@public` waiver is now falsifiable, and I can name what turns it red.** `tests/public-waivers.test.ts` walks `src`, `workers`, `scripts`, finds every `@public` JSDoc block, resolves the export it decorates, and asserts three things per waiver: the block names at least one repo-relative path; every named path exists; at least one named path consumes the symbol — **and when that path is a test, consumption means a named-brace `import`, not a source-text match**. A fourth test asserts the scan found ≥10 waivers, so an empty or broken walk cannot pass vacuously. `npx vitest run tests/public-waivers.test.ts --reporter=verbose`: 41 tests, 13 waivers × 3 plus the two fixed ones, all green.
   What would make it red, concretely: deleting `workers/booking-cleanup` from a waiver's prose (no path → red); the three consumers the last review found false (`workers/booking-cleanup/`, an overlay `.ts` caller, an invoice-page variant) would each fail the existence or the consumption assertion today; converting any of the 13 citing tests from `import { X }` to `expect(source()).toContain('export ... X')` flips that waiver red. I read all 13 blocks: every one cites a test file, so all 13 currently run through the strict import branch, and none leans on the weaker production-path rule.
   Residual (LOW): the production-path branch is a bare `\bsymbol\b` regex over the file body, so a waiver naming a production file that merely *mentions* the symbol in a comment would pass. Unexploited today; it is the one place the check is still a grep.

2. **[POSITIVE, verified] The dead-export gate now runs in production mode, it blocks, and an independent method finds nothing it misses.** `npm run deadcode` is `knip --production`; `.github/workflows/verify.yml:67` runs it inside the job named `Typecheck, Lint, Format, Test`, which is the required status check on `main` — so this gate blocks merges. Output at HEAD: two unused *dependencies* (a `warn` category, false positives — see §6) and nothing else.
   Independent check: I wrote a scanner (`scratchpad/deadscan2.mjs`) that indexes every `export function|const|let|class` in `src/`, `scripts/`, the three `workers/*/src`, `operator/assessment-eval`, `operator/voice-gate`, and counts references excluding the defining file and excluding self-use. It reports 22 exports with no production consumer. Eleven carry `@public` (each checked by the waiver test). Ten are inside files that `knip.jsonc` declares as entries and can account for by name: `scripts/*.mjs` and `scripts/*.ts` (`escape-analysis`, `runtime-ac-proof`, `migrate-to-entities`), `src/lib/seo/sitemap-filter.mjs`, `src/middleware.ts` (the Astro plugin's entry), `src/lib/portal/operator/facet-registry.ts` (a declared dev entry). The last two — `parseMcpMetadataResource` and `buildProtectedResourceMetadata` — were a bug in *my* scanner, which skips dot-directories: both are imported by `src/pages/.well-known/oauth-protected-resource/[...resource].ts:5,7`. So the residue is zero.
   I also re-checked ten of the exact 39 exports the last review named: `createMeeting`, `listTimeEntries`, `updateServiceStatus`, `getServicesForEntity`, `listOAuthProviderSlugs`, `jaroWinklerSimilarity`, `buildManualExtractionPrompt` are gone from the tree entirely; `FAST_MODEL` and `loadHomeFeeds` survive only as words inside comments; `templateHygieneViolations` survives with a `@public` tag naming the merge-gate test that imports it.
   Spot-check of five exports knip does **not** flag, read by hand: `isAuthorityHolder` (used at `src/lib/admin/authority-write.ts:65`), `ALLOWANCE_SETTING` (`admin/medchron-jobs-read.ts:186`), `splitList` (`admin/provisioning.ts:109,121`), `MAX_SESSION_TURNS` (`assessment/session.ts:195`), `formatAge` (`admin/fleet-status.ts:160,161,162,174,241`). All five are used inside their own file — exactly the `ignoreExportsUsedInFile: true` class the config declares and defends. knip's silence is correct on all five.

3. **[MEDIUM] Byte-identical duplication is unchanged, and the sharpest case is three copies inside one directory on the money path.** `src/lib/stripe/` holds three files that each declare `const STRIPE_API_BASE = 'https://api.stripe.com/v1'` and an identical five-line `stripeHeaders(apiKey)` (`checkout.ts:43,50`, `subscriptions.ts:45,62`, `client.ts:14,16`). `machineBaseUrl(template, app)` is one byte-identical line in five files (`portal/operator/entitlement-change.ts:74`, `portal/operator/pause-control.ts:44`, `operator/mcp/webhook-transport.ts:51`, `admin/sticky-stop-clear.ts:36`, `operator/runtime-read-transport.ts:86`) — the function that addresses the live Operator Machine. `escapeHtml` is defined in seven files, one of them the canonical `src/lib/api/helpers.ts:24` whose own header says it exists as the 2026-06-12 dedup outcome. `isRecord` 8, `trimString` 3, `base64UrlEncode` 4, `importSigningKey` 3.
   Two corrections to the carried finding's framing, both from reading the copies rather than counting them:
   - `jsonError` (7 files) is **not** drift. Every copy is now a one-line pass-through: `function jsonError(status, message) { return errorResponse(status, message) }` (`webhooks/signwell.ts:85`, `portal/operator/settings/skill-toggle.ts:42`, and five more). That is seven aliases that add nothing; delete them and call `errorResponse`.
   - `redirectWithStatus` (11 files) is the opposite and is the genuinely dangerous one: same name, **different behavior** per copy. `admin/operator/[customer]/authority.ts:35` builds `/admin/operator/<slug>/authority?status=`; `portal/products/operator/[instance]/pause.ts:44` builds `${OPERATOR_LANDING}/<instance>/settings?status=`. A reader who greps the name finds eleven functions that are not the same function.
   The `no-restricted-syntax` guard at `eslint.config.js:180-188` (and its API-route re-declaration at `:198-210`) still covers only `jsonResponse`.
   Instrument: `grep -rln "function <name>\b\|const <name> = " src` per symbol; `grep -n -A10` on each copy of `stripeHeaders`, `jsonError`, `redirectWithStatus`, `machineBaseUrl`.
   Recommendation: `machineBaseUrl` → `src/lib/operator/machine-url.ts`; `stripeHeaders` + `STRIPE_API_BASE` → `src/lib/stripe/client.ts` (they are already siblings); delete the seven `jsonError` aliases; leave `redirectWithStatus` alone but rename each to its actual destination so the name stops lying. Extend the ESLint guard to `escapeHtml`, `trimString`, `isRecord`, `machineBaseUrl`.

4. **[MEDIUM] Error handling is consistent; error *observability* is five files out of 174.** The behavioural half of the carried finding is closed and closed well: `src/middleware.ts:96-99` now reads `const renewal = renewSession(...).catch((err: unknown) => captureError(err, 'middleware.renew-session'))` followed by `context.locals.cfContext?.waitUntil(renewal)`, with a comment naming both defects it fixes. Repo-wide, non-test `src/` has **zero** empty catch blocks and **zero** `.catch(() => {})` swallows. What remains is reach: `captureError` is imported by 5 files (`middleware.ts`, `observability/sentry.ts`, `api/internal/heartbeat.ts`, `api/webhooks/resend.ts`, `api/portal/operator/settings/customer-yaml-update.ts`) against 174 non-test files containing a `catch`. The window's own new money route is the pattern: `api/admin/clients/[id]/operator-price.ts:58` catches and calls `console.error`, which reaches a Worker log nobody pages on rather than Sentry.
   Instrument: `grep -rln captureError src | grep -v '\.test\.'` (5) vs `grep -rln catch src | grep -v '\.test\.'` (174); `grep -rn 'catch {}\|\.catch(() => {})' src` (empty).
   Falsifier: a shared wrapper routing `console.error` to Sentry. `src/lib/observability/sentry.ts` exports `captureError`; nothing wraps `console.error`.
   Recommendation: adopt `captureError` on the webhook and money paths first (`api/webhooks/*`, `api/admin/clients/*`, `lib/stripe/*`), not by sweep.

5. **[LOW] The lint ceiling did not ratchet, and a free warning slot has opened.** `npm run lint` is `eslint . --max-warnings 13`; the actual count at HEAD is **12** warnings, 0 errors. All twelve are `no-unsafe-assignment` / `no-unsafe-member-access` from `JSON.parse` returning `any`, seven of them in `src/lib/db/quotes.ts:201-245`, the rest one each in `entities/recompute.ts:55`, three `customer-yaml/sections-*.ts` files, and one more. The count fell because a file was deleted, not because the pattern was fixed; the cap stayed at 13, so one new unsafe assignment can now land without turning the gate red. The config comment at `eslint.config.js:53-58` says the number "only ever ratchets DOWN", which is the intent this leaves unenforced.
   Instrument: `npm run lint` (tail: `✖ 12 problems (0 errors, 12 warnings)`).
   Recommendation: set the cap to 12 in the same breath as noticing, and annotate `const parsed: unknown` at the twelve sites to reach 0.

6. **[LOW] knip reports two false-positive unused dependencies because the root config files are outside its `project` set.** `npm run deadcode` prints `@astrojs/cloudflare` and `@astrojs/sitemap` as unused. Both are imported by `astro.config.mjs`, which matches none of the `project` patterns (`src/**`, `scripts/**`, `tests/**`, `operator/*/**/*.ts`, `public/js/*.js`, `.claude/hooks/**/*.mjs`). The category is `warn`, so it does not block — but the config's own instruction is "Read the warnings; do not silence them", and a warning list whose only two entries are known-false trains the reader to skip it.
   Instrument: `npm run deadcode`; `grep -n cloudflare astro.config.mjs`.
   Recommendation: add `*.{ts,mjs,js}!` to the root workspace's `project` array.

7. **[POSITIVE, verified] Type discipline in `src/` is intact and slightly stronger than last review.** Zero real `any` in non-test `src/`, `scripts/` and `workers/*/src`: the eight grep hits for `: any|as any|<any>|any[]|Record<string, any>` are all the English word "any" inside prose comments, each read individually (`portal/customer-config.ts:12`, `admin/fleet-status.ts:38`, `auth/after-sign-in.ts:88`, `customer-yaml/types.ts:860,925`, `packs/med-spa.astro:259`, `api/portal/operator/change-request.ts:28`, `scripts/backlog/classify.ts:128`). Zero `@ts-ignore`, zero `@ts-expect-error`. `npm run lint` reports 0 errors, so the error-tier structural rules hold across the tree: no function over 75 lines, no complexity over 15, max-depth 4, max-params 5, no floating promises, no misused promises, `switch` exhaustiveness.
   Unchanged note: `tsconfig.json` extends `astro/tsconfigs/strict`, not `strictest` — `noUncheckedIndexedAccess` and `exactOptionalPropertyTypes` are off. The tsconfig does carry a well-reasoned explicit include for `src/pages/.well-known/**/*`, since TypeScript's default glob skips dot-directories and the RFC 9728 discovery routes must live there.

8. **[LOW] `tests/public-waivers.test.ts` scans three roots; knip honors `@public` across six.** The test walks `src`, `workers`, `scripts`. knip's `project` set additionally includes `operator/*/**/*.ts` (16 files) and `.claude/hooks/**/*.mjs`, and knip's built-in `@public` handling applies there too. A waiver placed in `operator/voice-gate/scoring.ts` or `.claude/hooks/reply-contract.mjs` would exempt an export from the gate with nothing checking its prose. The same walk skips dot-directories, so `src/pages/.well-known/` is also outside the check. No such tag exists today (`grep -rn '@public' operator .claude/hooks --include='*.ts' --include='*.mjs'` → empty), so this is a hole, not a defect.
   Recommendation: add `operator`, `.claude/hooks` to `SCAN_ROOTS`, and stop skipping `.well-known`.

9. **[LOW] The operator TypeScript barrels exempt two whole subsystems from the dead-export gate by construction.** `knip.jsonc` declares `operator/*/index.ts!` and `operator/*/cli.ts!` as entries, and knip does not report unused exports in entry files. `operator/voice-gate/index.ts` re-exports 26 symbols and `operator/assessment-eval/index.ts` is five `export *` lines, so every export in both subsystems (16 `.ts` files) reads as used regardless of whether anything but a test calls it. Checked by hand: `stateForScore` (`voice-gate/scoring.ts:129`) is referenced by the barrel, its own file, and `tests/voice-gate-scoring.test.ts` — nothing else. The entry designation is nonetheless *correct*: both CLIs are genuinely hand-run doors, documented in their READMEs and, for the voice gate, exec'd by `operator/bin/run-voice-gate.sh:43`. Recording the exemption so a future reviewer does not read the gate's silence as coverage of these two trees.

### Code Quality grade: **B− (up from C)**

Rubric line for C is *"3+ dead exports OR inconsistent error handling OR notable DRY."* The first clause is now false by two independent methods, and it is false because the gate was fixed rather than the symptom swept: production-mode knip, a `@public` waiver that costs something, and 30 files actually deleted. Error handling is consistent — zero empty catches, zero swallows, the one middleware offender closed at the mechanism. What keeps it off B is DRY, which did not move: three identical Stripe helpers in one directory, five identical `machineBaseUrl`, seven `escapeHtml`, seven no-op `jsonError` aliases, and eleven same-named `redirectWithStatus` functions that behave differently — plus error observability at 5 files of 174. Not B: the duplication class has survived three reviews and the guard that exists still covers exactly one symbol.

---

## Top 5, ranked by what I would fix first

1. **The non-atomic money write** (`api/admin/clients/[id]/operator-price.ts:50-51`) — new code, decides whether a client is billed a 3% fee, one `db.batch()` away from correct.
2. **Delete the dead admin OAuth callback and its middleware exemption together** (`api/oauth/callback.ts`, `middleware.ts:129`) — 231 lines of unreachable token-exchange with no middleware auth.
3. **Ratchet `--max-warnings` to 12 and annotate the twelve `JSON.parse` sites** — the cap's own comment says it only ratchets down; right now it does not.
4. **Consolidate `src/lib/stripe/`'s three identical `stripeHeaders` + `STRIPE_API_BASE`, and the five `machineBaseUrl`** — then extend the `no-restricted-syntax` guard past `jsonResponse`.
5. **Add the `fleet-alerts` dry-run build to `verify.yml`** — one line, on the worker whose job is to page when the fleet is down, whose config was edited this window.


<!-- ===== agent: arch-operator ===== -->

## Code review 2026-09-10 — Architecture + Code Quality, `operator/` (Python + shell)

**Commit reviewed:** 1da55373 (== origin/main at review start; worktree `git diff --stat 1da55373` empty).
**Scope:** everything under `operator/` — `workspace_broker`, `adapter`, `safety-substrate`, `skills`,
`connectors`, `runners`, `bin`, `templates`, `contracts`, `rehearsal`, `customers`. A sibling agent owns `src/`.
**Read first:** `operator/CLAUDE.md` (Law 2), the Architecture and Code Quality sections of
`docs/reviews/code-review-2026-09-09.md`, `git log --stat 0a3722a3..1da55373`.
**Method:** read-only. Nothing edited, no branch, no live system touched.

Two commits in the 11-commit window touch `operator/`: **#2735** (decommission backends + sticky_stop
enrolment, 1da55373) and **#2723** (customer.yaml key retirement, eadc2ad7). **#2732** (30 dead files
deleted) is `src/`-only — `git show --stat 3d0ba79b -- operator/` returns nothing — so no carried
`operator/` dead-code finding was closed by it.

---

## Dimension 1 — Architecture

### Carried-finding re-verification (2026-09-09 Architecture findings that concern `operator/`)

| # | Carried finding | Status at HEAD | Instrument |
|---|---|---|---|
| 1 | Broker `handle()` positional authorization | **OPEN-UNCHANGED** | read `server.py:363-600`; `ruff --select C901` |
| 2 | No root Python packaging; `sys.path` + `noqa: E402` the cost | **OPEN-UNCHANGED** | `find -name pyproject.toml`; grep census |
| 3 | `sticky_stop.py` unwired ss-console copy | **CLOSED (enrolled)** | `shasum -a 256` both sides vs `overlay-pairs.json` |
| 4 | Tracker-skill duplication | **OPEN, CHANGED (wider than reported)** | AST byte-identical helper census over 6 skills |
| 5 | Vendored-copy gates use hand tuples; `broker_writer` twin ungated | **OPEN-UNCHANGED** | md5 census; `diff`; grep for `glob` in each sync test |
| 6 | `decommission.py` five stub backends + shell missing exit-5 arm | **CLOSED** | read `decommission_backends.py`; `sed -n '67,78p' decommission-customer.sh`; pytest |
| 7 | `adapter/` off the request path; two zero-importer modules | **OPEN-UNCHANGED** | repo-wide grep excluding self and tests |
| 10 | `packet.py` god object; `establishment_store.py` four-lifecycle facade | **OPEN-UNCHANGED** | AST top-level census |
| 11 | Shell scripts outside every size ceiling | **OPEN-UNCHANGED** | `wc -l`; read of `tests/operator-module-size.test.ts:89` |
| 13 | No package cycles in `operator/` (POSITIVE) | **CHANGED — two mutual pairs exist, both broken by deferred imports** | own AST import graph, local-directory-first resolution |

---

### Findings

1. **[POSITIVE, verified] `operator-substrate` is now a required check on `main`.** This was the top
   action item of the last three reviews. `gh api repos/venturecrane/ss-console/rulesets/15555219`
   returns exactly two required contexts: `Security Summary` and **`substrate`**, and
   `.github/workflows/operator-substrate.yml:43` declares `jobs.substrate`. The workflow now triggers
   with no `paths:` filter and decides inside the job via `operator/bin/substrate-paths-changed.py`
   against `.github/operator-substrate-paths.txt`, failing closed (no base sha or a diff error runs
   the full suite). That puts `ruff check .`, the `run()`-style safety invariants, the whole pytest
   inventory, `verify-overlay-pairs.py`, connector conformance and the medchron runner behind a merge
   gate for the first time. Instrument: the rulesets API read plus `sed -n '1,50p'` of the workflow.
   Falsifier: `substrate` absent from the required-checks list.

2. **[MEDIUM, NEW] `operator/fixtures/**` sits outside the required check's own trigger list, and a
   substrate test executes a Python file from there.** `operator/tests/test_closeout_seed.py:181` runs
   `subprocess.run([sys.executable, str(RENDERER)], check=True)` where `RENDERER` resolves to
   `operator/fixtures/law-firm/pi/lien-ledger-tracker/seed/render_seed.py` (`:30`). That path matches
   no pattern in the list. Proven with the repo's own matcher:

   ```
   python3 -c "import importlib.util; spec=importlib.util.spec_from_file_location('spc','operator/bin/substrate-paths-changed.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); pats=m.load_patterns(); print(any(m.matches(p,'operator/fixtures/law-firm/pi/lien-ledger-tracker/seed/render_seed.py') for p in pats))"
   False
   ```

   The conformance test that is supposed to keep the list honest checks the wrong thing:
   `test_every_test_file_area_triggers_the_workflow` (`bin/tests/test_ci_coverage_conformance.py:149`)
   pins **test files**, not the data and helper modules those tests execute. Six `operator/`
   directories are unlisted — `assessment-eval`, `bundles`, `fixtures`, `grading`, `observability`,
   `references`, `voice-gate` (180 files under `fixtures/` alone). A fixture-only PR reports "No
   substrate paths changed" and merges green; the break surfaces on the next unrelated PR, attributed
   to the wrong change. Falsifier: the matcher returning `True` for that path, or no substrate test
   reading anything under an unlisted directory. **Recommendation:** add `operator/fixtures/**` to the
   list, and extend the conformance test to walk each substrate test file for repo-relative paths it
   opens or executes and assert every resolved path's area triggers the workflow.

3. **[HIGH] `workspace_broker/server.py:363` `handle()` is unchanged: 313 lines, ruff McCabe 51, and
   authorization is still positional.** The structural gate `if peer_pid != self.gateway_pid: raise
   PermissionError` is at `:595`. Above it sit five hand-rolled agent-uid checks (`:381`, `:410`,
   `:460`, `:521`, `:572`) and a `medchron_dispatch(...)` delegation at `:371` that gates internally
   through a proper table (`medchron_verbs.py`). `handle` is the single most complex function in the
   tree by a factor of 1.6:

   ```
   ruff check --isolated --select C901 --config 'lint.mccabe.max-complexity=15' --target-version py311 --output-format json operator/
   → cx 51  handle  workspace_broker/server.py:363     (next: cx 31 strip.py:110)
   ```

   Instrument: that ruff run plus a read of `:355-600`. Falsifier: a verb table with a declared
   `auth` field per entry, which the medchron group already demonstrates. **Recommendation:** replace
   the if-chain with `{handler, auth: gateway | agent_uid | root}` entries and assert in
   `tests/broker-verb-registry.test.ts` that every entry declares one.

4. **[HIGH] No Python packaging for the four core subsystems.** No `pyproject.toml`, `setup.py` or
   `setup.cfg` at the repo root or at `operator/`; the five that exist are all leaves
   (`runners/medchron`, `connectors/_sdk`, `connectors/smokeball`, `connectors/msgraph-mail`,
   `connectors/_reference`). The cost is measurable and unchanged: **32 non-test `sys.path.insert`
   mutations** and **130 `# noqa: E402`** annotations, which is 33% of every suppression in the tree.
   The pattern is `sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "workspace_broker"))`
   then a bare `from chain import ...` (`bin/verify-audit-chain.py:45-46`). The three packaged
   subtrees prove the pattern works here. Instrument: `find . -maxdepth 4 -name pyproject.toml`;
   `grep -rn 'sys.path.insert' --include='*.py' operator/ | grep -v tests`; `grep -c 'noqa: E402'`.
   Note against the prior report: I measure **3** direct `bin -> workspace_broker` import edges
   (`chain` ×2, `recipient_policy` ×1), not 22; the prior figure is not reproducible with a
   name-resolved walk. The packaging conclusion is unaffected. **Recommendation:** one root
   `pyproject.toml` declaring the four subsystems as packages, on the medchron precedent.

5. **[MEDIUM] Skill duplication is wider than the last review measured, and the two skills that need
   the fix still lack the loader.** Byte-identical top-level helper bodies across the six substantive
   `skills/*/pre_run.py` modules (the other eight are byte-identical copies of
   `templates/pre_run_gate.py`):

   | copies | helper | lines | skills |
   |---|---|---|---|
   | 6 | `_emit_suppress` | 3 | all six |
   | 3 | `_pos_int`, `_next_scheduled_at`, `_handoff_values`, `_is_iso_day` | 2–12 | three each |
   | 2 | nine more, incl. `_write_pre_run_handoff` (29L), `_find_skill_settings` (18L) | 4–29 | pairs |

   15 distinct helper bodies, **176 redundant lines**. The heaviest pair is still
   medical-records-chaser × lien-ledger-tracker (8 identical helpers, 86 lines each side; 11 helpers
   at ≥98% `difflib` similarity). Two skills the last review never measured are now in the family:
   `paid-media-anomaly-watcher` and `retainer-hours-reconciler`. The stated reason for not splitting
   remains falsified — `_load_sibling_module` (`skills/client-verification-tracker/pre_run.py:301-320`)
   falls back to `/opt/data/skills/<skill>` and `/app/skills/<skill>`, and
   `templates/Dockerfile:1115` is `COPY operator/skills/ /app/skills/`. Adoption is 2 of 6:
   `grep -c '_load_sibling_module'` gives cvt 10, dme 7, **mrc 0, llt 0**, and mrc/llt hold exactly 2
   `.py` files each against cvt's 10. Instrument: AST census script over the six modules; the grep
   counts; `grep -n 'operator/skills' templates/Dockerfile`. **Recommendation:** give mrc and llt the
   sibling loader, extract the 15 helpers to one vendored `skill_helpers.py`, and gate it by glob
   rather than by name.

6. **[MEDIUM] Three vendored-copy gates discover by hand-maintained tuple; a fourth pair has no gate
   at all.** md5 over non-test `.py` under `operator/` finds exactly three duplicate classes and 14
   redundant copies: `pre_run_gate.py` ×9 (gated by `GATED_SKILLS`, `tests/test_pre_run_gate.py:28`),
   `escalation_ledger.py` ×6 (`VENDORED_SKILLS`, `tests/test_escalation_ledger_sync.py:29`),
   `routing.py` ×2 (`VENDORED_SKILLS`, `tests/test_case_alert_routing_sync.py:18`).
   `grep -n 'glob\|rglob\|iterdir\|listdir'` across all three sync tests returns **nothing**, so a
   tenth copy added to a new skill is invisible to every gate. The live instance is unchanged:
   `skills/deadline-miss-escalator/broker_writer.py` and
   `skills/client-verification-tracker/broker_writer.py` are 133 lines each and `diff` is a single
   hunk — one docstring word, "escalator" vs "chase". It is worse than ungated: `grep -rn
   broker_writer operator/ tests/` finds **no test file that names it at all**, and both skills load
   it by runtime string (`_load_sibling_module("broker_writer.py", ...)`), so a static consumer scan
   misses it too. Falsifier: a `glob` in any sync test, or a test file naming `broker_writer`.
   **Recommendation:** hash the canonical and glob `skills/*/<name>.py`; that one change closes all
   four classes at once.

7. **[POSITIVE, verified] The decommission pipeline's five real backends are the strongest new code
   in the window, and the exit-code contract is now consistent.** `bin/lib/decommission_backends.py`
   (516 raw lines, under the 500-logical ceiling so it does not enter the ratchet) implements each
   `Protocol` as the inverse of provisioning, and each inverse checks out against the provisioning
   side rather than against a spec:
   - healthchecks: the deleter queries `?tag=operator&tag=<slug>` and matches `name == hermes-<slug>`
     (`:393-403`); `bin/provision-customer.sh:1248-1250` creates the check with
     `'tags': f'operator {slug}'` and `'name': f'hermes-{slug}'`. They agree.
   - AgentMail: the deleter matches `entry['inbox_id'].lower() == address` (`:294-300`); the broker's
     own `agentmail_ops.inbox_id()` (`:190-196`) matches the same way against
     `agentmail_auth.seat_inbox_address`, and `seat_inbox_address` in the backend re-implements that
     three-line rule with a comment saying why it is copied rather than imported.
   - R2 walks `vaults/<slug>/`, `customers/<slug>/` and the skills bucket, and
     `customer_r2_prefixes` deliberately omits `smd-audit-archive` with the reason stated.
   - `backends_from_env` is fail-closed by construction: it only *adds* a backend when its credential
     is staged, so an absent credential leaves `decommission.py`'s stub in place and
     `unwired_destructive_backends()` still refuses `--live` at exit 5. `BACKEND_REQUIREMENTS`
     (`:436-442`) names the exact credential each missing backend needs.
   - `bin/decommission-customer.sh:73` now carries the exit-5 arm with the remediation text, closing
     the half of carried finding 6 that made the documented entry point log `unknown exit 5`.
   - `bin/tests/test_decommission_backends.py` is 20 tests covering happy path, absent-resource skip,
     and error surfacing for each of the five, plus four `backends_from_env` wiring cases.
     `python3 -m pytest bin/tests/test_decommission_backends.py -q` → **20 passed**.

8. **[MEDIUM, NEW] The observability backend reports D1 row deletion it never observed.**
   `HealthchecksAndFleetStatusCleanup.cleanup()` (`decommission_backends.py:415-428`) issues two
   `DELETE` statements and then returns `"fleet_status_row_deleted": True,
   "runtime_summary_row_deleted": True` unconditionally. `ConsoleD1.execute` (`console_d1.py:89-95`)
   returns `first_result_set(proc.stdout)`, which reads only wrangler's `results` array — empty for a
   DELETE — so the class cannot distinguish "deleted a row" from "matched nothing". That manifest is
   the artifact a completion report cites, and the venture's own removal doctrine requires a *negative
   probe per runtime layer* rather than the statement that ran. The same class already has the probe:
   `ConsoleD1.provisioned_slugs()` (`:108`) reads `fleet_status`. Instrument: read of both files.
   Falsifier: `execute` surfacing `meta.changes`, or a follow-up read in `cleanup()`.
   **Recommendation:** re-`SELECT` both tables for the slug after the deletes and report the observed
   absence; report `rows_deleted` as a count, not a boolean.

9. **[MEDIUM, NEW] A destructive backend arms itself from ambient CLI login.** `_fly_authenticated`
   (`decommission_backends.py:445-451`) returns true when `FLY_API_TOKEN` is set **or** when
   `fly auth whoami` exits 0. So a Captain who happens to be logged into the `fly` CLI has the
   `fly apps destroy` backend wired without staging anything, and the `--live` refusal that protected
   the pipeline for months silently stops covering that layer. The commit message for #2735 records
   that the CLI tests had to be neutralized against exactly this ("The CLI tests inherited ambient
   credentials (a logged-in fly CLI...)"), which is the same hazard observed one layer up. The other
   four backends require an explicit environment variable. Instrument: read of `:445-497`; the #2735
   commit body. Falsifier: `_fly_authenticated` keyed only on `FLY_API_TOKEN`. **Recommendation:**
   require `FLY_API_TOKEN`, or keep the whoami path but print the resolved fly org in the pre-flight
   plan so the operator sees which account is about to be destroyed against.

10. **[MEDIUM] `adapter/` (8,295 non-test lines) is reached at boot and by CLIs, never per request,
    and the two zero-importer modules are untouched.** `templates/bootstrap.sh:807` runs
    `/app/safety-substrate/run_invariants.py` at every container start, and those invariants import
    `adapter.audit_log` — real reachability. Beyond that the importers are
    `safety-substrate/refusal.py:150`, `safety-substrate/trust_ceiling_log.py:129`,
    `bin/lib/evidence.py:49`, `bin/lib/decommission_cli.py:107`, `bin/lib/voice_corpus.py:37`,
    `bin/lib/decommission.py:1013`. `adapter/inbound_envelope.py` (298 lines) still has exactly one
    reference in the repo and it is its own test; `adapter/resolve_skill_pins.py` (108 lines) has two
    comment mentions in `scripts/validate-customer-yaml.ts` and nothing else. Instrument:
    `grep -rn '<name>' --include='*.py' --include='*.ts' --include='*.sh' --include='*.json'
    --include='*.yaml' operator/ src/ scripts/ tests/ .github/` excluding self. **Recommendation:**
    enrol or retire, on the `adapter/validate_customer_yaml.py` precedent.

11. **[MEDIUM] `adapter/evidence/packet.py` is still a god object; `establishment_store.py` is still a
    40-method facade.** `packet.py` (1,652 raw / 1,128 logical) holds, at top level: five request and
    result dataclasses, a `ChainPin` verifier, a 169-line `AuditCoverage` class, a `ReadExecutor`
    abstraction with a SQLite implementation, YAML redaction plus secret scanning, nine `_fetch_*`
    data-access functions, three serializers, a **225-line `_readme_text`**, and a 367-line
    `EvidencePacketBuilder`. `establishment_store.py` (2,107 / 1,415) is a single `EstablishmentStore`
    class spanning `:56-2106` with 40 methods and four private refusal-namers (`_refuse_undeclinable`
    `:499`, `_claim_refusal` `:683`, `_refuse_unresolvable` `:895`, `_refuse_restated` `:1511`) — the
    tell for four independent state machines under one roof. For contrast,
    `templates/drafting/drafting_gate_check.py` (1,191 logical, the second-largest module) is a flat
    set of independent `gate_*` functions over one shared data model: large but cohesive, and not the
    same defect. Instrument: AST top-level census. **Recommendation:** lift `_readme_text` and the
    serializers into `packet_render.py` and the `_fetch_*` group into `packet_sources.py`; split
    `EstablishmentStore` along its four refusal namers.

12. **[LOW] Two shell scripts over 1,300 lines sit outside every size ceiling.**
    `bin/provision-customer.sh` (1,369), `templates/entrypoint.sh` (1,309), `bootstrap.sh` (916),
    `bin/boot-smoke-test.sh` (577). The ratchet walks `.py` only —
    `tests/operator-module-size.test.ts:89` is `else if (entry.endsWith('.py'))`. All four are
    structured (`set -euo pipefail`, functions) and clean under `shellcheck -S warning` (see Code
    Quality finding 6). **Recommendation:** extend the ratchet to `*.sh` with its own baseline, or say
    in the ratchet's header that shell is deliberately out of scope.

13. **[LOW, NEW] The module-size ratchet gives stale advice to every skill it flags.**
    `tests/operator-module-size.test.ts:254-261` appends, for any `skills/*/pre_run.py`, a NOTE saying
    "the scheduler stages this file ALONE ... so a naive split breaks the runtime". That is true only
    for skills still on the shared stamp. For the two that adopted `_load_sibling_module`, siblings
    resolve from `/opt/data/skills/<skill>` and `/app/skills/<skill>`, and `templates/Dockerfile:1115`
    copies `operator/skills/` to the second. Three of the four modules the NOTE will actually fire on
    (`lien-ledger-tracker` 969, `deadline-miss-escalator` 832, `medical-records-chaser` 692,
    `client-verification-tracker` 642) are exactly the ones that should split. Instrument: read of the
    test at `:247-261`, of `pre_run.py:301-320`, and `grep -n 'operator/skills' templates/Dockerfile`.
    **Recommendation:** condition the NOTE on the absence of `_load_sibling_module` in the file, and
    point the other branch at the loader.

14. **[LOW, CHANGED from POSITIVE] Two mutual import pairs exist; both are broken by deferred
    imports, so there is still no runtime cycle.** My AST graph (172 modules, 246 intra-`operator`
    edges, resolving a bare name against the importer's own directory first so basename collisions
    cannot masquerade as cycles) reports two SCCs:
    - `bin/lib/send_verify.py` ↔ `bin/lib/send_attribution.py` (via `send_report.py`). The back edge
      is `send_attribution.py:53-56`, inside `if TYPE_CHECKING:` with the comment "type names only;
      send_verify imports us". Erased at runtime.
    - `connectors/smokeball/smokeball_connector/extract.py` ↔ `vision.py`. The back edge is
      `extract.py:203`, a function-local `from .vision import transcribe_pdf` inside `extract_text`,
      after the cache and vision-permission checks. Deliberate and lazy, but it is a genuine mutual
      dependency papered over by import placement rather than by a seam.

    Instrument: `/private/tmp/.../scratchpad/cycles2.py`. Falsifier: either back edge at module scope.
    **Recommendation:** none required for the first; for the second, move `transcribe_pdf`'s call
    behind a small protocol `extract` owns, so the dependency runs one way.

15. **[LOW, NEW] `operator/fixtures/validator-regression/` is dead data.** Two YAML files
    (`enum-violation.yaml`, `nesting-violation.yaml`) with **zero** references anywhere in the repo:
    `grep -rn 'validator-regression' --include='*.ts' --include='*.py' --include='*.yml'
    --include='*.json' --include='*.md' .` (excluding `node_modules`) returns nothing.
    **Recommendation:** wire them into `tests/customer-yaml-validator.test.ts` as the negative cases
    their names promise, or delete them.

**Dimension summary.** The window closed two of the three carried Architecture items the Captain owned
— sticky_stop is enrolled as the tenth overlay pair with both hashes verifying, and the decommission
pipeline has five real, well-tested backends plus a consistent exit-code contract — and
`operator-substrate` became a required check, which is the first time the Python suite could block a
merge. What remains is structural and unmoved: one 313-line 51-complexity authorization if-chain, no
packaging under 451 Python files, three duplicate classes discovered by hand tuple with a fourth pair
discovered by nothing, and two god objects.

**Grade I would assign: C+ (up from C).** Rubric line: "3+ files over 500 lines OR unclear boundaries
OR mixed concerns in route handlers" — 16 modules are over the 500-logical ceiling and `handle()` mixes
authorization with dispatch, so it cannot be B. Above a flat C because both Captain-owned carried items
closed at the layer that matters, the newest module in the tree is the best-argued code in it, and the
gate that makes all of this enforceable is now required.

---

## Dimension 3 — Code Quality

1. **[POSITIVE, verified] `ruff check .` is clean and the gate is real.** `cd operator && ruff check .`
   (ruff 0.15.17, `/opt/homebrew/bin/ruff`) → `All checks passed!`. It runs in CI at
   `.github/workflows/operator-substrate.yml:83-90` with `working-directory: operator`, and that
   workflow is now a required check (Architecture finding 1), so this is the first review at which
   the Python linter can block a merge.

2. **[HIGH] Two thirds of the tree's `noqa` annotations are inert, and the ratio got slightly worse.**
   Census at HEAD: **398 annotations across 168 files** (392/166 on 09-09; 261/121 on 08-24). `ruff.toml`
   selects `E4`, `E7`, `E9`, `S102`. By code:

   | activated (136) | | inert (262) | |
   |---|---|---|---|
   | E402 | 130 | BLE001 | 210 |
   | E731 | 4 | F401 | 28 |
   | S102 | 2 | S310 | 6 |
   | | | ANN001 | 6 |
   | | | F403 | 5 |
   | | | SLF001, S608 | 2 each |
   | | | S603, F841, ARG002 | 1 each |

   **Ten distinct rule codes** are named by suppressions that ruff does not enable. Zero bare `# noqa`
   without a code, which is good discipline. Instrument: `grep -rhno '# noqa: *[A-Z]*[0-9]*'
   operator --include='*.py' | sed 's/.*noqa: *//' | sort | uniq -c`. **Recommendation:** the config's
   own "cheapest next step" is still right — enable `BLE001` and `F401`; between them they retire 238
   of the 262 inert annotations, and only 11 sites would actually fire (finding 3).

3. **[MEDIUM] `BLE001` costs 11 edits to enable, and 61 of its 210 suppressions carry no reason.**
   `ruff check --isolated --select BLE001 --statistics operator/` → **11** uncovered sites; the other
   199 blind-excepts already carry a `noqa`. So the rule the config calls the cheapest next step is
   eleven lines away from being a gate. Meanwhile
   `grep -rn 'noqa: BLE001' --include='*.py' operator/ | grep -vE 'noqa: BLE001 *[-—#(]'` returns
   **61** suppressions with nothing after the code, against 149 that carry a justification
   (e.g. `citation_filter.py:243` "allowlist failure must narrow, not widen", which is exactly the
   right form). Unjustified examples: `audit-chain-watch.py:459,468,541,649`,
   `invariants/invariant_6.py:555`, `invariants/spec_dir_ownership.py:324`,
   `connectors/smokeball/smokeball_connector/library.py:326`. **Recommendation:** enable `BLE001`;
   require a reason after the code with the same gate shape `security.yml` already applies to
   `nosemgrep` in `.ts`/`.js`.

4. **[HIGH] No type checker runs over 451 Python files, and no function or complexity ceiling covers
   Python.** `grep -rn 'mypy\|pyright\|pytype' .github/ operator/ruff.toml operator/pytest.ini
   package.json` returns only the sentence in `ruff.toml` saying no mypy config exists. 4,435 of 6,574
   defs (67%) carry a return annotation and nothing verifies any of it. On size and complexity, the TS
   side runs error-level ESLint rules at 75 lines and complexity 15 with zero violations; the Python
   side has neither:

   | measure | non-test count | worst |
   |---|---|---|
   | functions over 75 lines | 99 | `seed_data.py:241 build_documents` 346L |
   | functions over ruff C901 15 | 32 | `server.py:363 handle` cx 51 |

   Six of the 32 are the same `validate_append` (cx 27) in the six vendored `escalation_ledger.py`
   copies, so the complexity census and the duplication finding are partly the same defect. Instrument:
   `ruff check --isolated --select C901 --config 'lint.mccabe.max-complexity=15' --output-format json`;
   an AST line-count walk. Note: the 09-09 report's "91 exceed complexity 15" used a non-ruff metric;
   `ruff --select C901` is the reproducible one and gives 32. **Recommendation:** add
   `lint.mccabe.max-complexity` with a per-file baseline the way the module-size ratchet works; run
   pyright in the `substrate` job now that it is required.

5. **[MEDIUM] No Python formatter and no line-length convention.** No `black`, no `[format]` block, no
   `line-length` anywhere. `ruff check --select E501` at ruff's default 88 reports **4,346** lines; at
   120 it reports **380**. The tree is visually consistent enough that 120 is nearly satisfied, which
   makes this cheap to close. **Recommendation:** set `line-length = 120` and add `ruff format --check`
   to the substrate job; the 380 residual is one mechanical pass.

6. **[MEDIUM] `shellcheck` never runs anywhere, and five `# shellcheck disable` directives suppress
   nothing.** `grep -rn shellcheck .github/ package.json operator/bin/` finds only the directives
   themselves plus two `# shellcheck source=` hints. The five disables sit inside GitHub Actions
   `run:` blocks — `unaudited-send-reconcile.yml:171`, `control-probes.yml:93`,
   `cron-slot-watchdog.yml:101`, `terminal-state-reconcile.yml:100`, `audit-chain-verify.yml:133` —
   which no linter reads. Carried unchanged. The tree meanwhile is clean:
   `shellcheck -f gcc -S warning operator/bin/*.sh operator/templates/*.sh` → **3 findings, all in
   `templates/_env-arrays.generated.sh`** (SC2148 missing shebang, two SC2034 unused); 7 findings at
   all severities across 16 scripts. Nothing keeps it that way. **Recommendation:** one
   `shellcheck --severity=warning` step in `operator-substrate`, excluding the generated file.

7. **[MEDIUM] Error handling is disciplined in the aggregate but has one copy-pasted swallow class.**
   Of 542 non-test `except` handlers, 223 are broad. Sampling the broad ones in
   `safety-substrate/` shows the good shape: `citation_filter.py:243` returns an empty allowlist with
   "allowlist failure must narrow, not widen"; `identifier_filter.py:709` and
   `run_invariants.py:58,115` convert the exception into a `FAIL:` message. The genuine swallows are
   narrow and countable: `ruff --select S110,S112` reports **24 non-test** `try/except/pass` or
   `try/except/continue` sites. The class worth naming is one block, copy-pasted with an identical
   comment into six skills and seven files:

   ```
   except Exception:  # noqa: BLE001 — observability never gates the wake
       pass
   ```
   `skills/{lien-ledger-tracker:935, medical-records-chaser:633, paid-media-anomaly-watcher:396,
   retainer-hours-reconciler:393}/pre_run.py`, `deadline-miss-escalator/pre_run.py:970`, and both
   `blind_wake.py` copies. The premise is right — a failed observability write must not block a wake —
   but there is no `log.warning`, so a wake-row write that starts failing on a live seat is
   indistinguishable from one that never ran, on the exact rows built to make wake behaviour
   auditable. Instrument: the ruff run; `grep -rl 'observability never gates the wake' operator/skills/`.
   **Recommendation:** extract the block into the shared skill helper (Architecture finding 5) with
   one `log.warning` in it.

8. **[LOW, NEW] A `# noqa: S310` justification describes a property nothing enforces.**
   `bin/lib/decommission_backends.py:94` carries `# noqa: S310 - fixed https hosts, method-locked` on
   `urllib.request.urlopen(req, ...)`, but `http_request(method, url, headers, body)` accepts any
   `url`, and `urlopen` honours `file://` and `ftp://`. Today every call site passes a constant built
   from `CLOUDFLARE_API`, `AGENTMAIL_API` or `HEALTHCHECKS_API`, all `https://`, so the claim is true
   of the callers — but it is a claim about callers written at the callee, in a module whose whole
   purpose is destructive. Compare `bin/lib/seam_pull.py:129`, whose "https enforced at construction"
   is actually enforced. Instrument: read of `:85-96` and every `self.http(` call site.
   **Recommendation:** two lines at the top of `http_request` rejecting a non-`https://` URL, which
   turns the comment into the truth.

9. **[LOW, NEW] `_plan_counts` is two different functions with one name.** In
   `lien-ledger-tracker/pre_run.py:909` it is 4 lines returning `{"plans_total": len(...)}`; in
   `retainer-hours-reconciler/pre_run.py:333` it is 15 lines described as "the cap's own accounting".
   Both are called from the same-shaped `_emit_wake` block. Any future extraction that de-duplicates
   by name will silently merge two semantics. Instrument: `grep -n -A6 'def _plan_counts'` on both.
   **Recommendation:** rename the retainer variant before extracting anything.

10. **[POSITIVE, verified] Effectively zero dead Python modules.** Of 247 non-test modules, exactly
    two have no textual reference anywhere in the repo outside themselves and tests:
    `bin/killtest-msgraph-send.py` and `templates/drafting/corpus_measure.py`. Both are hand-run
    entry points whose opening docstring states why they exist and why they are not a unit test
    (killtest: "Every assertion ... is made against a fake Graph this repo also wrote ... None of
    them can prove the model is the mailbox"). Neither is a defect. The real dead weight is the two
    zero-importer `adapter/` modules in Architecture finding 10, which are library modules, not entry
    points. Instrument: per-module `grep -rIl` over `operator src scripts tests .github docs
    migrations`, excluding self and any `tests/` or `test_*` path.

11. **[POSITIVE, verified] The duplicate census is small and fully enumerated.** md5 over all non-test
    `.py` under `operator/` finds three groups and 14 redundant copies total: `pre_run_gate.py` ×9,
    `escalation_ledger.py` ×6, `routing.py` ×2. Every one is a deliberate vendored copy with a stated
    reason. The problem is how they are discovered, not that they exist (Architecture finding 6).

**Dimension summary.** The Python gate went from advisory to required in this window, which is the
single biggest quality change in three reviews, and the tree it gates is genuinely clean — ruff passes,
no bare `noqa`, two dead modules both documented as hand-run tools, and 14 duplicate copies all
deliberate. What has not moved is the ceiling on the gate itself: it enables four rule groups, so 262
of 398 suppressions still mean nothing, no type checker or formatter runs at all, and neither a
function-length nor a complexity ceiling covers a language where 99 functions exceed the TS side's
limit.

**Grade I would assign: C (stable).** Rubric line: "3+ dead exports OR inconsistent error handling OR
notable DRY" — the DRY half holds (15 duplicated helper bodies, 14 byte-identical file copies, 176
redundant lines) and one error-handling class is copy-pasted into six skills. Not D: `ruff check`
passes, swallowed exceptions are 24 sites out of 542 handlers and most carry a reason, and there is no
dead-module problem. Not B: the linter still activates a third of its own suppressions, and nothing
type-checks, formats, or bounds the size of 451 Python files.

---

## Not checked

- Live behaviour of any decommission backend against the real Cloudflare, AgentMail, Fly or
  healthchecks.io accounts — the backends have never been run live (`--live` refused until this
  commit), so their correctness rests on the unit tests and on the provisioning-inverse reads above.
- The overlay's own source beyond `shared/sticky_stop.py` at the pinned ref `5770b79f`.
- Whether the `skills/*/pre_run.py` test suites exercise `broker_writer.py` through
  `_load_sibling_module` (no test names it; the dynamic load makes a static answer impossible).
- `npm run verify` and the full `operator-substrate` pytest inventory were not run end to end; only
  `tests/operator-module-size.test.ts` (3 passed) and `bin/tests/test_decommission_backends.py`
  (20 passed).


<!-- ===== agent: sec-test ===== -->

## Code review 2026-09-10 — Dimensions 2 (Security) and 4 (Testing)

**Commit reviewed:** `1da55373` (== `origin/main`), worktree `.claude/worktrees/code-review-2026-09-10`, `git rev-parse HEAD` confirmed.
**Prior review:** `docs/reviews/code-review-2026-09-09.md` at `0a3722a3`. Security C, Testing C.
**Window:** `0a3722a3..1da55373`, 11 commits.
**Suites run at HEAD:** `npx vitest run` → 1 failed / 316 passed / 3 skipped files; 5,112 tests (1 failed, 5,099 passed, 5 skipped, 7 todo), 121s. CI-equivalent pytest in a clean `uv` venv (`pytest pyyaml cryptography`, exactly the substrate workflow's install) → 1 failed, 2,510 passed, 94.7s.

---

## Dimension 2 — Security

### A. Carried findings, re-verified at HEAD

Every carried item below was re-probed; none was carried forward on the prior report's authority.

| # | Carried finding | Verdict | Instrument |
|---|---|---|---|
| C3 | `/api/events` client-supplied `session_id` rate-limit bypass | **OPEN-UNCHANGED** | Read `src/pages/api/events.ts:65-79`; `grep -n "rateLimitByIp" src/pages/api/events.ts` → 0 hits, only `checkRateLimit` at `:128,:286` |
| C4 | gitleaks discards history | **OPEN-UNCHANGED, worse premise** | `.github/workflows/security.yml:119` `fetch-depth: 0`, `:191` `gitleaks detect --source . --verbose --no-git`. See finding 4 |
| C5 | `createRemoteJWKSet` per request | **OPEN-UNCHANGED** | `src/lib/operator/mcp/token-validation.ts:69`, inside `verifyPinnedClerkToken`, called per request |
| C6 | JIT MCP identity returns Clerk subject as `localUserId` | **OPEN-UNCHANGED** | `src/lib/operator/mcp/mcp-route.ts:143` `localUserId: subject` |
| C9 | "No Python security lint in Semgrep" | **CHANGED — the premise was wrong.** See finding 6 | 52 `# nosemgrep: python.lang.security.audit.*` suppressions exist in `operator/`, which only exist because those rules fire |
| Prior #2 | Portal routes bind tenant per-route, nothing enumerates the set | **OPEN-UNCHANGED and larger.** See finding 1 | Full enumeration of 19 API + 31 page routes, each read |
| Prior #5 | Sentry replay window bypassable with a non-numeric timestamp | **OPEN-UNCHANGED** | `src/pages/api/webhooks/sentry.ts:64-71`; `Number('abc')` → NaN → `Number.isFinite` false → staleness skipped. HMAC at `:135-155` is over `rawBody` only |
| Prior #6 | `seam_pull.py` `# noqa: S608` justification covers the safe half | **OPEN-UNCHANGED** | `operator/bin/lib/seam_pull.py:218` carries the justification, `:220` carries a bare `# noqa: S608`; `col_defs` at `:216` is still built from served keys |
| Prior #7 | CSRF rests on `SameSite=Lax` alone | **OPEN-UNCHANGED** | `src/lib/auth/session.ts:251` `HttpOnly; Secure; SameSite=Lax`; `grep -rniE "sec-fetch-site\|'origin'" src/middleware.ts src/lib/api/` → 0 hits |
| LOW | Booking limiter allows when KV undefined | **OPEN-UNCHANGED** | `src/lib/booking/rate-limit.ts:47-50` `if (!kv) return { allowed: true, ... }` |
| LOW | Raw upstream body logged | **OPEN-UNCHANGED** | `src/lib/db/integrations.ts:256` |
| LOW | Client email logged on Clerk invitation failure | **OPEN-UNCHANGED** | `src/pages/api/portal/products/operator/invitations.ts:150` `console.error('Clerk invitation failed', { email, message })` |
| LOW | `ss_sid` without HttpOnly | **OPEN-UNCHANGED, by design** | `src/pages/api/events.ts:333-335`: `SameSite=Lax` + conditional `Secure`, no `HttpOnly` |
| LOW | Unescaped filename in `Content-Disposition` | **OPEN-UNCHANGED** | `src/pages/api/admin/assessments/[id]/transcript.ts:40` interpolates `object.customMetadata?.originalName` into a quoted header |
| POSITIVE | Security headers live on all three hosts | **RE-VERIFIED at HEAD** | Own `curl -sI` on all three; see finding P1 |
| POSITIVE | Injection surface clean | **RE-VERIFIED at HEAD** | See finding P2 |
| POSITIVE | Secrets clean, the `.pem` is a public key | **RE-VERIFIED at HEAD** | See finding P3 |

---

### B. New findings

**1. [MEDIUM] `src/middleware.ts:153-171` — the portal enumerator gap is larger than last review measured: 31 page routes are also unenumerated, and 13 of them are gated only by a per-route `getPortalClient` call.**

`enforcePortalAuth` returns allow for any request carrying a Clerk `userId` or a legacy `session.role === 'client'` — no role check, no tenant check. Tenant binding is entirely per-route. I enumerated both sets at HEAD and read every file that a helper scan could not classify.

- **19 API routes** under `src/pages/api/portal/**` (down from 20; one file deleted in the window). 18 call `getPortalClient` / `resolveOperatorAccess` / `resolveHostedAgentAccess` / `authorizeAdvancedSettings`. The 19th, `settings/trust-ceiling.ts`, is an 11-line 410 tombstone (read in full).
- **31 page routes** under `src/pages/portal/**`, which the prior review did not enumerate. 25 carry a helper. Six carry none; I read all six. Five are pure redirect tombstones (`account/`, `configure/`, `people/`, `scope/`, `work/` under `[instance]/`), 10-15 lines each, echoing back the caller's own `instance` param. The sixth, `products/operator/oauth/[connector]/callback.ts`, binds the tenant by a different and stronger mechanism: HMAC-signed state with a 10-minute TTL, plus `reviewerMatchesClerk` at `:190-195` requiring `clerkAuth.userId === reviewer_id` from the signed state.

So the invariant holds today on both sets. What does not exist is the machine that keeps it holding. Admin has one — `tests/admin-routes-require-session.test.ts` walks `src/pages/api/admin` recursively, filters on an `export const GET|POST|...` regex, and fails on any file missing `requireAdminSession` with an `EXEMPT` set that is currently empty and is itself pruned by a second test. Portal has no equivalent for either the 19 API routes or the 31 page routes.

Instrument: shell loop over `git ls-files 'src/pages/api/portal/**'` and `'src/pages/portal/**'` grepping the real helper vocabulary, then `cat` on every `NONE`. Falsifier: a portal route with no tenant binding after reading it — none found; the six candidates all resolved.
Recommendation: clone the admin enumerator twice, once per set. The vocabulary is a union (`getPortalClient|resolveOperatorAccess|resolveHostedAgentAccess|authorizeAdvancedSettings|verifyOAuthState`), with the five redirect tombstones and the 410 in `EXEMPT`.

**2. [MEDIUM] `src/lib/auth/machine-key.ts:1-71` — one shared bearer authenticates every Operator seat, the tenant is an unbound header, and the module's own documented upgrade trigger ("customer #2") has passed.**

`verifyMachineRequest` compares a single `MACHINE_HEARTBEAT_KEY` in constant time, then trusts the `X-Tenant-Slug` header to name the tenant. The header is not bound to the key by anything. `operator/bin/provision-customer.sh:729,741` stages that same value onto every seat ("single value shared across the fleet"), and `operator/templates/bootstrap.sh:906-913` confirms the seat holds it.

`ls -d operator/customers/*/` returns four real seats: `ashton-price`, `pilot-smokeball`, `scott`, `smd-staging`. The module header (`:17-22`) says the per-tenant `machine_credentials` upgrade is the "upgrade path for customer #2." Customer #2 arrived, and one of these seats is a paying production client.

Blast radius, from reading the three consumers: any seat holding the key can post another tenant's `fleet_status` and `operator_runtime_summary` rows (`runtime-summary.ts:75-88` upserts on `entity_id` resolved from the spoofed slug), and can drive `reportAuditWriteFailureDelta` (`heartbeat.ts:564-583`) for another tenant — the alert that exists precisely to catch silent audit-row loss can be suppressed or forged across the tenant boundary by changing one header.

Instrument: read all of `machine-key.ts`, `grep -rn "verifyMachineRequest" src/pages/api/internal/`, `grep -rn MACHINE_HEARTBEAT_KEY operator/bin/*.sh operator/templates/`.
Falsifier: a slug/key binding anywhere in the chain, or a single-seat fleet. Neither holds.
Recommendation: execute the documented upgrade — per-tenant `key_hash` with per-row salt and dual-key rotation. It changes one file. In the interim, at minimum reject a slug whose resolved `entity_id` disagrees with a per-seat claim.

**3. [MEDIUM] `operator/bin/lib/decommission_cli.py:305-320` — `--allow-unwired --live` is documented "DEV/FIXTURE ONLY" and nothing restricts it to a fixture root.**

The new destructive pipeline (#2735) is otherwise carefully fail-closed: `_preflight` (`:209-219`) requires the seat directory to exist, and `--live` refuses with exit 5 naming each missing credential (`:286-303`) when any destructive backend is a stub. That refusal is real and well-tested.

`--allow-unwired` bypasses it. The flag's help text at `:161-168` says "DEV/FIXTURE ONLY: the run will NOT actually delete those substrates" — but it only skips the *unwired* backends. Every wired one still executes. On a Captain shell with a logged-in `fly` CLI (which `_fly_authenticated` at `decommission_backends.py:441-447` treats as wired without any token), `--live --allow-unwired ashton-price` against the default customers root destroys the real Fly app, its `/opt/data` volume and its secrets, while printing a warning that the run "does NOT fully decommission the customer." No confirmation prompt, no typed-slug re-entry, no restriction on `--customers-root`.

Instrument: read `decommission_cli.py:140-200,255-320` and `decommission_backends.py:441-500`.
Falsifier: a guard tying `--allow-unwired` to a non-default `--customers-root`, or an interactive confirmation on `--live`. `grep -n allow_unwired operator/bin/lib/decommission_cli.py` returns exactly two lines, both in the refusal branch.
Recommendation: refuse `--allow-unwired` unless `--customers-root` was passed explicitly and resolves outside `operator/customers`. Separately, require the slug typed twice for any `--live` run, the way `fly` itself requires confirmation before the code passes `--yes` on the Captain's behalf.

**4. [MEDIUM] `.github/workflows/security.yml:119,191` — gitleaks scans the working tree only, on a repository that is public at HEAD.**

Unchanged mechanically. The change is the premise: `gh api repos/venturecrane/ss-console --jq '{private,visibility}'` returns `{"private":false,"visibility":"public"}`. A secret committed and later removed is invisible to `--no-git` and permanently readable in the public history. The checkout already pays for `fetch-depth: 0`, so the fix is deleting one flag.

Related and worth stating: `tests/pricing-figures.test.ts:1-19` reasons from "This repo is private today, but its visibility is a setting somebody could flip." It has flipped. The gate still works (it bans the specific locked figures), but it is now the load-bearing control rather than defence-in-depth, and its header misinforms the next reader.

Instrument: `gh api`, read of the workflow and the test header.
Recommendation: drop `--no-git`; expect a first run to surface historical hits and triage them against `.gitleaks.toml`. Update the test header's premise in the same change.

**5. [LOW] `src/pages/api/assessment/llm.ts:89` — the bearer secret on the one public LLM proxy is compared with `!==`, alone among the repo's shared-secret checks.**

`if (auth !== \`Bearer ${expected}\`)`. Every sibling uses a constant-time compare: `machine-key.ts:65-71`, `webhooks/sentry.ts:150-154`, and the Resend and healthchecks handlers. The route is otherwise the best-designed public endpoint in the tree — fail-closed 503 when the secret is unset, explicit reasoning for why a per-IP bucket is wrong here (single ElevenLabs egress IP), size caps, and metadata-only logging (`:107`, roles and counts, never content).

Remote timing extraction against a Worker behind Cloudflare is not a practical attack; the finding is consistency, and the fact that this is the one endpoint whose compromise spends the Anthropic budget.
Instrument: read the file in full.
Recommendation: reuse the existing constant-time helper.

**6. [LOW] `.github/workflows/security.yml:88` — the nosemgrep-justification gate matches `//` only, so all 52 Python suppressions are exempt from it.**

The carried finding C9 said Semgrep has "no Python security lint." That is not what the config does: `p/security-audit` and `p/owasp-top-ten` both carry Python rules, the scan step excludes only `node_modules`, `dist` and `**/*.test.ts`, and the proof is that `operator/` carries 52 `# nosemgrep: python.lang.security.audit.*` annotations — rules that do not fire produce no suppressions. So Python **is** scanned.

The real gap is the enforcement asymmetry. The justification gate's regex is `'//[[:space:]]*nosemgrep...'`; Python comments start with `#`. Today the Python side voluntarily carries good justifications — of 52, only 4 lack ≥20 characters after the rule id, and 2 of those 4 are prose mentions rather than real suppressions. Nothing keeps it that way. `operator/bin/rehearse-card.py:202,292` are already bare.

Instrument: `grep -rn nosemgrep operator --include='*.py' | wc -l` → 52; `grep -rh nosemgrep operator --include='*.py' | grep -vcE '(—|: ).{20,}'` → 4; read of the gate step.
Recommendation: extend the gate's regex to `(//|#)`. Separately, add `--config=p/python` for the taint rules the audit packs do not carry.

**7. [LOW] `operator/workspace_broker/server.py:986-991` — the broker returns `str(exc)` for any unhandled exception to the socket peer.**

`RequestHandler.handle` catches bare `Exception` and replies `{"ok": False, "error": type(exc).__name__, "message": str(exc)}`. The authorization design around it is sound: `SO_PEERCRED` peer-uid extraction, per-verb `PermissionError` on uid mismatch (`:382,411,461,522,573`), gateway-PID gating (`:596`), a 0o660 socket with a justified suppression, and a `MAX_REQUEST_BYTES` cap. But an exception message is not a bounded vocabulary — a `KeyError`, an OS error carrying a path, or a vendor client's exception carrying a URL becomes a reply to whatever holds the socket group.
Recommendation: map unexpected exceptions to a generic `internal_error`, log the detail broker-side.

**8. [LOW] `src/pages/portal/products/operator/oauth/[connector]/callback.ts:230-244` — a reviewer's access is checked as it was at authorize time, not at callback time.**

The route requires `clerkAuth.userId === reviewer_id` from the signed state, which is the right binding. But the reviewer's *current* entitlement on `customer_id` is not re-resolved; if their access is revoked inside the 10-minute state TTL, the token still stores against that customer. Narrow window, requires a revocation mid-consent.
Recommendation: call `resolveOperatorAccess(customer_id)` after the reviewer match, before `store()`.

---

### C. Positives, re-verified at HEAD

**P1. [POSITIVE] Security headers live on all three hosts, including the 302 paths.** My own `curl -sI` on `smd.services` (200), `admin.smd.services` (302), `portal.smd.services` (302): all three return `strict-transport-security: max-age=31536000; includeSubDomains`, `content-security-policy: frame-ancestors 'none'`, `x-content-type-options: nosniff`, `x-frame-options: DENY`, `referrer-policy: strict-origin-when-cross-origin`. No `Permissions-Policy`, no resource CSP, no HSTS `preload` — each absence carries its reason in `src/lib/security/response-headers.ts`.

**P2. [POSITIVE] Injection surface clean in both languages, re-derived.** TypeScript: 468 `.prepare(` sites, of which 24 use a template literal; I read all 24 and every interpolation is a code-controlled constant (`placeholders` built from `entityIds.length`, `COLUMNS`/`BILLING_COLUMNS` module constants, `EMAIL_IDENTITY_PREDICATE` = the literal `'lower(email) = ?'`, and `conditions.join(' AND ')` in `services.ts:202` assembled at `:189-199` from literals with every value bound). Zero `dangerouslySetInnerHTML`, `eval(`, `new Function(`. All 8 `set:html` sites pass through `serializeJsonLd`, which escapes `<` to `<` — the correct fix for `</script>` breakout, with the reasoning written down. Python: 0 `shell=True`, 0 `os.system(`, 0 `pickle`, 0 unsafe `yaml.load` against 87 `safe_load` calls. `operator/bin/lib/console_d1.py:49-67` `sql_text` remains the model of the class: a hex blob literal cast back to TEXT, chosen over quote-doubling precisely because the inlined value can be a seat's own untrusted export, with the threat named in the docstring.

**P3. [POSITIVE] Secrets clean; the tracked `.pem` is a public key.** The shape sweep (`sk_live|sk_test|whsec_|AKIA|ghp_|-----BEGIN`) over `src operator workers scripts .github` returns only documentation prose, scrubber regex constants in `evidence/packet.py`, `voice_corpus.py` and `spec_leak_check.py`, and `sk_test_*` fixtures in tests. `git ls-files | grep '\.pem$'` → one file, `public/keys/evidence-packet-signing-key.pem`, whose first line is `-----BEGIN PUBLIC KEY-----` with an Ed25519 SPKI body. It is served from `public/` on purpose so a client's carrier can verify a detached evidence-packet signature.

**P4. [POSITIVE] The new destructive backends are built for the wrong-seat case, and the safety invariant is pinned by test.** `decommission_backends.py` — every backend lists before it deletes, so an already-gone artifact is a `skipped` with a reason rather than a failure; `customer_r2_prefixes` names two buckets and deliberately excludes `smd-audit-archive` (the retention copy step 02 just wrote); `ARCHIVE_SEGMENT` is skipped inside the surviving prefixes; the D1 deletes go through `sql_text`. `operator/bin/tests/test_decommission_backends.py:102-122` asserts all three of: the archive object survived, no delete URL contained `decommission-archive`, and **no listed URL contained `smd-audit-archive`** — so adding the archive bucket to the prefix list turns the suite red.

**P5. [POSITIVE] The Operator MCP door's grant model.** `src/lib/operator/mcp/grant-store.ts` and `customer-resolution.ts:158-170`: grants are read with `revoked_at IS NULL AND expires_at > now` in SQL, so revocation and lapse take effect on the very next request; TTL is never null and never infinite (`GRANT_TTL_MAX_DAYS = 90`); JIT auto-issue carries a shorter 7-day ceiling and a 50-grant per-customer cap; a revoked row makes JIT refuse permanently (sticky revoke) while an admin re-issue may lift it; every issue and revoke writes an append-only `operator_mcp_grant_audit` row. The retired `/api/mcp` is a genuine 410 on all three verbs.

---

### Dimension 2 summary and grade

Five carried mediums stand, one of them (gitleaks) on a now-public repository, and I found two new mediums the prior review did not reach: the fleet-wide shared Machine bearer whose documented upgrade trigger has passed, and the unrestricted `--allow-unwired --live` path on the new destructive pipeline. Nothing escalated to high, and the code written in this window is defensively excellent — the decommission backends list before they delete and pin the archive-bucket exclusion in a test.

**Grade: C (stable).** Rubric line: "any medium OR 3+ low." Seven mediums stand. Not D — no critical, no high, injection and secrets clean on both languages, headers live, webhook verification uniform. Not B — five mediums have now survived three reviews and two more joined them.

---

## Dimension 4 — Testing

### A. Carried findings, re-verified at HEAD

**T1. [CLOSED — verified at three layers] `operator-substrate` is a required check on `main`.**

The prior review's top action item. `gh api repos/venturecrane/ss-console/branches/main/protection --jq '.required_status_checks.contexts'` still returns only `["Typecheck, Lint, Format, Test"]`, which is where a shallow probe stops and concludes the finding is open. The ruleset is the live mechanism: `gh api repos/venturecrane/ss-console/rulesets/15555219 --jq '.rules[] | select(.type=="required_status_checks") | .parameters.required_status_checks[].context'` returns `Security Summary` and **`substrate`** — `substrate` is the job name in `.github/workflows/operator-substrate.yml`, so that is the check context. Enforcement is `active`, `bypass_actors` is empty, and the condition targets `~DEFAULT_BRANCH`.

And it reports: `gh api repos/venturecrane/ss-console/commits/1da55373/check-runs` lists `substrate  success` alongside the other nine.

The mechanism behind it is right, not merely present. #2726 removed the `paths:` filter — a path-filtered workflow reports nothing on the PRs it skips, and a required check that never reports strands the PR at "Expected" forever. The decision moved inside the job into `operator/bin/substrate-paths-changed.py`, which fails **closed**: an uncomputable diff or an absent base sha both answer `true` and run everything. The final step reports success on the no-change path so the required check is satisfied. `.github/operator-substrate-paths.txt` covers every directory that holds a Python test — I cross-checked `find operator -name 'test_*.py'` grouped by top-level dir (`bin` 40, `connectors` 37, `workspace_broker` 22, `safety-substrate` 19, `adapter` 19, `runners` 14, `skills` 8, `templates` 7, `rehearsal` 6, `tests` 5) against the pattern list; all ten are covered, and `bin/tests/test_ci_coverage_conformance.py` pins the two consumers to each other.

The workflow runs ruff, the run()-style invariants, one full pytest invocation over ten directories, the SEC-32 overlay runtime-hash drift gate, per-connector conformance in isolated `uv` venvs built the way the seat image builds them, and the medchron runner in its own venv.

One residual, low: the required context is the bare string `substrate` with no `integration_id` pin, so any check run named `substrate` from any app satisfies it.

**T2. [CLOSED, exemplary] Coverage thresholds never evaluated.** `vitest.config.ts:57-84` removes them and writes down why — not a lapsed guardrail but four numbers that had never been evaluated once, over a provider that was never installed and that collects zero files under Astro's `getViteConfig` wrapper. `tests/coverage-config-honesty.test.ts` fails if thresholds return without a runner.

**T3. [OPEN-UNCHANGED] `src/lib/db/assessments.ts` has nine production importers and zero test importers.** `grep -rln "db/assessments" src` excluding tests → 9. `tests/assessments.test.ts` is 208 lines with 22 `source()` calls and 48 `toContain` assertions of the exact form `expect(source()).toContain('export async function listAssessments')` against `readFileSync`. What would have turned it red: renaming an export. What would not: a dropped `org_id` predicate, an inverted `WHERE`, an SQL typo.

**T4. [OPEN-UNCHANGED, sharpened] `src/lib/stripe/checkout.ts` has three production importers and is mocked, never exercised.** `tests/portal-billing-manage.test.ts:28` is `vi.mock('../src/lib/stripe/checkout', ...)`. No test imports the real module.

**T5. [OPEN-UNCHANGED] The source-text test set.** I re-probed the 17 files the prior review named by that criterion. All 17 still have zero relative imports of a subject: `assessments` (22 `source()` calls), `clients` (2), `contacts` (13), `engagements` (26), `invoices` (49), `milestones` (18), `quotes` (37), `follow-ups` (59), `time-entries` (15), `parking-lot` (29), `reply-log` (0), `signwell` (49), `contact-endpoint` (0), `portal-quotes` (47), `portal-docs` (27), `client-identity-gate` (0), `middleware` (17). Nine of them were touched in this window by #2732's export deletions — touched, not upgraded.

One check I ran and was wrong about, recorded so the conclusion is not overstated: I expected the new retainer rail's 3% fee math to fall in this gap, since `cardProcessingFeeCents` lives in `src/lib/db/invoices.ts` whose test file is 49 source calls. It does not. `tests/operator-go-live-billing.test.ts:44,183-185` imports and exercises it with three real cases including the rounding boundary.

**T6. [OPEN-UNCHANGED] Nothing measures coverage in either language.** No `vitest --coverage` in any workflow; no `pytest-cov` or coverage config under `operator/`. A known, deliberately-held state, not a regression.

**T7. [HALF CLOSED, and I reproduced the open half] The two environment-dependent Python tests.**

- `bin/tests/test_decommission.py` — **CLOSED, and closed for the right reason.** #2735 added a `_no_ambient_backends` fixture (`:466-481`) that monkeypatches `backends_from_env` to report everything unwired and `seam_client_from_env` to return `None`, with the reasoning stated: a logged-in `fly` CLI in a developer's shell was enough to wire the real Fly destroyer inside a unit test that calls `main(--live --allow-unwired)`. That is a security fix wearing a test fixture's clothes.
- `adapter/evidence/tests/test_packet.py::test_build_emits_targz_with_every_expected_file` — **OPEN-UNCHANGED, reproduced.** My CI-equivalent run (clean `uv` venv, exactly the workflow's `pytest pyyaml cryptography`) gave `1 failed, 2510 passed in 94.72s`, and the single failure is this test: `Left contains one more item: 'manifest.sig'`. `signing.py:72` reads `EVIDENCE_PACKET_SIGNING_KEY_B64` from ambient `os.environ`; there is no `conftest.py` under `adapter/evidence/tests` and no `monkeypatch.delenv`. It passes in CI only because CI has no such key staged.

**T8. [RE-VERIFIED, with new numbers] Both suites are large, fast and nearly skip-free.** vitest at HEAD: 320 files, 5,112 tests, 121s. Python CI-equivalent: 2,510 passed, 94.7s. Counts are up on the Python side (was 2,486) and down 124 on the TS side (was 5,236) — the drop is #2732 deleting 30 dead files and the tests that only they consumed, which is the right direction. Zero snapshot tests (`grep -rn "toMatchSnapshot\|toMatchInlineSnapshot"` → 0; no `__snapshots__` directory), so the "snapshot that regenerates" failure mode does not exist here. Zero `assert True` / `assert 1 == 1` in 177 Python test files.

---

### B. New findings

**T9. [MEDIUM] `tests/build-output.test.ts:26` fails on a clean checkout, and its second test — the only guard on published dollar amounts in built output — is effectively inert.**

Two problems in a 40-line file.

*It fails.* `expect(existsSync(resolve('dist'))).toBe(true)` was the one failure in my `npx vitest run` at HEAD. `dist/` is gitignored (`.gitignore:8`); `npm run verify` runs `build` before `test`, so CI is green, but `npm test` alone is red on any fresh clone or worktree. This is the TS-side twin of the carried Python finding T7, and it is worse in one respect: the assertion's subject is not the code under test, it is whether a prior npm script ran.

*It cannot meaningfully fail.* The second test walks `dist/` for `.html` files and asserts `$2,500` appears in none. `astro.config.mjs:27` is `output: 'server'` and exactly one page opts out (`src/pages/404.astro`), so the walk yields one file that could never carry a price. The test's own comment says so: "This assertion passes trivially when nothing is prerendered." Meanwhile the real control on this class exists and is live — `tests/pricing-figures.test.ts` bans the specific locked figures at source level across the tree — which makes the build-output copy a vestige that contributes a false-red and a false sense of coverage.

Instrument: the vitest run output; `grep -n "^dist" .gitignore`; `grep -n output astro.config.mjs`; `grep -rln "prerender = true" src/pages`.
Falsifier: a workflow that runs `vitest` without a preceding build (none), or prerendered pages carrying prices (only 404 prerenders).
Recommendation: delete the file. `pricing-figures.test.ts` is the guard; if a built-output scan is wanted, it belongs in the build workflow after `astro build`, not in the unit suite.

**T10. [POSITIVE] `tests/public-waivers.test.ts` (new in the window) closes the prior review's highest Code Quality finding by making the escape hatch falsifiable.**

The prior review's HIGH #1 was that `@public` — the only exemption from the dead-export gate — carried unverified prose, with three of fifteen tags naming consumers that do not exist and six citing tests that merely grep the export's own source line. The new test enforces: a `@public` block must name at least one repo-relative path; every named path must exist; at least one must reference the symbol; and when that path is a test, the test must **import** the symbol, because `expect(source()).toContain('export function x')` is the assertion restating the line the tag sits above. Its header states the rule and the two legitimate shapes it permits. This is the right shape of fix — it makes the tag cost something rather than asking reviewers to check harder.

**T11. [POSITIVE] The window's money path is the best-tested code in the tree.** `tests/stripe-subscriptions.test.ts` (247 lines) imports the real `src/lib/stripe/subscriptions` — zero `source()` calls — stubs global fetch with per-URL responders, records every call, and asserts wire-level Stripe form params: `line_items[1][price_data][product]` resolving to the fee product, the metadata marker used to find-or-create it, `pause_collection[behavior]=void`, `DELETE` for cancel. A regression in the ACH-versus-card rail or in the 3% fee line turns it red.

**T12. [POSITIVE] The new destructive module is tested at 20 cases covering the safety properties, not just the happy path.** `operator/bin/tests/test_decommission_backends.py` — 20 tests over five backends. For each: the delete path, the absent-artifact skip, and the transport-failure surface. Plus paginated R2 listing, the authored-versus-convention inbox address rule, the five credential-wiring permutations including the `CF_*` aliases the provisioning scripts use, and the archive-bucket exclusion (finding P4).

**T13. [LOW] The SOW PDF render path has no executing test.** `tests/sow-render.test.ts` is a `describe.skip` containing two `it.skip`s whose bodies are `expect(true).toBe(true)` — these are the only two such assertions in 320 test files, and they are deliberate, documented placeholders (Forme WASM does not load under Node). Fair. But it means the one artifact a client actually signs is validated only by `npm run build` compiling the template and by manual checks, and `src/lib/pdf/sow-template.tsx` is a file the venture's own no-fabricated-content policy names as a past violation site.
Recommendation: a Workers-pool vitest project (`environment: 'workers'` via `@cloudflare/vitest-pool-workers`) would let the WASM load, or render one fixture SOW in CI after build and assert on extracted text.

**T14. [LOW] The admin route enumerator matches a substring, so a mention passes.** `tests/admin-routes-require-session.test.ts:43` is `if (!src.includes('requireAdminSession'))`. A route with the helper named only in a comment satisfies it. Not a live problem — all 48 admin routes genuinely call it — but the portal clones recommended in Security finding 1 should assert on a call site (`requireAdminSession(` or an import) rather than a bare substring.

---

### Dimension 4 summary and grade

The single largest carried finding closed, and closed at the mechanism rather than the appearance: `substrate` is now a required check that reports on every PR, decides inside the job from a pinned path list, and fails closed when it cannot decide — 2,510 Python tests moved from advisory to blocking. Against that, the two data-layer gaps are unchanged (`assessments.ts` at nine production importers with zero test importers, `stripe/checkout.ts` mocked and never exercised), all 17 source-text test files still assert on strings, nothing measures coverage in either language, and one test in each language fails outside CI for reasons that have nothing to do with the code.

**Grade: C+ (up from C).** Rubric line: "significant gaps OR critical paths untested" still applies — `assessments.ts` and `stripe/checkout.ts` are both live paths with no behavioural test. The plus is earned by the Python gate becoming real and by the window's own tests: `public-waivers.test.ts` converts a prose escape hatch into a merge gate, and `stripe-subscriptions.test.ts` and `test_decommission_backends.py` are behavioural tests over a money path and a destructive path respectively. Not B — the gap set did not shrink, it was held while the gate around it improved.

---

## Not checked

Clerk's own `__session` cookie attributes (vendor bundle). The `MACHINE_HEARTBEAT_KEY`, `ELEVENLABS_LLM_SECRET` and `AGENTMAIL_API_KEY` values. R2/D1 access policy in the Cloudflare dashboard. Live behaviour of any authenticated endpoint. The overlay's runtime source beyond what `verify-overlay-pairs.py` hashes. Whether `p/security-audit` and `p/owasp-top-ten` produce zero Python findings today because the code is clean or because the diff-aware baseline suppressed them — settling that needs a full non-baselined semgrep run, which the daily cron performs but which I did not trigger.


<!-- ===== agent: deps-docs-gp ===== -->

## Dimensions 5-7: Dependencies, Documentation, Golden Path Tier 2
## venturecrane/ss-console @ 1da55373 (2026-09-10) vs docs/reviews/code-review-2026-09-09.md @ 0a3722a3

---

## 5. Dependencies

**Carried findings, re-verified at HEAD:**

1. `npm audit` high/critical findings — **CLOSED.** Instrument: `npm audit --json` at root → `{critical:0, high:0, moderate:0, low:0, total:0}`; same in `workers/cost-anomaly`, `workers/cost-telemetry`, `workers/fleet-alerts` (all zero). `.github/audit-allowlist.json` `allow: {}` (confirmed empty, per #2731). Commit `4c328341` bumped `astro` 7.1.3→7.2.10, `sharp` 0.35.2→0.35.4 (override), `svgo` 4.0.2→4.1.0 (override), `js-yaml` 4.3.1→4.3.2 (override), plus `vitest`, `@humanfs/node`, `postcss-selector-parser` — all versions chosen to clear the `.npmrc` `min-release-age=7` cooldown by publish date. Falsifier: any nonzero severity count in any of the four `npm audit --json` outputs; none appeared. **Caveat:** local `node_modules/astro/package.json` still reads `7.1.3` (installed Sep 9, before the bump) while `package-lock.json` and `package.json` both read `7.2.10` — this is the known stale-`node_modules` trap in the primary checkout ([[reference_worktree_stale_node_modules_trap]]), not a repo defect; `npm audit` reads the lockfile, so the zero-vuln result is trustworthy regardless.
2. Seat image Python installs unpinned — **OPEN, UNCHANGED.** `operator/templates/Dockerfile:1017-1018`: `uv pip install --python /opt/workspace-broker/.venv/bin/python google-api-python-client google-auth pyyaml` — no version specifiers, unchanged since 08-23. No root `pyproject.toml`/`requirements*.txt`/`uv.lock` under `operator/` (only per-connector `pyproject.toml` in `operator/connectors/*` and `operator/runners/medchron`, none covering the broker venv). No `pip` or `docker` ecosystem in `.github/dependabot.yml` (five `npm` entries only). **Method-trap correction:** a naive `grep -rln "pip-audit\|safety" .github` hits `.github/operator-substrate-paths.txt:20` (`operator/safety-substrate/**`, a path glob) and job-name substrings in `operator-substrate.yml`/`control-probes.yml` that contain the string "safety" as part of `safety-substrate` — none of these run an actual pip vulnerability scanner. `grep -n "pip-audit\|pip_audit\|safety\|bandit" .github/workflows/*.yml` (files only, no path-list) returns nothing. No Python dependency of any kind is audited anywhere in CI.
3. `typescript` 6.0.3 / `@cloudflare/workers-types` one major behind — **OPEN, unchanged, deliberate.** `npm outdated --json`: typescript current 6.0.3, latest 7.0.2 (installed-tree read is the stale-node_modules artifact above; the lockfile pin is the same range). `.github/dependabot.yml:26-27,63-70,86-93` explicitly separates majors for manual review and pins `@cloudflare/workers-types` to move only with wrangler's major. Working as designed.
4. **[POSITIVE, re-verified]** SHA-pinning: `grep -rn "uses:" .github/workflows/*.yml` → 51 lines, zero non-SHA-pinned (all match `@[a-f0-9]{40}`). Zero `npm install` anywhere in workflows; 4 explicit `npm ci` calls (rest use cached setup-node install). No dynamic-codegen dependency reachable from the Worker bundle: re-verified beyond a grep this cycle — `grep -rn "new Function(" dist/server/chunks/*.mjs` hits one site, `dist/server/chunks/schemas_DVY78wzB.mjs:144`, inside zod's own `allowsEval` capability probe (`if (navigator?.userAgent?.includes("Cloudflare")) return false; try { new Function(""); } ...`), which short-circuits to `false` on the Cloudflare Workers runtime itself and never receives attacker-influenced input — not an ajv/handlebars-style compiler. `npm ls ajv --omit=dev` → empty (ajv is dev-only, pulled by `@astrojs/check` and `eslint`); no `handlebars` anywhere in the tree.

**New finding:**

5. **[LOW] `npx knip --production --dependencies` reports two "unused" dependencies that are real, active production dependencies — a tool-scope false positive worth flagging so the next reviewer doesn't recount it as a repo defect.** `@astrojs/cloudflare` and `@astrojs/sitemap` are imported at `astro.config.mjs:2,4` (the build/adapter config), which `knip --production` excludes from its production entrypoint graph by design (config files aren't "production" in knip's model). Plain `npx knip --dependencies` (no `--production` flag, matching last review's instrument) reports zero unused dependencies, confirming this is a flag-scope artifact, not new dead weight. Falsifier: either package being genuinely unreferenced by `astro.config.mjs` would have made this a real finding; both are referenced and required for the Cloudflare adapter and sitemap generation to function. Recommendation: none needed in the repo; note in the next preamble that `--production` mode false-flags config-only imports for this project.

**Dimension summary:** The critical/high npm-audit findings that dropped this dimension to D are fully closed by a real, verified version bump (not just allowlist bookkeeping) across all four packages. The one independent, unrelated gap — unpinned, unaudited Python dependencies in the seat image — is unchanged for the third consecutive review.

**Grade: B.** Rubric: no high-severity audit findings, no runtime-incompatible dependency; docked from A by the still-open unpinned/unaudited Python supply chain (a real, if lower-severity, gap) and the still-caret-capped TypeScript major (documented as deliberate, so not weighted further).

---

## 6. Documentation

**Carried findings, re-verified at HEAD:**

1. CLAUDE.md ADR range / decision count drift — **OPEN, UNCHANGED.** `git log 0a3722a3..1da55373 -- CLAUDE.md docs/adr/decision-stack.md docs/adr/index.md README.md` returns nothing — none of the four files touched in the window. `CLAUDE.md:304` still says "37 active decisions ... through #55"; `docs/adr/decision-stack.md:26` still says "38 active decisions ... through #56"; `docs/adr/index.md:9` still says "34 active ... through #51" — three numbers, one fact, unchanged. `CLAUDE.md:305` still says ADRs run "`0004-*.md` through `0061-*.md`"; highest tracked file is now `0087-chronology-package-content-control.md` (`ls docs/adr/*.md` sorted numeric, unchanged range gap from last review). `README.md:18` still says "TypeScript 5" against declared `^6.0.3` (whose own latest is now 7.0.2 per `npm outdated`, so the claim drifts further from current reality, not closer).
2. No API documentation — **OPEN, UNCHANGED, tracking issue still closed without delivery.** `git ls-files src/pages/api | wc -l` → 98 (down from 99 — net effect of the 30-dead-file cleanup and the decommission wave, not a docs change). `ls docs/api` → no such directory. `gh issue view 2385` → still `CLOSED` at 2026-08-31T17:25:06Z with the same "closed in the backlog reset" comment. `gh issue list --search "API documentation" --state open` → empty; no replacement tracking issue exists.
3. False in-code documentation claims naming a phantom `workers/booking-cleanup/` worker — **CHANGED: two of four instances closed, two remain open.**
   - **CLOSED:** `src/lib/operator/capabilities/conformance.ts` — the file no longer exists. `git show 3d0ba79b --stat` shows it deleted (292 lines) along with its test file `tests/capability-conformance.test.ts` (294 lines) as part of the 30-dead-file cleanup. Repo-wide `grep -rn "runConformanceSuite" . --include="*.ts" --include="*.md"` (excluding node_modules/worktrees) now returns zero hits outside this and prior review documents themselves.
   - **CLOSED:** `knip.jsonc`'s "ten categories reported by `npm run deadcode`" claim — commit `34f930ab` changed those ten rule settings from `"off"` to `"warn"` (`knip.jsonc:127-137`: `files`, `dependencies`, `devDependencies`, `optionalPeerDependencies`, `unlisted`, `binaries`, `unresolved`, `duplicates`, `cycles`, and one more), with an updated comment explaining that `warn` (not the old `off`) is what makes the "reported, non-blocking" claim true, since knip drops an `"off"` category before collection. The claim in the config now matches the config's own behavior.
   - **STILL OPEN:** `src/lib/booking/holds.ts:18` — "The daily cleanup cron (`workers/booking-cleanup/`) deletes long-stale..." — no such directory exists (`git ls-files workers | cut -d/ -f2 | sort -u` → `cost-anomaly`, `cost-telemetry`, `fleet-alerts` only).
   - **STILL OPEN:** `src/lib/db/oauth-states.ts:85` — dangling comment "Cleanup (optional — called by booking-cleanup cron)" with no function beneath it.
   - Neither `holds.ts` nor `oauth-states.ts` appears in `git log 0a3722a3..1da55373` — untouched in the window.
4. Handbook `data-model.md` stale against migrations — **OPEN, UNCHANGED, gap widened.** `git log -1 --format="%h %ai" -- docs/handbook/data-model.md` → `0f0e8181 2026-07-03`, same commit as last review. Migrations now run through `0113_services_payment_method.sql` (added in-window by the retainer-rail commit `8dab9408`); the page still predates `0105` through `0113` — nine migrations behind, one more than at last review (`0105` cron containment, `0106` webhook surface, `0107` ×2 audit ledger / gateway loop, `0108` audit head history, `0109` send refusals, `0110` invoices implementation_type, `0111` operator agreement documents, `0112` sticky_stop cause, `0113` services payment method — none reflected). `npm run handbook:drift` (advisory) independently flags `data-model.md`: "source changed ~69d after the page: `src/lib/db`" and "~39d after the page: `docs/specs/operator/d1-schema.md`". **Note, in the plus column:** the two commits in-window whose maintenance contract explicitly names doc updates both did update their own narrow pages — `8dab9408` touched `docs/handbook/client-portal.md`; `1da55373` touched `docs/specs/operator/decommission-customer.md` (26 lines). The gap is specifically `data-model.md`/`pricing-economics.md` not picking up cross-cutting schema/pricing changes, not a general lapse in the maintenance contract.
5. `crane_docs_drift_audit` scope note — **still applies, unchanged, informational only.**
6. **[POSITIVE, re-verified]** 87 ADRs still numbered and present (`0087-chronology-package-content-control.md` highest, unchanged this window). `tests/handbook-integrity.test.ts` still runs in `npm run verify`.

**Dimension summary:** Half of the phantom-worker documentation family was resolved this window as a side effect of dead-code cleanup and a knip-config fix rather than a targeted docs pass, and neither of the two closures required a docs-specific commit. The two structural gaps — no API docs with the tracking issue closed by policy, and three conflicting ADR/decision counts in three different files — are exactly as they were yesterday, and the schema-doc lag grew by one migration.

**Grade: C+.** Rubric: "no API documentation for a project with API endpoints" still applies verbatim, holding the grade out of B range; nudged up from C because two of four in-code false claims are now genuinely closed (one by outright deletion, one by a real behavioral fix) and nothing regressed.

---

## 7. Golden Path Compliance (Tier 2)

| Requirement | Status | Instrument |
|---|---|---|
| Source control, CLAUDE.md, TypeScript + ESLint, no hardcoded secrets (Tier 1) | Met | unchanged from last review |
| Error monitoring (Sentry) | Partial, denominator improved | `captureError(` call sites: `src/middleware.ts`, `src/pages/api/internal/heartbeat.ts`, `src/pages/api/webhooks/resend.ts`, `src/pages/api/portal/operator/settings/customer-yaml-update.ts` (4, plus the definition in `src/lib/observability/sentry.ts`); catch-bearing files in `src/`: 104 (down from 156, mostly from the 30-dead-file cleanup shrinking the denominator, not new adoption — none of the four call sites changed in the window per `git log`) |
| Full CI/CD | **Improved** | see finding 1 below — `substrate` is now a required check |
| Branch protection | Partial, unchanged | `required_approving_review_count: 0` (legacy and ruleset), `enforce_admins: false`, force-push and deletion still blocked, ruleset `bypass_actors: []` |
| Uptime monitoring | Partial, unchanged | Push-based via healthchecks.io; no poller; #2384 still closed by backlog reset |
| API docs | Missing, unchanged | see Documentation finding 2 |
| PWA | Missing (expected), unchanged | — |
| Secrets scanning | Partial, unchanged | `.gitleaks.toml` present (1430 bytes, unchanged), scanner runs `--no-git` |

1. **[CLOSED] `operator-substrate` is now a required status check.** Third review running this finding as open; this cycle it closes. Instrument: `gh api repos/venturecrane/ss-console/rulesets/15555219 --jq '.rules[] | select(.type=="required_status_checks") | .parameters.required_status_checks[].context'` → `Security Summary`, `substrate`. Job-name resolution verified: `operator-substrate.yml` job id `substrate` has no explicit `name:` override (`jobs.substrate:` at line 44, no `name:` key), so its check-run name defaults to `substrate`, matching the ruleset entry exactly — not a coincidental string match. Verified on a real merged PR: `gh pr checks 2735` shows `substrate` (2m42s, pass) alongside `Security Summary` (pass), `Typecheck, Lint, Format, Test` (pass, still the legacy branch-protection context), `Secret Detection`, `Static Analysis (Semgrep)`, `npm audit (high+)`, `probes`, `reconcile`, `reconcile-r2`, and the runtime-AC and TODO-deferral gates — all passing and all gating the merge. Landed by commit `6d3b8097` ("the workflow reports on every PR so it can be a required check") plus the ruleset update. Falsifier: `substrate` absent from the ruleset's required-checks list, or a merged PR without it in `gh pr checks`; neither occurred. This closes the third-review-running HIGH finding; combined required checks are now legacy protection's `Typecheck, Lint, Format, Test` plus the ruleset's `Security Summary` and `substrate` — the TypeScript-only gap the last three reviews flagged no longer exists.
2. **[MEDIUM] Zero required reviews and admin bypass permitted, still open.** Legacy `required_pull_request_reviews.required_approving_review_count: 0`; ruleset `pull_request` rule also `required_approving_review_count: 0` with `required_reviewers: []`; legacy `enforce_admins: false`; ruleset `bypass_actors: []` (so at least no bypass list beyond admin exemption from `enforce_admins`). CLAUDE.md's own rule ("all changes through PRs") is met mechanically but not backstopped by a human-review requirement.
3. **[LOW] Sentry adoption ratio improved on paper but not by new instrumentation.** Numerator held at effectively 4 production call sites (was 3; `resend.ts` was not touched in-window per `git log`, so this may be a prior-review undercounting rather than new adoption — orchestrator should re-derive independently); denominator dropped from 156 to 104 catch-bearing files purely from dead-code deletion. Coverage percentage looks better; underlying adoption did not visibly move this cycle.
4. **[LOW]** API-docs gap, counted once in Documentation, unchanged.

**Dimension summary:** The single concrete, multi-review-running HIGH — required checks covering only the TypeScript half of the pipeline — is now closed and verified against a live merged PR, not just the ruleset config. The remaining Partials (review requirement, uptime polling, API docs) are all unchanged from last review.

**Grade: B-.** Rubric: with the CI-coverage HIGH closed, the picture is now "1-2 non-critical items missing" (zero-required-reviews plus the polling/API-docs pair, which were already counted together last time) rather than "3+ missing" — moves the dimension out of the C band the last three reviews held it in. Not A/B+: `required_approving_review_count: 0` on a solo-operator repo is a deliberate but real gap against the Tier 2 rubric's own language, and API docs remain fully absent.

---

## Instruments used (for orchestrator re-probing)

- `npm audit --json` (root + each `workers/*` dir)
- `.github/audit-allowlist.json` read
- `git show 4c328341` (full commit body + package.json diff)
- `npm outdated --json`, `python3 -c "json.load(open('package-lock.json'))..."` for lockfile-vs-installed cross-check
- `npx knip --production --dependencies` and `npx knip --dependencies` (both, to isolate the flag-scope false positive)
- `npm ls ajv --all` / `--omit=dev`; `grep -rn "new Function(" dist/server/chunks/*.mjs` then read the hit in context
- `grep -rn "uv pip install\|pip install" operator/templates/Dockerfile`; `find operator -maxdepth 3 -name pyproject.toml -o -name "requirements*.txt" -o -name uv.lock`; `cat .github/dependabot.yml`
- `git ls-files workers | cut -d/ -f2 | sort -u`
- `grep -n "ADR range\|Operator ADRs\|active decisions" CLAUDE.md docs/adr/decision-stack.md docs/adr/index.md`; `ls docs/adr/*.md`
- `gh issue view 2385`, `gh issue list --search "API documentation" --state open`
- `git ls-files src/pages/api | wc -l`; `ls docs/api`
- `grep -rn "booking-cleanup" src/ docs/`; `git log --follow -- src/lib/operator/capabilities/conformance.ts`; `git show 3d0ba79b --stat`
- `sed -n` reads of `knip.jsonc` rules block before/context
- `git log -1 -- docs/handbook/data-model.md`; `ls migrations/`; `npm run handbook:drift`
- `gh api repos/venturecrane/ss-console/branches/main/protection`; `gh api repos/venturecrane/ss-console/rulesets`; `gh api repos/venturecrane/ss-console/rulesets/15555219`
- `gh pr checks 2735`; `gh pr list --state merged --limit 3`
- `grep -rl "captureError(" src/`; `grep -rl "catch (" src/ | wc -l`
- `git log 0a3722a3..1da55373 -- <path>` for every "touched in window?" check above
