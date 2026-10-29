#!/usr/bin/env node
/**
 * memory-trigger-test.mjs -- test a memory's triggers against history before
 * they go live.
 *
 *   .claude/bin/memory-trigger-test <memory file>              # last 60 days
 *   .claude/bin/memory-trigger-test <memory file> --since DATE
 *   .claude/bin/memory-trigger-test <memory file> --show 30    # more samples
 *
 * WHY. A trigger that matches ordinary work stops agents constantly, and an
 * agent stopped constantly learns to route around the stop, which kills the
 * lesson it carried. The first triggers other sessions wrote on 2026-10-29
 * matched "call", "draft" and "proof": every chronology reply would have
 * bounced. The fee trigger needed three rounds against 12,968 past replies
 * (53 hits, then 18, then 14, nine of them real) before it was fit to ship.
 * This is that round, as a command: it runs the memory's live triggers over
 * every transcript of this repo, canonical and worktree, and prints how often
 * each stage would have fired with samples to classify by eye.
 *
 * It also runs the memory's own self-check (trigger_example must fire,
 * trigger_counter must not, refused triggers named), and exits 1 when that
 * fails, so a trigger with no examples is not reported as tested.
 *
 * Read the samples. A hit that is not the mistake the memory names is a false
 * positive: narrow the trigger and add the sentence as a trigger_counter.
 */
import fs from 'node:fs'
import path from 'node:path'
import {
  matchDraft,
  matchPrompt,
  matchReply,
  parseMemory,
  resolveMemoryDir,
  selfCheck,
  transcriptDirs,
} from './lib/situational-memory.mjs'
import { captainPrompt, gmailDrafts, records, textOf, transcriptFiles } from './memory-recurrence.mjs'

const DAY = 86_400_000

function resolveFile(arg) {
  if (fs.existsSync(arg)) return arg
  const inStore = path.join(resolveMemoryDir(), arg.endsWith('.md') ? arg : `${arg}.md`)
  return fs.existsSync(inStore) ? inStore : null
}

/** Add one transcript record's hits (and denominators) to the tally. */
function tallyRecord(rec, mem, t) {
  const at = rec.timestamp?.slice(0, 16) ?? ''
  if (rec.type === 'assistant') {
    const text = textOf(rec.message?.content)
    if (text) t.replies++
    const r = text && matchReply(text, [mem])[0]
    if (r) t.stage.reply.push(`${at} ${r.sentence}`)
    const drafts = gmailDrafts(rec.message?.content)
    const d = drafts && matchDraft(drafts, [mem])[0]
    if (d) t.stage.draft.push(`${at} ${d.sentence}`)
    return
  }
  const p = captainPrompt(rec)
  if (!p) return
  t.prompts++
  if (matchPrompt(p, [mem]).length) t.stage.prompt.push(`${at} ${p.replace(/\s+/g, ' ').slice(0, 200)}`)
}

function sweep(mem, since) {
  const t = { stage: { reply: [], draft: [], prompt: [] }, replies: 0, prompts: 0 }
  for (const f of transcriptFiles(transcriptDirs(), since)) {
    try {
      for (const rec of records(f.full)) {
        if (Date.parse(rec.timestamp ?? '') >= since) tallyRecord(rec, mem, t)
      }
    } catch {
      /* an unreadable transcript is a gap, not a finding */
    }
  }
  return t
}

function parseArgs(args) {
  const target = args.find((a, i) => !a.startsWith('-') && !['--since', '--show'].includes(args[i - 1]))
  const si = args.indexOf('--since')
  const sh = args.indexOf('--show')
  return {
    file: target ? resolveFile(target) : null,
    since: si >= 0 ? Date.parse(args[si + 1]) : Date.now() - 60 * DAY,
    show: sh >= 0 ? Number(args[sh + 1]) : 15,
  }
}

function main() {
  const { file, since, show } = parseArgs(process.argv.slice(2))
  if (!file) {
    process.stderr.write('usage: memory-trigger-test <memory file> [--since YYYY-MM-DD] [--show N]\n')
    return 1
  }
  const mem = parseMemory(file, fs.readFileSync(file, 'utf8'))
  if (!mem) {
    process.stdout.write(`${path.basename(file)}: no trigger_reply / trigger_prompt / trigger_draft in its frontmatter\n`)
    return 1
  }
  const problems = selfCheck([mem])
  if (!mem.examples.length && (mem.triggers.reply.length || mem.triggers.draft.length)) {
    problems.push(`${mem.file}: no trigger_example, so nothing proves the trigger fires`)
  }
  const { stage, replies, prompts } = sweep(mem, since)
  const out = [`${mem.file} since ${new Date(since).toISOString().slice(0, 10)}:`]
  const line = (name, hits, denom) => {
    out.push(`  ${name}: ${hits.length} hit(s) in ${denom}`)
    for (const h of show > 0 ? hits.slice(-show) : []) out.push(`    ${h.slice(0, 220)}`)
  }
  if (mem.triggers.reply.length) line('reply', stage.reply, `${replies} assistant replies`)
  if (mem.triggers.reply.length || mem.triggers.draft.length) line('draft', stage.draft, 'Gmail drafts/sends')
  if (mem.triggers.prompt.length) line('prompt', stage.prompt, `${prompts} Captain prompts`)
  for (const p of problems) out.push(`  ! ${p}`)
  if (!problems.length) out.push('  self-check: examples fire, counters do not')
  process.stdout.write(out.join('\n') + '\n')
  return problems.length ? 1 : 0
}

if (import.meta.url === `file://${process.argv[1]}`) process.exitCode = main()
