#!/usr/bin/env node
/**
 * memory-audit.mjs -- reachability audit over the durable memory store.
 *
 *   .claude/bin/memory-audit              # audit the real store
 *   .claude/bin/memory-audit --wiring     # is the SessionStart hook actually firing?
 *   .claude/bin/memory-audit --self-test  # prove the audit can fail
 *
 * WHY THIS EXISTS. On 2026-09-09 an audit of
 * ~/.claude/projects/-Users-scottdurgan-dev-ss-console/memory/ found 60 memory
 * files referenced from no index at all. They had gone unread for weeks and
 * included standing Captain directives and two secret-leak hazards. The cause
 * was mechanical: a prior compaction of MEMORY.md deleted index rows instead of
 * moving them to an archive index, and a memory nothing points at is a memory
 * that no longer loads. Nothing in the system could see it happen. Every layer
 * looked healthy -- the files were on disk, the index parsed, sessions started
 * clean -- because no instrument was ever asked whether the index still reached
 * the store. This audit is that instrument.
 *
 * Reachability is TRANSITIVE from MEMORY.md. A memory linked only from a
 * sub-index (feedback_law12_family_index.md, project_archive_index.md) is
 * reachable: the tier model is the whole point of a sub-index, and an audit
 * that only read the top index would report the archive as 60 orphans and be
 * switched off within a week.
 *
 * Failure posture, matching engagement-guard.mjs:164-165: fail OPEN on a crash
 * (this is hygiene plumbing, not a safety gate) but fail CLOSED on a missing
 * store, because "cannot evaluate" must never read as "clean".
 *
 * Law 12: the audit carries its own falsifier. `--self-test` builds a scratch
 * store with a deliberately unreferenced file, asserts the orphan is detected,
 * then links it and asserts the same store comes back clean. An audit that has
 * never returned the other answer has measured nothing.
 */

import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

/**
 * MEMORY.md loads into every session's context, so it is capped, not merely watched.
 *
 * TWO thresholds, and the difference is load-bearing. The harness states both on any
 * edit to the file: a 24.4KB READ LIMIT, past which the index is truncated or the
 * write errors, and a 17.1KB RECOMMENDED TARGET it asks you to compact under. Headroom
 * is reported against the target, because that is the number that should change a
 * decision.
 *
 * Recorded because it was got wrong three times on 2026-09-09, twice by an agent and
 * once by a critique agent auditing that agent: all three measured against a flat
 * 25,000 from a changelog line, and one concluded "5,131 bytes free" while the
 * operating target said otherwise. A tool that reports headroom against the wrong
 * threshold launders that mistake into every future session.
 */
const READ_LIMIT_BYTES = 24985 // 24.4 KiB
const TARGET_BYTES = 17510 // 17.1 KiB
const BYTE_CAP = READ_LIMIT_BYTES
const LINE_CAP = 200

const INDEX = 'MEMORY.md'
const ATTIC = 'attic'
/** Not memories: the index itself and the backup a compaction leaves behind. */
const NOT_A_MEMORY = new Set([INDEX, 'MEMORY.md.bak'])

/**
 * PROOF OF EXECUTION. `--session-start` speaks only when it has something to
 * say, which is right for signal hygiene and wrong for trust: a clean run and a
 * hook that never fired produce byte-identical output, namely none. The check
 * written to make silent memory loss detectable was itself silent in exactly
 * the way it was built to catch. Measured 2026-09-09: the SessionStart block
 * printed session-peers.sh output and nothing from this hook, and no artifact
 * anywhere could distinguish "clean" from "never ran".
 *
 * So every hook run drops a receipt. Only the hook path writes it -- a manual
 * `memory-audit` deliberately does NOT, because a receipt a human can forge by
 * running the tool proves the tool works, not that the wiring fires.
 *
 * Not a `.md` file, so `listMarkdown` never sees it and it can never be counted
 * as a memory or an orphan.
 */
const RECEIPT_FILE = '.memory-audit-receipt.json'

/**
 * Grace between a session's transcript appearing and its hook receipt landing.
 * Both happen at session start with no guaranteed order, so without this the
 * current session's own transcript is permanent evidence against the hook.
 */
const RECEIPT_GRACE_MS = 120_000

