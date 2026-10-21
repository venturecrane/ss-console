#!/usr/bin/env node
/**
 * Plan Premise Gate (PreToolUse, matcher: ExitPlanMode).
 *
 * Why this exists (2026-08-01 autopsy): "What is the point of wired and plan
 * mode and critique if every single session ends up with us surprising
 * ourselves?" The mid-build surprises of 07-31/08-01 were, without exception,
 * premise failures -- a script needing a module nobody checked for (boto3), a
 * voice corpus that did not exist, an API field assumed present, work already
 * merged by a peer but believed open. /critique tests a plan's REASONING;
 * nothing tested its PREMISES. This gate does: a plan cannot leave plan mode
 * without a `## Premises` section whose rows carry evidence, or an explicit
 * opt-out for plans with no external premises.
 *
 * Plan text source, in order (per the 2026-08-01 critique consensus -- a
 * cross-session file hunt can silently gate one session on another session's
 * plan, and a guessing gate must not claim to enforce):
 *   1. tool_input.plan, when this harness provides it
 *   2. the session's OWN plan file, parsed from its transcript ("Plan File
 *      Info" system line names the exact path; the transcript is per-session,
 *      so this cannot cross sessions)
 *   3. neither -> WARN-ONLY: log, allow, never deny on a guess.
 *
 * Fail-open on every error the script catches. A gate that cannot start or
 * times out blocks instead (onFailure "block" in settings.json), since then it
 * never evaluated the plan at all. Escape hatch: SS_SKIP_PREMISE_GATE=1.
 *
 * Env: SS_BOARD_DIR (log dir), SS_SKIP_PREMISE_GATE.
 */
import { readFileSync, mkdirSync, appendFileSync } from 'node:fs'
import { join } from 'node:path'
import { homedir } from 'node:os'

const EVIDENCE_RE = /(`[^`]+`|vfy_[0-9A-Z]+|[\w./-]+\.(ts|mjs|sh|py|md|json|yaml|yml):\d+|https?:\/\/|probe|docs fetch|live |read )/i
const HOLLOW_RE = /\b(TODO|TBD|\?\?\?|fill in)\b/i

function log(entry) {
  try {
    const dir = process.env.SS_BOARD_DIR || join(homedir(), '.claude', 'ss-board')
    mkdirSync(dir, { recursive: true })
    appendFileSync(join(dir, 'premise-gate.log'), JSON.stringify({ ts: new Date().toISOString(), ...entry }) + '\n')
  } catch {
    /* never block on logging */
  }
}

function deny(reason) {
  process.stdout.write(
    JSON.stringify({
      hookSpecificOutput: {
        hookEventName: 'PreToolUse',
        permissionDecision: 'deny',
        permissionDecisionReason: reason,
      },
    }),
  )
}

/** Find the session's own plan file path from its transcript: the plan-mode
 *  system message names it ("Plan File Info ... /Users/x/.claude/plans/<name>.md").
 *  Scan from the end so a session with several plan-mode entries gets the
 *  newest. Per-session by construction: it is THIS session's transcript. */
export function planPathFromTranscript(transcriptText) {
  const re = /([\w/.-]+\/\.claude\/plans\/[\w.-]+\.md)/g
  let last = null
  for (const m of transcriptText.matchAll(re)) last = m[1]
  return last
}

/** The check itself: a `## Premises` section with at least one table row
 *  carrying evidence and no hollow rows, or the explicit opt-out line. */
export function premisesOk(planText) {
  if (/premises:\s*none\s*\(no external premises\)/i.test(planText)) {
    return { ok: true, mode: 'opt-out' }
  }
  const m = planText.match(/^##\s*Premises\b[\s\S]*?(?=^##\s|\n*$(?![\s\S]))/m)
  if (!m) return { ok: false, why: 'no ## Premises section and no explicit opt-out' }
  const rows = m[0]
    .split('\n')
    .filter((l) => l.trim().startsWith('|'))
    .filter((l) => !/^\|\s*-{2,}/.test(l.trim()) && !/^\|\s*Premise\s*\|/i.test(l.trim()))
  if (rows.length === 0) return { ok: false, why: 'a ## Premises section with no table rows' }
  const hollow = rows.filter((r) => HOLLOW_RE.test(r) || r.split('|').filter((c) => c.trim()).length < 2)
  if (hollow.length > 0) return { ok: false, why: `${hollow.length} premise row(s) with hollow or missing evidence` }
  const evidenced = rows.filter((r) => EVIDENCE_RE.test(r))
  if (evidenced.length === 0) return { ok: false, why: 'no premise row carries recognizable evidence (a command, file:line, vfy_ id, doc fetch, or probe note)' }
  return { ok: true, mode: 'table', rows: rows.length }
}

const TEACH =
  'Plans exit plan mode only with probed premises (2026-08-01 trust-restoration gate). Add to the plan file:\n' +
  '## Premises\n' +
  '| Premise | Evidence |\n|---|---|\n| <what the plan assumes> | <command output, file:line, vfy_ id, or doc fetch> |\n' +
  'Cover the four killers: environment/deps, data existence, API/tool shape, and current state (is this already built or merged?).\n' +
  'A plan with genuinely no external premises may instead state exactly: "Premises: none (no external premises)".\n' +
  'Escape hatch (Captain-sanctioned only): SS_SKIP_PREMISE_GATE=1.'

function main() {
  let payload
  try {
    payload = JSON.parse(readFileSync(0, 'utf8'))
  } catch {
    return
  }
  if (process.env.SS_SKIP_PREMISE_GATE === '1') {
    log({ event: 'skipped-by-env' })
    return
  }
  if (payload.tool_name && payload.tool_name !== 'ExitPlanMode') return

  // Premise probe for the contested harness field.
  log({
    event: 'invoked',
    tool_input_keys: payload.tool_input ? Object.keys(payload.tool_input).sort() : null,
    has_plan_field: typeof payload.tool_input?.plan === 'string',
  })

  let planText = null
  let source = null
  if (typeof payload.tool_input?.plan === 'string' && payload.tool_input.plan.trim() !== '') {
    planText = payload.tool_input.plan
    source = 'tool_input'
  } else if (typeof payload.transcript_path === 'string') {
    try {
      const transcript = readFileSync(payload.transcript_path, 'utf8')
      const path = planPathFromTranscript(transcript)
      if (path) {
        planText = readFileSync(path, 'utf8')
        source = 'transcript-derived:' + path
      }
    } catch {
      /* fall through to warn-only */
    }
  }

  if (planText === null) {
    // A guessing gate must not claim to enforce.
    log({ event: 'warn-only', why: 'plan text unavailable from tool_input and transcript' })
    return
  }

  const check = premisesOk(planText)
  log({ event: 'evaluated', source, ...check })
  if (!check.ok) {
    deny(`[premise-gate] This plan has ${check.why}. ${TEACH}`)
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  try {
    main()
  } catch {
    /* fail open */
  }
  process.exit(0)
}
