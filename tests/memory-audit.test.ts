/**
 * The memory reachability audit, asserted by EXECUTING the hook.
 *
 * WHY THIS TEST EXISTS AT ALL. `.claude/**` is excluded from eslint, prettier
 * and typecheck, so this file is the only gate on `.claude/hooks/memory-audit.mjs`.
 *
 * WHY THE AUDIT EXISTS. On 2026-09-09, 60 memory files in this venture's store
 * were reachable from no index, including standing Captain directives and two
 * secret-leak hazards, after a compaction of MEMORY.md deleted index rows
 * instead of moving them to an archive index. Nothing detected it.
 *
 * Every rule is asserted in BOTH directions (Law 12). A store with a known
 * orphan must be flagged, and the SAME store with the orphan linked must come
 * back clean; likewise for dangling references and for each budget. A check
 * that only ever returns one answer has measured nothing, and the failure mode
 * this audit guards against (silent loss) is invisible precisely because every
 * instrument around it was incapable of returning the other answer.
 *
 * Harness requirements, each from a recorded incident here:
 *   - cleanEnv() strips every GIT_* var (tests/staleness-detection.test.ts:104-116).
 *     Without it GIT_DIR beats cwd and a fixture measures the real repository.
 *   - SS_BOARD_DIR points at a scratch dir on every run. On 2026-08-01 a verify
 *     run pruned a live session's own board record because a test did not.
 *   - Stores are built with mkdtempSync, never checked-in fixtures, which drift.
 */
import { execFileSync } from 'node:child_process'
import {
  closeSync,
  mkdtempSync,
  mkdirSync,
  openSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, it, expect, afterEach } from 'vitest'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const SCRIPT = join(REPO_ROOT, '.claude', 'hooks', 'memory-audit.mjs')

const scratch: string[] = []
afterEach(() => {
  while (scratch.length) rmSync(scratch.pop() as string, { recursive: true, force: true })
})

function scratchDir(prefix: string): string {
  const dir = mkdtempSync(join(tmpdir(), prefix))
  scratch.push(dir)
  return dir
}

function cleanEnv(extra: Record<string, string> = {}): Record<string, string> {
  return {
    ...Object.fromEntries(
      Object.entries({ ...process.env, HUSKY: '0' }).filter(([k]) => !k.startsWith('GIT_'))
    ),
    ...extra,
  }
}

interface AuditResult {
  ok: boolean
  missing?: true
  dir: string
  total: number
  reachable: number
  orphans: string[]
  dangling: string[]
  attic: number
  index: {
    bytes: number
    lines: number
    byteCap: number
    byteTarget: number
    lineCap: number
    bytesRemaining: number
    bytesRemainingToReadLimit: number
    linesRemaining: number
  }
  problems: string[]
  warnings: string[]
}

/** Every observed exit status, so the floor assertion can prove both fired. */
const observedStatuses = new Set<number>()

function run(
  args: string[],
  env: Record<string, string> = {}
): { stdout: string; stderr: string; status: number } {
  const board = scratchDir('ss-memory-audit-board-')
  // execFileSync surfaces stderr only on the THROW path, and the clean-exit
  // cases need it too (the malformed-input case asserts the absence of the
  // fail-open crash line, which is only observable on a zero exit). So stderr
  // goes to a file descriptor and is read back on both paths.
  const errPath = join(board, 'stderr.txt')
  const errFd = openSync(errPath, 'w')
  let out: { stdout: string; status: number }
  try {
    const stdout = execFileSync('node', [SCRIPT, ...args], {
      encoding: 'utf8',
      stdio: ['pipe', 'pipe', errFd],
      env: cleanEnv({ SS_BOARD_DIR: board, ...env }),
    })
    out = { stdout, status: 0 }
  } catch (err) {
    const e = err as { status?: number; stdout?: string }
    out = { stdout: e.stdout ?? '', status: e.status ?? -1 }
  } finally {
    closeSync(errFd)
  }
  observedStatuses.add(out.status)
  return { ...out, stderr: readFileSync(errPath, 'utf8') }
}

function audit(dir: string): { result: AuditResult; status: number; stderr: string } {
  const r = run([], { SS_MEMORY_DIR: dir })
  // A crash would fail open at exit 0 with no JSON; parsing is itself the check
  // that the tool produced a real answer rather than swallowing an exception.
  expect(r.stdout.trim(), `no JSON on stdout (stderr: ${r.stderr})`).not.toBe('')
  return { result: JSON.parse(r.stdout) as AuditResult, status: r.status, stderr: r.stderr }
}

