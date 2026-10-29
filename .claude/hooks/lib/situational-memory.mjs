/**
 * situational-memory.mjs -- deliver a saved lesson at the moment it applies.
 *
 * WHY THIS EXISTS (2026-10-29). An agent costed an A&P lane at "$20 to $60 a
 * month against the firm's ... monthly fee" and recommended folding it in free
 * on that basis. The Captain had corrected exactly this on 09-23, 10-08 ("for
 * the 50th time") and again that day. The rule was written down, in
 * feedback_never_call_a_cost_small_against_the_fee.md, and the agent never saw
 * it: the store held 589 memories, MEMORY.md (the only file that auto-loads)
 * linked about 100, and that one sat two hops away. The Captain: "what is the
 * point of a memory file if it's not getting used?" His constraint: no new
 * doctrine laws.
 *
 * THE MECHANISM. A memory may carry its trigger in its own frontmatter, as
 * single-quoted YAML scalars (one per line, keys repeatable):
 *
 *   trigger_reply: '<regex>'     tested per sentence (and adjacent sentence
 *                                pair) of the agent's own words
 *   trigger_prompt: '<regex>'    tested on the Captain's prompt
 *   trigger_draft: '<regex>'     like trigger_reply, but only on a Gmail
 *                                draft's text: for rules about what a client
 *                                reads ("we", never "I checked") that chat
 *                                replies to the Captain legitimately break
 *   trigger_since: 'YYYY-MM-DD'  when recurrence counting starts
 *   trigger_example: '<text>'    must match a reply trigger (self-check)
 *   trigger_counter: '<text>'    must NOT match (self-check)
 *
 * Three surfaces read them: reply-contract.mjs (Stop) bounces a draft reply
 * once with the memory's own text; situational-gate.mjs (PreToolUse on Gmail
 * writes) denies a draft once before it exists; situational-recall.mjs (via
 * reflex-primer, UserPromptSubmit) puts the memory in front of the agent
 * before it starts. A memory with no trigger costs nothing; a trigger that does
 * not match emits nothing. The hook quotes the memory rather than restating
 * the rule, so editing the memory edits what the agent is shown.
 *
 * Triggers live in the private memory store, not in git: ss-console is public
 * and a trigger names client figures. memory-audit runs every memory's
 * examples and counters against its live triggers at SessionStart, which is
 * what keeps an untested store from rotting silently.
 *
 * SAFETY. Regex sources are the Captain's own local files, but a bad one must
 * not hang a Stop hook or bounce every reply, so a trigger is rejected (and
 * reported by memory-audit) when it matches the empty string, exceeds
 * MAX_SOURCE characters, or nests a quantifier inside a quantified group.
 * Tested text is capped per sentence. Everything fails open.
 */
import crypto from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const ATTIC = 'attic'
const NOT_A_MEMORY = new Set(['MEMORY.md', 'MEMORY.md.bak'])
/** Frontmatter sits at the top; reading the whole store on every turn would not scale. */
const HEAD_BYTES = 4096
const MAX_SOURCE = 500
const MAX_SENTENCE = 600
/** Characters of a memory's guidance shown at the reply stage. */
const TEXT_CAP = 900
/** Characters shown at the prompt stage, where it rides on every matching turn. */
const BRIEF_CAP = 250

/** Same resolution as memory-audit.mjs: every worktree reads the one real store. */
export function canonicalProjectPath(cwd) {
  return cwd.replace(/\/\.claude\/worktrees\/.*$/, '')
}

export function resolveMemoryDir(env = process.env, cwd = process.cwd()) {
  if (env.SS_MEMORY_DIR) return env.SS_MEMORY_DIR
  const munged = canonicalProjectPath(cwd).replace(/[/.]/g, '-')
  return path.join(os.homedir(), '.claude', 'projects', munged, 'memory')
}

/**
 * Every transcript directory belonging to this repo: the canonical one plus
 * one per worktree, which the harness keeps separately. Reading only the
 * canonical directory missed every worktree session, which is most of them.
 */
export function transcriptDirs(env = process.env, cwd = process.cwd()) {
  if (env.SS_TRANSCRIPT_DIR) return [env.SS_TRANSCRIPT_DIR]
  const root = path.join(os.homedir(), '.claude', 'projects')
  const base = canonicalProjectPath(cwd).replace(/[/.]/g, '-')
  let names
  try {
    names = fs.readdirSync(root)
  } catch {
    return []
  }
  return names
    .filter((n) => n === base || n.startsWith(base + '--claude-worktrees-'))
    .map((n) => path.join(root, n))
}

/** Single-quoted YAML scalar -> string. Anything else is not a valid trigger value. */
function singleQuoted(v) {
  const s = v.trim()
  if (s.length >= 2 && s.startsWith("'") && s.endsWith("'")) return s.slice(1, -1).replace(/''/g, "'")
  return null
}

