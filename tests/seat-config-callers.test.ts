/**
 * The seat-config caller table (operator/contracts/seat-config-callers.json)
 * restates two of the sprawl census's file rules in Python, so the capture can
 * run in the bare Python CI job. This holds the two equal: every file the
 * census counts as a customer.yaml hand parser or a broker socket client has
 * its sites executed in the table, and the table counts no file the census
 * does not. The behaviour golden itself is operator/bin/tests/
 * test_seat_config_callers.py.
 */
import { readFileSync } from 'node:fs'
import { expect, it } from 'vitest'
import { conceptCensus } from '../scripts/sprawl/concepts'
import { readSources, trackedFiles } from '../scripts/sprawl/sources'

type Table = { concepts: Record<string, { census: string; files: string[] }> }

const table = JSON.parse(
  readFileSync('operator/contracts/seat-config-callers.json', 'utf8')
) as Table

it('covers exactly the files the census counts, for each census-backed concept', () => {
  const census = Object.fromEntries(
    conceptCensus(readSources(trackedFiles())).map((r) => [r.id, r.instances])
  )
  for (const concept of ['customer-yaml', 'broker-socket']) {
    const body = table.concepts[concept]
    expect(body.census in census, `${body.census} is a census concept`).toBe(true)
    expect(body.files, concept).toEqual(census[body.census])
  }
})
