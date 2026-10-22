/**
 * Regenerate every file derived from the seat inventory
 * (operator/customers/<slug>/seat.yaml; scripts/lib/seat-inventory.ts).
 *
 *   npm run gen:seat-registries            write the files
 *   npm run gen:seat-registries -- --check exit 1 and name each stale file
 *
 * tests/seat-inventory.test.ts runs the --check comparison in CI.
 */

import { readFileSync, writeFileSync, existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { loadSeatInventory } from './lib/seat-inventory'
import { renderSeatRegistries } from './lib/seat-registries'

const repoRoot = resolve(import.meta.dirname, '..')
const check = process.argv.includes('--check')

const seats = loadSeatInventory(resolve(repoRoot, 'operator/customers'))
const files = await renderSeatRegistries(repoRoot, seats)

let stale = 0
for (const file of files) {
  const abs = resolve(repoRoot, file.path)
  const current = existsSync(abs) ? readFileSync(abs, 'utf8') : null
  if (current === file.text) {
    console.log(`current  ${file.path}`)
    continue
  }
  if (check) {
    console.error(`STALE    ${file.path}`)
    stale++
  } else {
    writeFileSync(abs, file.text)
    console.log(`wrote    ${file.path}`)
  }
}
if (stale) {
  console.error(`${stale} generated file(s) stale. Run \`npm run gen:seat-registries\`.`)
  process.exit(1)
}
