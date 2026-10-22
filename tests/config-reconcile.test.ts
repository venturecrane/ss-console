/**
 * Coverage for the git -> D1 projection RECONCILER (ss #2292).
 *
 * `scripts/ci-sync-customer-configs.sh` is a trigger: it syncs whatever changed
 * inside one push range. A range that is never processed is never revisited, so
 * a row can sit stale indefinitely with nothing watching — which is how the
 * smd-staging row carried `format_spec: none` for eleven days while main said
 * `expected`. `scripts/ci-reconcile-customer-configs.sh` is the control that
 * closes it: it compares EVERY row's stamped provenance against the commit HEAD
 * names for that file, re-projects the mismatches, and alerts on a git_sha that
 * main's history no longer contains.
 *
 * The first describe block is the falsifier for the whole PR: it reconstructs
 * the real smd-staging shape and shows the EXISTING push-range sync finds
 * nothing in it. If that test ever starts reporting drift, the reconciler was
 * not needed and this suite is measuring the wrong thing.
 *
 * No live D1 and no live wrangler. `npx` is replaced by a stub on PATH backed by
 * a TSV file standing in for the `customer_configs` table, so detection,
 * re-projection, the apply, and the landback verification are all exercised
 * against state that actually changes.
 */
import { execFileSync } from 'node:child_process'
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterEach, describe, expect, it } from 'vitest'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const RECONCILER = join(REPO_ROOT, 'scripts', 'ci-reconcile-customer-configs.sh')
const PUSH_RANGE_SYNC = join(REPO_ROOT, 'scripts', 'ci-sync-customer-configs.sh')

/**
 * Stand-in for `npx`, covering the three invocations the reconciler makes:
 * the wrangler row read, the tsx projection, and the wrangler apply + read-back.
 * Backed by FAKE_D1_FILE (a `slug<TAB>entity<TAB>git_sha` table) so the
 * landback check compares against state the run itself mutated.
 */
const FAKE_NPX = `#!/usr/bin/env bash
echo "$*" >> "\${FAKE_NPX_LOG}"
export DBFILE="\${FAKE_D1_FILE}"

# $1 = slug filter ("" for every row).
emit_rows() {
  ONLY_SLUG="$1" node -e '
    const fs=require("fs");
    const lines=fs.readFileSync(process.env.DBFILE,"utf8").split("\\n").filter(Boolean);
    const only=process.env.ONLY_SLUG||"";
    const rows=lines.map(l=>{const [customer_slug,entity_id,git_sha]=l.split("\\t");return {customer_slug,entity_id,git_sha}})
      .filter(r=>!only||r.customer_slug===only);
    process.stdout.write(JSON.stringify([{results:rows}]));
  '
}

if [ "$1" = "wrangler" ]; then
  cmd=""; file=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --command) cmd="$2"; shift 2 ;;
      --file=*) file="\${1#--file=}"; shift ;;
      *) shift ;;
    esac
  done

  if [ -n "\${FAKE_D1_UNREADABLE:-}" ]; then
    echo "could not reach d1" >&2
    exit 1
  fi

  if [ -n "$cmd" ]; then
    case "$cmd" in
      *"SELECT customer_slug"*)
        if [ -n "\${FAKE_D1_GARBAGE:-}" ]; then echo "not json at all"; exit 0; fi
        emit_rows ""
        exit 0 ;;
      *"SELECT git_sha"*)
        slug=$(printf '%s' "$cmd" | sed -E "s/.*customer_slug = '([^']*)'.*/\\1/")
        emit_rows "$slug"
        exit 0 ;;
    esac
    echo "unexpected wrangler command: $cmd" >&2
    exit 99
  fi

  if [ -n "$file" ]; then
    # Apply the projection: adopt the sha the generated SQL stamped.
    slug=$(grep -oE 'customer_slug=[a-z0-9-]+' "$file" | head -1 | cut -d= -f2)
    sha=$(grep -oE 'git_sha [0-9a-f]{40}' "$file" | head -1 | awk '{print $2}')
    if [ -n "\${FAKE_APPLY_FAIL:-}" ]; then echo "apply exploded" >&2; exit 1; fi
    # FAKE_APPLY_NOOP: the apply reports success but the row never moves — the
    # exact shape the landback check exists to catch.
    if [ -z "\${FAKE_APPLY_NOOP:-}" ]; then
      SLUG="$slug" SHA="$sha" node -e '
        const fs=require("fs");
        const [slug,sha]=[process.env.SLUG,process.env.SHA];
        const out=fs.readFileSync(process.env.DBFILE,"utf8").split("\\n").filter(Boolean)
          .map(l=>{const p=l.split("\\t"); if(p[0]===slug) p[2]=sha; return p.join("\\t")});
        fs.writeFileSync(process.env.DBFILE,out.join("\\n")+"\\n");
      '
    fi
    exit 0
  fi
  echo "unexpected wrangler invocation" >&2
  exit 99
fi

if [ "$1" = "tsx" ]; then
  slug=""; entity=""; out=""
  shift  # tsx
  shift  # script path
  while [ $# -gt 0 ]; do
    case "$1" in
      --out=*) out="\${1#--out=}" ;;
      --*) : ;;
      *) if [ -z "$slug" ]; then slug="$1"; else entity="$1"; fi ;;
    esac
    shift
  done
  if [ -n "\${FAKE_PROJECT_FAIL:-}" ]; then echo "projection failed" >&2; exit 1; fi
  sha=$(git log -1 --format=%H -- "operator/customers/\${slug}/customer.yaml")
  mkdir -p "$(dirname "$out")"
  printf -- '-- customer.yaml projection for customer_slug=%s (git_sha %s).\\n' "$slug" "$sha" > "$out"
  echo "projected $slug ($entity)"
  exit 0
fi

echo "unexpected npx invocation: $*" >&2
exit 99
`

