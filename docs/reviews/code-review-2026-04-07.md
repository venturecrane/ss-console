# Code Review: SMD Services

**Date:** 2026-04-07
**Reviewer:** Claude Code (automated)
**Scope:** Full codebase
**Mode:** Full (Phase 1 Claude-only)
**Models Used:** Claude
**Golden Path Tier:** 1 (default; not explicitly listed in compliance dashboard)

## Summary

**Overall Grade: D**

The codebase has strong test depth and generally solid TypeScript patterns, but there are two material risk areas: authorization scoping in one admin endpoint and dependency vulnerabilities with high-severity advisories present in the root package graph. The highest-priority fixes are tenant-safe query scoping and canonical URL hardening for magic-link generation.

## Scorecard

| Dimension     | Grade | Trend |
| ------------- | ----- | ----- |
| Architecture  | B     | new   |
| Security      | D     | new   |
| Code Quality  | B     | new   |
| Testing       | A     | new   |
| Dependencies  | D     | new   |
| Documentation | C     | new   |
| Golden Path   | C     | new   |

## Detailed Findings

### 1. Architecture

Findings:

1. [medium] `src/pages/admin/entities/[id].astro:1` - Entity detail page is 868 lines and mixes data loading, transformation, rendering, and client-side behavior in one file. Recommendation: split into loader helpers plus presentational components (timeline, dossier card, related-panels) with typed props.
2. [medium] `src/lib/pdf/sow-template.tsx:1` - SOW template file is 665 lines and contains style, layout, business-rule assumptions, and rendering logic in one unit. Recommendation: extract layout primitives and payment-section variants into smaller template modules.

Grade: B
Rationale: Structure is coherent overall (routes, `lib`, workers), but large view/template files increase maintenance cost and review complexity.

### 2. Security

Findings:

1. [high] `src/pages/api/admin/resend-invitation.ts:55` - User lookup for resend invitation is scoped only by `id` and `role`, not `session.orgId`; admin could target a client user outside their org if they know a UUID. Recommendation: require `org_id = ?` on lookup and all subsequent updates.
2. [high] `src/pages/api/admin/resend-invitation.ts:71` - Email update query updates by `id` only, without org constraint. Recommendation: update with `WHERE id = ? AND org_id = ?` and verify affected row count.
3. [medium] `src/pages/api/auth/magic-link.ts:54`, `src/pages/api/admin/resend-invitation.ts:82`, `src/pages/api/admin/follow-ups/[id].ts:97` - Link base URL derives from request host/protocol. This can enable poisoned links if host/origin is not canonicalized at edge. Recommendation: use a trusted env var (e.g., `APP_BASE_URL`) for all outbound links.
4. [medium] `src/pages/api/auth/login.ts:22`, `src/pages/api/auth/magic-link.ts:27` - No per-IP/account throttling on login or magic-link issuance. Recommendation: add KV-backed rate limiting and lockout/backoff policy.

Grade: D
Rationale: High-severity authz scoping issue in admin API drives a D under rubric.

### 3. Code Quality

Findings:

1. [low] `src/pages/api/auth/login.ts:71` - Catch-all error path drops error details and returns generic redirect; operational debugging depends on external logs only. Recommendation: log structured error context while keeping user-safe response.
2. [low] `src/pages/api/contact.ts:95`, `src/pages/api/booking/intake.ts:114`, `src/pages/api/ingest/signals.ts:131` - Repeated local helpers (`trimString`, `jsonResponse`) across endpoints. Recommendation: centralize request-validation helpers to reduce duplication drift.

Grade: B
Rationale: Strict TS baseline is strong (`tsconfig` extends strict), no `any` abuse found, and error handling is mostly consistent.

### 4. Testing

Findings:

1. [low] `tests` suite - Strong breadth (22 test files, 839 tests) but no explicit coverage threshold enforcement was found in repo config. Recommendation: add coverage reporting/thresholds in CI for regression guardrails.

Grade: A
Rationale: Comprehensive and fast suite covering auth, invoices, quotes, portal docs, analytics, and rendering paths.

### 5. Dependencies

Findings:

1. [high] `package.json:22` - Root dependency graph reports high vulnerabilities via `npm audit` (notably `vite`, `undici`, `defu`). Recommendation: execute controlled upgrades for Astro/Vite toolchain and refresh lockfile after compatibility validation.
2. [medium] `package.json:22-44` - Multiple key packages are behind latest majors (`astro`, `@astrojs/cloudflare`, `vitest`, `typescript`, `eslint`). Recommendation: schedule staged major upgrades with CI gates and changelog-driven migration plan.

Grade: D
Rationale: High-severity vulnerabilities in the active dependency graph produce D per rubric.

### 6. Documentation

Findings:

1. [medium] `README.md:1` - README is minimal and no longer represents the actual application surface (admin APIs, portal, workers, deployment/runtime setup). Recommendation: rewrite README with architecture, runbook, and endpoint index.
2. [medium] `README.md:9` - Quick-start commands reference `/sod` and `/eod`, while current session workflow uses `/sos` and `/eos`. Recommendation: align command docs with current agent command set.
3. [medium] `docs/` - No dedicated API reference directory found for a codebase with many HTTP routes. Recommendation: add `docs/api` with endpoint contracts and auth requirements.

Grade: C
Rationale: `CLAUDE.md` is strong, but public-facing operational docs are incomplete/outdated.

### 7. Golden Path Compliance

Findings:

1. [medium] `astro.config.mjs:19-22` - No PWA integration/plugin configured, and required manifest/icon assets were not found in `public/`. Recommendation: implement PWA baseline (manifest, icons, service worker shell caching) per Golden Path standard.
2. [low] `.github/workflows/verify.yml:1` - CI quality gates are present and strong for Tier 1; security scanning workflow is not present (acceptable for Tier 1 but needed before Tier 2). Recommendation: pre-stage security workflow to reduce lift at Tier progression.

Grade: C
Rationale: Core Tier 1 items are mostly met, but missing PWA baseline prevents higher compliance confidence.

## Model Convergence

Single-model review (Phase 1). Convergence analysis not applicable.

## Trend Analysis

No prior `code-review` scorecard was found in VCMS for venture `ss`, so this review is the baseline.

Previous issue resolution: 0/0 (no prior `source:code-review` issues found).

## File Manifest

- Total files: 237
- Total lines (approx): 59,473
- Languages/extensions: `.ts` (137), `.astro` (34), `.md` (29), `.json` (13), `.sql` (10), others
- Large codebase note: over 50K lines. Review prioritized high-risk paths (auth, admin APIs, webhooks, DB access, CI, and dependency graph).

## Raw Model Outputs

### Claude Review

Primary risk concentrations:

- Authorization scope defect in `resend-invitation` endpoint (`users` lookup/update missing org constraint)
- Outbound URL construction based on request host in email-link flows
- Dependency graph includes high-severity advisories

Strong signals:

- Strict TS config and clean parameterized DB query pattern in most data access code
- High-value test suite breadth with 839 passing tests

### Codex Review

Skipped (Phase 1 - Claude-only)

### Gemini Review

Skipped (Phase 1 - Claude-only)
