#!/usr/bin/env node
/**
 * Reply Contract Hook (Stop) -- the wall-of-text gate.
 *
 * Why this exists (2026-08-01, the trust-collapse autopsy): across 14
 * sessions the Captain's own words name the failure -- "look at that wall of
 * text... so what is the finding?", "in that wall of text, i can't even find
 * the defect you're raising", "I can't stop and read a book on every turn. I
 * have other sessions running." Four concurrent sessions each ending turns
 * with unstructured prose means the one reviewable item per session is the
 * item that gets skimmed. Law 11 states the rule; this hook is the mechanism
 * that enforces the part of it a machine can check: SHAPE.
 *
 * What it checks (deterministic, format-only -- content judgment stays with
 * the model and the Captain):
 *
 *   BLOCKING (one rule, conservative by design):
 *     A reply with more than MAX_PROSE_LINES prose lines must
 *       (a) open with the header  MISSION: / STATUS: / DID: / NEXT:
 *           where STATUS is one of OK|BLOCKED|DECISION-NEEDED|DEFECT-FOUND
 *       (b) carry a `--- Detail` fold with at most FOLD_PROSE_LINES prose
 *           lines above it.
 *     "Prose lines" excludes fenced code blocks, tables, and blockquotes:
 *     a short answer quoting a 30-line test log is not a wall of text.
 *
 *   No log-only rules remain. Both were deleted 2026-10-24 by Captain
 *   directive after review: DECISION-NEEDED without Stakes/My pick (347 fires,
 *   no consequence) and failure words below the fold under STATUS: OK (139
 *   fires; a 17-reply transcript sample found ~2 real, the rest "0 failed" or
 *   a test failing as designed). A rule that only logs, with nothing reading
 *   the log, measures nothing; a new one should arrive with its reader.
 *
 * Loop safety: `stop_hook_active` is honored when the harness provides it;
 * a marker file keyed on the worktree provides the one-bounce guarantee when
 * it does not. Either alone is sufficient; both are kept deliberately.
 *
 * Payload capture: every invocation appends one JSON line (keys only, never
 * message content) to the board log. This is the probe that resolves the
 * contested premises (does the payload carry stop_hook_active? prompt_id?
 * last_assistant_message?) with evidence instead of docs claims.
 *
 * Fail-open everywhere: a hook that can silence a session's replies over its
 * own bug is worse than no hook. Exit 0 on every error path.
 *
 * Env:
 *   SS_BOARD_DIR              board/log dir (default ~/.claude/ss-board)
 *   SS_REPLY_CONTRACT_DISABLE set to 1 to disable entirely
 *   SS_REPLY_CONTRACT_MAX     override MAX_PROSE_LINES (tests)
 */