interface Fixture {
  root: string
  binDir: string
  dbFile: string
  npxLog: string
  /** Commit shas by label, for building drifted / orphaned row states. */
  shas: Record<string, string>
}

const fixtures: string[] = []

function git(root: string, args: string[]): string {
  return execFileSync(
    'git',
    [
      '-c',
      'user.email=t@example.com',
      '-c',
      'user.name=t',
      '-c',
      'core.hooksPath=/dev/null',
      ...args,
    ],
    {
      cwd: root,
      encoding: 'utf8',
      // Strip GIT_*: GIT_DIR / GIT_WORK_TREE / GIT_INDEX_FILE win over `cwd` and
      // git exports them to every hook, so under `git push` these fixtures would
      // otherwise operate on the parent repository instead of themselves.
      env: Object.fromEntries(
        Object.entries({ ...process.env, HUSKY: '0' }).filter(([k]) => !k.startsWith('GIT_'))
      ),
    }
  )
}

function writeConfig(root: string, slug: string, body: string): void {
  mkdirSync(join(root, 'operator', 'customers', slug), { recursive: true })
  writeFileSync(join(root, 'operator', 'customers', slug, 'customer.yaml'), body)
}

/**
 * A throwaway repo whose history mirrors the real defect: an OLD commit that
 * authored the config, then a LATER commit that changed it (the 008a5731
 * "declare format enforcement" shape). `shas.old` / `shas.head` are what the
 * tests stamp rows with.
 */