/**
 * Claude Code keys a project's store by the session path with `/` and `.`
 * flattened to `-`. Under a worktree that yields a SIBLING directory
 * (-Users-...-ss-console--claude-worktrees-sos-2026-09-09) which is empty of
 * durable memory: on this machine 40 such siblings exist. Strip the worktree
 * suffix so every session of this repo audits the one real store.
 */
function canonicalProjectPath(cwd) {
  // Greedy to the end, not one segment: EnterWorktree accepts `/`-separated
  // names, so `.claude/worktrees/team/feature-x` is a legal worktree. The
  // single-segment form matched nothing at all for those and stripped nothing,
  // resolving the empty worktree sibling instead of the real store. Probed
  // 2026-09-09.
  return cwd.replace(/\/\.claude\/worktrees\/.*$/, '')
}

function resolveMemoryDir(env = process.env, cwd = process.cwd()) {
  if (env.SS_MEMORY_DIR) return env.SS_MEMORY_DIR
  const munged = canonicalProjectPath(cwd).replace(/[/.]/g, '-')
  return path.join(os.homedir(), '.claude', 'projects', munged, 'memory')
}

/**
 * Markdown link targets ending in .md, anchored on the `](` that makes it a
 * link rather than a parenthetical. A bare `(...)` match reported the prose
 * "can't modify core (AGENTS.md)" as a dangling reference on the first run
 * against the real store: a check whose false positives are indistinguishable
 * from its findings gets switched off.
 *
 * Targets must be store-relative filenames (optionally `./`-prefixed), which
 * is what every index row is. Anything carrying a slash or a scheme addresses
 * something outside the store: the second false positive on the real store was
 * a memory quoting `[file:///Users/.../file.md](file:///Users/.../file.md)` as
 * an example of a clickable path.
 *
 * Deliberately NOT [[wikilinks]]: those are body cross-references between
 * memories, and following them would make almost every file reachable from
 * almost any other, which is precisely the check that cannot fail.
 */
function linkTargets(text) {
  // Fenced blocks are quoted examples, not links. A memory that documents link
  // conventions (several here do) would otherwise have its illustration parsed
  // as a real reference, and a fenced example naming a file that does not exist
  // would report as dangling. Third instance of the same false-positive class.
  const prose = text.replace(/```[\s\S]*?```/g, '')
  const out = []
  for (const m of prose.matchAll(/\]\((?:\.\/)?([A-Za-z0-9._-]+\.md)\)/g)) out.push(m[1])
  return out
}

function listMarkdown(dir) {
  return fs
    .readdirSync(dir, { withFileTypes: true })
    .filter((e) => e.isFile() && e.name.endsWith('.md'))
    .map((e) => e.name)
}

/**
 * @returns {{ok: boolean, missing?: true, dir: string, total: number,
 *   reachable: number, orphans: string[], dangling: string[], attic: number,
 *   index: {bytes: number, lines: number, byteCap: number, byteTarget: number,
 *   lineCap: number, bytesRemaining: number, bytesRemainingToReadLimit: number,
 *   linesRemaining: number}, problems: string[], warnings?: string[]}}
 */
