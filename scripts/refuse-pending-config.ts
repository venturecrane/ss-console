#!/usr/bin/env tsx
/**
 * Refuse to provision a seat that is authored but not ready.
 *
 * Usage: npx tsx scripts/refuse-pending-config.ts --slug <slug> --config <customer.yaml>
 *
 * Exit 0 when the seat may be provisioned; exit 1, naming every reason, when
 * its seat.yaml authors `fly_app: null` or its customer.yaml still holds a
 * PENDING marker (scripts/lib/pending-config.ts lists them). Run by
 * operator/bin/lib/stage-seat-spec.sh before the customer.yaml reaches R2, so a
 * placeholder mailbox or app id can never become a seat's boot input.
 */

import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parseArgs } from 'node:util'

import { provisioningRefusals } from './lib/pending-config'

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const { values } = parseArgs({
  options: { slug: { type: 'string' }, config: { type: 'string' } },
})
const slug = String(values.slug ?? '')
if (!/^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$/.test(slug) || !values.config) {
  process.stderr.write('usage: refuse-pending-config.ts --slug <slug> --config <customer.yaml>\n')
  process.exit(2)
}
const refusals = provisioningRefusals(REPO, slug, readFileSync(values.config, 'utf8'))
for (const line of refusals) process.stderr.write(`${slug}: ${line}\n`)
process.exit(refusals.length ? 1 : 0)
