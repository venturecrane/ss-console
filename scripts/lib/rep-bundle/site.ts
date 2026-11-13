/**
 * The public website's words, read from the site itself for the rep training
 * bundle (scripts/build-rep-bundle.ts).
 *
 * How: the site is built with `astro build` into a temporary directory, served
 * by `astro preview` (the Cloudflare adapter runs the built Worker in workerd,
 * the same runtime production uses), and each public page is fetched as a
 * visitor would and reduced to its visible text by htmlToText. The page list is
 * the built sitemap: every sitemap page must belong to one bundle file or to
 * the named exclusions below, so a new public page cannot be silently left out.
 *
 * Clerk's middleware refuses to render any page without a publishable key and
 * a secret key, so this throwaway build and its preview get placeholders: a
 * publishable key naming a host under the reserved .test TLD, and a secret key
 * that is only the right shape. No page request carries a session, so Clerk
 * never calls out. No real key is read or needed: the shell's own CLERK_*
 * values are removed from both processes, and the build directory is deleted
 * when the pages are read.
 */
import { spawn, type ChildProcess } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:net'
import { join } from 'node:path'
import { htmlToText } from './html-text'

export interface SitePage {
  path: string
  title: string
  text: string
}

export interface SiteText {
  main: SitePage[]
  industry: SitePage[]
  trust: SitePage[]
}

const MAIN_PAGES = [
  '/',
  '/operator',
  '/case-studies/personal-injury-law-firm',
  '/industries',
  '/about',
  '/book',
  '/contact',
]
const TRUST_PAGES = ['/security', '/trust', '/ai-disclosure', '/agent']
/** Legal and post-purchase pages: in the sitemap, not sales material. */
const EXCLUDED_PAGES = new Set(['/privacy', '/terms', '/agent/terms', '/agent/thanks'])
/** Element ids whose content never enters the bundle (the self-serve page's prices). */
const SKIP_IDS: Record<string, string[]> = { '/agent': ['pricing'] }

/** The placeholder publishable key: `pk_live_` + base64 of a host under the reserved .test TLD. */
const PLACEHOLDER_CLERK_KEY = `pk_live_${btoa('clerk.example.test$')}`
/** A placeholder in the secret key's shape, for this throwaway local build only. */
const PLACEHOLDER_CLERK_SECRET = ['sk', 'live', 'placeholder-for-local-preview'].join('_')

function childEnv(): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {}
  for (const [k, v] of Object.entries(process.env)) {
    if (k.startsWith('VITEST') || k.startsWith('CLERK_') || k.startsWith('GIT_')) continue
    env[k] = v
  }
  env.NODE_ENV = 'production'
  env.PUBLIC_CLERK_PUBLISHABLE_KEY = PLACEHOLDER_CLERK_KEY
  env.CLERK_SECRET_KEY = PLACEHOLDER_CLERK_SECRET
  return env
}

function run(cmd: string, args: string[], cwd: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, { cwd, env: childEnv(), stdio: ['ignore', 'pipe', 'pipe'] })
    let log = ''
    child.stdout.on('data', (d: Buffer) => (log += d.toString()))
    child.stderr.on('data', (d: Buffer) => (log += d.toString()))
    child.on('error', reject)
    child.on('close', (code) =>
      code === 0
        ? resolve()
        : reject(new Error(`${cmd} ${args.join(' ')} exited ${code}\n${log.slice(-4000)}`))
    )
  })
}

function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = createServer()
    srv.on('error', reject)
    srv.listen(0, '127.0.0.1', () => {
      const addr = srv.address()
      const port = typeof addr === 'object' && addr ? addr.port : 0
      srv.close(() => resolve(port))
    })
  })
}

interface Preview {
  child: ChildProcess
  detachedPid?: number
  base: string
}

/**
 * Start `astro preview`. In a non-interactive shell the CLI may move itself to
 * the background and print its pid; both shapes are handled and both stopped.
 */
