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
 *   LOG-ONLY (promoted to blocking only after the observed false-positive
 *   rate says they deserve it -- the primer's own design law: a line that is
 *   sometimes wrong and always loud teaches agents to skim past everything):
 *     - DECISION-NEEDED present but missing Stakes: / My pick:
 *     - failure-signal words below the fold while STATUS: OK
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

const MAX_PROSE_LINES = Number(process.env.SS_REPLY_CONTRACT_MAX || 25)
const FOLD_PROSE_LINES = 12
const STATUS_ENUM = /^(OK|BLOCKED|DECISION-NEEDED|DEFECT-FOUND)\b/
const FOLD_RE = /^\s*-{3,}\s*Detail/im
const BOUNCE_TTL_MS = 5 * 60 * 1000

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

const FAILURE_WORDS = /\b(error|failed|failure|regression|broken)\b/i

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

export function evaluate(msg) {
  const fee = feeComparison(msg)
  if (fee.length) return { verdict: 'block', rule: 'fee-comparison', sentences: fee }
  if (SKILL_EXEMPT.some((re) => re.test(msg))) return { verdict: 'exempt' }
  const { total, aboveFold, foldSeen } = proseLines(msg)
  const advisories = []

  if (/DECISION-NEEDED/.test(msg) && !(/Stakes\s*:/i.test(msg) && /My pick\s*:/i.test(msg))) {
    advisories.push('decision-needed-without-stakes-or-pick')
  }
  if (foldSeen && /STATUS\s*:\s*OK\b/i.test(msg)) {
    const below = msg.split(FOLD_RE)[1] || ''
    if (FAILURE_WORDS.test(below)) advisories.push('failure-words-below-fold-with-status-ok')
  }

  if (total <= MAX_PROSE_LINES) return { verdict: 'pass', total, advisories }

  const header = headerOk(msg)
  const fold = foldSeen && aboveFold <= FOLD_PROSE_LINES
  if (header && fold) return { verdict: 'pass', total, advisories }

  const missing = []
  if (!header) missing.push('the MISSION:/STATUS:/DID:/NEXT: header (STATUS one of OK|BLOCKED|DECISION-NEEDED|DEFECT-FOUND)')
  if (!fold) missing.push(`a "--- Detail" fold with at most ${FOLD_PROSE_LINES} prose lines above it`)
  return { verdict: 'block', total, aboveFold, missing, advisories }
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

  const result = evaluate(msg)
  log({ ts: new Date().toISOString(), event: 'evaluated', ...('missing' in result ? { ...result } : result) })
  if (result.verdict !== 'block') return

  // Loop guard 2: one bounce max, keyed on the session's tree so it works
  // even if stop_hook_active is absent from this harness's payload.
  try {
    const key = (payload.cwd || payload.session_id || 'unknown').toString()
    const marker = join(boardDir(), 'bounce-' + [...key].reduce((a, c) => (a * 33 + c.charCodeAt(0)) >>> 0, 5381).toString(16))
    try {
      const age = Date.now() - statSync(marker).mtimeMs
      if (age < BOUNCE_TTL_MS) return // already bounced this reply once
    } catch {
      /* no marker: first bounce */
    }
    mkdirSync(boardDir(), { recursive: true })
    writeFileSync(marker, String(Date.now()))
  } catch {
    return // if the guard cannot be recorded, do not risk a loop: let it pass
  }

  if (result.rule === 'fee-comparison') {
    process.stdout.write(
      JSON.stringify({
        decision: 'block',
        reason:
          `[reply-contract] This reply weighs a cost against the monthly fee: "${result.sentences[0]}". ` +
          `The Captain has ruled this out repeatedly: the fee already carries chronology cost of goods, labor and hosting, and the margin is slim. ` +
          `Restate the reply without comparing any cost to the fee or calling it small, covered or a fraction. ` +
          `Report the dollar figure with its basis as ADDED cost of goods, and stop there. Do not apologize or mention this check.`,
      }),
    )
    return
  }
  process.stdout.write(
    JSON.stringify({
      decision: 'block',
      reason:
        `[reply-contract] This reply has ${result.total} prose lines and is missing ${result.missing.join(' and ')}. ` +
        `Restate it now, compactly: lead with the header, keep at most ${FOLD_PROSE_LINES} prose lines above a "--- Detail" fold, ` +
        `move detail below the fold or into the PR/issue and link it. Do NOT repeat the full text you already wrote. ` +
        `Facts carry their source inline (command, file:line, or "inferring"). This bounce happens at most once per reply.`,
    }),
  )
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
