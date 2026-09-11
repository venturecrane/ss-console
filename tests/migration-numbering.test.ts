/**
 * Merge gate on migration filenames in migrations/.
 *
 * WHY. On 2026-08-21 two PRs merged within an hour of each other and both
 * claimed the number 0107: `0107_audit_ledger_observability.sql` (ss#2500) and
 * `0107_gateway_loop_observability.sql` (ss#2488). Each PR was green on its own
 * branch, because the number a migration claims is a property of the MERGED
 * directory and neither branch could see the other. Nothing in `npm run verify`
 * or in CI looked at the directory as a whole, so main took the collision
 * silently and Deploy stayed green.
 *
 * Writing this gate turned up FIVE MORE that nobody had ever noticed -- 0011,
 * 0013, 0027, 0028, 0029 -- so the count is six, not one, and the failure is a
 * standing property of parallel merges rather than one bad afternoon.
 *
 * All six pairs happen to be inert: disjoint objects, no ordering dependency
 * between the two halves of any pair. This is therefore a gate against the
 * seventh, not a repair of the six. The seventh will not be inert by default.
 *
 * WHY THEY ARE GRANDFATHERED RATHER THAN RENUMBERED. Wrangler records applied
 * migrations in the `d1_migrations` table keyed by FILENAME. Renaming a
 * migration that has already run makes it read as unapplied, and the next
 * `d1 migrations apply` re-runs it against prod. For an `ADD COLUMN` that is a
 * hard error; for anything with an INSERT it is silent duplication. Every one
 * of these twelve files has long since been applied. So the entries below are
 * exact filename pairs, not exemptions for the numbers: a third file claiming
 * 0107 -- or any of the other five -- fails this test like any other collision.
 *
 * THE FALSIFIER IS IN THE FILE. `findCollisions` is a pure function and the
 * third test runs it against a synthetic duplicate. If the detector were ever
 * gutted to return an empty map, that test goes red rather than this gate
 * quietly passing forever -- Law 12, a check that cannot fail has measured
 * nothing.
 */

import { describe, it, expect } from 'vitest'
import { readdirSync } from 'fs'
import { resolve, join } from 'path'

const MIGRATIONS = resolve('migrations')
const ROLLBACKS = join(MIGRATIONS, 'rollbacks')

/** `0107_audit_ledger_observability.sql` -> `0107`. Null if unnumbered. */
function numberOf(filename: string): string | null {
  const match = /^(\d{4})_[a-z0-9_]+\.sql$/.exec(filename)
  return match ? match[1] : null
}

/** Every number claimed by more than one file, in directory order. */
function findCollisions(filenames: string[]): Map<string, string[]> {
  const byNumber = new Map<string, string[]>()
  for (const name of filenames) {
    const n = numberOf(name)
    if (n === null) continue
    byNumber.set(n, [...(byNumber.get(n) ?? []), name])
  }
  return new Map([...byNumber].filter(([, files]) => files.length > 1))
}

/**
 * The six collisions already on main when this gate was written, all applied to
 * prod and therefore unrenameable. Exact filenames, so a NEW file claiming any
 * of these numbers is still a failure.
 *
 * Do not add to this list to make a red build green. Renumber the unapplied
 * migration instead. If it has already been applied to prod, that is a Captain
 * conversation, not a list entry.
 */
const GRANDFATHERED: Record<string, string[]> = {
  '0011': ['0011_booking_tables.sql', '0011_verify.sql'],
  '0013': ['0013_add_alert_context_type.sql', '0013_contacts_add_entity_id.sql'],
  '0027': ['0027_create_enrichment_runs.sql', '0027_harden_magic_links_context_and_milestones.sql'],
  '0028': ['0028_create_outreach_events.sql', '0028_originating_signal_attribution.sql'],
  '0029': ['0029_create_pipeline_settings.sql', '0029_create_scan_requests.sql'],
  '0107': ['0107_audit_ledger_observability.sql', '0107_gateway_loop_observability.sql'],
}

function sqlFilesIn(dir: string): string[] {
  return readdirSync(dir)
    .filter((f) => f.endsWith('.sql'))
    .sort()
}

describe('migration numbering', () => {
  it('gives every migration a unique number', () => {
    const collisions = findCollisions(sqlFilesIn(MIGRATIONS))

    const unexpected = [...collisions].filter(([number, files]) => {
      const allowed = GRANDFATHERED[number]
      if (!allowed) return true
      return !(files.length === allowed.length && files.every((f) => allowed.includes(f)))
    })

    expect(
      unexpected,
      unexpected
        .map(([n, files]) => `migrations/: ${n} claimed by ${files.join(' and ')}`)
        .join('\n')
    ).toEqual([])
  })

  it('gives every rollback a unique number', () => {
    const collisions = findCollisions(sqlFilesIn(ROLLBACKS))

    // Rollbacks mirror their migration, so they inherit the same exemption.
    const unexpected = [...collisions].filter(([number, files]) => {
      const allowed = GRANDFATHERED[number]?.map((f) => f.replace(/\.sql$/, '_down.sql'))
      if (!allowed) return true
      return !(files.length === allowed.length && files.every((f) => allowed.includes(f)))
    })

    expect(
      unexpected,
      unexpected
        .map(([n, files]) => `migrations/rollbacks/: ${n} claimed by ${files.join(' and ')}`)
        .join('\n')
    ).toEqual([])
  })

  it('detects a collision when one exists', () => {
    // The falsifier. Without this, a gutted findCollisions would leave the two
    // tests above passing on any directory at all.
    const collisions = findCollisions([
      '0001_first.sql',
      '0002_second.sql',
      '0002_second_again.sql',
    ])

    expect([...collisions.keys()]).toEqual(['0002'])
    expect(collisions.get('0002')).toEqual(['0002_second.sql', '0002_second_again.sql'])
  })

  it('names every migration <4-digit>_<lower_snake>.sql', () => {
    const malformed = sqlFilesIn(MIGRATIONS).filter((f) => numberOf(f) === null)

    expect(malformed, `unnumbered or misnamed: ${malformed.join(', ')}`).toEqual([])
  })
})
