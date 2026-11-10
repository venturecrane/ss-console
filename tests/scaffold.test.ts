import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'
import { parse as parseToml } from 'smol-toml'

type Binding = { binding?: unknown }
type WranglerEnv = {
  assets?: { run_worker_first?: unknown }
  d1_databases?: Binding[]
  kv_namespaces?: Binding[]
}

const wrangler = parseToml(readFileSync(resolve('wrangler.toml'), 'utf-8')) as WranglerEnv & {
  env?: Record<string, WranglerEnv>
}

const bindings = (list: Binding[] | undefined) => (list ?? []).map((b) => b.binding)

describe('wrangler.toml: the Worker runs before static assets (parsed, production and staging)', () => {
  // run_worker_first is what puts the subdomain rewrite and the session
  // middleware in front of every asset request. A false or missing value fails
  // no build: admin.* and portal.* would serve static files with no auth.
  const envs: Array<[string, WranglerEnv | undefined]> = [
    ['production', wrangler],
    ['staging', wrangler.env?.staging],
  ]
  for (const [name, env] of envs) {
    it(`${name}: [assets] run_worker_first = true`, () => {
      expect(env?.assets?.run_worker_first).toBe(true)
    })

    it(`${name}: the SESSIONS KV and DB D1 bindings the middleware reads are declared`, () => {
      expect(bindings(env?.kv_namespaces)).toContain('SESSIONS')
      expect(bindings(env?.d1_databases)).toContain('DB')
    })
  }
})

describe('cloudflare SSR scaffolding', () => {
  it('migrations directory exists', () => {
    expect(existsSync(resolve('migrations'))).toBe(true)
  })

  it('health check API route exists', () => {
    expect(existsSync(resolve('src/pages/api/health.ts'))).toBe(true)
  })

  it('404 page must be SSR (never prerendered — middleware dependency)', () => {
    // If 404.astro is prerendered, Astro's error-page fallback serves the
    // static dist/client/404.html via the ASSETS binding and BYPASSES
    // middleware entirely. That breaks every subdomain rewrite that depends
    // on the fallback path (admin.smd.services/analytics, portal.smd.services/
    // quotes/abc, etc.). Keep 404 server-rendered so middleware always runs.
    const notFound = readFileSync(resolve('src/pages/404.astro'), 'utf-8')
    expect(notFound).toContain('export const prerender = false')
    expect(notFound).not.toMatch(/export\s+const\s+prerender\s*=\s*true/)
  })

  it('book page is SSR (needs runtime DB access for prefill-token entity lookup)', () => {
    const book = readFileSync(resolve('src/pages/book.astro'), 'utf-8')
    expect(book).toContain('export const prerender = false')
  })

  it('index page is SSR for portal subdomain routing', () => {
    const index = readFileSync(resolve('src/pages/index.astro'), 'utf-8')
    expect(index).toContain('export const prerender = false')
  })
})