function makeFixture(): Fixture {
  const root = mkdtempSync(join(tmpdir(), 'config-reconcile-'))
  fixtures.push(root)

  git(root, ['init', '-q', '-b', 'main'])
  writeFileSync(join(root, 'README.md'), 'fixture\n')
  writeConfig(root, 'smd-staging', 'customer_id: smd-staging\nformat_spec: none\n')
  writeConfig(root, 'in-sync-seat', 'customer_id: in-sync-seat\n')
  git(root, ['add', '-A'])
  git(root, ['commit', '-q', '-m', 'author configs'])
  const old = git(root, ['rev-parse', 'HEAD']).trim()

  // An unrelated commit, so `old` is a genuine ancestor rather than HEAD~0.
  writeFileSync(join(root, 'README.md'), 'fixture v2\n')
  git(root, ['add', '-A'])
  git(root, ['commit', '-q', '-m', 'unrelated'])

  // The change that never reached D1.
  writeConfig(root, 'smd-staging', 'customer_id: smd-staging\nformat_spec: expected\n')
  git(root, ['add', '-A'])
  git(root, ['commit', '-q', '-m', 'raise smd-staging to format_spec: expected'])
  const head = git(root, ['rev-parse', 'HEAD']).trim()

  // A commit that exists but is NOT an ancestor of main — the rewritten-history
  // shape, reachable only off a side branch.
  git(root, ['checkout', '-q', '-b', 'rewritten-away'])
  writeFileSync(join(root, 'README.md'), 'orphan branch\n')
  git(root, ['add', '-A'])
  git(root, ['commit', '-q', '-m', 'commit main no longer contains'])
  const offMain = git(root, ['rev-parse', 'HEAD']).trim()
  git(root, ['checkout', '-q', 'main'])

  const binDir = join(root, '.fakebin')
  mkdirSync(binDir)
  writeFileSync(join(binDir, 'npx'), FAKE_NPX)
  chmodSync(join(binDir, 'npx'), 0o755)

  return {
    root,
    binDir,
    dbFile: join(root, '.d1.tsv'),
    npxLog: join(root, 'npx.log'),
    shas: {
      old,
      head,
      offMain,
      // 40 hex that is not an object in this clone at all — the live 895dad9f /
      // 812873d6 / 249d7a93 shape, resolvable only via the GitHub API.
      unknown: 'deadbeef'.repeat(5),
    },
  }
}

/** Seed the stand-in customer_configs table. */
function seedRows(fx: Fixture, rows: Array<[string, string, string]>): void {
  writeFileSync(fx.dbFile, rows.map((r) => r.join('\t')).join('\n') + '\n')
}

function readRows(fx: Fixture): Record<string, string> {
  const out: Record<string, string> = {}
  for (const line of readFileSync(fx.dbFile, 'utf8').split('\n').filter(Boolean)) {
    const [slug, , sha] = line.split('\t')
    out[slug] = sha
  }
  return out
}

interface RunResult {
  code: number
  out: string
  npxCalls: string[]
}

function runScript(script: string, fx: Fixture, env: Record<string, string> = {}): RunResult {
  // Strip GIT_* for the same reason as `git` above, and it matters more here:
  // the reconciler's whole judgement is `git log` / `git merge-base` in the
  // fixture, and an inherited GIT_DIR would point it at the real repository.
  const inherited = Object.fromEntries(
    Object.entries(process.env).filter(([k]) => !k.startsWith('GIT_'))
  ) as Record<string, string>

  let outcome: { code: number; out: string }
  try {
    const stdout = execFileSync('bash', [script], {
      cwd: fx.root,
      encoding: 'utf8',
      env: {
        ...inherited,
        PATH: `${fx.binDir}:${process.env.PATH ?? ''}`,
        FAKE_NPX_LOG: fx.npxLog,
        FAKE_D1_FILE: fx.dbFile,
        ...env,
      },
    })
    outcome = { code: 0, out: stdout }
  } catch (err) {
    const e = err as { status?: number; stdout?: Buffer | string; stderr?: Buffer | string }
    outcome = { code: e.status ?? 1, out: String(e.stdout ?? '') + String(e.stderr ?? '') }
  }

  let npxCalls: string[]
  try {
    npxCalls = readFileSync(fx.npxLog, 'utf8').split('\n').filter(Boolean)
  } catch {
    npxCalls = []
  }
  return { ...outcome, npxCalls }
}

const run = (fx: Fixture, env: Record<string, string> = {}): RunResult =>
  runScript(RECONCILER, fx, env)

/** Projection invocations only — the calls that would rewrite a row. */
const projections = (calls: string[]): string[] => calls.filter((c) => c.startsWith('tsx '))

afterEach(() => {
  while (fixtures.length > 0) {
    const dir = fixtures.pop()
    if (dir) rmSync(dir, { recursive: true, force: true })
  }
})