function writeStore(dir: string, files: Record<string, string>): void {
  for (const [name, body] of Object.entries(files)) {
    const full = join(dir, name)
    if (name.includes('/')) mkdirSync(join(dir, name.split('/')[0]), { recursive: true })
    writeFileSync(full, body)
  }
}

const CLEAN_STORE = {
  'MEMORY.md': ['# Index', '', '- [One](one.md) - hook', '- [Two](two.md) - hook', ''].join('\n'),
  'one.md': 'first memory\n',
  'two.md': 'second memory\n',
}

describe('memory-audit: reachability', () => {
  it('passes a store whose every memory is indexed', () => {
    const dir = scratchDir('ss-memory-clean-')
    writeStore(dir, CLEAN_STORE)
    const { result, status } = audit(dir)
    expect(result.orphans).toEqual([])
    expect(result.dangling).toEqual([])
    expect(result.total).toBe(2)
    expect(result.reachable).toBe(2)
    expect(result.ok).toBe(true)
    expect(status).toBe(0)
  })

  it('flags a memory on disk that no index reaches, and clears when it is linked', () => {
    const dir = scratchDir('ss-memory-orphan-')
    writeStore(dir, { ...CLEAN_STORE, 'lost.md': 'a standing directive nothing points at\n' })

    const dirty = audit(dir)
    expect(dirty.result.orphans).toEqual(['lost.md'])
    expect(dirty.result.total).toBe(3)
    expect(dirty.result.reachable).toBe(2)
    expect(dirty.result.ok).toBe(false)
    expect(dirty.status).toBe(1)

    // Control, same store: link it and the finding must disappear.
    writeStore(dir, { 'MEMORY.md': `${CLEAN_STORE['MEMORY.md']}- [Lost](lost.md) - hook\n` })
    const clean = audit(dir)
    expect(clean.result.orphans).toEqual([])
    expect(clean.result.reachable).toBe(3)
    expect(clean.result.ok).toBe(true)
    expect(clean.status).toBe(0)
  })

  it('flags an index row pointing at a file that is not there, and clears when it lands', () => {
    const dir = scratchDir('ss-memory-dangling-')
    writeStore(dir, {
      ...CLEAN_STORE,
      'MEMORY.md': `${CLEAN_STORE['MEMORY.md']}- [Ghost](ghost.md) - hook\n`,
    })

    const dirty = audit(dir)
    expect(dirty.result.dangling).toEqual(['ghost.md'])
    expect(dirty.result.orphans).toEqual([])
    expect(dirty.result.ok).toBe(false)
    expect(dirty.status).toBe(1)

    // Control: write the missing file and the same index is sound.
    writeStore(dir, { 'ghost.md': 'it exists now\n' })
    const clean = audit(dir)
    expect(clean.result.dangling).toEqual([])
    expect(clean.result.ok).toBe(true)
    expect(clean.status).toBe(0)
  })

  /**
   * The case that matters most. The tier model (an always-on index under a hard
   * cap, depth behind sub-indexes) only works if reachability follows the
   * pointer. An audit that read MEMORY.md alone would report every archived
   * memory as an orphan and be switched off inside a week.
   */
  it('follows sub-indexes transitively, and stops when the chain is cut', () => {
    const dir = scratchDir('ss-memory-transitive-')
    writeStore(dir, {
      'MEMORY.md': '# Index\n\n- [FULL INDEX](sub_index.md) - the depth\n',
      'sub_index.md': '# Sub\n\n- [Deep](deep.md) - hook\n- [Deeper index](sub_two.md) - hook\n',
      'sub_two.md': '# Sub two\n\n- [Deepest](deepest.md) - hook\n',
      'deep.md': 'two hops from MEMORY.md\n',
      'deepest.md': 'three hops from MEMORY.md\n',
    })

    const clean = audit(dir)
    expect(clean.result.orphans).toEqual([])
    expect(clean.result.reachable).toBe(4)
    expect(clean.result.ok).toBe(true)
    expect(clean.status).toBe(0)

    // Control: cut the top link and everything below it becomes unreachable.
    writeStore(dir, { 'MEMORY.md': '# Index\n\nno rows\n' })
    const dirty = audit(dir)
    expect(dirty.result.orphans).toEqual(['deep.md', 'deepest.md', 'sub_index.md', 'sub_two.md'])
    expect(dirty.result.reachable).toBe(0)
    expect(dirty.status).toBe(1)
  })

  it('counts the attic separately and never calls a retired memory an orphan', () => {
    const dir = scratchDir('ss-memory-attic-')
    writeStore(dir, { ...CLEAN_STORE, 'attic/retired.md': 'deliberately retired\n' })
    const { result, status } = audit(dir)
    expect(result.attic).toBe(1)
    expect(result.total).toBe(2)
    expect(result.orphans).toEqual([])
    expect(result.ok).toBe(true)
    expect(status).toBe(0)
  })

  it('does not treat MEMORY.md or its compaction backup as memories', () => {
    const dir = scratchDir('ss-memory-notamemory-')
    writeStore(dir, { ...CLEAN_STORE, 'MEMORY.md.bak': 'the pre-compaction copy\n' })
    const { result, status } = audit(dir)
    expect(result.total).toBe(2)
    expect(result.orphans).toEqual([])
    expect(status).toBe(0)
  })
})

