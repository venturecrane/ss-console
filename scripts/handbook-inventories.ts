#!/usr/bin/env tsx
/**
 * The handbook's inventories: regenerate the scheduled-jobs block in
 * docs/handbook/scheduled-jobs.md, and report anything a listing page omits.
 *
 * Usage:
 *   npm run handbook:inventories            # report; exit 1 when stale or incomplete
 *   npm run handbook:inventories -- --write # rewrite the scheduled-jobs block in place
 *
 * Runbooks, workers and tables are not regenerated (the pages group them by
 * hand); this names what is missing so the author can add it. The same checks
 * run in tests/handbook-integrity.test.ts.
 */

import { readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import {
  committedJobsBlock,
  inventoryOmissions,
  renderScheduledJobs,
  scheduledJobs,
} from './lib/handbook-inventories'

const root = resolve('.')
const target = resolve('docs/handbook/scheduled-jobs.md')
const page = readFileSync(target, 'utf8')
const rendered = renderScheduledJobs(scheduledJobs(root))
const current = committedJobsBlock(page)

if (process.argv.includes('--write')) {
  if (current === null) throw new Error('scheduled-jobs.md has no generated-block markers')
  writeFileSync(
    target,
    page.replace(current, () => rendered)
  )
  console.log(`wrote the scheduled-jobs block in ${target}`)
} else if (current === rendered) {
  console.log('scheduled-jobs.md is current')
} else {
  console.log('scheduled-jobs.md is STALE; run with -- --write')
  process.exitCode = 1
}

for (const o of inventoryOmissions(root)) {
  if (o.missing.length === 0) continue
  console.log(`${o.page} does not name ${o.missing.length} ${o.what}(s): ${o.missing.join(', ')}`)
  process.exitCode = 1
}