function plainScalar(v) {
  const q = singleQuoted(v)
  if (q !== null) return q
  const s = v.trim()
  if (s.length >= 2 && s.startsWith('"') && s.endsWith('"')) return s.slice(1, -1).replace(/\\"/g, '"')
  return s
}

const NESTED_QUANTIFIER = /\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)[+*{]/

/** Compile a trigger source, or say why it is refused. */
export function compileTrigger(src) {
  if (typeof src !== 'string' || src.trim() === '') return { error: 'empty' }
  if (src.length > MAX_SOURCE) return { error: `longer than ${MAX_SOURCE} characters` }
  if (NESTED_QUANTIFIER.test(src)) return { error: 'nested quantifier (catastrophic backtracking risk)' }
  let re
  try {
    // nosemgrep: javascript.lang.security.audit.detect-non-literal-regexp.detect-non-literal-regexp -- source is the Captain's own local memory file; length, nesting and empty-match are refused above and tested text is capped per sentence
    re = new RegExp(src, 'i')
  } catch (e) {
    return { error: String(e.message || e) }
  }
  if (re.test('')) return { error: 'matches the empty string, so it would fire on every reply' }
  return { re }
}

function frontmatterOf(raw) {
  const m = raw.match(/^---\n([\s\S]*?)\n---\n?/)
  return m ? { fm: m[1], body: raw.slice(m[0].length) } : { fm: '', body: raw }
}

/** Description plus the memory's "How to apply" paragraph (or its opening), capped. */
export function guidanceOf(description, body) {
  const how = body.match(/\*\*How to apply:\*\*\s*([\s\S]*?)(?:\n\s*\n|$)/)
  const lead = how ? how[1] : (body.trim().split(/\n\s*\n/)[0] ?? '')
  const text = [description, lead.replace(/\s+/g, ' ').trim()].filter(Boolean).join(' How to apply: ')
  return text.length > TEXT_CAP ? text.slice(0, TEXT_CAP - 1) + '…' : text
}

function cap(s, n) {
  return s.length > n ? s.slice(0, n - 1) + '…' : s
}

/** Parse one memory. Returns null when it carries no trigger key at all. */
export function parseMemory(file, raw) {
  if (!/^\s*trigger_(reply|prompt|draft)\s*:/m.test(raw)) return null
  const { fm, body } = frontmatterOf(raw)
  const triggers = { reply: [], prompt: [], draft: [] }
  const invalid = []
  const examples = []
  const counters = []
  let since = null
  for (const m of fm.matchAll(/^\s*trigger_(reply|prompt|draft|since|example|counter)\s*:(.*)$/gm)) {
    const [, key, value] = m
    if (key === 'since') {
      since = plainScalar(value)
      continue
    }
    const src = singleQuoted(value)
    if (src === null) {
      invalid.push({ stage: key, source: value.trim(), error: 'not a single-quoted scalar' })
      continue
    }
    if (key === 'example') examples.push(src)
    else if (key === 'counter') counters.push(src)
    else {
      const c = compileTrigger(src)
      if (c.re) triggers[key].push(c.re)
      else invalid.push({ stage: key, source: src, error: c.error })
    }
  }
  const name = (fm.match(/^name:\s*(.+)$/m)?.[1] ?? path.basename(file, '.md')).trim()
  const description = plainScalar(fm.match(/^description:\s*(.+)$/m)?.[1] ?? '')
  return {
    name,
    file: path.basename(file),
    triggers,
    invalid,
    examples,
    counters,
    since,
    text: guidanceOf(description, body),
    brief: cap(description, BRIEF_CAP),
  }
}

function readHead(file) {
  const fd = fs.openSync(file, 'r')
  try {
    const buf = Buffer.alloc(HEAD_BYTES)
    const n = fs.readSync(fd, buf, 0, HEAD_BYTES, 0)
    return buf.subarray(0, n).toString('utf8')
  } finally {
    fs.closeSync(fd)
  }
}

/** Every memory that declares a trigger (attic excluded: retired lessons stay retired). */
export function loadTriggered(dir) {
  const out = []
  let names
  try {
    names = fs.readdirSync(dir)
  } catch {
    return out
  }
  for (const n of names) {
    if (!n.endsWith('.md') || NOT_A_MEMORY.has(n) || n === ATTIC) continue
    const file = path.join(dir, n)
    try {
      const head = readHead(file)
      if (!/trigger_(reply|prompt|draft)\s*:/.test(head)) continue
      // The body (for the shown text) is read only for the few triggered files.
      const mem = parseMemory(file, fs.readFileSync(file, 'utf8'))
      if (mem) out.push(mem)
    } catch {
      /* unreadable file: skip, never throw */
    }
  }
  return out.sort((a, b) => a.name.localeCompare(b.name))
}

/**
 * The text a reply asserts: fenced and inline code removed, nothing else.
 * Quotes and blockquotes are deliberately kept: a client draft shown in chat
 * usually sits in one, and a post-mortem that quotes the forbidden sentence
 * costs one restatement, where a miss costs the Captain another correction.
 */
export function ownWords(msg) {
  return msg.replace(/(^|\n)[ \t]*(```|~~~)[\s\S]*?(\n[ \t]*\2[^\n]*|$)/g, '\n').replace(/`[^`\n]*`/g, ' ')
}

export function sentencesOf(text) {
  return text
    .split(/\n\s*\n|\n(?=\s*(?:[-*+>]|\d+\.)\s)/)
    .flatMap((p) => p.replace(/\s*\n\s*/g, ' ').split(/(?<=[.!?;])\s+/))
    .map((s) => s.replace(/^[\s>*+-]+/, '').trim())
    .filter(Boolean)
    .map((s) => s.slice(0, MAX_SENTENCE))
}

/** Single sentences first, then adjacent pairs (a cost in one, the fee in the next). */
function candidates(text) {
  const s = sentencesOf(text)
  const pairs = []
  for (let i = 0; i + 1 < s.length; i++) pairs.push(`${s[i]} ${s[i + 1]}`)
  return [...s, ...pairs]
}

export function sentenceHash(s) {
  return crypto.createHash('sha1').update(s.toLowerCase().replace(/\s+/g, ' ').trim()).digest('hex').slice(0, 12)
}

/** Memories whose reply trigger matches the agent's own words. */
export function matchReply(msg, memories, stages = ['reply']) {
  const hits = []
  if (typeof msg !== 'string' || !msg) return hits
  const cands = candidates(ownWords(msg))
  for (const mem of memories) {
    const res = stages.flatMap((st) => mem.triggers[st] ?? [])
    if (!res.length) continue
    const s = cands.find((x) => res.some((re) => re.test(x)))
    if (s) hits.push({ name: mem.name, file: mem.file, sentence: s.slice(0, 240), hash: sentenceHash(s), text: mem.text })
  }
  return hits
}

/** A Gmail draft is checked against reply AND draft-only triggers. */
export function matchDraft(text, memories) {
  return matchReply(text, memories, ['reply', 'draft'])
}

/** Memories whose prompt trigger matches the Captain's prompt. */
export function matchPrompt(prompt, memories) {
  if (typeof prompt !== 'string' || !prompt) return []
  const text = prompt.slice(0, 4000)
  return memories
    .filter((mem) => mem.triggers.prompt.some((re) => re.test(text)))
    .map((mem) => ({ name: mem.name, file: mem.file, brief: mem.brief, text: mem.text }))
}

/** One line per memory whose self-check fails: an example that no longer fires, a counter that does. */
export function selfCheck(memories) {
  const problems = []
  for (const mem of memories) {
    for (const inv of mem.invalid) problems.push(`${mem.file}: trigger_${inv.stage} refused (${inv.error})`)
    for (const ex of mem.examples) {
      if (!matchDraft(ex, [mem]).length) problems.push(`${mem.file}: trigger_example no longer matches: ${cap(ex, 80)}`)
    }
    for (const c of mem.counters) {
      if (matchDraft(c, [mem]).length) problems.push(`${mem.file}: trigger_counter now matches: ${cap(c, 80)}`)
    }
  }
  return problems
}

export function boardDir(env = process.env) {
  return env.SS_BOARD_DIR || path.join(os.homedir(), '.claude', 'ss-board')
}

export const FIRE_LOG = 'situational-memory.log'

/** One line per fire: memory, stage, session, sentence hash. Never message content. */
export function logFire(stage, hits, sessionId, env = process.env) {
  if (!hits.length) return
  try {
    const dir = boardDir(env)
    fs.mkdirSync(dir, { recursive: true })
    const ts = new Date().toISOString()
    const lines = hits.map(
      (h) => JSON.stringify({ ts, stage, memory: h.name, session: sessionId ?? null, hash: h.hash ?? null }) + '\n',
    )
    fs.appendFileSync(path.join(dir, FIRE_LOG), lines.join(''))
  } catch {
    /* logging never blocks */
  }
}

/** Fire-log entries, newest last; a missing or partly written log reads as what parses. */
export function readFires(env = process.env, maxBytes = 1_000_000) {
  const file = path.join(boardDir(env), FIRE_LOG)
  let raw
  try {
    const st = fs.statSync(file)
    const fd = fs.openSync(file, 'r')
    try {
      const len = Math.min(st.size, maxBytes)
      const buf = Buffer.alloc(len)
      fs.readSync(fd, buf, 0, len, st.size - len)
      raw = buf.toString('utf8')
    } finally {
      fs.closeSync(fd)
    }
  } catch {
    return []
  }
  const out = []
  for (const line of raw.split('\n')) {
    try {
      if (line.trim()) out.push(JSON.parse(line))
    } catch {
      /* the first line of a tail read is usually partial */
    }
  }
  return out
}
