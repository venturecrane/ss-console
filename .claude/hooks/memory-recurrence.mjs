#!/usr/bin/env node
/**
 * memory-recurrence.mjs -- is a saved lesson still being stumbled into, and is
 * the Captain still having to correct it?
 *
 *   .claude/bin/memory-recurrence               # each memory since its trigger_since
 *   .claude/bin/memory-recurrence --since DATE  # one floor for every memory
 *   .claude/bin/memory-recurrence --json
 *   .claude/bin/memory-recurrence --self-test   # prove the counter can fail
 *
 * Per triggered memory (lib/situational-memory.mjs), over every transcript of
 * this repo, the canonical directory AND every worktree's:
 *
 *   STUMBLES     assistant messages whose own words match a trigger_reply.
 *                Encounters, in trap-recurrence's terms: the condition arose.
 *   BOUNCED      stumbles whose matched sentence hash is in the fire log
 *                (reply or gmail stage): the hook caught it before it stood.
 *   ESCAPED      stumbles with no such fire: the sentence stood. An
 *                intermediate message followed by tool calls is never seen by
 *                the Stop hook, so it lands here, which is correct: the
 *                Captain read it.
 *   CORRECTIONS  Captain prompts that match the memory's trigger_prompt AND
 *                correction language ("again", "50th time", "told you").
 *                The headline, because it does not depend on the reply regex:
 *                a paraphrase the trigger misses is invisible to STUMBLES and
 *                ESCAPED but not to the Captain repeating himself.
 *
 * Exit 0 when nothing escaped and no correction recurred, 1 otherwise or when
 * nothing can be read. Blind spots are printed on every run (Law 12): subagent
 * transcripts outside these directories, sessions the harness pruned, and a
 * bounce whose sentence the hook split differently than this reader does.
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import {
  loadTriggered,
  matchReply,
  readFires,
  resolveMemoryDir,
  transcriptDirs,
} from './lib/situational-memory.mjs'

const DEFAULT_SINCE = '2026-10-29'
const CORRECTION =
  /\b(again|(?:\d+|fiftieth|third|fourth|hundredth)(?:st|nd|rd|th)? time|how many times|told you|i (?:have|'ve) (?:said|corrected)|stop (?:doing|saying))\b/i

export const BLIND_SPOTS = [
  'subagent transcripts outside this repo’s project directories',
  'sessions whose transcript the harness pruned',
  'a bounce whose sentence the hook split differently than this reader (reads as ESCAPED)',
]

function textOf(content) {
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return ''
  return content
    .filter((c) => c?.type === 'text')
    .map((c) => c.text ?? '')
    .join('\n')
}

/** A user record that is the Captain typing, not a tool result, hook echo or command wrapper. */
function captainPrompt(rec) {
  if (rec.type !== 'user' || rec.isMeta) return ''
  const c = rec.message?.content
  if (Array.isArray(c) && c.some((x) => x?.type === 'tool_result')) return ''
  if (rec.isCompactSummary) return ''
  const t = textOf(c).trim()
  // Compaction summaries and peer/teammate messages arrive as user records but
  // are not the Captain speaking; counted, they made the 09-23..10-29 window
  // read 44 re-corrections where the Captain had typed one.
  if (t.startsWith('<') || /^(This session is being continued|Another Claude session sent a message)/.test(t)) return ''
  if (/<(teammate|cross-session)-message\b/.test(t)) return ''
  return t
}

function* records(file) {
  for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
    if (!line.trim()) continue
    try {
      yield JSON.parse(line)
    } catch {
      /* a partially flushed final line is normal on a live session */
    }
  }
}

export function scan({ dirs, memories, fires, since }) {
  // One fire catches one occurrence, in the session it fired in. Keyed on the
  // hash alone, a bounced sentence repeated word for word in the restatement
  // (which reaches the Captain) also read as BOUNCED, hiding the exact escape
  // this counter exists to find.
  const caught = new Map()
  for (const f of fires) {
    if (f.stage !== 'reply' && f.stage !== 'gmail') continue
    const key = `${f.session ?? ''}:${f.hash}`
    caught.set(key, (caught.get(key) ?? 0) + 1)
  }
  const rows = memories.map((m) => ({
    memory: m.name,
    file: m.file,
    since: since ?? m.since ?? DEFAULT_SINCE,
    stumbles: 0,
    bounced: 0,
    escaped: 0,
    corrections: 0,
    escapedAt: [],
  }))
  const floor = Math.min(...rows.map((r) => Date.parse(r.since)).filter(Number.isFinite), Date.now())
  const files = transcriptFiles(dirs, floor)
  for (const f of files) {
    try {
      for (const rec of records(f.full)) tally(rec, f.name, memories, rows, caught)
    } catch {
      /* an unreadable transcript is a gap, not a finding */
    }
  }
  const escaped = rows.reduce((a, r) => a + r.escaped, 0)
  const corrections = rows.reduce((a, r) => a + r.corrections, 0)
  return {
    ok: escaped === 0 && corrections === 0,
    dirs: dirs.length,
    files: files.length,
    escaped,
    corrections,
    rows,
    blindSpots: BLIND_SPOTS,
  }
}