async function startPreview(root: string, outDir: string): Promise<Preview> {
  const port = await freePort()
  const args = ['astro', 'preview', '--port', String(port), '--outDir', outDir, '--ignore-lock']
  const child = spawn('npx', args, {
    cwd: root,
    env: childEnv(),
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  const preview: Preview = { child, base: `http://localhost:${port}` }
  let log = ''
  const onData = (d: Buffer) => {
    log += d.toString()
    const m = /\(pid (\d+)\)/.exec(log)
    if (m) preview.detachedPid = Number(m[1])
  }
  child.stdout?.on('data', onData)
  child.stderr?.on('data', onData)
  const deadline = Date.now() + 90_000
  let lastStatus = 'no answer'
  for (;;) {
    try {
      const r = await fetch(`${preview.base}/`, { redirect: 'manual' })
      if (r.status === 200) return preview
      lastStatus = `status ${r.status}`
    } catch {
      // not listening yet
    }
    if (Date.now() > deadline) {
      stopPreview(preview)
      throw new Error(
        `astro preview did not serve ${preview.base}/ within 90 s (${lastStatus})\n${log.slice(-3000)}`
      )
    }
    await new Promise((r) => setTimeout(r, 500))
  }
}

function stopPreview(p: Preview): void {
  p.child.kill('SIGTERM')
  if (p.detachedPid) {
    try {
      process.kill(p.detachedPid, 'SIGTERM')
    } catch {
      // already gone
    }
  }
}

function sitemapPaths(outDir: string): string[] {
  const xml = readFileSync(join(outDir, 'client', 'sitemap-0.xml'), 'utf8')
  const paths = [...xml.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => {
    const p = new URL(m[1]).pathname.replace(/\/$/, '')
    return p === '' ? '/' : p
  })
  if (paths.length === 0) throw new Error('the built sitemap lists no pages')
  return paths
}

function groupPaths(paths: string[]): { main: string[]; industry: string[]; trust: string[] } {
  const industry = paths.filter((p) => p.startsWith('/packs/')).sort()
  const known = new Set([...MAIN_PAGES, ...TRUST_PAGES, ...industry, ...EXCLUDED_PAGES])
  const stray = paths.filter((p) => !known.has(p))
  if (stray.length) {
    throw new Error(`public pages with no place in the rep bundle: ${stray.join(', ')}`)
  }
  const missing = [...MAIN_PAGES, ...TRUST_PAGES].filter((p) => !paths.includes(p))
  if (missing.length)
    throw new Error(`bundle pages missing from the sitemap: ${missing.join(', ')}`)
  if (industry.length === 0) throw new Error('the sitemap lists no industry pages')
  return { main: MAIN_PAGES, industry, trust: TRUST_PAGES }
}

function pageTitle(html: string, path: string): string {
  if (path === '/') return 'Home'
  const m = /<title>([^<]*)<\/title>/i.exec(html)
  const parts = htmlToText(m ? m[1] : path)
    .split(/\s+[|\u00b7]\s+/)
    .map((t) => t.trim())
    .filter((t) => t && t !== 'SMD Services')
  return parts[0] ?? path
}

async function fetchPage(base: string, path: string): Promise<SitePage> {
  const r = await fetch(`${base}${path}`, { redirect: 'manual' })
  if (r.status !== 200) throw new Error(`GET ${path} returned ${r.status}`)
  const html = await r.text()
  return {
    path,
    title: pageTitle(html, path),
    text: htmlToText(html, { scope: 'main', skipIds: SKIP_IDS[path] }),
  }
}

/**
 * `astro build` rewrites .wrangler/deploy/config.json to point at its outDir,
 * and `wrangler deploy` reads that pointer. Put back whatever was there.
 */
function keepDeployPointer(root: string): () => void {
  const path = join(root, '.wrangler', 'deploy', 'config.json')
  const before = existsSync(path) ? readFileSync(path, 'utf8') : undefined
  return () => {
    if (before === undefined) rmSync(path, { force: true })
    else writeFileSync(path, before)
  }
}

/**
 * Build the site, serve it, and read every bundle page. The build goes to a
 * fresh directory under the project's own (gitignored) dist/: workerd fails to
 * start a Worker built to a directory outside the project.
 */
export async function readSite(root: string): Promise<SiteText> {
  mkdirSync(join(root, 'dist'), { recursive: true })
  const outDir = mkdtempSync(join(root, 'dist', '.rep-bundle-site-'))
  const restorePointer = keepDeployPointer(root)
  try {
    await run('npx', ['astro', 'build', '--outDir', outDir], root)
    return await readBuilt(root, outDir)
  } finally {
    restorePointer()
    rmSync(outDir, { recursive: true, force: true })
  }
}

async function readBuilt(root: string, outDir: string): Promise<SiteText> {
  const groups = groupPaths(sitemapPaths(outDir))
  const preview = await startPreview(root, outDir)
  try {
    const read = async (paths: string[]) => {
      const pages: SitePage[] = []
      for (const p of paths) pages.push(await fetchPage(preview.base, p))
      return pages
    }
    return {
      main: await read(groups.main),
      industry: await read(groups.industry),
      trust: await read(groups.trust),
    }
  } finally {
    stopPreview(preview)
  }
}