describe('the gap this reconciler exists to close', () => {
  // Structural, not executed: ci-sync-customer-configs.sh needs `mapfile`, so it
  // only runs on bash 4+ (CI) and not on a macOS operator box's bash 3.2. A test
  // that passes in one place and errors in the other measures its environment
  // rather than its subject.
  const sync = readFileSync(PUSH_RANGE_SYNC, 'utf8')

  it('derives the push-range sync work set from the push range and nothing else', () => {
    // This is the whole defect in one assertion. The sync's universe is one
    // diff; a range that is never processed is never revisited, because no later
    // push's range contains it. If a future edit gives the sync a second source
    // of work, this breaks and the reconciler's premise should be re-examined.
    const diffs = sync.match(/^.*git diff --name-only.*$/gm) ?? []
    expect(diffs).toHaveLength(1)
    expect(diffs[0]).toContain('"$BEFORE" "$AFTER"')
    expect(sync).toContain('BEFORE_SHA')
    expect(sync).toContain('AFTER_SHA')
    // Nothing in it enumerates the live rows, which is what a reconciler must do.
    expect(sync).not.toContain('SELECT customer_slug, entity_id, git_sha')
  })

  it('has no staleness signal of its own to fall back on', () => {
    // The row's own timestamp cannot stand in: the sync never writes updated_at
    // (the live smd row shows 06-10 against a 07-30 git_sha).
    expect(sync).not.toContain('updated_at')
  })
})

describe('ci-reconcile-customer-configs: drift detection and convergence', () => {
  it('detects and re-projects the smd-staging case: a row behind HEAD for its file', () => {
    const fx = makeFixture()
    seedRows(fx, [
      ['in-sync-seat', 'ent_2', fx.shas.old],
      ['smd-staging', 'ent_1', fx.shas.old],
    ])

    const res = run(fx)

    // Reported, not silently healed.
    expect(res.code).toBe(2)
    expect(res.out).toContain('smd-staging DRIFTED')
    expect(res.out).toContain(`DRIFT    smd-staging: ${fx.shas.old} -> ${fx.shas.head}`)
    // Converged: the row now carries the commit HEAD names for that file.
    expect(readRows(fx)['smd-staging']).toBe(fx.shas.head)
    // And it re-projected ONLY the drifted slug.
    expect(projections(res.npxCalls)).toHaveLength(1)
    expect(projections(res.npxCalls)[0]).toContain('smd-staging')
    expect(res.out).toContain('in-sync-seat: in sync')
  })

  it('exits clean and projects nothing when every row matches HEAD', () => {
    const fx = makeFixture()
    seedRows(fx, [
      ['in-sync-seat', 'ent_2', fx.shas.old],
      ['smd-staging', 'ent_1', fx.shas.head],
    ])

    const res = run(fx)

    expect(res.code).toBe(0)
    expect(res.out).toContain("every row's provenance is the commit HEAD names")
    expect(projections(res.npxCalls)).toHaveLength(0)
  })

  it('reports drift without writing anything under RECONCILE_DRY_RUN', () => {
    const fx = makeFixture()
    seedRows(fx, [['smd-staging', 'ent_1', fx.shas.old]])

    const res = run(fx, { RECONCILE_DRY_RUN: '1' })

    expect(res.code).toBe(2)
    expect(res.out).toContain('would re-project smd-staging')
    expect(projections(res.npxCalls)).toHaveLength(0)
    expect(readRows(fx)['smd-staging']).toBe(fx.shas.old)
  })
})