/** Transcript files modified on or after `floor`, across every directory. */
function transcriptFiles(dirs, floor) {
  const out = []
  for (const dir of dirs) {
    let names
    try {
      names = fs.readdirSync(dir).filter((n) => n.endsWith('.jsonl'))
    } catch {
      continue
    }
    for (const n of names) {
      const full = path.join(dir, n)
      try {
        if (fs.statSync(full).mtimeMs >= floor) out.push({ full, name: n })
      } catch {
        /* vanished between list and stat */
      }
    }
  }
  return out
}

/** Apply one transcript record to every memory's row. */
function tally(rec, file, memories, rows, caught) {
  const at = Date.parse(rec.timestamp ?? '')
  if (!Number.isFinite(at)) return
  const isReply = rec.type === 'assistant'
  const prompt = isReply ? '' : captainPrompt(rec)
  const reply = isReply ? textOf(rec.message?.content) : ''
  memories.forEach((mem, i) => {
    const row = rows[i]
    if (at < Date.parse(row.since)) return
    if (prompt) {
      if (CORRECTION.test(prompt) && mem.triggers.prompt.some((re) => re.test(prompt))) row.corrections++
      return
    }
    const hit = reply ? matchReply(reply, [mem])[0] : null
    if (!hit) return
    row.stumbles++
    const key = `${file.replace(/\.jsonl$/, '')}:${hit.hash}`
    const left = caught.get(key) ?? 0
    if (left > 0) {
      caught.set(key, left - 1)
      row.bounced++
    } else {
      row.escaped++
      if (row.escapedAt.length < 5) row.escapedAt.push(`${rec.timestamp} ${file.slice(0, 8)}: ${hit.sentence.slice(0, 120)}`)
    }
  })
}

function selfTest() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'memory-recurrence-'))
  try {
    const store = path.join(tmp, 'memory')
    const tdir = path.join(tmp, 'transcripts')
    fs.mkdirSync(store)
    fs.mkdirSync(tdir)
    fs.writeFileSync(
      path.join(store, 'feedback_x.md'),
      "---\nname: feedback_x\ndescription: never say widget\ntrigger_reply: '\\bwidget\\b'\ntrigger_prompt: '\\bgadget\\b'\ntrigger_since: '2026-01-01'\n---\nbody\n",
    )
    const mems = loadTriggered(store)
    const line = (type, text, ts) =>
      JSON.stringify({ type, timestamp: ts, message: { content: [{ type: 'text', text }] } })
    fs.writeFileSync(
      path.join(tdir, 's.jsonl'),
      [
        line('assistant', 'We shipped the widget.', '2026-02-01T00:00:00Z'),
        line('assistant', 'Another widget here.', '2026-02-01T00:01:00Z'),
        line('user', 'the gadget again?', '2026-02-01T00:02:00Z'),
        line('assistant', 'A clean reply.', '2026-02-01T00:03:00Z'),
      ].join('\n'),
    )
    const caughtHash = matchReply('Another widget here.', mems)[0].hash
    const r = scan({ dirs: [tdir], memories: mems, fires: [{ stage: 'reply', session: 's', hash: caughtHash }] })
    const row = r.rows[0]
    const pass = row.stumbles === 2 && row.bounced === 1 && row.escaped === 1 && row.corrections === 1 && !r.ok
    const clean = scan({ dirs: [tdir], memories: mems, fires: [], since: '2027-01-01' })
    const ok = pass && clean.ok && clean.rows[0].stumbles === 0
    process.stdout.write(`memory-recurrence self-test: ${ok ? 'PASS' : 'FAIL'} ${JSON.stringify({ row, cleanOk: clean.ok })}\n`)
    return ok
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true })
  }
}

function main() {
  const args = process.argv.slice(2)
  if (args.includes('--self-test')) process.exit(selfTest() ? 0 : 1)
  const i = args.indexOf('--since')
  const since = i >= 0 ? args[i + 1] : undefined
  const dirs = transcriptDirs()
  const memories = loadTriggered(resolveMemoryDir())
  if (!dirs.length || !memories.length) {
    process.stdout.write(`memory-recurrence: nothing to read (${dirs.length} transcript dirs, ${memories.length} triggered memories)\n`)
    process.exit(1)
  }
  const r = scan({ dirs, memories, fires: readFires(process.env, 20_000_000), since })
  if (args.includes('--json')) {
    process.stdout.write(JSON.stringify(r, null, 2) + '\n')
  } else {
    process.stdout.write(
      `memory-recurrence: ${r.memories ?? memories.length} triggered memories, ${r.files} transcripts in ${r.dirs} dirs. ` +
        `Captain re-corrections ${r.corrections}, escaped ${r.escaped}.\n`,
    )
    for (const row of r.rows) {
      process.stdout.write(
        `  ${row.memory} since ${row.since}: corrections ${row.corrections}, stumbles ${row.stumbles} (bounced ${row.bounced}, escaped ${row.escaped})\n`,
      )
      for (const e of row.escapedAt) process.stdout.write(`    escaped ${e}\n`)
    }
    process.stdout.write(`  blind spots: ${BLIND_SPOTS.join('; ')}\n`)
  }
  process.exit(r.ok ? 0 : 1)
}

if (import.meta.url === `file://${process.argv[1]}`) main()
