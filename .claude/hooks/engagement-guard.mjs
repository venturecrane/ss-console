#!/usr/bin/env node
/**
 * engagement-guard.mjs -- PreToolUse hook on Edit|Write|NotebookEdit.
 *
 * THE RULE (Law 2, docs/doctrine/agent-operating-doctrine.md): a session
 * does not mutate files under operator/customers/<slug>/ until it has read
 * that engagement's dossier.md. The dossier carries the relationship map,
 * commercial rationale, and canonical-document ledger; editing engagement
 * artifacts without it is how the 2026-07-26 Christa-reply incident
 * happened (an approved client draft edited, two commercial terms invented,
 * by an agent that had not loaded the engagement posture).
 *
 * CROSS-REPO. Engagement material lives in the private `venturecrane/engagements`
 * repo; only `customer.yaml`, `routine-grid.yaml`, and `_template/` remain
 * here. So the dossier being read and the file being written are routinely in
 * different checkouts. See .claude/hooks/lib/engagement-paths.mjs for how that
 * is resolved and why the read log is pinned to one absolute location.
 *
 * Decision chain:
 *   1. SS_ALLOW_UNREAD_ENGAGEMENT_WRITES matches this target -> allow, audited
 *   2. target not under operator/customers/ -> allow (out of scope)
 *   3. slug starts with '_' (template)      -> allow
 *   4. target IS the slug's dossier.md      -> allow (authoring the dossier
 *      must not require having read it; that would block bootstrap)
 *   5. no dossier found in any tree:
 *        engagements repo PRESENT -> allow (genuine bootstrap, no dossier yet)
 *        engagements repo ABSENT  -> BLOCK (broken checkout; Law 2 cannot be
 *        evaluated, and "cannot evaluate" must never read as "permitted")
 *   6. dossier suffix in this session's read log -> allow
 *   7. dossier suffix in ANY read log fresher than SUBAGENT_WINDOW_MS
 *      -> allow (subagents can carry a different session_id than the parent
 *      that did the reading; hygiene posture accepts the looseness)
 *   8. otherwise -> exit 2, remedy on stderr naming the exact file.
 *
 * Matching is by `operator/customers/...` SUFFIX, never absolute path, so
 * worktree, primary-checkout, and engagements-repo paths all compare equal.
 * Read logs are written by read-tracker.mjs.
 *
 * Failure posture: fail open on CRASHES (a broken hook must not brick
 * engagement work -- the worktree-guard idiom), but fail CLOSED on the one
 * decision that used to be open and is now across a repo boundary: a missing
 * engagements checkout. Bash writes are not intercepted; that gap is
 * prose-covered in CLAUDE.md, same as the worktree rule.
 */

import fs from 'node:fs'
import path from 'node:path'
import {
  CUSTOMERS_MARKER,
  engagementsDir,
  engagementsRepoPresent,
  findDossier,
  hatchAuditFile,
  readLogDir,
  suffixOf,
} from './lib/engagement-paths.mjs'

const SUBAGENT_WINDOW_MS = 8 * 60 * 60 * 1000 // 8h: covers a long session's subagents

/**
 * The Captain escape hatch, path-scoped.
 *
 * It used to be `=1`: a global off-switch that disabled Law 2 for every
 * engagement at once, indefinitely, leaving no trace. The value must now name
 * a path fragment, so the exemption covers the file the Captain meant and
 * nothing else, and every use is appended to an audit file that
 * reflex-primer.sh surfaces. A permanently-exported hatch becomes visible
 * rather than quiet.
 */
function hatchAllows(suffix) {
  const raw = process.env.SS_ALLOW_UNREAD_ENGAGEMENT_WRITES
  if (typeof raw !== 'string' || raw.length === 0) return false

  if (raw === '1' || raw.toLowerCase() === 'true') {
    process.stderr.write(
      `engagement-guard: SS_ALLOW_UNREAD_ENGAGEMENT_WRITES=1 is no longer accepted -- a global ` +
        `off-switch disabled Law 2 for every engagement at once and left no trace. Scope it to the ` +
        `path you mean, e.g. SS_ALLOW_UNREAD_ENGAGEMENT_WRITES="${suffix}". Every use is audited.\n`
    )
    return false
  }
  return suffix.includes(raw)
}

