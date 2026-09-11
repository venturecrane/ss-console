#!/usr/bin/env node
/**
 * trap-recurrence.mjs -- did the traps in MEMORY.md still fire after they were
 * written down?
 *
 *   .claude/bin/trap-recurrence               # scan since the traps block shipped
 *   .claude/bin/trap-recurrence --since DATE  # scan a different window
 *   .claude/bin/trap-recurrence --self-test   # prove the counter can fail
 *
 * WHY THIS EXISTS. The traps block in MEMORY.md pre-registers its own
 * experiment: "On/after 2026-09-23: check whether any of these still recurred;
 * if so a delivery hook is justified, if not this tier is enough." On
 * 2026-09-09 that was a dated decision rule with no instrument and nothing
 * scheduled -- the same shape as the silent SessionStart hook fixed in #2724,
 * where a criterion nobody could measure read as a criterion that was being
 * met. This is the missing instrument.
 *
 * THE DISTINCTION THAT MAKES IT MEAN ANYTHING. "The trap recurred" is not "the
 * condition arose". A BEHIND pull request is not a failure; a BEHIND pull
 * request that an agent responded to by re-running `gh pr merge` is. So every
 * rule below records an ENCOUNTER (the condition appeared in tool output) and
 * asks whether the documented remedy followed within a short window. The
 * decision number for 2026-09-23 is the STUMBLE count, not the encounter count:
 * encounters prove the traps are still live terrain, stumbles prove the index
 * tier failed to deliver.
 *
 * WHAT IT CANNOT SEE, stated because a check whose blind spots are undeclared
 * gets read as complete (Law 12):
 *   - Traps that fire inside a subagent whose transcript is not in this project
 *     directory.
 *   - Any trap whose remedy is a judgement rather than a call: T7's real
 *     signature is a wrong ANSWER from a mutation test, which no grep can see,
 *     so T7 reports hygiene (was bytecode caching disabled) rather than harm.
 *   - Sessions before the transcripts on disk, and any session whose transcript
 *     the harness pruned.
 * Each is reported in `blindSpots` on every run rather than left to the reader.
 */

import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

/** The traps block shipped with the delivery-tier rewrite on this date. */
const DEFAULT_SINCE = '2026-09-09'
/** Tool calls after an encounter within which the remedy still counts as a response. */
const REMEDY_WINDOW = 6

function canonicalProjectPath(cwd) {
  return cwd.replace(/\/\.claude\/worktrees\/.*$/, '')
}

function resolveProjectDir(env = process.env, cwd = process.cwd()) {
  if (env.SS_TRANSCRIPT_DIR) return env.SS_TRANSCRIPT_DIR
  const munged = canonicalProjectPath(cwd).replace(/[/.]/g, '-')
  return path.join(os.homedir(), '.claude', 'projects', munged)
}

/**
 * One rule per trap in MEMORY.md's "Traps that fire at a specific call" block,
 * in the same order, each naming the memory it belongs to so a finding leads
 * straight to the mechanism rather than to a slug.
 *
 * `encounter(step)` says the condition appeared. `remedy(step)` says this later
 * step is the documented response. `direct: true` marks a trap whose only
 * detectable signature IS the wrong move, so the encounter and the stumble are
 * the same event and no window is consulted.
 */
