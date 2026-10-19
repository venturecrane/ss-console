#!/usr/bin/env node
/**
 * client-data-guard.mjs: PreToolUse hook on Bash|Edit|Write|NotebookEdit,
 * installed at USER level (~/.claude/settings.json) on the Captain's laptops
 * by laptop/install.sh, so it applies in every repo and every session.
 *
 * THE RULE (Captain, 2026-10-17): client documents (matter files, medical
 * records, chronologies, anything made from them) are opened only in that
 * client's ops workspace (Fly app ops-<slug>), never on a laptop. Seat work,
 * rebuilds, deploys and product development stay on the laptops.
 *
 * This hook catches the requests that would pull documents onto a laptop
 * (the chronology/drafting/file-review tools, their data folders, document
 * downloads through seat-probe or fly sftp) BEFORE they run, and tells the
 * agent to hand the task to the workspace instead. It is a habit-breaker, not
 * a lock: an agent can phrase a command it does not recognise, and the
 * Captain's own logins reach everything. It exists so the default path is the
 * right one.
 *
 * Decision chain:
 *   1. /etc/smd-ops-workspace exists (we ARE on a workspace) -> allow
 *   2. SS_ALLOW_CLIENT_DATA_ON_LAPTOP is set to a reason -> allow, audited
 *      (break-glass: a workspace is down and a client needs something now)
 *   3. the command or path matches a client-document pattern -> exit 2,
 *      unless the whole command is one off-laptop tool (OFFBOARD_TOOL)
 *   4. otherwise -> allow
 * Fails open on a crash: a broken hook must not brick unrelated work.
 */

import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const MARKER = process.env.SMD_OPS_WORKSPACE_MARKER || '/etc/smd-ops-workspace'
const HOME = os.homedir()

/**
 * Tools whose job is to fetch or transform a client's documents, matched only
 * when RUN (command position, or after python/bash/sh/uv run/exec/ops-job), so
 * editing, testing, diffing or grepping their code on a laptop stays allowed:
 * tool development is product work.
 */
