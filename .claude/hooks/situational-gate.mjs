#!/usr/bin/env node
/**
 * situational-gate.mjs (PreToolUse on Gmail writes) -- a saved lesson stops a
 * draft before it exists.
 *
 * The Stop hook (reply-contract.mjs) sees only the agent's last chat message,
 * never the body of a Gmail draft, and CLAUDE.md routes every message drafted
 * for the Captain through create_draft. So the channel a client-facing
 * sentence actually travels was the one channel no memory reached. This runs
 * the same triggers (lib/situational-memory.mjs) over the draft's subject and
 * body and refuses the call once, with the memory's own text, per distinct
 * content. A second identical attempt passes: the agent has seen the lesson,
 * and a gate that can wedge a send is worse than none.
 *
 * Exit 2 + stderr is this repo's PreToolUse deny idiom (worktree-guard.mjs).
 * Fail-open on every error path.
 */
import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { boardDir, loadTriggered, logFire, matchDraft, resolveMemoryDir } from './lib/situational-memory.mjs'

const ONCE_TTL_MS = 30 * 60 * 1000

/** The text a draft would put in front of its reader. */
export function draftText(input = {}) {
  const html = typeof input.htmlBody === 'string' ? input.htmlBody.replace(/<[^>]+>/g, ' ') : ''
  return [input.subject, input.body, input.forwardText, html]
    .filter((x) => typeof x === 'string' && x.trim())
    .join('\n\n')
}

function main() {
  let payload
  try {
    payload = JSON.parse(readFileSync(0, 'utf8'))
  } catch {
    return
  }
  if (process.env.SS_SITUATIONAL_DISABLE === '1') return
  const text = draftText(payload.tool_input)
  if (!text) return
  const memories = loadTriggered(resolveMemoryDir(process.env, (payload.cwd || process.cwd()).toString()))
  const hits = matchDraft(text, memories)
  if (!hits.length) return

  try {
    const key = createHash('sha1').update(`${payload.session_id || ''}:${text}`).digest('hex').slice(0, 16)
    const marker = join(boardDir(), 'gate-' + key)
    try {
      if (Date.now() - statSync(marker).mtimeMs < ONCE_TTL_MS) return
    } catch {
      /* first time for this content */
    }
    mkdirSync(boardDir(), { recursive: true })
    writeFileSync(marker, String(Date.now()))
  } catch {
    return // cannot record the once-guard: never risk wedging a send
  }

  logFire('gmail', hits, payload.session_id)
  process.stderr.write(
    hits
      .map(
        (h) =>
          `[memory ${h.file}] This draft says: "${h.sentence}". The Captain already corrected this; the saved lesson reads: ${h.text}`,
      )
      .join('\n') + '\nRewrite the draft without it, then call the tool again. Do not mention this check.\n',
  )
  process.exit(2)
}

if (import.meta.url === `file://${process.argv[1]}`) {
  try {
    main()
  } catch {
    /* fail open */
  }
  process.exit(0)
}