import { readFileSync, mkdirSync, appendFileSync, writeFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { homedir } from 'node:os'
import { createHash } from 'node:crypto'
import { loadTriggered, matchReply, resolveMemoryDir, logFire } from './lib/situational-memory.mjs'

const MAX_PROSE_LINES = Number(process.env.SS_REPLY_CONTRACT_MAX || 25)
const FOLD_PROSE_LINES = 12
const STATUS_ENUM = /^(OK|BLOCKED|DECISION-NEEDED|DEFECT-FOUND)\b/
const BOUNCE_TTL_MS = 5 * 60 * 1000
const SESSION_COOLDOWN_MS = Number(process.env.SS_REPLY_CONTRACT_COOLDOWN_MS || 60 * 1000)

// Skill-formatted reports have their own mandated shapes; forcing them
// through the header+fold shape would make every /critique and /sos output
// bounce. Detected by their distinctive top-level markers. Tuned from the
// log, not guessed broader.
const SKILL_EXEMPT = [
  /^#\s*\/(sos|eos|wired|critique|code-review)/m, // skill echo headers
  /^##\s+(Devil's Advocate|Simplifier|Pragmatist|Contrarian|Critique Summary|Session Briefing)/m,
  /^Sentence:\s/m, // /wired contract output
  /^## Revised Plan/m,
]

// Cost-minimized-against-the-fee (BLOCKING, any reply length). The Captain,
// 2026-09-23 and again 2026-10-08 ("for the 50th time"): never say a cost is
// small, a fraction, or covered against the monthly fee. The fee already
// carries chronology cost of goods (~$800-1,000 a month), labor and hosting,
// and the margin is slim; a memory saying so did not stop it, so this does.
// A cost is reported as the number and its basis, framed as ADDED cost of
// goods, never weighed against the fee. Sentence-level: a fee reference and a
// minimizing word in the same sentence.
const FEE_REF = /(\bmonthly fee\b|\bthe fee\b|\bretainer\b|\$\s?5,?[01][05]0\b|\$\s?5k\b|\bfee covers\b|\bagainst the fee\b)/i
const MINIMIZER =
  /\b(fraction|small|tiny|cheap|negligible|trivial|rounding|covers?|covered|many times|well within|nothing (?:against|next to)|dwarf|pales|threaten|easily absorb|absorbs?|comfortabl)/i

export function feeComparison(msg) {
  const sentences = msg.replace(/\n+/g, ' ').split(/(?<=[.!?;])\s+/)
  return sentences.filter((s) => FEE_REF.test(s) && MINIMIZER.test(s)).map((s) => s.trim().slice(0, 200))
}

function boardDir() {
  return process.env.SS_BOARD_DIR || join(homedir(), '.claude', 'ss-board')
}

function log(entry) {
  try {
    const dir = boardDir()
    mkdirSync(dir, { recursive: true })
    appendFileSync(join(dir, 'reply-contract.log'), JSON.stringify(entry) + '\n')
  } catch {
    /* logging must never block the gate, and the gate must never block the reply */
  }
}

/** Count prose lines: non-empty lines outside fenced code blocks that are
 *  not table rows or blockquotes. Returns { total, aboveFold }. */
export function proseLines(msg) {
  const lines = msg.split('\n')
  let inFence = false
  let total = 0
  let aboveFold = 0
  let foldSeen = false
  for (const line of lines) {
    if (/^\s*(```|~~~)/.test(line)) {
      inFence = !inFence
      continue
    }
    if (inFence) continue
    if (/^\s*-{3,}\s*Detail/i.test(line)) {
      foldSeen = true
      continue
    }
    const t = line.trim()
    if (t === '') continue
    if (t.startsWith('|') || t.startsWith('>')) continue
    total++
    if (!foldSeen) aboveFold++
  }
  return { total, aboveFold, foldSeen }
}

/** The header must open the message: MISSION:, then STATUS: from the enum,
 *  DID:, NEXT: within the first eight non-empty lines. Bold markers allowed. */
export function headerOk(msg) {
  const head = msg
    .split('\n')
    .map((l) => l.replace(/\*\*/g, '').trim())
    .filter((l) => l !== '')
    .slice(0, 8)
  if (head.length === 0 || !/^MISSION\s*:/i.test(head[0])) return false
  const statusLine = head.find((l) => /^STATUS\s*:/i.test(l))
  if (!statusLine || !STATUS_ENUM.test(statusLine.replace(/^STATUS\s*:\s*/i, ''))) return false
  return head.some((l) => /^DID\s*:/i.test(l)) && head.some((l) => /^NEXT\s*:/i.test(l))
}

/**
 * Every rule that fires contributes to ONE block. They used to return on the
 * first hit, and with one bounce per reply a second rule (another memory, the
 * shape) was silently waived whenever a first one claimed the slot.
 *
 * `memories` are the triggered memories from the store (situational-memory):
 * a saved lesson whose trigger matches this reply is put in front of the agent
 * in its own words. The hardcoded fee rule stays as the floor that works when
 * the store is missing.
 */
export function evaluate(msg, memories = []) {
  const memHits = matchReply(msg, memories)
  const fee = feeComparison(msg)
  const shape = evaluateShape(msg)
  const rules = []
  if (memHits.length) rules.push('memory')
  if (fee.length) rules.push('fee-comparison')
  if (shape.verdict === 'block') rules.push('shape')
  if (!rules.length) return shape
  return {
    verdict: 'block',
    rule: rules[0],
    rules,
    memHits,
    sentences: fee,
    total: shape.total,
    missing: shape.missing,
  }
}

export function memoryReason(hits) {
  return hits
    .map(
      (h) =>
        `[memory ${h.file}] This reply says: "${h.sentence}". The Captain already corrected this; the saved lesson reads: ${h.text}`,
    )
    .join(' ')
}

export function blockReason(result) {
  const parts = []
  if (result.rules.includes('memory')) {
    parts.push(memoryReason(result.memHits))
  }
  if (result.rules.includes('fee-comparison')) {
    parts.push(
      `[reply-contract] This reply weighs a cost against the monthly fee: "${result.sentences[0]}". ` +
        `The Captain has ruled this out repeatedly: the fee already carries chronology cost of goods, labor and hosting, and the margin is slim. ` +
        `Report the dollar figure with its basis as ADDED cost of goods, and stop there.`,
    )
  }
  if (result.rules.includes('shape')) {
    parts.push(
      `[reply-contract] This reply has ${result.total} prose lines and is missing ${result.missing.join(' and ')}. ` +
        `Lead with the header, keep at most ${FOLD_PROSE_LINES} prose lines above a "--- Detail" fold, ` +
        `move detail below the fold or into the PR/issue and link it. Do NOT repeat the full text you already wrote. ` +
        `Facts carry their source inline (command, file:line, or "inferring").`,
    )
  }
  const content = result.rules.includes('memory') || result.rules.includes('fee-comparison')
  parts.push(
    content
      ? 'Restate the reply now without that sentence or its comparison. Do not apologize or mention this check. This bounce happens at most once per reply.'
      : 'This bounce happens at most once per reply.',
  )
  return parts.join(' ')
}

function evaluateShape(msg) {
  if (SKILL_EXEMPT.some((re) => re.test(msg))) return { verdict: 'exempt' }
  const { total, aboveFold, foldSeen } = proseLines(msg)
  if (total <= MAX_PROSE_LINES) return { verdict: 'pass', total }

  const header = headerOk(msg)
  const fold = foldSeen && aboveFold <= FOLD_PROSE_LINES
  if (header && fold) return { verdict: 'pass', total }

  const missing = []
  if (!header) missing.push('the MISSION:/STATUS:/DID:/NEXT: header (STATUS one of OK|BLOCKED|DECISION-NEEDED|DEFECT-FOUND)')
  if (!fold) missing.push(`a "--- Detail" fold with at most ${FOLD_PROSE_LINES} prose lines above it`)
  return { verdict: 'block', total, aboveFold, missing }
}

/** The triggered memories for this session's repo; none when the store is absent. */
function storeMemories(payload) {
  try {
    return loadTriggered(resolveMemoryDir(process.env, (payload.cwd || process.cwd()).toString()))
  } catch {
    return []
  }
}

/**
 * Loop guard 2: one bounce per reply, keyed on the session AND the reply text,
 * so it holds even if stop_hook_active is absent from this harness's payload.
 * It used to be keyed on the tree alone, which let one rule's bounce silently
 * waive every other rule for five minutes. False when this reply was already
 * bounced, or when the guard cannot be recorded (never risk a loop).
 */
function claimBounce(payload, msg, result) {
  try {
    const session = `${payload.session_id || payload.cwd || 'unknown'}`
    const markerFor = (key) => join(boardDir(), 'bounce-' + createHash('sha1').update(key).digest('hex').slice(0, 16))
    const fresh = (marker, ttl) => {
      try {
        return Date.now() - statSync(marker).mtimeMs < ttl
      } catch {
        return false
      }
    }
    // Two markers. Per reply (5 min): the same text is never bounced twice.
    // Per session (60 s): the restatement written right after a bounce always
    // stands, even if the harness ever stops sending stop_hook_active, so a
    // reply that keeps failing a rule cannot loop.
    const perReply = markerFor(`${session}:${msg}`)
    const perSession = markerFor(`session:${session}`)
    if (fresh(perReply, BOUNCE_TTL_MS) || fresh(perSession, SESSION_COOLDOWN_MS)) {
      log({ ts: new Date().toISOString(), event: 'passed-under-guard', rules: result.rules })
      return false
    }
    mkdirSync(boardDir(), { recursive: true })
    writeFileSync(perReply, String(Date.now()))
    writeFileSync(perSession, String(Date.now()))
    return true
  } catch {
    return false
  }
}

function main() {
  let payload
  try {
    payload = JSON.parse(readFileSync(0, 'utf8'))
  } catch {
    return
  }
  if (process.env.SS_REPLY_CONTRACT_DISABLE === '1') return

  // Premise probe: record which fields this harness actually sends.
  log({
    ts: new Date().toISOString(),
    event: 'invoked',
    keys: Object.keys(payload).sort(),
    stop_hook_active: payload.stop_hook_active ?? null,
    has_last_assistant_message: typeof payload.last_assistant_message === 'string',
  })

  // Loop guard 1: the harness's own signal.
  if (payload.stop_hook_active) return

  const msg = payload.last_assistant_message
  if (typeof msg !== 'string' || msg === '') return

  const result = evaluate(msg, storeMemories(payload))
  // Never log reply text: memory hits are reduced to names, fee sentences to a count.
  const { memHits, sentences, ...logged } = result
  if (sentences) logged.feeSentences = sentences.length
  log({
    ts: new Date().toISOString(),
    event: 'evaluated',
    ...logged,
    ...(memHits && memHits.length ? { memories: memHits.map((h) => h.name) } : {}),
  })
  if (result.verdict !== 'block') return
  if (!claimBounce(payload, msg, result)) return

  if (result.memHits.length) logFire('reply', result.memHits, payload.session_id)
  process.stdout.write(JSON.stringify(stopOutput(blockReason(result))))
}

/**
 * How a bounce is delivered. `decision: "block"` prints the whole reason in
 * the Captain's terminal as "Stop hook error: ...", and he read the
 * instruction to the agent as an error (2026-10-29). Stop also accepts
 * `hookSpecificOutput.additionalContext`, documented as "non-error feedback
 * that continues the conversation" and not shown as a chat message, so that is
 * the default. SS_REPLY_CONTRACT_STYLE=block restores the old shape if the
 * quiet one ever stops making the agent continue.
 */
export function stopOutput(reason) {
  if (process.env.SS_REPLY_CONTRACT_STYLE === 'block') return { decision: 'block', reason }
  return { hookSpecificOutput: { hookEventName: 'Stop', additionalContext: reason } }
}

// Only run main when executed as a hook, not when imported by tests.
if (import.meta.url === `file://${process.argv[1]}`) {
  try {
    main()
  } catch {
    /* fail open */
  }
  process.exit(0)
}