const TRAPS = [
  {
    id: 'gh-merge-aborting',
    memory: 'reference_gh_pr_merge_reports_aborting_after_the_remote_merge_succeeded.md',
    what: '`gh pr merge` printed Aborting after the remote merge had already succeeded',
    encounter: (s) => /gh pr merge/.test(s.command) && /Aborting/i.test(s.output),
    // The documented response is to ask the REMOTE what happened, not to retry.
    remedy: (s) => /gh pr view[^\n]*\b(state|mergedAt|mergeCommit|--json)/.test(s.command),
    stumbleAlso: (s) => /gh pr merge/.test(s.command),
  },
  {
    id: 'behind-pr',
    memory: 'reference_a_behind_pr_needs_update_branch_then_auto.md',
    what: 'a sibling merge left the PR BEHIND, where `gh pr merge` merges nothing',
    encounter: (s) => /BEHIND/.test(s.output) && /gh pr (view|merge|checks)/.test(s.command),
    remedy: (s) => /gh pr update-branch/.test(s.command),
  },
  {
    id: 'checks-stopped-firing',
    memory: 'reference_pr_checks_stop_firing_rebase_not_reopen.md',
    what: 'a PR was closed and reopened to restart checks instead of rebased',
    direct: true,
    encounter: (s) => /gh pr reopen/.test(s.command),
  },
  {
    id: 'pgrep-own-shell',
    memory: 'feedback_pgrep_f_matches_your_own_shell.md',
    what: '`pgrep -f` can resolve to the shell running it, so the environ read describes your own process',
    encounter: (s) => /pgrep\s+-[a-z]*f/.test(s.command),
    /**
     * A bare `pgrep -f` is not the failure -- the failure is trusting the pid it
     * returns. So the stumble REQUIRES the downstream read the memory names.
     * Without this the rule scored 58 encounters and 58 stumbles on history: a
     * check that cannot pass is as empty as one that cannot fail, and it would
     * have handed the 2026-09-23 review a guaranteed RECURRED.
     */
    stumbleRequires: (s) => /\/proc\/[^/\s]*\/environ|\bps\s+e\b/.test(s.command),
    remedy: (s) => /(grep -v|--exclude|\$\$|\[\[:alnum:\]\])/.test(s.command),
  },
  {
    id: 'gmail-stale-draft-id',
    memory: 'reference_gmail_create_draft_id_goes_stale_use_list_drafts.md',
    what: 'update_draft was called without taking the live draft id from list_drafts first',
    direct: true,
    encounter: (s) => s.tool.endsWith('update_draft') && !s.sessionSawListDraftsBefore,
  },
  {
    id: 'gmail-draft-detaches-thread',
    memory: 'reference_gmail_update_draft_drops_reply_threading.md',
    what: 'update_draft can detach a reply from its thread and drop attachments',
    encounter: (s) => s.tool.endsWith('update_draft'),
    remedy: (s) => /get_draft|list_drafts|get_thread/.test(s.tool),
  },
  {
    id: 'stale-pyc',
    memory: 'reference_stale_pyc_can_invert_a_mutation_test.md',
    what: 'a .py file was re-executed after being edited, with bytecode caching left on',
    /**
     * The signature is a MUTATION PASS -- run, edit, run the same file again --
     * not "this session touched Python". The first draft latched on the first
     * .py edit and counted every later python invocation, scoring 1,128
     * encounters and 1,056 stumbles across history and drowning the other seven
     * traps. Noise a reader cannot separate from findings is how a report gets
     * switched off, which is the failure mode this whole review exists to avoid.
     */
    encounter: (s) => s.pyMutationRun,
    remedy: (s) =>
      /PYTHONDONTWRITEBYTECODE/.test(s.command) || /__pycache__/.test(s.command),
  },
  {
    id: 'fly-eats-stdin',
    memory: 'feedback_a_fly_call_inside_a_read_loop_eats_the_loops_stdin.md',
    what: 'a `fly` call inside a `while read` loop consumes the loop stdin, so it runs once',
    direct: true,
    encounter: (s) =>
      /while[^\n]*\bread\b/.test(s.command) &&
      /\bfly\s/.test(s.command) &&
      !/<\s*\/dev\/null/.test(s.command),
  },
]

const BLIND_SPOTS = [
  'traps fired inside a subagent whose transcript is not in this project directory',
  'stale-pyc reports hygiene (was caching disabled), not the wrong answer it can cause',
  'sessions whose transcript the harness has pruned',
]

function textOf(content) {
  if (Array.isArray(content)) return content.map((c) => c?.text ?? '').join('\n')
  if (typeof content === 'string') return content
  return ''
}

/**
 * Flatten one transcript into an ordered list of steps: a tool call paired with
 * the result that came back. Pairing is by tool_use_id, because results arrive
 * in a later message and interleave once calls run in parallel -- matching by
 * position instead reported one call's output against another's command, which
 * is how a counter invents findings.
 */
function stepsOf(file) {
  const calls = new Map()
  const order = []
  let sawListDrafts = false
  /** file -> {ran: boolean, editedAfterRun: boolean}, per .py basename. */
  const py = new Map()
  const pyFiles = (text) => [...text.matchAll(/([\w./-]+\.py)\b/g)].map((m) => path.basename(m[1]))

  for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
    if (!line.trim()) continue
    let rec
    try {
      rec = JSON.parse(line)
    } catch {
      continue // a partially flushed final line is normal on a live session
    }
    const content = rec?.message?.content
    if (!Array.isArray(content)) continue

    for (const block of content) {
      if (block?.type === 'tool_use') {
        const input = block.input ?? {}
        const command = [input.command, input.file_path, input.pattern, input.path]
          .filter(Boolean)
          .join(' ')
        const name = block.name ?? ''
        const isEdit =
          /^(Write|Edit|NotebookEdit)$/.test(name) || /(cat\s*>|tee\s|sed -i)/.test(command)
        const isRun = /\bpython3?\b|\bpytest\b|\.venv\/bin\/python/.test(command)

        // A mutation run is: this .py has been executed before, was edited
        // since that run, and is being executed again.
        let pyMutationRun = false
        for (const f of pyFiles(command)) {
          const st = py.get(f) ?? { ran: false, editedAfterRun: false }
          if (isRun && st.ran && st.editedAfterRun) pyMutationRun = true
          py.set(f, st)
        }

        const step = {
          tool: name,
          command,
          output: '',
          sessionSawListDraftsBefore: sawListDrafts,
          pyMutationRun,
        }
        if (/list_drafts/.test(name)) sawListDrafts = true
        for (const f of pyFiles(command)) {
          const st = py.get(f)
          if (isRun) {
            st.ran = true
            st.editedAfterRun = false
          } else if (isEdit && st.ran) {
            st.editedAfterRun = true
          }
        }
        calls.set(block.id, step)
        order.push(step)
      } else if (block?.type === 'tool_result') {
        const step = calls.get(block.tool_use_id)
        if (step) step.output = textOf(block.content)
      }
    }
  }
  return order
}

