/**
 * Retired migrations stay retired (#2091).
 *
 * "Gone means gone" (CLAUDE.md): a removal is complete only when the artifact
 * is absent from every layer it lived in — and the repo layer is the one a CI
 * guard can hold, which is exactly why it needs one. Runtime layers cannot be
 * guarded here and were probed instead; the probes and their verify IDs are
 * recorded in `operator/migrations/README.md`.
 *
 * The failure this prevents is not someone re-adding the file on purpose. It is
 * a future agent reading a doc that still describes the table, concluding it
 * exists, and designing against it — which is how `0010` came to name a runtime
 * consumer (`adapter/voice/corrections.py::select_active`) that was never
 * written.
 *
 * Pure filesystem reads: no git subprocess, so the `GIT_*` strip in
 * `tests/provision-source-guard.test.ts` does not apply.
 */

import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const repoRoot = fileURLToPath(new URL('..', import.meta.url))

function read(relative: string): string {
  return readFileSync(new URL(relative, import.meta.url), 'utf8')
}

describe('0010_voice_corrections is retired', () => {
  it('the migration is absent from operator/migrations/', () => {
    const files = readdirSync(new URL('../operator/migrations', import.meta.url))
    expect(files).not.toContain('0010_voice_corrections.sql')
    expect(files.filter((f) => f.startsWith('0010'))).toEqual([])
  })

  it('its test is gone too — a test for a deleted table passes vacuously', () => {
    const files = readdirSync(new URL('../operator/tests', import.meta.url))
    expect(files).not.toContain('test_migration_0010_voice_corrections.py')
  })

  it('the retirement and its negative probes are written down', () => {
    // The store decision has to be findable by the next person, or it will be
    // re-derived — probably differently.
    const readme = read('../operator/migrations/README.md')
    expect(readme).toContain('Retired: 0010_voice_corrections')
    expect(readme).toContain('Negative probes')
    expect(readme).toMatch(/vfy_[A-Z0-9]{26}/)
  })

  it('the replacement store exists and says why it is where it is', () => {
    const migration = read('../migrations/0102_operator_voice_corrections.sql')
    expect(migration).toContain('CREATE TABLE IF NOT EXISTS operator_voice_corrections')
    expect(migration).toContain('Retires operator/migrations/0010_voice_corrections.sql')
  })

  it('no source file still describes the table as a live runtime store', () => {
    // Prose that says "corrections live in voice_corrections" is the exact
    // artifact that misled the last reader. Historical references — changelog
    // entries and dated ADR notes recording that it WAS removed — are fine and
    // are what the allowance below covers.
    const offenders: string[] = []
    for (const relative of [
      'src/lib/operator/customer-yaml/types.ts',
      'src/lib/operator/customer-yaml/sections-relationship.ts',
      'operator/contracts/customer-yaml-blocks.yaml',
      'operator/customers/smd-staging/customer.yaml',
    ]) {
      const lines = readFileSync(new URL(relative, `file://${repoRoot}`), 'utf8').split('\n')
      lines.forEach((line, i) => {
        if (!line.includes('voice_corrections')) return
        // The console table keeps the name under its `operator_` prefix; the
        // bare name is the retired per-customer one.
        if (line.includes('operator_voice_corrections')) return
        // A mention that says it was retired is the correction, not the defect.
        // The disclaimer often wraps onto the next line of a comment, so the
        // window is the sentence, not the line.
        const window = lines.slice(Math.max(0, i - 1), i + 3).join(' ')
        if (window.includes('retired') || window.includes('#2091')) return
        offenders.push(`${relative}: ${line.trim()}`)
      })
    }
    expect(offenders).toEqual([])
  })
})
