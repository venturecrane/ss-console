---
title: The Website
section: engineering
order: 13
owner: engineering
review_every: 90
last_reviewed: "2026-10-21"
summary: One Astro app, one Cloudflare Worker, three custom domains - routed entirely by src/middleware.ts rather than separate deployments
sources:
  - label: src/middleware.ts - subdomain routing, cookie boundaries, legacy 301s
    href: https://github.com/venturecrane/ss-console/blob/main/src/middleware.ts
  - label: src/lib/routing/legacy-redirects.ts - the declarative 301 rule table
    href: https://github.com/venturecrane/ss-console/blob/main/src/lib/routing/legacy-redirects.ts
  - label: src/lib/config/app-url.ts - the *_BASE_URL env vars
    href: https://github.com/venturecrane/ss-console/blob/main/src/lib/config/app-url.ts
  - label: CLAUDE.md - Three-Subdomain Architecture
    href: https://github.com/venturecrane/ss-console/blob/main/CLAUDE.md
  - label: wrangler.toml - the one Worker, its custom domains and [vars]
    href: https://github.com/venturecrane/ss-console/blob/main/wrangler.toml
---

This page explains how one deployment serves the marketing site, the admin console and the client portal, which old addresses still redirect, and which settings decide the links in outbound email. An administrator needs it when an agent's report mentions a redirect, a cookie, or a base-URL setting, or when a link in an email points at the wrong site.

## One app, one Worker, three domains

The website is a single Astro SSR application deployed as a single Cloudflare Worker (`ss-web`). It serves three custom domains. Routing between them is **not** three deployments - it is one Worker with host-aware logic in `src/middleware.ts`.

| Host | Serves | Auth role |
| --- | --- | --- |
| `smd.services` | Marketing pages (public) | none |
| `admin.smd.services` | Admin console, rewritten to `/admin/*` | `admin` |
| `portal.smd.services` | Client portal, rewritten to `/portal/*` | `client` |

The admin and portal source files live under `src/pages/admin/*` and `src/pages/portal/*`. The subdomain is a front door; the middleware rewrites the request path so the host decides which tree answers. For the surfaces themselves, see `/admin/playbook/admin-console` and `/admin/playbook/client-portal`. For where the website sits in the larger system, see `/admin/playbook/architecture-map`.

## How the rewrite works

`src/middleware.ts` inspects `context.url.hostname` in `handleSubdomainRewrite`:

- On a `portal.` host, a path that does **not** already start with `/portal`, `/api/portal`, `/auth`, or `/api/auth` gets `/portal` prepended (`/` becomes `/portal`). The request is then `context.rewrite`-d to the new path.
- On an `admin.` host, the same pattern prepends `/admin`, with the exclusions `/admin`, `/api/admin`, `/auth`, and `/api/auth`.

The rewrite is internal - the URL in the browser does not change. The middleware pipeline is composed as `sequence(clerkMiddleware(), ssMiddleware)`: Clerk parses the session and populates `locals.auth()` first, then the SS-owned middleware does the subdomain rewrites, legacy redirects, the admin session shim, and auth enforcement.

The rewrite runs **before** auth enforcement, and the operator-rename redirect (see below) runs before the rewrite, because a rewrite terminates the chain. After auth, a state-changing request that arrives cross-site is refused (`src/lib/security/cross-site.ts`), and every response, including early redirects and denials, leaves with the security headers from `src/lib/security/response-headers.ts`.

## Auth enforcement per host

Auth is unified on Clerk (2026-05-25). After the rewrite, `enforceAuth` gates by path:

- **Admin** (`/admin`, `/api/admin`): requires a Clerk `userId` and a local users row with `role='admin'`, resolved by `resolveAdminSessionFromClerk` into `locals.session`. A signed-in Clerk user with no admin row is treated as forbidden (403 on API, redirect to `/portal` on a page).
- **Portal** (`/portal`, `/api/portal`): Clerk is the primary path. Legacy magic-link sessions (set by `/auth/verify`) are accepted as a fallback so in-flight client invitation emails keep working during the Clerk transition.

`/admin/playbook/secrets-access` covers the identity and credential side in depth.

## Per-host cookie boundaries

The legacy magic-link session cookie (`session_token`) carries no `Domain` attribute, so it is host-only: the browser returns it only to the host that set it (`buildSessionCookie` in `src/lib/auth/session.ts`). Clerk's own session cookies are set and scoped by Clerk, not by this code. No code clears a stale cookie on the apex: the only cookie writers in `src/` are the magic-link session (`src/pages/auth/verify.astro`), the anonymous marketing-events session (`src/pages/api/events.ts`), the change-request flash cookie (`src/lib/portal/operator/change-request-flash.ts`, set by `src/pages/api/portal/operator/change-request.ts`), and the first-touch ad-attribution cookie the middleware sets on the marketing host only when a landing URL carries `utm_*`, `gclid` or `fbclid` (`captureAdAttribution` in `src/middleware.ts`, ADR 0066). A second layer sits behind the cookies: state-changing requests on the signed-in surfaces are refused when they come cross-site (`src/lib/security/cross-site.ts`).