function scanFile(file) {
  const steps = stepsOf(file)
  const hits = []
  for (const trap of TRAPS) {
    for (let i = 0; i < steps.length; i++) {
      let isEncounter = false
      try {
        isEncounter = trap.encounter(steps[i])
      } catch {
        continue
      }
      if (!isEncounter) continue

      if (trap.direct) {
        hits.push({ trap: trap.id, stumble: true, at: i })
        continue
      }
      const window = steps.slice(i + 1, i + 1 + REMEDY_WINDOW)
      const handled = window.some((s) => {
        try {
          return trap.remedy(s)
        } catch {
          return false
        }
      })
      const matches = (fn) =>
        Boolean(fn) &&
        window.some((s) => {
          try {
            return fn(s)
          } catch {
            return false
          }
        })
      // A trap with `stumbleRequires` only counts when the named harm actually
      // follows; without it, an unremedied encounter is the stumble.
      const worsened = matches(trap.stumbleAlso)
      const stumble = trap.stumbleRequires
        ? matches(trap.stumbleRequires) && !handled
        : !handled || worsened
      hits.push({ trap: trap.id, stumble, at: i })
    }
  }
  return hits
}

function scan(projectDir, sinceISO) {
  if (!fs.existsSync(projectDir)) {
    return { ok: false, missing: true, dir: projectDir, problems: [`no transcripts at ${projectDir}`] }
  }
  const since = Date.parse(sinceISO)
  const files = fs
    .readdirSync(projectDir, { withFileTypes: true })
    .filter((e) => e.isFile() && e.name.endsWith('.jsonl'))
    .map((e) => {
      const full = path.join(projectDir, e.name)
      const st = fs.statSync(full)
      return { full, name: e.name, startedMs: (st.birthtime ?? st.mtime).getTime() }
    })
  const inWindow = files.filter((f) => f.startedMs >= since)

  const byTrap = Object.fromEntries(
    TRAPS.map((t) => [t.id, { encounters: 0, stumbles: 0, sessions: [], what: t.what, memory: t.memory }])
  )
  for (const f of inWindow) {
    let hits = []
    try {
      hits = scanFile(f.full)
    } catch {
      continue // an unreadable transcript is a gap, not a finding
    }
    for (const h of hits) {
      const row = byTrap[h.trap]
      row.encounters += 1
      if (h.stumble) {
        row.stumbles += 1
        if (!row.sessions.includes(f.name)) row.sessions.push(f.name)
      }
    }
  }

  const stumbles = Object.values(byTrap).reduce((n, r) => n + r.stumbles, 0)
  const encounters = Object.values(byTrap).reduce((n, r) => n + r.encounters, 0)
  return {
    ok: true,
    dir: projectDir,
    since: sinceISO,
    sessionsScanned: inWindow.length,
    sessionsOnDisk: files.length,
    encounters,
    stumbles,
    /**
     * The verdict the 2026-09-23 review is registered to act on. Deliberately
     * NOT computed from encounters: a trap whose condition arose and was
     * handled correctly is the index tier working, which is the outcome the
     * experiment was written to detect.
     */
    verdict:
      stumbles > 0
        ? 'RECURRED -- a delivery hook is justified'
        : encounters > 0
          ? 'HELD -- the conditions arose and were handled; this tier is enough'
          : 'NO EVIDENCE -- none of the trap conditions arose in the window',
    byTrap,
    blindSpots: BLIND_SPOTS,
  }
}

