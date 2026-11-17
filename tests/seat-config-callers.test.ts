/**
 * The seat-config caller table (operator/contracts/seat-config-callers.json)
 * restates two of the sprawl census's file rules in Python, so the capture can
 * run in the bare Python CI job. This holds the two in step: every file the
 * census counts as a customer.yaml hand parser or a broker socket client has
 * its sites executed in the table, and the table counts no file the census
 * does not, except one whose every site now calls the one shared reader
 * (wave 2, B1: seat_config / broker_socket, stamped by gen-skill-shared). Such
 * a file is no longer a hand parser, so the census drops it, and the table
 * keeps it so its recorded behaviour is still held. The readers' own sources
 * are counted by the census (each is the "1 reader per process") and are not
 * sites. The behaviour golden itself is operator/bin/tests/
 * test_seat_config_callers.py.
 *
 * The table's Python rule is the census's first, wider one ("a yaml load, and
 * customer.yaml named anywhere"), so it also runs files that load another YAML
 * document and only mention customer.yaml. The census now counts a file only
 * when the load is applied to customer.yaml (scripts/sprawl/
 * customer-yaml-reads.ts), and declines those. Each one it declines is named in
 * DECLINED with what it really loads, and the list must equal the declined set
 * exactly: a new file the census stops counting fails here until someone reads
 * it and says what it loads, and an entry that is counted again, or leaves the
 * table, fails as stale.
 */
import { readFileSync } from 'node:fs'
import { expect, it } from 'vitest'
import { parse as parseYaml } from 'yaml'
import { conceptCensus } from '../scripts/sprawl/concepts'
import { readSources, trackedFiles } from '../scripts/sprawl/sources'

type Row = { file: string; calls: string[] }
type Table = { concepts: Record<string, { census: string; files: string[]; rows: Row[] }> }

const table = JSON.parse(
  readFileSync('operator/contracts/seat-config-callers.json', 'utf8')
) as Table

/** The shared readers: their module names, and each one's source file. */
const READERS = ['seat_config', 'broker_socket']
const manifest = parseYaml(
  readFileSync('operator/contracts/skill-shared-modules.yaml', 'utf8')
) as {
  modules: Record<string, { source?: string }>
}
const readerSources = new Set(READERS.map((m) => manifest.modules[m]?.source).filter(Boolean))

/** Files the table runs and the census declines: not customer.yaml parsers. What each loads. */
const DECLINED: Record<string, string> = {
  'operator/bin/lib/boot_harness/cli.py': "each seat's seat.yaml (seat_apps)",
  'operator/bin/lib/boot_harness/collect.py':
    'profile config.yaml files, inside a program string it runs on the harness Machine; customer.yaml is scanned line by line, never parsed (on_harness)',
  'operator/bin/lib/seat_purpose.py':
    'fleet/seats.yaml (load) and the seat twin.yaml (declared_twin_of)',
  'operator/bin/voice-fetch-corpus.py': 'the corpus manifest (load_manifest)',
  'operator/hermes-smd-overlay/bootstrap/role_persona.py': "the role package's role.yaml",
  'operator/hermes-smd-overlay/bootstrap/webhook_toolsets.py':
    'a profile config.yaml; customer.yaml goes through seat_config.load',
  'operator/hermes-smd-overlay/plugins/hermes-smd-initiation/seat_facts.py':
    "a SKILL.md's front matter",
  'operator/hermes-smd-overlay/shared/policy_profile.py':
    "the role package's role.yaml; customer.yaml goes through shared.customer_config",
  'operator/hermes-smd-overlay/tests_e2e/_seat.py':
    'an end-to-end test harness (the template customer.yaml and a profile config.yaml); the census counts no test',
  'operator/runners/jobs/jobs/medchron/config.py': 'the medchron firm config',
  'operator/templates/ensure-curator-disabled.py': 'profile config.yaml files',
  'operator/templates/ensure-telegram-allowlist.py': 'profile config.yaml files',
  'operator/workspace_broker/casefile_scheduled.py': "the broker's casefile-firm.yaml",
}

/** A call in the table (`<file>:<line> <expr>`) that goes through a shared reader. */
const throughReader = (call: string): boolean =>
  READERS.some((m) => call.slice(call.indexOf(' ') + 1).startsWith(`${m}.`))

it('names both shared readers and their sources', () => {
  expect(readerSources.size).toBe(READERS.length)
})

// The census reads the whole tree; on a loaded machine that alone passes the 5 s default.
it('covers exactly the files the census counts, apart from files moved onto the shared readers', () => {
  const census = Object.fromEntries(
    conceptCensus(readSources(trackedFiles())).map((r) => [r.id, r.instances])
  )
  for (const concept of ['customer-yaml', 'broker-socket']) {
    const body = table.concepts[concept]
    expect(body.census in census, `${body.census} is a census concept`).toBe(true)
    const counted = new Set<string>(census[body.census])
    const inTable = new Set(body.files)
    // Every hand parser the census counts is a file whose sites the table runs.
    expect(
      [...counted].filter((f) => !inTable.has(f) && !readerSources.has(f)),
      `${concept}: counted by the census, absent from the table`
    ).toEqual([])
    // A file the census no longer counts reaches the seat through the shared reader at every
    // site: a yaml site parses only through it; a socket site opens its socket through it (a
    // client that classifies its own connect failure still calls connect() on that socket).
    const notCounted = body.files.filter((f) => !counted.has(f))
    if (concept === 'customer-yaml') {
      // Declined files: exactly the named ones, each a table file the census does not count.
      const declined = notCounted.filter((f) => f in DECLINED)
      expect(
        Object.keys(DECLINED).filter((f) => !declined.includes(f)),
        `${concept}: DECLINED names a file the census counts or the table does not run`
      ).toEqual([])
    }
    const moved = notCounted.filter((f) => concept !== 'customer-yaml' || !(f in DECLINED))
    for (const file of moved) {
      const rows = body.rows.filter((r) => r.file === file)
      expect(rows.length, `${concept}: ${file} has no site`).toBeGreaterThan(0)
      for (const row of rows) {
        expect(row.calls.some(throughReader), `${concept}: ${file} left the census by hand`).toBe(
          true
        )
        if (concept === 'customer-yaml') {
          expect(
            row.calls.filter((c) => !throughReader(c)),
            `${concept}: ${file} left the census but still parses by hand`
          ).toEqual([])
        }
      }
    }
  }
}, 60_000)