## Backwards-compat 301s

The middleware keeps old URLs working with permanent (301) redirects, so bookmarks and indexed links do not break. The rules live as a declarative table in `src/lib/routing/legacy-redirects.ts` (each rule is a predicate plus a target builder; the middleware evaluates the list in order and issues the first match), split into pre-rewrite and post-rewrite lists because the subdomain rewrite terminates the chain:

- **Admin and portal host moves:** on `smd.services`, any `/admin` or `/admin/*` path 301s to `admin.smd.services` (the `admin-host-canonicalize` rule), and any `/portal` or `/portal/*` path 301s to `portal.smd.services` (`portal-host-canonicalize`).
- **Legacy auth paths:** `/auth/login`, `/auth/portal-sign-in`, `/auth/portal-sign-up`, `/auth/portal-login` all 301 to the unified `/auth/sign-in` or `/auth/sign-up` (the `legacy-auth-paths` rule).
- **Operator rename (ADR 0034):** the product was renamed "AI Employee" to "Operator." Every `/ai-employee` path - marketing, portal product surface, admin - 301s to the `/operator` equivalent. The redirect SOURCES are the legacy `/ai-employee` paths; renaming them would self-redirect into a loop.
- **Retired marketing surfaces:** `/scan`, `/scorecard`, `/outside-view`, `/consulting`, `/ai` (and anything under the last four prefixes), and bare `/get-started` 301 to home; `/why` 301s to `/operator#compare`. `/scan`, `/scorecard` and `/outside-view` were the Outside View surfaces, retired 2026-05-04 (PR #702/#703); the rest went in the marketing consolidation of 2026-06-29 or earlier lead-magnet retirements (the comment on the rule table). They are gone, not merely hidden. `/get-started?booked=1` stays live as the post-booking questionnaire, and the old `/book/thanks` 301s to it.
- **Portal reorganization:** the old top-level portal routes `/portal/quotes`, `/portal/invoices` and `/portal/documents` 301 into `/portal/engagement/proposals`, `/portal/billing/invoices` and `/portal/engagement/documents` (the `portal-ia-redirects` rule).

## The base-URL environment variables

Outbound links (magic links, invitation and follow-up emails, invoice notifications, OAuth redirect URIs, signature webhook callbacks) must be built from environment configuration, never from the inbound request's host. Trusting the request host is unsafe: if the edge does not tightly canonicalize Host/Origin, a spoofed Host header can poison a generated link. The helpers live in `src/lib/config/app-url.ts`:

- **`APP_BASE_URL`** - the canonical marketing origin (for example `https://smd.services`). Used for marketing links and SignWell webhook callbacks. `requireAppBaseUrl` throws if it is unset.
- **`PORTAL_BASE_URL`** - the portal origin. **Falls back to `APP_BASE_URL`** when unset, since the portal is served by the same Worker under a subdomain rewrite.
- **`ADMIN_BASE_URL`** - the admin origin (for example `https://admin.smd.services`). Used for the OAuth redirect URI and outbound admin links. It does **not** fall back to `APP_BASE_URL` - a silent fallback would emit OAuth redirect URIs on the marketing domain, producing `redirect_uri_mismatch` errors that are hard to diagnose. `requireAdminBaseUrl` throws when it is missing.

A note for anyone reading that file: its doc comments (lines 12 and 46) still say "the same Pages project", and the `ADMIN_BASE_URL` error message (line 131) still says "Cloudflare Pages env". The site moved from Cloudflare Pages to the `ss-web` Worker (see the `wrangler.toml` header); the behavior those comments describe is unchanged, only the product name in them is out of date. On the Worker, these values are the `[vars]` block in `wrangler.toml`.

## Local development

Subdomain routing keys off `hostname.startsWith('admin.')` / `portal.`. At `localhost:4321` neither fires, which is usually fine - hit `/admin/*` and `/portal/*` paths directly. For full-fidelity subdomain testing, map `admin.localhost` and `portal.localhost` to `127.0.0.1` in `/etc/hosts` and set matching `ADMIN_BASE_URL` / `PORTAL_BASE_URL` in `.dev.vars`, so the rewrite fires and outbound-URL builders emit the right origin. See `/admin/playbook/architecture-map` for the full stack and build commands.