function auditHatch(suffix, scope, sessionId) {
  try {
    const file = hatchAuditFile()
    fs.mkdirSync(path.dirname(file), { recursive: true })
    fs.appendFileSync(file, `${new Date().toISOString()}\t${sessionId}\t${scope}\t${suffix}\n`)
  } catch {
    /* auditing must never block the write it is recording */
  }
}

/** Subagent fallback: does any sufficiently fresh read log show the dossier? */
function freshLogSawDossier(logDir, dossierSuffix) {
  const now = Date.now()
  for (const f of fs.readdirSync(logDir)) {
    try {
      const full = path.join(logDir, f)
      if (now - fs.statSync(full).mtimeMs > SUBAGENT_WINDOW_MS) continue
      if (fs.readFileSync(full, 'utf8').includes(dossierSuffix)) return true
    } catch {
      /* per-file read errors don't decide anything */
    }
  }
  return false
}

try {
  const payload = JSON.parse(fs.readFileSync(0, 'utf8'))
  const target = payload?.tool_input?.file_path ?? payload?.tool_input?.notebook_path
  if (typeof target !== 'string' || target.length === 0) process.exit(0)

  const suffix = suffixOf(target)
  if (!suffix) process.exit(0)

  const slug = suffix.slice(CUSTOMERS_MARKER.length).split('/')[0]
  if (!slug || slug.startsWith('_')) process.exit(0)

  const sessionId =
    typeof payload?.session_id === 'string' && payload.session_id.length > 0
      ? payload.session_id.replaceAll(/[^A-Za-z0-9_-]/g, '')
      : 'unknown-session'

  if (hatchAllows(suffix)) {
    auditHatch(suffix, process.env.SS_ALLOW_UNREAD_ENGAGEMENT_WRITES, sessionId)
    process.exit(0)
  }

  const dossierSuffix = `${CUSTOMERS_MARKER}${slug}/dossier.md`
  if (suffix === dossierSuffix) process.exit(0)

  const projectDir = process.env.CLAUDE_PROJECT_DIR || payload?.cwd || process.cwd()
  const dossierPath = findDossier(dossierSuffix, { targetPath: target, projectDir })

  if (!dossierPath) {
    // No dossier anywhere. Whether that is innocent depends entirely on
    // whether the repo that holds dossiers is even on this machine.
    if (engagementsRepoPresent()) process.exit(0) // bootstrap: engagement has no dossier yet

    process.stderr.write(
      `engagement-guard: the engagements repo is not checked out at ${engagementsDir()}, so Law 2 ` +
        `cannot be evaluated for "${suffix}" -- and "cannot evaluate" must not read as "permitted". ` +
        `Client dossiers and correspondence live in the private repo. Clone it:\n\n` +
        `  git clone https://github.com/venturecrane/engagements.git ${engagementsDir()}\n\n` +
        `(Set SS_ENGAGEMENTS_DIR if you keep it elsewhere.)\n`
    )
    process.exit(2)
  }

  const logDir = readLogDir()
  if (fs.existsSync(logDir)) {
    const own = path.join(logDir, sessionId)
    if (fs.existsSync(own) && fs.readFileSync(own, 'utf8').includes(dossierSuffix)) {
      process.exit(0)
    }
    if (freshLogSawDossier(logDir, dossierSuffix)) process.exit(0)
  }

  process.stderr.write(
    `engagement-guard: "${suffix}" is engagement material for "${slug}", and this session has not read ` +
      `${dossierSuffix}. Read it first at ${dossierPath} (relationship map, commercial rationale, ` +
      `canonical-document ledger), then retry the edit. See Law 2, ` +
      `docs/doctrine/agent-operating-doctrine.md. (Captain-only escape hatch, path-scoped and audited: ` +
      `SS_ALLOW_UNREAD_ENGAGEMENT_WRITES="${suffix}".)\n`
  )
  process.exit(2)
} catch {
  process.exit(0) // fail open on crashes: hygiene plumbing, not a safety gate
}
