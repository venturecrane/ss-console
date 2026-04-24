# SMD Services

Operations consulting venture website + admin/portal infrastructure.

## What this repo is

SMD Services is a solutions consulting venture under SMDurgan, LLC — not a SaaS product. This repo is the operational hub: the marketing site (`smd.services`), admin console (`admin.smd.services`), and client portal (`portal.smd.services`) all live here as a single Astro app deployed to a single Cloudflare Worker (`ss-web`) with three custom domains. Routing between subdomains is handled by `src/middleware.ts`, not by separate deployments.

See `CLAUDE.md` for full context on the business model, enterprise rules, and architecture decisions.

## Stack

- Astro 6 SSR (with React 19 islands)
- Cloudflare Workers + Static Assets (single Worker `ss-web`)
- D1 (SQLite) for primary structured data
- R2 for object storage
- KV for sessions and rate-limit cache
- TypeScript 5, Vitest 3, ESLint 9 (flat config), Tailwind 4

## Three-subdomain architecture

One Astro app, one Cloudflare Worker, three custom domains — routing is handled by `src/middleware.ts` via hostname inspection.

| Host                  | Serves                                   | Auth role |
| --------------------- | ---------------------------------------- | --------- |
| `smd.services`        | Marketing pages                          | Public    |
| `admin.smd.services`  | Admin console (rewritten to `/admin/*`)  | `admin`   |
| `portal.smd.services` | Client portal (rewritten to `/portal/*`) | `client`  |

See `CLAUDE.md` for routing implementation details, cookie boundaries, and backwards-compat redirects.

## Setup

```bash
npm install
```

Copy `.dev.vars.example` to `.dev.vars` (gitignored). The public env vars are listed in `wrangler.toml`. Secret names and rotation instructions are in the secrets section of `CLAUDE.md` — do not enumerate secret names here.

```bash
npm run dev      # Local Astro dev server on localhost:4321
npm run preview  # Local Worker preview via wrangler (full runtime fidelity)
```

Subdomain routing only fires on `admin.localhost` / `portal.localhost`, not on `localhost`. For full-fidelity subdomain testing, add to `/etc/hosts`:

```
127.0.0.1 admin.localhost
127.0.0.1 portal.localhost
```

Then `http://admin.localhost:4321/` and `http://portal.localhost:4321/` exercise the rewrite. Set matching values in `.dev.vars` (e.g. `ADMIN_BASE_URL=http://admin.localhost:4321`).

## Build commands

```bash
npm run dev          # Astro dev server
npm run build        # Production build
npm run preview      # Local Worker preview via wrangler

npm run test         # Vitest test suite
npm run lint         # ESLint
npm run typecheck    # TypeScript validation (astro check)

npm run verify       # Full CI-equivalent run — typecheck, lint, build, test
npm run db:migrate:local  # Apply D1 migrations locally
```

See `package.json` for the full script list.

## Repo layout

```
ss-console/
├── src/              # Astro app (pages, components, lib, middleware)
│   ├── pages/        # Routes — marketing, /admin/*, /portal/*, /api/*
│   ├── lib/          # DAL, services, integrations
│   ├── components/   # Astro components
│   └── middleware.ts # Subdomain routing
├── workers/          # Standalone Cloudflare Workers (lead-gen pipelines)
├── migrations/       # D1 SQL migrations
├── tests/            # Vitest suites
├── docs/             # Internal documentation
├── public/           # Static assets
├── wrangler.toml     # Cloudflare Worker config
└── CLAUDE.md         # Agent context (full reference)
```

## Where to learn more

- `CLAUDE.md` — full agent context, business model, enterprise rules, architecture details
- `docs/` — internal documentation
- `docs/reviews/` — code review history
- `docs/adr/decision-stack.md` — locked decisions across business and engineering layers
