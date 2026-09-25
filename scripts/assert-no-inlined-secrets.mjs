#!/usr/bin/env node
/**
 * assert-no-inlined-secrets.mjs -- the Worker bundle carries no secret.
 *
 * Astro statically inlines private env reads (Vite's `import.meta` env
 * accessor) in server code at
 * build time, and `@clerk/astro` reads its secret key that way. So a build run
 * in a shell that carries CLERK_SECRET_KEY bakes the key into
 * dist/server/chunks/*.mjs as `CLERK_SECRET_KEY: "<value>"` (review
 * 2026-09-25, Security 2). Production is unaffected (deploy.yml builds with no
 * Clerk env), but a laptop `npm run build && wrangler deploy` ships it in a
 * script body the Cloudflare dashboard displays, and a `dist/` shared for
 * debugging hands it over.
 *
 * `npm run build` now strips the Clerk secrets from its own environment, which
 * prevents the inlining; this script is the detector behind that prevention,
 * run as `postbuild` and as a verify.yml step. Two checks:
 *
 *   1. NAME. An inlined assignment to a known secret name (`NAME: "`), which is
 *      the shape Astro emits.
 *   2. VALUE. The value of any secret-named variable in THIS process's
 *      environment appearing anywhere in the bundle, whatever the shape.
 *
 * It never prints a value: a hit reports the file, the variable name and the
 * check that found it.
 *
 * Usage: node scripts/assert-no-inlined-secrets.mjs [dir]   (default dist/server)
 */

import { readdirSync, readFileSync, statSync, existsSync } from 'node:fs'
import { join, relative } from 'node:path'

/**
 * Private names @clerk/astro reads through Vite's env accessor
 * (node_modules/@clerk/astro/dist). The accessor is never spelled out in this
 * file: vitest rewrites the token into the whole process environment, see
 * tests/assert-no-inlined-secrets.test.ts.
 */
export const INLINED_SECRET_NAMES = ['CLERK_SECRET_KEY', 'CLERK_MACHINE_SECRET_KEY']

/** Environment names whose values are secrets. PUBLIC_* is public by Astro's own rule. */
const SECRET_NAME_RE = /(SECRET|TOKEN|PASSWORD|PRIVATE|_KEY)$/
/** Shorter values match by coincidence; every real key here is far longer. */
const MIN_VALUE_LENGTH = 20

const TEXT_FILE_RE = /\.(m?js|cjs|json|map|txt|html)$/

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) yield* walk(path)
    else if (TEXT_FILE_RE.test(name)) yield path
  }
}

/**
 * Public by construction even without the prefix: a Clerk publishable key is
 * meant for the browser and ships in the bundle under PUBLIC_CLERK_*.
 */
const PUBLIC_NAME_RE = /^PUBLIC_|PUBLISHABLE/

/** Secret-named variables in `env` long enough to test by value. */
export function secretValues(env) {
  return Object.entries(env)
    .filter(([name]) => !PUBLIC_NAME_RE.test(name) && SECRET_NAME_RE.test(name))
    .filter(([, value]) => typeof value === 'string' && value.length >= MIN_VALUE_LENGTH)
}

/**
 * Every inlined secret in `dir`, as { file, name, check }. Never the value.
 * @param {string} dir
 * @param {Record<string, string | undefined>} env
 */
export function findInlinedSecrets(dir, env = process.env) {
  const namePatterns = INLINED_SECRET_NAMES.map((name) => ({
    name,
    re: new RegExp(`\\b${name}["']?\\s*:\\s*["'\`]`),
  }))
  const values = secretValues(env)
  const hits = []
  for (const file of walk(dir)) {
    const text = readFileSync(file, 'utf8')
    const rel = relative(process.cwd(), file)
    for (const { name, re } of namePatterns) {
      if (re.test(text)) hits.push({ file: rel, name, check: 'name' })
    }
    for (const [name, value] of values) {
      if (text.includes(value)) hits.push({ file: rel, name, check: 'value' })
    }
  }
  return hits
}

const invokedDirectly = process.argv[1]?.endsWith('assert-no-inlined-secrets.mjs')
if (invokedDirectly) {
  const dir = process.argv[2] || 'dist/server'
  if (!existsSync(dir)) {
    console.error(`assert-no-inlined-secrets: ${dir} does not exist; run the build first.`)
    process.exitCode = 1
  } else {
    const hits = findInlinedSecrets(dir)
    if (hits.length > 0) {
      console.error(`assert-no-inlined-secrets: the build inlined ${hits.length} secret(s):`)
      for (const h of hits) console.error(`  ${h.file}: ${h.name} (found by ${h.check})`)
      console.error(
        '  Rebuild with those variables unset (npm run build does this for the Clerk keys),'
      )
      console.error('  and do not deploy or share this dist/.')
      process.exitCode = 1
    } else {
      console.log(`assert-no-inlined-secrets: ${dir} carries no inlined secret.`)
    }
  }
}
