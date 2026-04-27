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
    // Workers mode: `[assets]` block. Pages-specific
    // `pages_build_output_dir` must not be present.
    //
    // No `main` override: the @astrojs/cloudflare adapter's
    // `entrypoints/server` default is used. The previous custom
    // `main = "src/worker.ts"` re-exported `ScanDiagnosticWorkflow` for
    // an in-Worker [[workflows]] binding (#614), but production
    // observed `env.SCAN_WORKFLOW` undefined at runtime (#618) — the
    // Astro adapter's build artifact was unreliable as a Workflow host.
    // The Workflow now lives on its own Worker (`workers/scan-workflow/`)
    // and ss-web invokes it via the `SCAN_WORKFLOW_SERVICE` service
    // binding asserted below.
    expect(wrangler).not.toContain('main = "src/worker.ts"')
    expect(wrangler).toContain('[assets]')
    expect(wrangler).toContain('run_worker_first = true')
    expect(wrangler).not.toContain('pages_build_output_dir')
    expect(wrangler).toContain('d1_databases')
    expect(wrangler).toContain('binding = "DB"')
    expect(wrangler).toContain('r2_buckets')
    expect(wrangler).toContain('binding = "STORAGE"')
    expect(wrangler).toContain('kv_namespaces')
    expect(wrangler).toContain('binding = "SESSIONS"')
    // Service binding to the scan-workflow Worker (#618). The
    // [[workflows]] binding lives on `workers/scan-workflow/wrangler.toml`,
    // not here — co-locating the Workflow class with a vanilla Worker is
    // the durable workaround for the bug described in #618.
    expect(wrangler).toContain('[[services]]')
    expect(wrangler).toContain('binding = "SCAN_WORKFLOW_SERVICE"')
    expect(wrangler).toContain('service = "ss-scan-workflow"')
    expect(wrangler).not.toContain('[[workflows]]')
    expect(wrangler).not.toContain('binding = "SCAN_WORKFLOW"')
  })

  it('scan-workflow Worker is scaffolded (#618)', () => {
    const swPath = resolve('workers/scan-workflow')
    expect(existsSync(swPath)).toBe(true)
    expect(existsSync(resolve(swPath, 'wrangler.toml'))).toBe(true)
    expect(existsSync(resolve(swPath, 'package.json'))).toBe(true)
    expect(existsSync(resolve(swPath, 'src/index.ts'))).toBe(true)

    // The [[workflows]] binding lives on this Worker — not on ss-web.
    const swWrangler = readFileSync(resolve(swPath, 'wrangler.toml'), 'utf-8')
    expect(swWrangler).toContain('name = "ss-scan-workflow"')
    expect(swWrangler).toContain('[[workflows]]')
    expect(swWrangler).toContain('binding = "SCAN_WORKFLOW"')
    expect(swWrangler).toContain('class_name = "ScanDiagnosticWorkflow"')
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

  it('404 page must be SSR (never prerendered — middleware dependency)', () => {
    // If 404.astro is prerendered, Astro's error-page fallback serves the
    // static dist/client/404.html via the ASSETS binding and BYPASSES
    // middleware entirely. That breaks every subdomain rewrite that depends
    // on the fallback path (admin.smd.services/analytics, portal.smd.services/
    // quotes/abc, etc.). Keep 404 server-rendered so middleware always runs.
    // Same guard duplicated in tests/middleware.test.ts for discoverability.
    const notFound = readFileSync(resolve('src/pages/404.astro'), 'utf-8')
    expect(notFound).toContain('export const prerender = false')
    expect(notFound).not.toMatch(/export\s+const\s+prerender\s*=\s*true/)
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

  it('deploy workflow deploys ss-scan-workflow before ss-web (#618)', () => {
    const deploy = readFileSync(resolve('.github/workflows/deploy.yml'), 'utf-8')
    // ss-scan-workflow must deploy before the root `wrangler deploy` so
    // ss-web's service binding has its target on the first ever deploy.
    expect(deploy).toContain('workingDirectory: workers/scan-workflow')
    const scanIdx = deploy.indexOf('workingDirectory: workers/scan-workflow')
    // The root ss-web deploy is the one without `workingDirectory:` —
    // identifiable by its named step. Search past the job name (line 9)
    // by anchoring on the step header form `- name: Deploy to Cloudflare`.
    const rootDeployIdx = deploy.indexOf('- name: Deploy to Cloudflare Workers')
    expect(scanIdx).toBeGreaterThan(0)
    expect(rootDeployIdx).toBeGreaterThan(scanIdx)
  })

  it('deploy workflow includes D1 migration step', () => {
    const deploy = readFileSync(resolve('.github/workflows/deploy.yml'), 'utf-8')
    expect(deploy).toContain('d1 migrations apply')
  })
})