describe('ci-reconcile-customer-configs: orphaned provenance alerts', () => {
  it('alerts when a row git_sha is a commit main no longer contains', () => {
    const fx = makeFixture()
    seedRows(fx, [['smd-staging', 'ent_1', fx.shas.offMain]])

    const res = run(fx)

    expect(res.code).toBe(2)
    expect(res.out).toContain('is not an ancestor of HEAD')
    expect(res.out).toContain(`ORPHAN   smd-staging (${fx.shas.offMain})`)
    // An orphaned stamp is still drift against HEAD, so it also converges.
    expect(readRows(fx)['smd-staging']).toBe(fx.shas.head)
  })

  it('treats a git_sha that is not an object in this clone as orphaned', () => {
    // The live shape: 895dad9f / 812873d6 / 249d7a93 resolve via the GitHub API
    // but are absent from the clone. `merge-base --is-ancestor` errors rather
    // than answering, and "could not tell" must not read as "fine".
    const fx = makeFixture()
    seedRows(fx, [['smd-staging', 'ent_1', fx.shas.unknown]])

    const res = run(fx)

    expect(res.code).toBe(2)
    expect(res.out).toContain(`ORPHAN   smd-staging (${fx.shas.unknown})`)
  })

  it('surfaces an orphaned stamp on a row whose content is already current', () => {
    // The live scott / smd shape: content byte-identical to main, provenance
    // pointing at a commit the rewrite removed. A reconciler that compared
    // CONTENT would call these clean and the rewrite would stay invisible a
    // second time; comparing provenance raises both signals — orphaned, and
    // therefore also mismatched against the commit HEAD names for the file.
    const fx = makeFixture()
    seedRows(fx, [['in-sync-seat', 'ent_2', fx.shas.offMain]])

    const res = run(fx)

    expect(res.code).toBe(2)
    expect(res.out).toContain(`ORPHAN   in-sync-seat (${fx.shas.offMain})`)
    expect(res.out).toContain(`DRIFT    in-sync-seat: ${fx.shas.offMain} -> ${fx.shas.old}`)
    // Re-stamped onto the commit main actually names for that file.
    expect(readRows(fx)['in-sync-seat']).toBe(fx.shas.old)
  })

  it('rejects a malformed git_sha rather than comparing it', () => {
    const fx = makeFixture()
    seedRows(fx, [['smd-staging', 'ent_1', 'not-a-sha']])

    const res = run(fx)

    expect(res.code).toBe(2)
    expect(res.out).toContain('ORPHAN   smd-staging (not-a-sha)')
  })
})

describe('ci-reconcile-customer-configs: never creates, never drops', () => {
  it('ignores a slug that has a customer.yaml but no row', () => {
    // First projection binds a config to an owning entity and stays
    // Captain-gated. The loop's universe is the row set, so this is structural.
    const fx = makeFixture()
    writeConfig(fx.root, 'never-seeded', 'customer_id: never-seeded\n')
    git(fx.root, ['add', '-A'])
    git(fx.root, ['commit', '-q', '-m', 'author an unseeded customer'])
    seedRows(fx, [['smd-staging', 'ent_1', fx.shas.head]])

    const res = run(fx)

    expect(res.code).toBe(0)
    expect(projections(res.npxCalls)).toHaveLength(0)
    expect(Object.keys(readRows(fx))).toEqual(['smd-staging'])
    expect(res.out).not.toContain('never-seeded')
  })

  it('warns but never drops a row whose customer.yaml is gone', () => {
    const fx = makeFixture()
    rmSync(join(fx.root, 'operator', 'customers', 'in-sync-seat', 'customer.yaml'))
    git(fx.root, ['add', '-A'])
    git(fx.root, ['commit', '-q', '-m', 'retire in-sync-seat from git'])
    seedRows(fx, [
      ['in-sync-seat', 'ent_2', fx.shas.old],
      ['smd-staging', 'ent_1', fx.shas.head],
    ])

    const res = run(fx)

    expect(res.out).toContain('customer retirement is manual')
    expect(res.out).toContain('NO-YAML  in-sync-seat')
    expect(projections(res.npxCalls)).toHaveLength(0)
    expect(readRows(fx)['in-sync-seat']).toBe(fx.shas.old)
  })

  it('never projects a scaffold or tombstone dir that somehow holds a row', () => {
    const fx = makeFixture()
    seedRows(fx, [
      ['_template', 'ent_x', fx.shas.old],
      ['gone.decommissioned.2026-10-01', 'ent_y', fx.shas.old],
      ['smd-staging', 'ent_1', fx.shas.head],
    ])

    const res = run(fx)

    expect(res.code).toBe(0)
    expect(res.out).toContain('_template is a scaffold dir name, not a seat')
    expect(res.out).toContain('gone.decommissioned.2026-10-01 is a tombstone dir name, not a seat')
    expect(projections(res.npxCalls)).toHaveLength(0)
  })

  it('refuses a slug from D1 that could escape the path or the SQL literal', () => {
    const fx = makeFixture()
    seedRows(fx, [["bad'; DROP", 'ent_x', fx.shas.old]])

    const res = run(fx)

    expect(res.code).toBe(1)
    expect(res.out).toContain('refusing to reconcile suspicious slug')
    expect(projections(res.npxCalls)).toHaveLength(0)
  })

  it('passes the row own entity_id to the projection, never a guessed one', () => {
    const fx = makeFixture()
    seedRows(fx, [['smd-staging', 'ent_specific', fx.shas.old]])

    const res = run(fx)

    expect(projections(res.npxCalls)[0]).toContain('ent_specific')
  })
})

