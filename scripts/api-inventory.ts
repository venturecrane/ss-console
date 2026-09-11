#!/usr/bin/env tsx
/**
 * Regenerate docs/api/README.md from src/pages/api.
 *
 * Usage:
 *   npx tsx scripts/api-inventory.ts          # print whether the page is current
 *   npx tsx scripts/api-inventory.ts --write  # rewrite the page
 *
 * The generator lives in scripts/lib/api-inventory.ts so
 * tests/api-inventory.test.ts can run the same code and fail the merge when the
 * committed page and the tree disagree.
 */

import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { buildInventory, renderInventory } from './lib/api-inventory'

const repoRoot = resolve(process.cwd())
const apiRoot = resolve(repoRoot, 'src/pages/api')
const target = resolve(repoRoot, 'docs/api/README.md')

const rendered = renderInventory(buildInventory(apiRoot, repoRoot)) + '\n'

if (process.argv.includes('--write')) {
  mkdirSync(dirname(target), { recursive: true })
  writeFileSync(target, rendered)
  console.log(`wrote ${target}`)
} else {
  const current = existsSync(target) ? readFileSync(target, 'utf8') : ''
  if (current === rendered) {
    console.log('docs/api/README.md is current')
  } else {
    console.log('docs/api/README.md is STALE; run with --write')
    process.exitCode = 1
  }
}
