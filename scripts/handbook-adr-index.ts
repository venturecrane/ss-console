#!/usr/bin/env tsx
/**
 * Regenerate docs/handbook/adr-index.md from docs/adr/.
 *
 * Usage:
 *   npm run handbook:adr-index            # print whether the page is current
 *   npm run handbook:adr-index -- --write # rewrite the page
 *
 * The renderer lives in scripts/lib/handbook-adr-index.ts so
 * tests/handbook-integrity.test.ts runs the same code and fails the merge when
 * the committed page and the ADR corpus disagree.
 */

import { readFileSync, writeFileSync, existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { buildAdrRows, renderAdrIndex } from './lib/handbook-adr-index'

const target = resolve('docs/handbook/adr-index.md')
const rendered = renderAdrIndex(buildAdrRows(resolve('docs/adr'))) + '\n'

if (process.argv.includes('--write')) {
  writeFileSync(target, rendered)
  console.log(`wrote ${target}`)
} else {
  const current = existsSync(target) ? readFileSync(target, 'utf8') : ''
  if (current === rendered) {
    console.log('docs/handbook/adr-index.md is current')
  } else {
    console.log('docs/handbook/adr-index.md is STALE; run npm run handbook:adr-index -- --write')
    process.exitCode = 1
  }
}
