#!/usr/bin/env node
/**
 * read-tracker.mjs -- PostToolUse hook on Read. Two jobs:
 *
 *  1. LOG: append the `operator/customers/...` suffix of every Read target to
 *     a per-session read log. The log is what engagement-guard.mjs consults to
 *     decide whether this session has loaded an engagement's dossier before
 *     writing into that engagement.
 *
 *  2. ADVISE (radar tier, Law 2): a Read of engagement correspondence
 *     without a prior read of that engagement's dossier.md emits an
 *     advisory to the model (exit 2 on PostToolUse surfaces stderr WITHOUT
 *     undoing the Read -- the tool already ran; nothing is blocked). This
 *     covers the analysis path: reviews that never write a file.
 *
 * CROSS-REPO. Engagement material lives in the private `venturecrane/engagements`
 * repo, so the dossier read logged here and the write it later authorizes are
 * routinely in different checkouts. The log therefore lives at ONE absolute
 * location shared by every repo and worktree; deriving it from the project dir
 * would split it in two and silently make the guard unsatisfiable. See
 * .claude/hooks/lib/engagement-paths.mjs.
 *
 * Suffix matching everywhere: paths are recorded and compared by their
 * `operator/customers/...` suffix, so worktree, primary-checkout, and
 * engagements-repo absolute paths never mismatch.
 *
 * Incident: 2026-07-26 Christa-reply session -- a client letter was
 * critiqued with the engagement posture unread. See Law 2,
 * docs/doctrine/agent-operating-doctrine.md.
 *
 * Failure posture: FAIL OPEN, silently. This is workflow hygiene in the
 * worktree-guard idiom, not a safety gate.
 */

import fs from 'node:fs'
import path from 'node:path'
import {
  CUSTOMERS_MARKER,
  findDossier,
  readLogDir,
  slugOf,
  suffixOf,
} from './lib/engagement-paths.mjs'

// Kept 45 days, longer than the obligation-capture-at-close review interval:
// that review joins these logs against ~/.claude/ss-obligation-journal, and a
// 7-day prune deleted the evidence before the review could read it (scored
// 2026-11-16). The log is keyed by session id, so age changes nothing else.
const LOG_TTL_MS = 45 * 24 * 60 * 60 * 1000 // prune logs older than 45 days

try {
  const payload = JSON.parse(fs.readFileSync(0, 'utf8'))
  const target = payload?.tool_input?.file_path ?? payload?.tool_input?.notebook_path
  if (typeof target !== 'string' || target.length === 0) process.exit(0)

  const suffix = suffixOf(target)
  if (!suffix) process.exit(0) // only engagement paths are tracked

  const sessionId =
    typeof payload?.session_id === 'string' && payload.session_id.length > 0
      ? payload.session_id.replaceAll(/[^A-Za-z0-9_-]/g, '')
      : 'unknown-session'

  const projectDir = process.env.CLAUDE_PROJECT_DIR || payload?.cwd || process.cwd()
  const logDir = readLogDir()
  fs.mkdirSync(logDir, { recursive: true })

  // Opportunistic prune: read logs are session-scoped, kept long enough to be
  // the evidence for the obligation-capture review, then dropped.
  const now = Date.now()
  for (const f of fs.readdirSync(logDir)) {
    try {
      const full = path.join(logDir, f)
      if (now - fs.statSync(full).mtimeMs > LOG_TTL_MS) fs.unlinkSync(full)
    } catch {
      /* prune is best-effort */
    }
  }

  const logFile = path.join(logDir, sessionId)
  const prior = fs.existsSync(logFile) ? fs.readFileSync(logFile, 'utf8') : ''
  fs.appendFileSync(logFile, suffix + '\n')

  // Advisory: correspondence read without the engagement dossier loaded.
  const slug = slugOf(suffix)
  const isCorrespondence = slug && suffix.includes(`${CUSTOMERS_MARKER}${slug}/correspondence/`)
  if (isCorrespondence && !slug.startsWith('_')) {
    const dossierSuffix = `${CUSTOMERS_MARKER}${slug}/dossier.md`
    const dossierPath = findDossier(dossierSuffix, { targetPath: target, projectDir })
    if (dossierPath && !prior.includes(dossierSuffix) && !suffix.endsWith('/dossier.md')) {
      process.stderr.write(
        `read-tracker (advisory, nothing blocked): you are reading ${slug} correspondence without having read ` +
          `${dossierSuffix} this session. The dossier carries the relationship map, commercial rationale, and ` +
          `canonical-document ledger this correspondence answers to. Read it at ${dossierPath} before forming ` +
          `a view. See Law 2, docs/doctrine/agent-operating-doctrine.md.\n`
      )
      process.exit(2)
    }
  }

  process.exit(0)
} catch {
  process.exit(0) // fail open: a broken tracker must not degrade Reads
}
