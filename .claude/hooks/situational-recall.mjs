#!/usr/bin/env node
/**
 * situational-recall.mjs (UserPromptSubmit, called from reflex-primer.sh) --
 * put a saved lesson in front of the agent before it starts, when the
 * Captain's prompt raises the situation the lesson is about.
 *
 * Reads the hook payload on stdin, prints one `[memory]` line per matching
 * memory (at most MAX_SHOWN), and nothing otherwise. Each memory is shown once
 * per session: the fire log says whether it already was, so a long pricing
 * conversation is not reminded on every turn and the line keeps meaning
 * something. The full text arrives from the Stop hook if the reply goes wrong
 * anyway; this line is the short version.
 */
import { readFileSync } from 'node:fs'
import { loadTriggered, logFire, matchPrompt, readFires, resolveMemoryDir } from './lib/situational-memory.mjs'

const MAX_SHOWN = 2

export function recall(payload, env = process.env) {
  const prompt = payload.prompt ?? payload.prompt_text
  const memories = loadTriggered(resolveMemoryDir(env, (payload.cwd || process.cwd()).toString()))
  const hits = matchPrompt(prompt, memories)
  if (!hits.length) return []
  const session = payload.session_id ?? null
  const shown = new Set(
    readFires(env)
      .filter((f) => f.stage === 'prompt' && session && f.session === session)
      .map((f) => f.memory),
  )
  return hits.filter((h) => !shown.has(h.name)).slice(0, MAX_SHOWN)
}

function main() {
  let payload
  try {
    payload = JSON.parse(readFileSync(0, 'utf8'))
  } catch {
    return
  }
  if (process.env.SS_SITUATIONAL_DISABLE === '1') return
  const hits = recall(payload)
  if (!hits.length) return
  logFire('prompt', hits, payload.session_id)
  for (const h of hits) process.stdout.write(`[memory] ${h.brief} (${h.file})\n`)
}

if (import.meta.url === `file://${process.argv[1]}`) {
  try {
    main()
  } catch {
    /* fail open */
  }
  process.exit(0)
}
