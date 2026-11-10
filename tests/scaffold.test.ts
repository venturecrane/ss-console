import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

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