function auditStore(dir) {
  if (!fs.existsSync(dir) || !fs.statSync(dir).isDirectory()) {
    return {
      ok: false,
      missing: true,
      dir,
      total: 0,
      reachable: 0,
      orphans: [],
      dangling: [],
      attic: 0,
      index: {
        bytes: 0,
        lines: 0,
        byteCap: BYTE_CAP,
        byteTarget: TARGET_BYTES,
        lineCap: LINE_CAP,
        bytesRemaining: TARGET_BYTES,
        bytesRemainingToReadLimit: READ_LIMIT_BYTES,
        linesRemaining: LINE_CAP,
      },
      problems: [`memory store not found at ${dir}`],
    }
  }

  const indexPath = path.join(dir, INDEX)
  if (!fs.existsSync(indexPath)) {
    return {
      ok: false,
      missing: true,
      dir,
      total: 0,
      reachable: 0,
      orphans: [],
      dangling: [],
      attic: 0,
      index: {
        bytes: 0,
        lines: 0,
        byteCap: BYTE_CAP,
        byteTarget: TARGET_BYTES,
        lineCap: LINE_CAP,
        bytesRemaining: TARGET_BYTES,
        bytesRemainingToReadLimit: READ_LIMIT_BYTES,
        linesRemaining: LINE_CAP,
      },
      problems: [`no ${INDEX} at ${dir}`],
    }
  }

  const memories = listMarkdown(dir).filter((n) => !NOT_A_MEMORY.has(n))
  const memorySet = new Set(memories)

  const atticDir = path.join(dir, ATTIC)
  const atticFiles = fs.existsSync(atticDir) ? listMarkdown(atticDir) : []
  const atticSet = new Set(atticFiles)

  const indexRaw = fs.readFileSync(indexPath, 'utf8')

  // Fixed-point walk from the index, following only into files that exist as
  // top-level memories. Attic entries are terminal: retired, still addressable.
  const reachable = new Set()
  const referenced = new Set()
  const queue = linkTargets(indexRaw)
  for (const t of queue) referenced.add(t)
  while (queue.length) {
    const name = queue.shift()
    if (!memorySet.has(name) || reachable.has(name)) continue
    reachable.add(name)
    let body = ''
    try {
      body = fs.readFileSync(path.join(dir, name), 'utf8')
    } catch {
      continue // an unreadable memory is still reachable; it just leads nowhere
    }
    for (const t of linkTargets(body)) {
      referenced.add(t)
      if (memorySet.has(t) && !reachable.has(t)) queue.push(t)
    }
  }

  const orphans = memories.filter((n) => !reachable.has(n)).sort()
  /**
   * Dangling asks "does this file exist", which is a DIFFERENT question from
   * "is this file a memory". `memorySet` deliberately excludes MEMORY.md and
   * MEMORY.md.bak so they are never orphan candidates, and reusing it here
   * reported a sub-index's `[back to the index](MEMORY.md)` link as dangling,
   * exit 1, on a store that was perfectly healthy. Probed 2026-09-09.
   */
  const onDisk = new Set([...memorySet, ...NOT_A_MEMORY])
  const dangling = [...referenced].filter((n) => !onDisk.has(n) && !atticSet.has(n)).sort()

  const bytes = Buffer.byteLength(indexRaw, 'utf8')
  const lines = indexRaw.split('\n').length

  const problems = []
  if (orphans.length) problems.push(`${orphans.length} orphan(s): reachable from no index`)
  if (dangling.length) problems.push(`${dangling.length} dangling reference(s): indexed, absent`)
  if (bytes > BYTE_CAP) problems.push(`${INDEX} is ${bytes} bytes, over the ${BYTE_CAP} read limit`)
  if (lines > LINE_CAP) problems.push(`${INDEX} is ${lines} lines, over the ${LINE_CAP} cap`)

  /**
   * Warnings do not fail the audit. Exceeding the recommended target is a compaction
   * cue, not a broken store, and an audit that exits non-zero on a soft cue teaches
   * people to stop reading it.
   */
  const warnings = []
  if (bytes > TARGET_BYTES) {
    warnings.push(
      `${INDEX} is ${bytes} bytes, over the ${TARGET_BYTES} recommended target ` +
        `(read limit is ${READ_LIMIT_BYTES}); archive a settled row WITH its annotation`
    )
  }

  return {
    ok: problems.length === 0,
    dir,
    total: memories.length,
    reachable: reachable.size,
    orphans,
    dangling,
    attic: atticFiles.length,
    index: {
      bytes,
      lines,
      byteCap: BYTE_CAP,
      byteTarget: TARGET_BYTES,
      lineCap: LINE_CAP,
      bytesRemaining: TARGET_BYTES - bytes,
      bytesRemainingToReadLimit: READ_LIMIT_BYTES - bytes,
      linesRemaining: LINE_CAP - lines,
    },
    problems,
    warnings,
  }
}

function writeReceipt(dir, result, now = Date.now()) {
  try {
    fs.writeFileSync(
      path.join(dir, RECEIPT_FILE),
      JSON.stringify(
        { at: new Date(now).toISOString(), ok: result.ok, total: result.total, pid: process.pid },
        null,
        2
      ) + '\n'
    )
  } catch {
    // Best effort. A store we cannot write to is a finding for the audit
    // proper, not a reason to fail a session start on hygiene plumbing.
  }
}

function readReceipt(dir) {
  try {
    const raw = JSON.parse(fs.readFileSync(path.join(dir, RECEIPT_FILE), 'utf8'))
    const at = Date.parse(raw.at)
    return Number.isFinite(at) ? { ...raw, atMs: at } : null
  } catch {
    return null
  }
}

