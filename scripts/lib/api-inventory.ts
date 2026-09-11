/**
 * API inventory: the route table for docs/api/README.md, derived from the tree.
 *
 * Every file under src/pages/api is a route in Astro's file-based router, so
 * the inventory is generated from the files themselves rather than written by
 * hand. `scripts/api-inventory.ts --write` regenerates the page;
 * tests/api-inventory.test.ts fails when the committed page and the tree
 * disagree, so a route cannot be added, moved, retired, or re-gated without
 * the documentation following in the same PR (code review 2026-09-10,
 * Documentation finding 2: 98 route files, no API documentation, and the
 * tracking issue closed without delivery).
 *
 * The auth gate column is read from the file: the helper vocabulary below is
 * the same set tests/admin-routes-require-session.test.ts and
 * tests/portal-routes-bind-tenant.test.ts enforce, so the inventory names the
 * gate the tests prove rather than a gate someone remembers.
 */

import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, sep } from 'node:path'

export interface RouteRow {
  area: string
  file: string
  path: string
  methods: string
  gate: string
  purpose: string
}

/** Display order for the top-level areas under src/pages/api. */
export const AREA_ORDER = [
  'admin',
  'portal',
  'internal',
  'webhooks',
  'booking',
  'assessment',
  'auth',
  'oauth',
  'checkout',
  'intake',
  'operator',
  'public',
] as const

const AREA_TITLES: Record<string, string> = {
  admin: 'Admin console (admin.smd.services)',
  portal: 'Client portal (portal.smd.services)',
  internal: 'Machine to control plane',
  webhooks: 'Inbound webhooks',
  booking: 'Booking',
  assessment: 'Assessment',
  auth: 'Auth',
  oauth: 'OAuth',
  checkout: 'Checkout',
  intake: 'Intake',
  operator: 'Operator MCP door',
  public: 'Public (top level)',
}

const HTTP_EXPORT = /^export\s+(?:const|async\s+function)\s+(GET|POST|PUT|PATCH|DELETE|ALL)\b/gm

/**
 * Ordered gate classification. The first matching rule wins, so a route that
 * carries both an admin session and a rate limiter reads as admin-gated.
 */
