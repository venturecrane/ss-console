import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

describe('scaffold', () => {
  it('astro config exists and references smd.services', () => {
    const config = readFileSync(resolve('astro.config.mjs'), 'utf-8')
    expect(config).toContain('smd.services')
  })

  it('global CSS imports tailwindcss', () => {
    const css = readFileSync(resolve('src/styles/global.css'), 'utf-8')
    expect(css).toContain('tailwindcss')
  })
})

describe('cloudflare SSR scaffolding', () => {
  it('astro config enables SSR with server output', () => {
    const config = readFileSync(resolve('astro.config.mjs'), 'utf-8')
    expect(config).toContain("output: 'server'")
  })

  it('astro config uses cloudflare adapter', () => {
    const config = readFileSync(resolve('astro.config.mjs'), 'utf-8')
    expect(config).toContain('@astrojs/cloudflare')
    expect(config).toContain('adapter: cloudflare')
  })

  it('astro config enables platformProxy for local dev', () => {
    const config = readFileSync(resolve('astro.config.mjs'), 'utf-8')
    expect(config).toContain('platformProxy')
    expect(config).toContain('enabled: true')
  })

  it('wrangler.toml exists with D1, R2, and KV bindings', () => {
    const wrangler = readFileSync(resolve('wrangler.toml'), 'utf-8')
    expect(wrangler).toContain('d1_databases')
    expect(wrangler).toContain('binding = "DB"')
    expect(wrangler).toContain('r2_buckets')
    expect(wrangler).toContain('binding = "STORAGE"')
    expect(wrangler).toContain('kv_namespaces')
    expect(wrangler).toContain('binding = "SESSIONS"')
  })

  it('env.d.ts declares Cloudflare binding types', () => {
    const envDts = readFileSync(resolve('src/env.d.ts'), 'utf-8')
    expect(envDts).toContain('D1Database')
    expect(envDts).toContain('R2Bucket')
    expect(envDts).toContain('KVNamespace')
  })

  it('tsconfig includes Cloudflare workers types', () => {
    const tsconfig = readFileSync(resolve('tsconfig.json'), 'utf-8')
    expect(tsconfig).toContain('@cloudflare/workers-types')
  })

  it('migrations directory exists', () => {
    expect(existsSync(resolve('migrations'))).toBe(true)
  })

  it('health check API route exists', () => {
    expect(existsSync(resolve('src/pages/api/health.ts'))).toBe(true)
  })

  it('static pages are marked for prerendering', () => {
    const notFound = readFileSync(resolve('src/pages/404.astro'), 'utf-8')
    expect(notFound).toContain('export const prerender = true')
  })

  it('book page is SSR (needs runtime env for Turnstile key)', () => {
    const book = readFileSync(resolve('src/pages/book.astro'), 'utf-8')
    expect(book).toContain('export const prerender = false')
  })

  it('index page is SSR for portal subdomain routing', () => {
    const index = readFileSync(resolve('src/pages/index.astro'), 'utf-8')
    expect(index).toContain('export const prerender = false')
  })

  it('deploy workflow targets ss-web project', () => {
    const deploy = readFileSync(resolve('.github/workflows/deploy.yml'), 'utf-8')
    expect(deploy).toContain('--project-name=ss-web')
  })

  it('deploy workflow includes D1 migration step', () => {
    const deploy = readFileSync(resolve('.github/workflows/deploy.yml'), 'utf-8')
    expect(deploy).toContain('d1 migrations apply')
  })
})
