#!/usr/bin/env tsx
/**
 * Regenerate the seat roster block in docs/handbook/clients-and-commitments.md.
 *
 * Usage:
 *   npm run handbook:roster            # print whether the block is current
 *   npm run handbook:roster -- --write # rewrite the block in place
 *
 * Only the text between the BEGIN/END GENERATED markers is rewritten; the rest
 * of the page is authored. The renderer lives in scripts/lib/handbook-seat-roster.ts
 * so tests/handbook-integrity.test.ts runs the same code.
 */

import { readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import {
  buildSeatRoster,
  committedBlock,
  renderSeatRoster,
  withBlock,
} from './lib/handbook-seat-roster'

const target = resolve('docs/handbook/clients-and-commitments.md')
const page = readFileSync(target, 'utf8')
const rendered = renderSeatRoster(buildSeatRoster(resolve('operator/customers')))

if (process.argv.includes('--write')) {
  writeFileSync(target, withBlock(page, rendered))
  console.log(`wrote the seat roster block in ${target}`)
} else if (committedBlock(page) === rendered) {
  console.log('clients-and-commitments.md seat roster is current')
} else {
  console.log('clients-and-commitments.md seat roster is STALE; run with -- --write')
  process.exitCode = 1
}