describe('memory-audit: the index budget', () => {
  const rows = (n: number) =>
    Array.from({ length: n }, (_, i) => `- [Row ${i}](one.md) - hook`).join('\n')

  it('flags a MEMORY.md over the byte cap, and clears when it is trimmed', () => {
    const dir = scratchDir('ss-memory-bytes-')
    const fat = `# Index\n\n- [One](one.md) - ${'x'.repeat(26000)}\n- [Two](two.md) - hook\n`
    writeStore(dir, { ...CLEAN_STORE, 'MEMORY.md': fat })

    const dirty = audit(dir)
    expect(dirty.result.index.bytes).toBeGreaterThan(dirty.result.index.byteCap)
    expect(dirty.result.index.bytesRemaining).toBeLessThan(0)
    expect(dirty.result.problems.join(' ')).toMatch(/bytes, over the/)
    expect(dirty.result.ok).toBe(false)
    expect(dirty.status).toBe(1)

    // Control: same store, index back under the cap.
    writeStore(dir, { 'MEMORY.md': CLEAN_STORE['MEMORY.md'] })
    const clean = audit(dir)
    expect(clean.result.index.bytes).toBeLessThan(clean.result.index.byteCap)
    expect(clean.result.index.bytesRemaining).toBeGreaterThan(0)
    expect(clean.result.ok).toBe(true)
    expect(clean.status).toBe(0)
  })

  it('flags a MEMORY.md over the line cap while under the byte cap, and clears when trimmed', () => {
    const dir = scratchDir('ss-memory-lines-')
    writeStore(dir, { ...CLEAN_STORE, 'MEMORY.md': `# Index\n\n${rows(250)}\n- [Two](two.md)\n` })

    const dirty = audit(dir)
    expect(dirty.result.index.lines).toBeGreaterThan(dirty.result.index.lineCap)
    // The point of asserting both caps: this is a LINE failure, not a byte one.
    expect(dirty.result.index.bytes).toBeLessThan(dirty.result.index.byteCap)
    expect(dirty.result.problems.join(' ')).toMatch(/lines, over the/)
    expect(dirty.result.ok).toBe(false)
    expect(dirty.status).toBe(1)

    writeStore(dir, { 'MEMORY.md': `# Index\n\n${rows(20)}\n- [Two](two.md)\n` })
    const clean = audit(dir)
    expect(clean.result.index.lines).toBeLessThan(clean.result.index.lineCap)
    expect(clean.result.ok).toBe(true)
    expect(clean.status).toBe(0)
  })

  /**
   * The recommended target and the read limit are different numbers, and conflating
   * them is what went wrong on 2026-09-09: three separate measurements against a flat
   * 25,000 produced "5,131 bytes free" while the operating target said otherwise.
   *
   * Crossing the target WARNS and still exits 0. A soft cue that fails the build gets
   * switched off, which is the primer's own "must not cry wolf" constraint.
   */
  it('warns without failing between the target and the read limit, and is silent below both', () => {
    const dir = scratchDir('ss-memory-target-')
    const betweenThresholds = `# Index\n\n- [One](one.md) - ${'x'.repeat(19000)}\n- [Two](two.md) - hook\n`
    writeStore(dir, { ...CLEAN_STORE, 'MEMORY.md': betweenThresholds })

    const warned = audit(dir)
    expect(warned.result.index.bytes).toBeGreaterThan(warned.result.index.byteTarget)
    expect(warned.result.index.bytes).toBeLessThan(warned.result.index.byteCap)
    expect(warned.result.index.bytesRemaining).toBeLessThan(0)
    expect(warned.result.index.bytesRemainingToReadLimit).toBeGreaterThan(0)
    expect(warned.result.warnings.join(' ')).toMatch(/recommended target/)
    expect(warned.result.problems).toEqual([])
    expect(warned.result.ok).toBe(true)
    expect(warned.status).toBe(0)

    // Control: back under the target, and the warning goes away entirely.
    writeStore(dir, { 'MEMORY.md': CLEAN_STORE['MEMORY.md'] })
    const quiet = audit(dir)
    expect(quiet.result.index.bytes).toBeLessThan(quiet.result.index.byteTarget)
    expect(quiet.result.warnings).toEqual([])
    expect(quiet.result.ok).toBe(true)
    expect(quiet.status).toBe(0)
  })

  it('reports headroom against the target, not the read limit', () => {
    const dir = scratchDir('ss-memory-headroom-')
    writeStore(dir, CLEAN_STORE)
    const r = audit(dir).result
    // The two numbers must differ, or the tool is quietly reporting the wrong one.
    expect(r.index.byteTarget).toBeLessThan(r.index.byteCap)
    expect(r.index.bytesRemaining).toBe(r.index.byteTarget - r.index.bytes)
    expect(r.index.bytesRemainingToReadLimit).toBe(r.index.byteCap - r.index.bytes)
  })
})

