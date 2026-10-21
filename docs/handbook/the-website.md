---
title: The Website
section: engineering
order: 13
owner: engineering
review_every: 90
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
---

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

The rewrite runs **before** auth enforcement, and the operator-rename redirect (see below) runs before the rewrite, because a rewrite terminates the chain.

## Auth enforcement per host

Auth is unified on Clerk (2026-05-25). After the rewrite, `enforceAuth` gates by path:

- **Admin** (`/admin`, `/api/admin`): requires a Clerk `userId` and a local users row with `role='admin'`, resolved by `resolveAdminSessionFromClerk` into `locals.session`. A signed-in Clerk user with no admin row is treated as forbidden (403 on API, redirect to `/portal` on a page).
- **Portal** (`/portal`, `/api/portal`): Clerk is the primary path. Legacy magic-link sessions (set by `/auth/verify`) are accepted as a fallback so in-flight client invitation emails keep working during the Clerk transition.

`/admin/playbook/secrets-access` covers the identity and credential side in depth.

## Per-host cookie boundaries

The legacy magic-link session cookie (`session_token`) carries no `Domain` attribute, so it is host-only: the browser returns it only to the host that set it (`buildSessionCookie` in `src/lib/auth/session.ts`). Clerk's own session cookies are set and scoped by Clerk, not by this code. No code clears a stale cookie on the apex: the only `Set-Cookie` writers in `src/` are the magic-link session (`src/pages/auth/verify.astro`), the anonymous marketing-events session (`src/pages/api/events.ts`), and the change-request flash cookie (`src/lib/portal/operator/change-request-flash.ts`). A second layer sits behind the cookies: state-changing requests on the signed-in surfaces are refused when they come cross-site (`src/lib/security/cross-site.ts`).

## Backwards-compat 301s

The middleware keeps old URLs working with permanent (301) redirects, so bookmarks and indexed links do not break. The rules live as a declarative table in `src/lib/routing/legacy-redirects.ts` (each rule is a predicate plus a target builder; the middleware evaluates the list in order and issues the first match), split into pre-rewrite and post-rewrite lists because the subdomain rewrite terminates the chain:

- **Admin and portal host moves:** on `smd.services`, any `/admin` or `/admin/*` path 301s to `admin.smd.services` (the `admin-host-canonicalize` rule), and any `/portal` or `/portal/*` path 301s to `portal.smd.services` (`portal-host-canonicalize`).
- **Legacy auth paths:** `/auth/login`, `/auth/portal-sign-in`, `/auth/portal-sign-up`, `/auth/portal-login` all 301 to the unified `/auth/sign-in` or `/auth/sign-up` (the `legacy-auth-paths` rule).
- **Operator rename (ADR 0034):** the product was renamed "AI Employee" to "Operator." Every `/ai-employee` path - marketing, portal product surface, admin - 301s to the `/operator` equivalent. The redirect SOURCES are the legacy `/ai-employee` paths; renaming them would self-redirect into a loop.
- **Retired lead-magnet surfaces:** `/scan`, `/scorecard`, `/outside-view`, and bare `/get-started` 301 to home. These were the Outside View surfaces, retired 2026-05-04 (PR #702/#703); they are gone, not merely hidden.

## The base-URL environment variables

Outbound links (magic links, invitation and follow-up emails, invoice notifications, OAuth redirect URIs, signature webhook callbacks) must be built from environment configuration, never from the inbound request's host. Trusting the request host is unsafe: if the edge does not tightly canonicalize Host/Origin, a spoofed Host header can poison a generated link. The helpers live in `src/lib/config/app-url.ts`:

- **`APP_BASE_URL`** - the canonical marketing origin (for example `https://smd.services`). Used for marketing links and SignWell webhook callbacks. `requireAppBaseUrl` throws if it is unset.
- **`PORTAL_BASE_URL`** - the portal origin. **Falls back to `APP_BASE_URL`** when unset, since the portal is served by the same Worker under a subdomain rewrite.
- **`ADMIN_BASE_URL`** - the admin origin (for example `https://admin.smd.services`). Used for the OAuth redirect URI and outbound admin links. It does **not** fall back to `APP_BASE_URL` - a silent fallback would emit OAuth redirect URIs on the marketing domain, producing `redirect_uri_mismatch` errors that are hard to diagnose. `requireAdminBaseUrl` throws when it is missing.

> TODO(why): the doc comments in `src/lib/config/app-url.ts` still say "the same Pages project" and reference Cloudflare Pages, but the venture migrated off Pages to Cloudflare Workers in April 2026 (per `CLAUDE.md`). The behavior is unchanged (same Worker, subdomain rewrite), but the comment is stale. Flagged here rather than edited, since this handbook page is documentation-only. Checked: src/lib/config/app-url.ts lines 12, 46, and the `ADMIN_BASE_URL` error message at line 131, which still says "Cloudflare Pages env"; CLAUDE.md §Three-Subdomain Architecture.

## Local development

Subdomain routing keys off `hostname.startsWith('admin.')` / `portal.`. At `localhost:4321` neither fires, which is usually fine - hit `/admin/*` and `/portal/*` paths directly. For full-fidelity subdomain testing, map `admin.localhost` and `portal.localhost` to `127.0.0.1` in `/etc/hosts` and set matching `ADMIN_BASE_URL` / `PORTAL_BASE_URL` in `.dev.vars`, so the rewrite fires and outbound-URL builders emit the right origin. See `/admin/playbook/architecture-map` for the full stack and build commands.