const GATE_RULES: ReadonlyArray<{ label: string; test: (src: string, path: string) => boolean }> = [
  {
    label: 'retired (410)',
    test: (s) => /\b410\b/.test(s) && !/\b(2\d\d)\b/.test(s.replace(/\b410\b/g, '')),
  },
  { label: 'admin session', test: (s) => /requireAdminSession\(/.test(s) },
  // Routes outside /admin and /api/admin, where the middleware never runs the
  // Clerk-to-admin shim, resolve the admin identity themselves through the
  // same shim (2026-09-10; /api/auth/google/connect was unreachable before).
  {
    label: 'admin session (resolved in-route via Clerk)',
    test: (s) => /resolveAdminSessionForRoute\(/.test(s),
  },
  { label: 'Machine bearer (per-seat credential)', test: (s) => /verifyMachineRequest\(/.test(s) },
  {
    label: 'portal client, tenant-bound',
    test: (s) =>
      /(getPortalClient|resolveOperatorAccess|resolveHostedAgentAccess|authorizeAdvancedSettings)\(/.test(
        s
      ),
  },
  {
    label: 'signed OAuth state',
    test: (s) => /validateStateOrReject\(|verifyOAuthState\(/.test(s),
  },
  { label: 'Stripe signature', test: (s) => /verifyStripeSignature\(/.test(s) },
  { label: 'Svix signature (Resend)', test: (s) => /verifySvixSignature\(/.test(s) },
  { label: 'SignWell event hash', test: (s) => /verifyEventHash\(/.test(s) },
  { label: 'HMAC signature', test: (s) => /verifyHmac\(/.test(s) },
  { label: 'MCP grant (inside handleMcpPost)', test: (s) => /handleMcpPost\(/.test(s) },
  {
    label: 'shared bearer, constant-time',
    test: (s) =>
      /headers\.get\(['"]authorization['"]\)/i.test(s) &&
      (/constantTimeEqual\(/.test(s) || /charCodeAt\(i\)\s*\^/.test(s)),
  },
  { label: 'single-use OAuth state nonce', test: (s) => /consumeOAuthState\(/.test(s) },
  {
    label: 'locals.session required',
    test: (s) => /locals\.session\b/.test(s) && /if \(!session\)/.test(s),
  },
  { label: 'manage token in the URL, hashed before lookup', test: (_s, p) => /:token\b/.test(p) },
  { label: 'public, IP rate-limited', test: (s) => /rateLimitByIp\(/.test(s) },
  { label: 'signed magic link', test: (s) => /consumeMagicLink\(|verifyMagicLink\(/.test(s) },
]

export function collectRouteFiles(apiRoot: string): string[] {
  const out: string[] = []
  const walk = (dir: string): void => {
    for (const entry of readdirSync(dir).sort()) {
      const full = join(dir, entry)
      if (statSync(full).isDirectory()) walk(full)
      else if (entry.endsWith('.ts') && !entry.endsWith('.test.ts')) out.push(full)
    }
  }
  walk(apiRoot)
  return out
}

export function routePathFor(apiRoot: string, file: string): string {
  const rel = relative(apiRoot, file).split(sep).join('/').replace(/\.ts$/, '')
  const segments = rel.split('/').filter((s) => s !== 'index')
  const mapped = segments.map((s) =>
    s.replace(/^\[\.\.\.(.+)\]$/, '*$1').replace(/^\[(.+)\]$/, ':$1')
  )
  return '/api' + (mapped.length ? '/' + mapped.join('/') : '')
}

export function areaFor(apiRoot: string, file: string): string {
  const rel = relative(apiRoot, file).split(sep)
  return rel.length > 1 ? rel[0] : 'public'
}

export function methodsFor(src: string): string {
  const found = new Set<string>()
  for (const m of src.matchAll(HTTP_EXPORT)) found.add(m[1])
  if (found.size === 0) return 'none (helper module, not a route)'
  const order = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'ALL']
  return order.filter((m) => found.has(m)).join(', ')
}

export function gateFor(src: string, path: string, methods: string): string {
  if (methods.startsWith('none')) return 'not a handler'
  for (const rule of GATE_RULES) if (rule.test(src, path)) return rule.label
  return 'public'
}

/** Plain text, no em dashes, no arrows, one line, capped. */
function sanitize(text: string): string {
  return text
    .replace(/\s+—\s+/g, ', ')
    .replace(/—/g, ', ')
    .replace(/\s+→\s+/g, ' to ')
    .replace(/→/g, ' to ')
    .replace(/\s+/g, ' ')
    .replace(/\|/g, '/')
    .trim()
}

/**
 * The first sentence of the file's leading JSDoc block, skipping the
 * `METHOD /api/...` title line many headers carry. A file with no JSDoc block
 * reads as undocumented, which is itself information.
 */
export function purposeFor(src: string): string {
  const block = src.match(/\/\*\*([\s\S]*?)\*\//)
  if (!block) return '(no header comment)'
  const lines = block[1]
    .split('\n')
    .map((l) => l.replace(/^\s*\*\s?/, '').trim())
    .filter((l) => l.length > 0)
    .filter((l) => !/^(GET|POST|PUT|PATCH|DELETE|ALL)\s+\/api\//.test(l))
    .filter((l) => !/^@/.test(l))
  if (lines.length === 0) return '(no header comment)'
  const joined = sanitize(lines.join(' '))
  const sentence = joined.match(/^(.{20,}?[.!?])(\s|$)/)?.[1] ?? joined
  return sentence.length > 160 ? sentence.slice(0, 157).trimEnd() + '...' : sentence
}

export function buildInventory(apiRoot: string, repoRoot: string): RouteRow[] {
  return collectRouteFiles(apiRoot).map((file) => {
    const src = readFileSync(file, 'utf8')
    const path = routePathFor(apiRoot, file)
    const methods = methodsFor(src)
    return {
      area: areaFor(apiRoot, file),
      file: relative(repoRoot, file).split(sep).join('/'),
      path,
      methods,
      gate: gateFor(src, path, methods),
      purpose: purposeFor(src),
    }
  })
}

export function renderInventory(rows: RouteRow[]): string {
  const byArea = new Map<string, RouteRow[]>()
  for (const row of rows) {
    const list = byArea.get(row.area) ?? []
    list.push(row)
    byArea.set(row.area, list)
  }
  const areas = [
    ...AREA_ORDER.filter((a) => byArea.has(a)),
    ...[...byArea.keys()].filter((a) => !(AREA_ORDER as readonly string[]).includes(a)).sort(),
  ]
  const out: string[] = []
  out.push('# API inventory')
  out.push('')
  out.push(
    '<!-- GENERATED by scripts/api-inventory.ts. Do not edit by hand: run `npx tsx scripts/api-inventory.ts --write` and commit the result. tests/api-inventory.test.ts fails when this page and src/pages/api disagree. -->'
  )
  out.push('')
  out.push(
    `Every file under \`src/pages/api\` is a route (Astro file-based routing). ${rows.length} files. The gate column is read from the file: it names the helper the route calls, which is the same vocabulary the route-enumerator tests enforce. Regenerate with \`npx tsx scripts/api-inventory.ts --write\`.`
  )
  out.push('')
  out.push(
    'Hosts: admin routes are served on admin.smd.services, portal routes on portal.smd.services, everything else on smd.services; `src/middleware.ts` rewrites the subdomains.'
  )
  out.push('')
  for (const area of areas) {
    const list = (byArea.get(area) ?? []).slice().sort((a, b) => a.path.localeCompare(b.path))
    out.push(`## ${AREA_TITLES[area] ?? area} (${list.length})`)
    out.push('')
    out.push('| Path | Methods | Gate | Purpose | File |')
    out.push('|---|---|---|---|---|')
    for (const r of list) {
      out.push(`| \`${r.path}\` | ${r.methods} | ${r.gate} | ${r.purpose} | \`${r.file}\` |`)
    }
    out.push('')
  }
  return out.join('\n')
}