describe('memory-audit: failure posture', () => {
  /**
   * The one fail-CLOSED branch, for the engagement-guard reason: "cannot
   * evaluate" must never read as "clean". A missing store is the exact shape a
   * mis-resolved path takes (a worktree-munged sibling directory holds no
   * durable memory), and reporting that as a pass is how the audit would come
   * to certify nothing while looking green.
   */
  it('exits 1 on a missing store rather than reporting it clean', () => {
    const parent = scratchDir('ss-memory-missing-')
    const { result, status } = audit(join(parent, 'does-not-exist'))
    expect(result.missing).toBe(true)
    expect(result.ok).toBe(false)
    expect(status).toBe(1)
  })

  it('exits 1 on a store directory with no MEMORY.md', () => {
    const dir = scratchDir('ss-memory-noindex-')
    writeStore(dir, { 'one.md': 'orphaned by absence of any index\n' })
    const { result, status } = audit(dir)
    expect(result.missing).toBe(true)
    expect(status).toBe(1)
  })

  it('survives malformed markdown without crashing or fabricating findings', () => {
    const dir = scratchDir('ss-memory-malformed-')
    writeStore(dir, {
      ...CLEAN_STORE,
      'MEMORY.md': [
        '# Index',
        '',
        '- [One](one.md) - hook',
        '- [Two](two.md) - hook',
        '- [unclosed link(nope.md',
        '- [nested](a(b).md)',
        '- bare parenthetical (AGENTS.md) is prose, not a link',
        '- [absolute](file:///Users/x/dev/file.md) is outside the store',
        '- [[wikilink-style]] and ]( stray',
        '```',
        '- [fenced](two.md)',
        '```',
        '- [empty]()',
        '',
      ].join('\n'),
    })
    const { result, status, stderr } = audit(dir)
    expect(stderr).not.toMatch(/crashed/)
    expect(result.dangling).toEqual([])
    expect(result.orphans).toEqual([])
    expect(result.ok).toBe(true)
    expect(status).toBe(0)
  })

  it('never exits 2 (it is a report, not a PreToolUse gate)', () => {
    const dir = scratchDir('ss-memory-exit-')
    writeStore(dir, { ...CLEAN_STORE, 'lost.md': 'orphan\n' })
    expect(audit(dir).status).toBe(1)
    expect([...observedStatuses]).not.toContain(2)
  })
})

describe('memory-audit: the built-in falsifier', () => {
  it('--self-test passes, exiting 0 and saying the orphan was detected', () => {
    const r = run(['--self-test'])
    expect(r.status).toBe(0)
    expect(r.stderr).toMatch(/self-test passed/)
    expect(r.stderr).toMatch(/orphan detected/)
  })
})

/**
 * Guard the guard. An expectation set that silently emptied would let every
 * case above pass by measuring nothing: this asserts the suite actually
 * exercised both answers, which is the property the audit itself is for.
 */
describe('memory-audit: the suite exercised both answers', () => {
  it('observed at least one clean exit and at least one failing exit', () => {
    expect(observedStatuses.has(0)).toBe(true)
    expect(observedStatuses.has(1)).toBe(true)
    expect(observedStatuses.size).toBeGreaterThanOrEqual(2)
  })
})