describe('ci-reconcile-customer-configs: cannot-evaluate is never reported as clean', () => {
  it('fails rather than reports clean when D1 cannot be read', () => {
    const fx = makeFixture()
    seedRows(fx, [['smd-staging', 'ent_1', fx.shas.old]])

    const res = run(fx, { FAKE_D1_UNREADABLE: '1' })

    expect(res.code).toBe(1)
    expect(res.out).toContain('could not read customer_configs')
  })

  it('fails rather than reports clean when the row read is unparseable', () => {
    const fx = makeFixture()
    seedRows(fx, [['smd-staging', 'ent_1', fx.shas.old]])

    const res = run(fx, { FAKE_D1_GARBAGE: '1' })

    expect(res.code).toBe(1)
    expect(res.out).toContain('could not parse')
  })

  it('refuses to reconcile from a shallow clone', () => {
    // In a shallow clone `git log -1 -- <path>` reports the newest commit in the
    // slice and every older sha looks orphaned. A confident wrong answer here is
    // worse than no answer.
    const fx = makeFixture()
    seedRows(fx, [['smd-staging', 'ent_1', fx.shas.head]])
    const shallowRoot = mkdtempSync(join(tmpdir(), 'config-reconcile-shallow-'))
    fixtures.push(shallowRoot)
    execFileSync('git', ['clone', '-q', '--depth', '1', `file://${fx.root}`, shallowRoot], {
      env: Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('GIT_'))),
    })

    const res = run({ ...fx, root: shallowRoot })

    expect(res.code).toBe(1)
    expect(res.out).toContain('shallow clone')
  })

  it('fails when a projection cannot be produced', () => {
    const fx = makeFixture()
    seedRows(fx, [['smd-staging', 'ent_1', fx.shas.old]])

    const res = run(fx, { FAKE_PROJECT_FAIL: '1' })

    expect(res.code).toBe(1)
    expect(res.out).toContain('projection failed; row left as-is')
    expect(readRows(fx)['smd-staging']).toBe(fx.shas.old)
  })

  it('fails when the apply reports success but the row never moved', () => {
    const fx = makeFixture()
    seedRows(fx, [['smd-staging', 'ent_1', fx.shas.old]])

    const res = run(fx, { FAKE_APPLY_NOOP: '1' })

    expect(res.code).toBe(1)
    expect(res.out).toContain('re-projection did not land')
  })
})

describe('the reconciler is actually scheduled', () => {
  const workflow = join(REPO_ROOT, '.github', 'workflows', 'customer-config-reconcile.yml')

  it('runs on a schedule, not only on a push range', () => {
    expect(existsSync(workflow)).toBe(true)
    const source = readFileSync(workflow, 'utf8')
    expect(source).toContain('schedule:')
    expect(source).toMatch(/cron:/)
    // A reconciler that cannot be run on demand is one nobody runs after a fix.
    expect(source).toContain('workflow_dispatch:')
  })

  it('opens an issue on findings instead of only annotating the log', () => {
    const source = readFileSync(workflow, 'utf8')
    expect(source).toContain('gh issue create')
    // Exit 2 is the findings code; exit 1 fails the run on its own.
    expect(source).toContain("== '2'")
  })

  it('checks out full history, which the reconciler requires', () => {
    const source = readFileSync(workflow, 'utf8')
    expect(source).toContain('fetch-depth: 0')
  })
})

