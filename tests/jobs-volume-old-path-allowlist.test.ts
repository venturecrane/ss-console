/**
 * ADR 0103 R2: the seat volume's job tree is /opt/data/jobs, bound at
 * /run/smd-jobs. The old names (/opt/data/medchron, /run/smd-medchron) may be
 * written only where the move itself, its one-release compatibility bind, the
 * record-store fence or dated history needs them. Any other tracked file that
 * names one fails here, so a later lane cannot quietly read or write the old
 * tree: on a moved seat that path is either gone or, through the compatibility
 * bind, about to be.
 *
 * The list is also held the other way: an entry whose file no longer names the
 * old tree fails too, so the list shrinks when the compatibility bind goes and
 * never becomes a standing permission.
 *
 * `medchron` alone is not the old path: it is the chronology lane's name
 * (jobs/medchron/, medchron-firm.yaml, the queue/ and jobs/ dirs it keeps).
 * ~/smd-medchron-data is a laptop directory and the retired plugin
 * hermes-smd-medchron is a name; neither matches.
 *
 * Files are listed by `git grep --untracked` (tracked files, and new ones not
 * yet added; ignored files never), run with every GIT_* variable stripped:
 * under a pre-push hook GIT_DIR points git at the wrong repository.
 */
import { describe, it, expect } from 'vitest'
import { spawnSync } from 'node:child_process'
import { resolve } from 'node:path'

// No leading slash: a path joined from parts (root / 'run/smd-medchron') is the same path.
const OLD_PATH = 'opt/data/medchron|run/smd-medchron'

/** Why each file may still name the old tree. A trailing slash is a directory. */
const ALLOWED: Record<string, string> = {
  'operator/templates/entrypoint-lanes.sh':
    'the move itself, and the one-release bind at /run/smd-medchron for jobs held across it',
  'operator/templates/entrypoint.sh':
    'the boot chown sweep prunes the old name too, so an old tree is never handed to the agent',
  'operator/templates/seat-checks.sh':
    'the self-test asserts the old tree holds no file and the compatibility run dir is bound',
  'operator/templates/tests/test_selftest.py':
    'plants an old tree and the two run dirs to prove those checks can fail',
  'operator/bin/lib/fleet/remote_busy_probe.py':
    'the busy gate runs from the newest tooling on a seat not yet moved: old run dir when the new one is absent',
  'operator/bin/lib/fleet/remote_quiesce.py':
    'the quiesce marker is written where that seat reads it, by the same rule',
  'operator/bin/lane-proof.sh': 'pull finds a proof run on a seat not yet moved',
  'operator/bin/lib/fleet/volume_epoch.py':
    'names the epoch-1 layout a rollback must never land on',
  'operator/bin/tests/test_fleet_release.py': 'drives the rollback refusal across the epoch change',
  'migrations/0150_fleet_releases_volume_epoch.sql':
    'documents what epoch 1 and 2 mean on the volume',
  'operator/bin/tests/test_fleet_busy.py':
    'runs the probe and the quiesce writer against a seat laid out before the move',
  'operator/hermes-smd-overlay/shared/record_store.py':
    'reserved prefix: a record store may be authored under neither name',
  'src/lib/operator/customer-yaml/sections-record-stores.ts':
    'reserved prefix: the validator twin of record_store.py',
  'tests/operator-entrypoint.test.ts': 'drives the move, the refusals and the binds',
  'tests/jobs-volume-old-path-allowlist.test.ts': 'this test names what it forbids',
  'docs/adr/0024-hermes-consumption-and-update-cadence.md':
    'dated history: item 5 as decided, and the amendment that moved it',
  'docs/adr/0102-matter-files-on-a-staff-reply.md':
    'dated history: the page index path of amendment 4',
  'docs/adr/0103-the-jobs-runner.md': 'the decision to move the tree names both ends',
  'docs/adr/index.md': 'the ADR 0103 summary line',
  'docs/handbook/adr-index.md': 'the ADR 0103 summary line, generated from docs/adr/index.md',
  'docs/handbook/deployment-release.md': 'the R2 move and its hand rollback',
  'operator/bin/tests/fixtures/boot-harness/': 'boot logs recorded from real seats before the move',
  '.stitch/audits/backlog-snapshot-2026-08-31.json': 'a dated snapshot of issue bodies',
  'operator/contracts/seat-layout.yaml':
    'its left_out list names the old paths so the census cannot treat them as undeclared',
  'scripts/sprawl/snapshot.json':
    'the sprawl census of the tree, which counts the old path where it still appears',
  'operator/templates/tests/fixtures/seat-checks-golden.txt':
    'the seat check list as composed, captured: its old-tree checks name the old path',
  'operator/hermes-smd-overlay/tests/test_seat_layout_golden.py':
    "pins the record-store fence's reserved prefixes, the old name among them",
  'tests/record-stores-seat-layout.test.ts':
    'pins the console fence refusing a store under the old name',
  'scripts/sprawl/literal-baseline.json':
    'the restated-literal ratchet, which caps the old path at its remaining count',
}

function tracked(pattern: string): string[] {
  const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('GIT_')))
  const r = spawnSync('git', ['grep', '--untracked', '-l', '-I', '-E', pattern, '--', '.'], {
    cwd: resolve('.'),
    env,
    encoding: 'utf8',
    maxBuffer: 16 * 1024 * 1024,
  })
  // 0: matches, 1: none. Anything else is git failing, which must not read as "none".
  if (r.status !== 0 && r.status !== 1)
    throw new Error(`git grep failed (${r.status}): ${r.stderr}`)
  return r.stdout.split('\n').filter(Boolean).sort()
}

const allowedBy = (file: string): string | undefined =>
  Object.keys(ALLOWED).find((entry) =>
    entry.endsWith('/') ? file.startsWith(entry) : file === entry
  )

describe('the old jobs tree path is named only where the move needs it (ADR 0103 R2)', () => {
  const files = tracked(OLD_PATH)

  it('the search itself works: it finds the file that performs the move', () => {
    expect(files).toContain('operator/templates/entrypoint-lanes.sh')
    // And the lane name alone is not a match.
    expect(tracked('jobs/medchron/lane').length).toBeGreaterThan(0)
    expect(files).not.toContain('operator/runners/jobs/jobs/medchron/lane.py')
  })

  it('no file outside the list names /opt/data/medchron or /run/smd-medchron', () => {
    const offenders = files.filter((f) => allowedBy(f) === undefined)
    expect(
      offenders,
      'These files name the old jobs tree. Use /opt/data/jobs and /run/smd-jobs ' +
        '(or JOBS_DATA_DIR / SMD_JOBS_RUN_DIR); add a file to ALLOWED only with the reason it must keep the old name.'
    ).toEqual([])
  })

  it('every listed file still names it (a stale entry is removed, not kept)', () => {
    const stale = Object.keys(ALLOWED).filter((entry) => !files.some((f) => allowedBy(f) === entry))
    expect(stale).toEqual([])
  })
})