function summarize(r) {
  if (r.missing) return `trap-recurrence: ${r.problems.join('; ')}\n`
  const lines = [
    `trap-recurrence: ${r.verdict}`,
    `  window: since ${r.since}, ${r.sessionsScanned}/${r.sessionsOnDisk} session(s) on disk`,
    `  ${r.encounters} encounter(s), ${r.stumbles} stumble(s)`,
  ]
  for (const [id, row] of Object.entries(r.byTrap)) {
    if (!row.encounters) continue
    const mark = row.stumbles ? '!' : '·'
    lines.push(`  ${mark} ${id}: ${row.encounters} encounter(s), ${row.stumbles} stumble(s)`)
    if (row.stumbles) lines.push(`      ${row.what}`)
  }
  lines.push(`  blind spots: ${r.blindSpots.length} (see JSON)`)
  return lines.join('\n') + '\n'
}

/**
 * The falsifier. A counter that has only ever returned zero is indistinguishable
 * from a counter that cannot count, and zero is the answer this one is under the
 * most pressure to return -- it is the answer that closes the review quietly.
 * So: a synthetic transcript with one planted stumble and one planted correctly
 * handled encounter, asserting that the first is caught and the second is not.
 */
function selfTest() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'trap-recurrence-selftest-'))
  const failures = []
  const msg = (blocks) => JSON.stringify({ message: { content: blocks } }) + '\n'
  const use = (id, name, input) => ({ type: 'tool_use', id, name, input })
  const res = (id, text) => ({ type: 'tool_result', tool_use_id: id, content: [{ text }] })

  try {
    // A stumble: BEHIND observed, and the agent never runs update-branch.
    fs.writeFileSync(
      path.join(dir, 'stumble.jsonl'),
      msg([use('a1', 'Bash', { command: 'gh pr view 1 --json mergeStateStatus' })]) +
        msg([res('a1', '{"mergeStateStatus":"BEHIND"}')]) +
        msg([use('a2', 'Bash', { command: 'gh pr merge 1 --squash' })]) +
        msg([res('a2', '')])
    )
    // Handled: same condition, followed by the documented remedy.
    fs.writeFileSync(
      path.join(dir, 'handled.jsonl'),
      msg([use('b1', 'Bash', { command: 'gh pr view 2 --json mergeStateStatus' })]) +
        msg([res('b1', '{"mergeStateStatus":"BEHIND"}')]) +
        msg([use('b2', 'Bash', { command: 'gh pr update-branch 2' })]) +
        msg([res('b2', 'PR branch updated')])
    )

    const r = scan(dir, '2000-01-01')
    const behind = r.byTrap['behind-pr']
    if (behind.encounters !== 2)
      failures.push(`expected 2 encounters of behind-pr, got ${behind.encounters}`)
    if (behind.stumbles !== 1)
      failures.push(`expected exactly 1 stumble (the handled case must NOT count), got ${behind.stumbles}`)
    if (r.verdict.startsWith('NO EVIDENCE')) failures.push('planted stumble produced NO EVIDENCE')

    // Control: delete the stumble and the same scanner must report HELD.
    fs.rmSync(path.join(dir, 'stumble.jsonl'))
    const clean = scan(dir, '2000-01-01')
    if (clean.stumbles !== 0) failures.push(`handled-only store still reported ${clean.stumbles} stumble(s)`)
    if (!clean.verdict.startsWith('HELD')) failures.push(`handled-only store reported: ${clean.verdict}`)
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }

  if (failures.length) {
    process.stderr.write(
      `trap-recurrence --self-test FAILED\n${failures.map((f) => `  ! ${f}`).join('\n')}\n`
    )
    return 1
  }
  process.stderr.write(
    'trap-recurrence --self-test passed (planted stumble caught, handled encounter not counted)\n'
  )
  return 0
}

function main() {
  const argv = process.argv.slice(2)
  if (argv.includes('--self-test')) return selfTest()

  const sinceIdx = argv.indexOf('--since')
  const since = sinceIdx >= 0 ? argv[sinceIdx + 1] : DEFAULT_SINCE
  if (Number.isNaN(Date.parse(since))) {
    process.stderr.write(`trap-recurrence: --since ${since} is not a date\n`)
    return 1
  }
  const dirArg = argv.find((a, i) => !a.startsWith('-') && argv[i - 1] !== '--since')
  const dir = dirArg ?? resolveProjectDir()
  const result = scan(dir, since)

  process.stdout.write(JSON.stringify(result, null, 2) + '\n')
  process.stderr.write(summarize(result))
  // Stumbles are the finding the review acts on; a clean window exits 0.
  return result.ok && result.stumbles === 0 ? 0 : 1
}

try {
  process.exitCode = main()
} catch (err) {
  // Fail LOUD, unlike memory-audit: this tool exists to answer a scheduled
  // question, and a crash that exits 0 answers it wrongly.
  process.stderr.write(`trap-recurrence: crashed (${err?.message ?? err})\n`)
  process.exitCode = 1
}
