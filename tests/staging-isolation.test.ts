import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync } from 'fs'
import { resolve } from 'path'

/**
 * Staging must never share a data resource with production.
 *
 * The staging environment exists so a change can be exercised before a client
 * sees it. A staging Worker pointed at production D1/R2/KV is worse than no
 * staging at all: it looks like a safety net while writing to live client data.
 *
 * Until 2026-07-31 `[env.staging]` declared only `name`, which meant it
 * inherited every top-level binding and would have run against production. It
 * had never been deployed, so the trap was latent. These tests make that
 * configuration impossible to reintroduce silently.
 *
 * Related trap, guarded below: `wrangler deploy --env staging` does NOT work in
 * this repo. The @astrojs/cloudflare adapter emits `dist/server/wrangler.json`
 * flattened from the TOP-LEVEL config, and `wrangler deploy` consumes that file
 * via `.wrangler/deploy/config.json` — so `--env staging` has no environment to
 * resolve and silently deploys PRODUCTION bindings under the staging name. The
 * environment must be selected at BUILD time with `CLOUDFLARE_ENV=staging`.
 */

const toml = readFileSync(resolve('wrangler.toml'), 'utf-8')

/** Everything before the `[env.staging]` header is the production config. */
function productionSection(): string {
  const idx = toml.indexOf('[env.staging]')
  expect(idx).toBeGreaterThan(-1)
  return toml.slice(0, idx)
}

function stagingSection(): string {
  const idx = toml.indexOf('[env.staging]')
  return toml.slice(idx)
}

/**
 * Collect `key = "value"` pairs for a given key across a config section.
 * Parses line by line against one hardcoded pattern — building a regex from the
 * `key` argument would be a dynamic-RegExp construction (ReDoS lint).
 */
const ASSIGNMENT = /^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"([^"]+)"\s*$/

function valuesOf(section: string, key: string): string[] {
  const found: string[] = []
  for (const line of section.split('\n')) {
    const m = ASSIGNMENT.exec(line)
    if (m && m[1] === key) found.push(m[2])
  }
  return found
}

describe('staging is isolated from production', () => {
  it('declares its own bindings rather than inheriting production', () => {
    const staging = stagingSection()
    // Wrangler does not inherit these into a named environment. Declaring only
    // `name` is exactly the latent trap this suite exists to prevent.
    expect(staging).toContain('[env.staging.vars]')
    expect(staging).toContain('[[env.staging.d1_databases]]')
    expect(staging).toContain('[[env.staging.r2_buckets]]')
    expect(staging).toContain('[[env.staging.kv_namespaces]]')
    expect(staging).toContain('[env.staging.assets]')
  })

  it('shares no D1 database with production', () => {
    const prodDbs = valuesOf(productionSection(), 'database_name')
    const stagingDbs = valuesOf(stagingSection(), 'database_name')
    expect(stagingDbs.length).toBeGreaterThan(0)
    for (const db of stagingDbs) expect(prodDbs).not.toContain(db)

    const prodIds = valuesOf(productionSection(), 'database_id')
    const stagingIds = valuesOf(stagingSection(), 'database_id')
    for (const id of stagingIds) expect(prodIds).not.toContain(id)
  })

  it('shares no R2 bucket with production', () => {
    const prodBuckets = valuesOf(productionSection(), 'bucket_name')
    const stagingBuckets = valuesOf(stagingSection(), 'bucket_name')
    expect(stagingBuckets.length).toBeGreaterThan(0)
    for (const b of stagingBuckets) expect(prodBuckets).not.toContain(b)
  })

  it('shares no KV namespace with production', () => {
    const prodKv = valuesOf(productionSection(), 'id')
    const stagingKv = valuesOf(stagingSection(), 'id')
    expect(stagingKv.length).toBeGreaterThan(0)
    for (const id of stagingKv) expect(prodKv).not.toContain(id)
  })

  it('every production binding has a staging twin', () => {
    // A binding added to production without its staging counterpart ships a
    // staging Worker that is missing that binding, and fails at runtime only.
    const prodBindings = valuesOf(productionSection(), 'binding').sort()
    const stagingBindings = valuesOf(stagingSection(), 'binding').sort()
    expect(stagingBindings).toEqual(prodBindings)
  })

  it('points its public origins away from the production hostnames', () => {
    const staging = stagingSection()
    for (const key of ['APP_BASE_URL', 'ADMIN_BASE_URL', 'PORTAL_BASE_URL']) {
      const [value] = valuesOf(staging, key)
      expect(value, `${key} must be declared for staging`).toBeTruthy()
      expect(value).not.toContain('//smd.services')
      expect(value).not.toContain('//admin.smd.services')
      expect(value).not.toContain('//portal.smd.services')
    }
  })
})

