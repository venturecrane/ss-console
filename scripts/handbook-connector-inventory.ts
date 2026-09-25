#!/usr/bin/env tsx
/**
 * Regenerate the connector inventory block in docs/handbook/connectors-channels.md.
 *
 * Usage:
 *   npm run handbook:connectors            # print whether the block is current
 *   npm run handbook:connectors -- --write # rewrite the block in place
 *
 * Only the text between the BEGIN/END GENERATED markers is rewritten; the rest
 * of the page is authored. The renderer lives in
 * scripts/lib/handbook-connector-inventory.ts so tests/handbook-integrity.test.ts
 * runs the same code and fails the merge when the block and the tree disagree.
 */

import { readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import {
  buildConnectorInventory,
  committedBlock,
  renderConnectorInventory,
  withBlock,
} from './lib/handbook-connector-inventory'

const target = resolve('docs/handbook/connectors-channels.md')
const page = readFileSync(target, 'utf8')
const rendered = renderConnectorInventory(buildConnectorInventory(resolve('operator/connectors')))

if (process.argv.includes('--write')) {
  writeFileSync(target, withBlock(page, rendered))
  console.log(`wrote the connector inventory block in ${target}`)
} else if (committedBlock(page) === rendered) {
  console.log('connectors-channels.md connector inventory is current')
} else {
  console.log('connectors-channels.md connector inventory is STALE; run with -- --write')
  process.exitCode = 1
}
