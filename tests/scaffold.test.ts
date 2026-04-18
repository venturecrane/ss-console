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

  it('astro config targets cloudflare workers (adapter v13)', () => {
    const config = readFileSync(resolve('astro.config.mjs'), 'utf-8')
    // v13 dropped platformProxy/routes.extend; the adapter is Workers-only
    // and the build output is driven by wrangler.toml + [assets] + main.
    expect(config).not.toContain('platformProxy')
    expect(config).not.toContain('routes.extend')
  })

  it('wrangler.toml exists with D1, R2, KV, and assets bindings (Workers mode)', () => {
    const wrangler = readFileSync(resolve('wrangler.toml'), 'utf-8')
    // Workers mode: `main` entry + `[assets]` block. Pages-specific
    // `pages_build_output_dir` must not be present.
    expect(wrangler).toContain('main = "@astrojs/cloudflare/entrypoints/server"')
    expect(wrangler).toContain('[assets]')
    expect(wrangler).toContain('run_worker_first = true')
    expect(wrangler).not.toContain('pages_build_output_dir')
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

  it('deploy workflow uses wrangler deploy (Workers, not Pages)', () => {
    const deploy = readFileSync(resolve('.github/workflows/deploy.yml'), 'utf-8')
    // Pages-era `pages deploy` / `--project-name=ss-web` should be gone.
    expect(deploy).not.toContain('pages deploy')
    expect(deploy).not.toContain('--project-name=ss-web')
    // Workers deploy + dry-run gate should be present.
    expect(deploy).toMatch(/command:\s*deploy\b/)
  })

  it('deploy workflow includes D1 migration step', () => {
    const deploy = readFileSync(resolve('.github/workflows/deploy.yml'), 'utf-8')
    expect(deploy).toContain('d1 migrations apply')
  })
})