/**
 * Is the SessionStart hook actually firing?
 *
 * The receipt alone cannot answer that: if the hook stops running, the receipt
 * stops updating, and a check that reads only the receipt sees a quiet store
 * and calls it quiet. Circular. The non-circular witness is the transcript
 * directory -- Claude Code writes one `.jsonl` per session into the store's
 * parent whether or not any hook fires, so a session that STARTED after the
 * last receipt is a session the hook did not serve.
 *
 * birthtime, not mtime: the live session appends to its transcript constantly,
 * so its mtime is always newer than any receipt and mtime would report every
 * healthy session as a failure.
 */
function auditWiring(dir, now = Date.now(), projectDir = path.dirname(dir)) {
  const receipt = readReceipt(dir)
  let sessions = []
  try {
    sessions = fs
      .readdirSync(projectDir, { withFileTypes: true })
      .filter((e) => e.isFile() && e.name.endsWith('.jsonl'))
      .map((e) => {
        const s = fs.statSync(path.join(projectDir, e.name))
        return { name: e.name, startedMs: (s.birthtime ?? s.mtime).getTime() }
      })
  } catch {
    return {
      ok: true,
      unknown: true,
      message: `wiring: cannot check (no transcript directory at ${projectDir})`,
    }
  }

  if (!receipt) {
    return {
      ok: false,
      lastRun: null,
      unserved: sessions.length,
      message:
        `wiring: the SessionStart hook has NEVER recorded a run (no ${RECEIPT_FILE}), ` +
        `across ${sessions.length} session transcript(s)`,
    }
  }

  const unserved = sessions.filter((s) => s.startedMs > receipt.atMs + RECEIPT_GRACE_MS)
  if (unserved.length) {
    return {
      ok: false,
      lastRun: receipt.at,
      unserved: unserved.length,
      message:
        `wiring: ${unserved.length} session(s) started after the hook's last run (${receipt.at}) -- ` +
        `the SessionStart entry in .claude/settings.json is not firing`,
    }
  }

  return {
    ok: true,
    lastRun: receipt.at,
    unserved: 0,
    message: `wiring: hook last ran ${receipt.at}, no session has started unserved since`,
  }
}

function summarize(r) {
  const head = r.missing
    ? `memory-audit: STORE MISSING (${r.dir})`
    : `memory-audit: ${r.ok ? 'clean' : 'PROBLEMS'} -- ${r.reachable}/${r.total} reachable, ` +
      `${r.attic} in attic, ${INDEX} ${r.index.bytes}b/${r.index.lines}L ` +
      `(${r.index.bytesRemaining}b to the ${r.index.byteTarget}b target, ` +
      `${r.index.linesRemaining}L headroom)`
  const lines = [head]
  for (const p of r.problems) lines.push(`  ! ${p}`)
  for (const w of r.warnings ?? []) lines.push(`  ~ ${w}`)
  if (r.wiring) lines.push(`  ${r.wiring.ok ? '·' : '!'} ${r.wiring.message}`)
  for (const o of r.orphans.slice(0, 20)) lines.push(`    orphan: ${o}`)
  if (r.orphans.length > 20) lines.push(`    ... and ${r.orphans.length - 20} more`)
  for (const d of r.dangling.slice(0, 20)) lines.push(`    dangling: ${d}`)
  if (r.dangling.length > 20) lines.push(`    ... and ${r.dangling.length - 20} more`)
  return lines.join('\n') + '\n'
}

/**
 * The falsifier. Both directions on purpose: an audit that only proves it can
 * flag a bad store has not shown that it can pass a good one, and a check that
 * flags everything is as useless as one that flags nothing.
 */
