#!/usr/bin/env node
/**
 * context-size.mjs -- how large is this session's context right now? LOG-ONLY.
 *
 * Why (wave 2, Q7, 2026-11-09). The Captain's /usage breakdown put long
 * context first among token costs (98% of spend carried a long context). A
 * hand-off signal at 250K was proposed and replayed: main sessions START at a
 * median of 193K, and 48 of 64 pass 250K, so a 250K line would speak in almost
 * every session and teach agents to skim it. The decision was to LOG the size
 * at every prompt first, at the 300K and 400K bands, and to speak only after
 * the log has shown how often a line would fire. This module is that log.
 * Nothing it computes is printed to the agent.
 *
 * The measure: the last assistant line in the transcript that carries
 * `message.usage`. Context = input_tokens + cache_read_input_tokens +
 * cache_creation_input_tokens, which is what the model read on that call.
 * The transcript is read BACKWARDS in chunks, at most CAP_BYTES, because main
 * transcripts here reach ~100MB and this runs on every prompt.
 *
 * Outcomes, each its own value so "could not look" never reads as "small":
 *   ok             tokens is a number
 *   not_found      the file was read (to CAP_BYTES or its start) and holds no
 *                  usage line: a first prompt, or one line longer than the cap
 *   no_transcript  the payload carried no transcript_path, or the file is absent
 *   error          anything thrown
 *
 * CLI (called by reflex-primer.sh with the UserPromptSubmit payload on stdin):
 *   node context-size.mjs log     appends one JSON line to
 *                                 $SS_BOARD_DIR/context-size.log
 *                                 (default ~/.claude/ss-board); prints nothing;
 *                                 exits 0 always.
 *
 * @see tests/context-size.test.ts
 */

import { appendFileSync, closeSync, existsSync, fstatSync, mkdirSync, openSync, readSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

export const CAP_BYTES = 1024 * 1024
const CHUNK_BYTES = 64 * 1024

/** Tokens the model read on one call, or null when the line is not a real call. */
export function contextOfLine(line) {
  if (!line.includes('"usage"') || !line.includes('"assistant"')) return null
  let o
  try {
    o = JSON.parse(line)
  } catch {
    return null
  }
  if (o?.type !== 'assistant') return null
  const m = o.message
  const u = m?.usage
  if (!u || typeof u !== 'object') return null
  // Synthetic lines (API errors, interrupts) carry an all-zero usage block;
  // they are not a call and would read as an empty context.
  if (m.model === '<synthetic>') return null
  const n = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : 0)
  return n(u.input_tokens) + n(u.cache_read_input_tokens) + n(u.cache_creation_input_tokens)
}

/**
 * The last usage line among the complete lines of one chunk, scanning from the
 * end. `carry` is the bytes before the chunk's first newline, which may be the
 * tail of a line that began earlier: kept as bytes, so a multi-byte character
 * split across chunks is never decoded in halves.
 */
function scanChunk(joined, atStart) {
  let whole = joined
  let carry = Buffer.alloc(0)
  if (!atStart) {
    const nl = joined.indexOf(10)
    if (nl === -1) return { tokens: null, carry: joined }
    carry = joined.subarray(0, nl)
    whole = joined.subarray(nl + 1)
  }
  const parts = whole.toString('utf8').split('\n')
  for (let i = parts.length - 1; i >= 0; i--) {
    const tokens = contextOfLine(parts[i])
    if (tokens !== null) return { tokens, carry }
  }
  return { tokens: null, carry }
}

function scanBackwards(fd, cap, chunk) {
  let pos = fstatSync(fd).size
  let carry = Buffer.alloc(0)
  let scanned = 0
  while (pos > 0 && scanned < cap) {
    const len = Math.min(chunk, pos, cap - scanned)
    pos -= len
    scanned += len
    const buf = Buffer.alloc(len)
    readSync(fd, buf, 0, len, pos)
    const res = scanChunk(Buffer.concat([buf, carry]), pos === 0)
    if (res.tokens !== null) return { tokens: res.tokens, outcome: 'ok' }
    carry = res.carry
  }
  return { tokens: null, outcome: 'not_found' }
}

/**
 * The context size at the end of a transcript.
 * @param {string | undefined | null} path
 * @param {{ cap?: number, chunk?: number }} [opts]
 * @returns {{ tokens: number | null, outcome: 'ok' | 'not_found' | 'no_transcript' | 'error' }}
 */
export function lastContextSize(path, opts = {}) {
  if (!path || typeof path !== 'string' || !existsSync(path)) {
    return { tokens: null, outcome: 'no_transcript' }
  }
  let fd
  try {
    fd = openSync(path, 'r')
    return scanBackwards(fd, opts.cap ?? CAP_BYTES, opts.chunk ?? CHUNK_BYTES)
  } catch {
    return { tokens: null, outcome: 'error' }
  } finally {
    if (fd !== undefined) {
      try {
        closeSync(fd)
      } catch {
        /* nothing to do */
      }
    }
  }
}

function boardDir() {
  return process.env.SS_BOARD_DIR || join(homedir(), '.claude', 'ss-board')
}

function readStdin() {
  try {
    const chunks = []
    const buf = Buffer.alloc(65536)
    for (;;) {
      let n = 0
      try {
        n = readSync(0, buf, 0, buf.length, null)
      } catch (e) {
        if (e && e.code === 'EAGAIN') continue
        break
      }
      if (n <= 0) break
      chunks.push(Buffer.from(buf.subarray(0, n)))
    }
    return Buffer.concat(chunks).toString('utf8')
  } catch {
    return ''
  }
}

function logMain() {
  let payload
  try {
    payload = JSON.parse(readStdin() || '{}') ?? {}
  } catch {
    payload = {}
  }
  const res = lastContextSize(payload.transcript_path)
  const row = {
    ts: new Date().toISOString(),
    session_id: typeof payload.session_id === 'string' ? payload.session_id : null,
    tokens: res.tokens,
    outcome: res.outcome,
  }
  const dir = boardDir()
  mkdirSync(dir, { recursive: true })
  appendFileSync(join(dir, 'context-size.log'), `${JSON.stringify(row)}\n`)
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  try {
    if (process.argv[2] === 'log') logMain()
  } catch {
    /* log-only: never surface anything */
  }
  process.exit(0)
}
