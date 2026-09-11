#!/usr/bin/env node
/**
 * Apply a staging seed to the STAGING D1 database, and only ever to that one.
 *
 * Usage:
 *   npm run db:seed:staging            # portal access fixture (entity + users)
 *
 * Safety, in layers:
 *
 * 1. The database is addressed by LITERAL NAME, pinned as a constant below and
 *    never taken from argv or env. This is the safe form: wrangler resolves an
 *    unrecognised literal name through the account API, so it reaches staging
 *    with or without `--env`. The DANGEROUS form is addressing by BINDING —
 *    `wrangler d1 execute DB --remote` resolves `DB` through the top-level
 *    wrangler.toml and lands on PRODUCTION. This script never names a binding.
 *    (Note this is the opposite of `wrangler deploy`, where `--env staging` is
 *    the broken form. The two subcommands do not share resolution rules.)
 *
 * 2. The pinned uuid is checked against `wrangler d1 info` before anything is
 *    executed, so a rename in the Cloudflare account fails the run rather than
 *    silently redirecting it.
 *
 * 3. Seeds live in scripts/seeds/, never migrations/ — deploy.yml applies
 *    migrations/ to PRODUCTION automatically on every push to main.
 */

import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')

// Pinned. Not configurable — see safety layer 1.
const DB_NAME = 'ss-console-db-staging'
const DB_UUID = 'e14b1ad9-9f61-4435-bea9-e094af46a19f'

const SEEDS = {
  1: 'scripts/seeds/staging-portal-access.sql',
}

function wrangler(args, opts = {}) {
  return execFileSync('npx', ['wrangler', ...args], {
    cwd: REPO_ROOT,
    encoding: 'utf-8',
    stdio: opts.inherit ? 'inherit' : ['pipe', 'pipe', 'pipe'],
  })
}

function assertPinnedDatabase() {
  let info
  try {
    info = JSON.parse(wrangler(['d1', 'info', DB_NAME, '--json']))
  } catch (err) {
    console.error(`Could not read D1 database "${DB_NAME}".`)
    console.error(String(err.stderr ?? err.message ?? err))
    process.exit(2)
  }
  const uuid = info?.uuid ?? info?.uid ?? info?.database_id
  if (uuid !== DB_UUID) {
    console.error(
      `Refusing to seed: "${DB_NAME}" resolved to ${uuid}, expected ${DB_UUID}.\n` +
        'The staging database may have been recreated or renamed. Verify before proceeding.'
    )
    process.exit(1)
  }
  console.log(`Target: ${DB_NAME} (${uuid})`)
}

function main() {
  const tierArg = process.argv.find((a) => a.startsWith('--tier='))
  const tier = tierArg ? Number(tierArg.split('=')[1]) : 1
  const seed = SEEDS[tier]
  if (!seed) {
    console.error(`Unknown tier ${tier}. Known: ${Object.keys(SEEDS).join(', ')}`)
    process.exit(1)
  }
  const seedPath = join(REPO_ROOT, seed)
  if (!existsSync(seedPath)) {
    console.error(`Seed file not found: ${seed}`)
    process.exit(2)
  }

  assertPinnedDatabase()
  console.log(`Applying ${seed} …`)

  // `-y` is required: `d1 execute --remote --file` prompts for confirmation on
  // a TTY, which would hang an unattended run.
  wrangler(['d1', 'execute', DB_NAME, '--remote', '-y', `--file=${seedPath}`], { inherit: true })

  console.log('\nSeed applied. Verify with:')
  console.log(
    `  npx wrangler d1 execute ${DB_NAME} --remote --json --command \\\n` +
      `    "SELECT email, role, entity_id, clerk_user_id FROM users WHERE id LIKE '01JSTAGING%'"`
  )
}

main()