const TOOL_PATTERNS = [
  /(?:^|[;&|(\n]\s*|\b(?:python[0-9.]*|bash|sh|exec|uv\s+run|ops-job)\s+)(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*(?:\S*\/)?(?:download\.py|index_msg\.py|mint_small\.sh|run_seat\.sh|seatrun\.sh|file_to_matter\.sh|pull_docs\.py|seat_mint\.py|seat_list_mint\.py|draft_run\.py|reread_pull\.py|push_uploads\.py)(?=\s|$|[;&|)])/,
]
/**
 * Where the tools keep documents, matched as a PATH (home-relative or
 * absolute) or as the tools' data variables being SET, never as a bare word,
 * so searching the source for the folder name stays allowed.
 */
const DATA_PATTERNS = [
  /(?:~|\$HOME|\$\{HOME\}|\/Users\/[^/\s]+|\/home\/[^/\s]+)\/smd-(?:medchron|filereview)-data\b/,
  /\bSMD_(?:MC|FR)_DATA=/,
]
/** Pulling a file off a seat. */
const PULL_PATTERNS = [
  /\bfly(ctl)?\s+(ssh\s+)?sftp\s+get\b/,
  /seat-probe\.sh\b[^|;&]*\b(download|mint|get_document|read_document|read_attachment|extract)/,
]

/**
 * The tools that take client documents OFF a laptop (copy to a workspace with a
 * verified manifest, then delete the laptop copy). Naming a data folder is their
 * job, so a command that is exactly one of them, nothing chained, is allowed.
 * WHY (2026-10-19): a 260k-file folder no manifest covered could not be moved to
 * the A&P workspace, because the guard blocked the move it exists to cause.
 */
const OFFBOARD_TOOL =
  /^\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*(?:(?:\/bin\/)?bash\s+|python3?\s+(?:-I\s+)?)?(?:\S*\/)?operator\/ops-machine\/(?:move-to-ops\.sh|move-by-folder\.sh|move-from-host\.sh|laptop-offboard\.py)(?:\s+[^;&|`$<>()\n]*)?$/

/** A string field of a parsed JSON object, or '' (external input: never cast). */
function str(obj, key) {
  if (obj === null || typeof obj !== 'object') return ''
  const v = /** @type {Record<string, unknown>} */ (obj)[key]
  return typeof v === 'string' ? v : ''
}

/** @returns {{slug: string, alias: string}[]} */
function workspaces() {
  try {
    const f = path.join(HOME, '.claude', 'smd-ops-workspaces.json')
    /** @type {unknown} */
    const j = JSON.parse(fs.readFileSync(f, 'utf8'))
    const ws =
      j !== null && typeof j === 'object'
        ? /** @type {Record<string, unknown>} */ (j).workspaces
        : null
    if (ws === null || typeof ws !== 'object') return []
    return Object.entries(/** @type {Record<string, unknown>} */ (ws)).map(([slug, w]) => {
      const aliases =
        w !== null && typeof w === 'object'
          ? /** @type {Record<string, unknown>} */ (w).aliases
          : null
      const first = Array.isArray(aliases) && typeof aliases[0] === 'string' ? aliases[0] : slug
      return { slug, alias: first }
    })
  } catch {
    return []
  }
}

function which(text, isBash) {
  // Edit/Write: only a write INTO a data folder counts; editing tool code is fine.
  const res = isBash ? [...TOOL_PATTERNS, ...DATA_PATTERNS, ...PULL_PATTERNS] : DATA_PATTERNS
  for (const re of res) {
    const m = text.match(re)
    if (m) return m[0].trim()
  }
  return null
}

function guess(text, list) {
  for (const w of list) if (text.includes(w.slug)) return w
  return list.length === 1 ? list[0] : null
}

try {
  if (fs.existsSync(MARKER)) process.exit(0)
  /** @type {unknown} */
  const payload = JSON.parse(fs.readFileSync(0, 'utf8'))
  const input =
    payload !== null && typeof payload === 'object'
      ? /** @type {Record<string, unknown>} */ (payload).tool_input
      : null
  const command = str(input, 'command')
  const isBash = command !== ''
  const raw = isBash ? command : str(input, 'file_path') || str(input, 'notebook_path')
  if (!raw) process.exit(0)
  // An absolute laptop path to a data folder also counts for Edit/Write.
  const text = isBash ? raw : raw.replace(HOME, '~')
  const hit = which(text, isBash)
  if (!hit) process.exit(0)
  if (isBash && OFFBOARD_TOOL.test(command)) process.exit(0)

  const reason = process.env.SS_ALLOW_CLIENT_DATA_ON_LAPTOP
  if (reason && reason !== '0') {
    try {
      fs.mkdirSync(path.join(HOME, '.claude'), { recursive: true })
      fs.appendFileSync(
        path.join(HOME, '.claude', 'client-data-guard-audit.log'),
        `${new Date().toISOString()}\t${str(payload, 'session_id') || '?'}\t${reason}\t${hit}\n`
      )
    } catch {
      /* auditing never blocks the act it records */
    }
    process.exit(0)
  }

  const list = workspaces()
  const w = guess(text, list)
  const alias = w ? w.alias : '<client>'
  const known = list.length
    ? list.map((x) => `${x.alias} (${x.slug})`).join(', ')
    : 'none installed'
  process.stderr.write(
    `client-data-guard: this would put client documents on a laptop (matched "${hit}").\n` +
      `Client documents are opened only in that client's ops workspace.\n` +
      `Do not run it here. Instead:\n` +
      `  1. hand the task over: ops-handoff ${alias} "<the task, one paragraph, everything the next session needs>"\n` +
      `  2. tell the Captain, in one line: type \`ops ${alias}\` to continue on the workspace.\n` +
      `Known workspaces: ${known}.\n` +
      `Break-glass only (workspace down, client waiting): SS_ALLOW_CLIENT_DATA_ON_LAPTOP="<reason>" (audited); ` +
      `then offboard the laptop again (operator/ops-machine/laptop-offboard.py).\n`
  )
  process.exit(2)
} catch {
  process.exit(0)
}