describe('staging deploy uses the build-time environment selector', () => {
  it('exposes npm scripts that set CLOUDFLARE_ENV at build time', () => {
    const pkg = JSON.parse(readFileSync(resolve('package.json'), 'utf-8')) as {
      scripts: Record<string, string>
    }
    expect(pkg.scripts['build:staging']).toContain('CLOUDFLARE_ENV=staging')
    // The deploy script must build with the selector, not pass `--env` to
    // wrangler, which would silently deploy production bindings.
    expect(pkg.scripts['deploy:staging']).toContain('build:staging')
    expect(pkg.scripts['deploy:staging']).not.toContain('--env staging')
  })

  it('records why --env staging is wrong, so the trap is not re-learned', () => {
    expect(toml).toContain('CLOUDFLARE_ENV=staging')
  })
})

/**
 * Staging seed fixtures must be unable to reach production.
 *
 * Two distinct escape routes exist, and they fail in opposite directions:
 *
 *   1. `migrations/`  — .github/workflows/deploy.yml runs
 *      `d1 migrations apply ss-console-db --remote` on every push to main with
 *      `migrations_dir` unset, so ANY .sql dropped there is auto-applied to
 *      PRODUCTION. Seeds therefore live in scripts/seeds/.
 *
 *   2. Addressing D1 by BINDING — `wrangler d1 execute DB --remote` resolves
 *      `DB` through the top-level wrangler.toml and lands on production.
 *      Addressing by literal NAME is the safe form: wrangler resolves an
 *      unrecognised name through the account API, so it reaches staging with or
 *      without `--env`. Note this is the exact inverse of `wrangler deploy`,
 *      where `--env staging` is the broken form — the two subcommands do not
 *      share resolution rules, which is why the guard targets the binding.
 */
describe('staging seeds cannot reach production', () => {
  const SEED_DIR = 'scripts/seeds'
  const STAGING_MARKERS = ['01JSTAGING', 'staging-fixture', 'staging-client@', 'staging-admin@']

  it('no migration carries a staging fixture marker', () => {
    const migrations = readdirSync(resolve('migrations')).filter((f) => f.endsWith('.sql'))
    expect(migrations.length).toBeGreaterThan(0)
    for (const file of migrations) {
      const sql = readFileSync(resolve('migrations', file), 'utf-8')
      for (const marker of STAGING_MARKERS) {
        expect(sql, `${file} must not contain the staging marker "${marker}"`).not.toContain(marker)
      }
    }
  })

  it('the seed runner never addresses D1 by binding', () => {
    const runner = readFileSync(resolve('scripts/seed-staging.mjs'), 'utf-8')
    // `d1 execute DB` / `'d1','execute','DB'` would resolve through the
    // top-level config straight to production.
    expect(runner).not.toMatch(/d1['"\s,]+execute['"\s,]+['"]DB['"]/)
    expect(runner).toContain('ss-console-db-staging')
  })

  it('the seed runner pins the staging database and cannot be retargeted', () => {
    const runner = readFileSync(resolve('scripts/seed-staging.mjs'), 'utf-8')
    // The name is a constant, and the uuid is checked before anything executes.
    expect(runner).toContain("DB_NAME = 'ss-console-db-staging'")
    expect(runner).toContain('DB_UUID')
    expect(runner).toContain('assertPinnedDatabase')
  })

  it('seed SQL never writes customer_configs (ADR 0012)', () => {
    // customer_configs rows are projected from a committed customer.yaml via
    // scripts/project-customer-config.ts. A hand-seeded row is the violation.
    for (const file of readdirSync(resolve(SEED_DIR)).filter((f) => f.endsWith('.sql'))) {
      const sql = readFileSync(resolve(SEED_DIR, file), 'utf-8').toLowerCase()
      expect(sql, `${file} must not insert into customer_configs`).not.toMatch(
        /insert\s+into\s+customer_configs/
      )
    }
  })

  it('seed SQL never writes clerk_user_id, so a re-seed cannot unlink an identity', () => {
    // ensureLocalUser claims a seeded row by case-insensitive email when
    // clerk_user_id IS NULL. Writing that column would break the auto-link and
    // could orphan a signed-in identity on re-seed.
    for (const file of readdirSync(resolve(SEED_DIR)).filter((f) => f.endsWith('.sql'))) {
      const sql = readFileSync(resolve(SEED_DIR, file), 'utf-8')
      const statements = sql.replace(/--[^\n]*/g, '')
      expect(statements, `${file} must not write clerk_user_id`).not.toContain('clerk_user_id')
    }
  })
})

describe('the Clerk fixture script targets only the development instance', () => {
  it('refuses any key that is not sk_test_', () => {
    const script = readFileSync(resolve('scripts/staging-clerk-users.mjs'), 'utf-8')
    expect(script).toContain("startsWith('sk_test_')")
    // The guard must precede any network call — it is the only safety layer.
    expect(script.indexOf("startsWith('sk_test_')")).toBeLessThan(script.indexOf('fetch('))
  })

  it('never generates a password, which would have to be printed to be usable', () => {
    const script = readFileSync(resolve('scripts/staging-clerk-users.mjs'), 'utf-8')
    expect(script).not.toContain('randomBytes')
    expect(script).toContain('STAGING_PORTAL_CLIENT_PASSWORD')
  })
})