function selfTest() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'memory-audit-selftest-'))
  const failures = []
  try {
    fs.writeFileSync(path.join(dir, 'linked.md'), 'a linked memory\n')
    fs.writeFileSync(path.join(dir, 'orphan.md'), 'nothing points at this\n')
    fs.writeFileSync(path.join(dir, INDEX), '# Index\n\n- [Linked](linked.md)\n')

    const dirty = auditStore(dir)
    if (!dirty.orphans.includes('orphan.md'))
      failures.push('deliberate orphan was NOT detected (the audit cannot fail)')
    if (dirty.ok) failures.push('store with an orphan reported ok')

    // Control: link the orphan and the same store must come back clean.
    fs.writeFileSync(
      path.join(dir, INDEX),
      '# Index\n\n- [Linked](linked.md)\n- [Orphan](orphan.md)\n'
    )
    const clean = auditStore(dir)
    if (clean.orphans.length) failures.push(`linked store still reported orphans: ${clean.orphans}`)
    if (!clean.ok) failures.push(`linked store reported problems: ${clean.problems}`)

    /**
     * The wiring check gets its own falsifier, and it needs one more than the
     * orphan check does: its failure mode is silence, so a wiring check that
     * cannot report "not firing" is indistinguishable from the bug it exists
     * to catch. Three states, all three asserted.
     */
    const projectDir = fs.mkdtempSync(path.join(os.tmpdir(), 'memory-audit-sessions-'))
    try {
      const never = auditWiring(dir, Date.now(), projectDir)
      if (never.ok) failures.push('a store with no receipt reported the hook as firing')

      // A session transcript that starts well after the last receipt is the
      // signature of a hook that has stopped running.
      fs.writeFileSync(path.join(projectDir, 'session.jsonl'), '{}\n')
      writeReceipt(dir, clean, Date.now() - 60 * 60 * 1000)
      const stale = auditWiring(dir, Date.now(), projectDir)
      if (stale.ok) failures.push('an unserved session did NOT trip the wiring check')
      if (stale.unserved !== 1) failures.push(`expected 1 unserved session, got ${stale.unserved}`)

      // Control: the same session, once the hook has run for it, is clean.
      writeReceipt(dir, clean, Date.now())
      const served = auditWiring(dir, Date.now(), projectDir)
      if (!served.ok) failures.push(`a served session still reported unwired: ${served.message}`)
    } finally {
      fs.rmSync(projectDir, { recursive: true, force: true })
    }
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }

  if (failures.length) {
    process.stderr.write(`memory-audit --self-test FAILED\n${failures.map((f) => `  ! ${f}`).join('\n')}\n`)
    return 1
  }
  process.stderr.write('memory-audit --self-test passed (orphan detected, wiring gap detected, controls clean)\n')
  return 0
}

function main() {
  const argv = process.argv.slice(2)
  if (argv.includes('--self-test')) return selfTest()

  const dirArg = argv.find((a) => !a.startsWith('-'))
  const dir = dirArg ?? resolveMemoryDir()
  const result = auditStore(dir)

  /**
   * --session-start is the SessionStart wiring, and it speaks ONLY when it has
   * something to say. A registry entry claiming "the audit reports zero orphans
   * across the sessions between now and the review date" is unmeasurable unless
   * something actually runs it, and a report nobody runs is the built-not-wired
   * failure this venture calls Law 9. It also stays silent when clean, matching
   * session-peers.sh, because a line that fires on every session and says
   * nothing new is how agents learn to skim the startup block.
   */
  if (argv.includes('--session-start')) {
    // The receipt is written BEFORE the early return, and on every run whatever
    // the verdict: its claim is "the hook fired", which is true even of a run
    // that found problems and especially of one that found nothing to say.
    if (!result.missing) writeReceipt(dir, result)
    if (!result.ok || result.warnings?.length) process.stderr.write(summarize(result))
    return 0 // never fail a session start on hygiene plumbing
  }

  // Manual runs report the wiring but never stamp it: a receipt a human can
  // mint by running the tool is not evidence that the hook runs itself.
  result.wiring = auditWiring(dir)

  if (argv.includes('--wiring')) {
    process.stdout.write(JSON.stringify(result.wiring, null, 2) + '\n')
    process.stderr.write(`memory-audit ${result.wiring.message}\n`)
    return result.wiring.ok ? 0 : 1
  }

  process.stdout.write(JSON.stringify(result, null, 2) + '\n')
  process.stderr.write(summarize(result))
  return result.ok ? 0 : 1
}

// process.exitCode rather than process.exit(): exit() can truncate a piped
// stdout mid-write, and this tool's stdout IS the machine-readable result.
try {
  process.exitCode = main()
} catch (err) {
  // Fail open on crashes: hygiene plumbing, not a safety gate.
  process.stderr.write(`memory-audit: crashed, treating as clean (${err?.message ?? err})\n`)
  process.exitCode = 0
}