/**
 * ss#2307. The script's exit-code contract was correct and covered by every
 * test above; the WORKFLOW STEP consuming it threw that contract away. GitHub
 * runs `run:` bodies under `bash -e`, so the reconciler exiting 2 — the
 * findings code, the only non-zero path that is not an error — aborted the step
 * before `cat reconcile.txt` and before `status` reached GITHUB_OUTPUT, leaving
 * the issue-opening step (gated on `status == '2'`) unreachable. The control
 * was mute in precisely the case it exists for: the 2026-08-12 dry run found
 * three drifted rows and reported them nowhere.
 *
 * These tests execute the REAL step body extracted from the YAML, under the
 * shell GitHub uses, against a stub that exits 2. Asserting on the body's text
 * would only restate the fix; running it is what can fail.
 */
describe('the reconcile step reports what the reconciler found (ss#2307)', () => {
  const workflow = join(REPO_ROOT, '.github', 'workflows', 'customer-config-reconcile.yml')
  let dir: string | undefined

  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true })
    dir = undefined
  })

  /** The `run:` body of the reconcile step, dedented, exactly as GitHub runs it. */
  const stepBody = (): string => {
    const source = readFileSync(workflow, 'utf8')
    const start = source.indexOf('      - name: Reconcile every row against main')
    expect(start).toBeGreaterThan(-1)
    const body = source.slice(source.indexOf('run: |', start) + 'run: |\n'.length)
    const lines: string[] = []
    for (const line of body.split('\n')) {
      if (line.trim() !== '' && !line.startsWith('          ')) break
      lines.push(line.slice(10))
    }
    return lines.join('\n')
  }

  /**
   * Run a step body the way the runner does: `bash -e <file>`, with the
   * reconciler replaced by a stub that prints a report and exits `code`.
   */
  const runStep = (body: string, code: number) => {
    dir = mkdtempSync(join(tmpdir(), 'step-2307-'))
    mkdirSync(join(dir, 'scripts'), { recursive: true })
    writeFileSync(
      join(dir, 'scripts', 'ci-reconcile-customer-configs.sh'),
      `#!/usr/bin/env bash\necho "REPORT: smd-staging DRIFTED"\nexit ${code}\n`
    )
    const outputs = join(dir, 'github_output')
    writeFileSync(outputs, '')
    writeFileSync(join(dir, 'step.sh'), body)
    let stdout: string
    let failed = false
    try {
      stdout = execFileSync('bash', ['-e', join(dir, 'step.sh')], {
        cwd: dir,
        encoding: 'utf-8',
        env: { ...process.env, GITHUB_OUTPUT: outputs },
      })
    } catch (err) {
      failed = true
      stdout = String((err as { stdout?: string }).stdout ?? '')
    }
    return { stdout, failed, outputs: readFileSync(outputs, 'utf-8') }
  }

  it('prints the report and records status=2 when the reconciler finds drift', () => {
    const run = runStep(stepBody(), 2)
    // Without this the finding exists only in an exit code nobody can read.
    expect(run.stdout).toContain('REPORT: smd-staging DRIFTED')
    // The issue-opening step is gated on exactly this.
    expect(run.outputs).toContain('status=2')
    expect(run.failed).toBe(false)
  })

  it('still fails the run when the reconciler could not evaluate (exit 1)', () => {
    const run = runStep(stepBody(), 1)
    expect(run.failed).toBe(true)
    // Even a HOLD must not be silent — a control that cannot evaluate says so.
    expect(run.stdout).toContain('REPORT: smd-staging DRIFTED')
  })

  it('passes the report through untouched on a clean run', () => {
    const run = runStep(stepBody(), 0)
    expect(run.failed).toBe(false)
    expect(run.outputs).toContain('status=0')
  })

  /**
   * The falsifier. Reintroduce the pre-fix body and confirm this harness
   * reports the defect — otherwise the three tests above are green for a reason
   * nobody has established.
   */
  it('detects the regression if the `|| STATUS=$?` guard is removed', () => {
    const regressed = stepBody().replace(/ \|\| STATUS=\$\?/, '\n          STATUS=$?')
    expect(regressed).not.toEqual(stepBody())
    const run = runStep(regressed, 2)
    expect(run.stdout).not.toContain('REPORT: smd-staging DRIFTED')
    expect(run.outputs).not.toContain('status=2')
  })
})
