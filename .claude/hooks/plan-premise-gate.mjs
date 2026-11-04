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

/**
 * Law 16 corollary (2026-11-03, the A&P matter-records lane): a plan for
 * client work must carry at least one premise MEASURED on the real request
 * and the real inputs. That plan had eleven mechanical premises (permissions,
 * spool, lanes, caps) and none about the client's request or file set; it was
 * built, reviewed, released, and failed its first dry-run two ways a $0
 * measurement on the seat would have shown (five records parsed as seven; a
 * cost pre-flight of 4,775 pages against a $5 cap).
 *
 * CLIENT_RE is a heuristic for "this plan is client work". "Operator" is
 * case-sensitive (the product, not the word); "matter" skips "no matter" /
 * "doesn't matter"; "client" skips "client-side". A row is MEASURED when it
 * carries both a number with a unit (MEASURE_RE) and a probe reference
 * (PROBE_REF_RE) in the same row.
 */
const CLIENT_RE =
  /(\bseats?\b|\bclients?\b(?![- ]side)|(?<!\bno |n't |\bnot )\bmatters?\b|customer\.yaml|ashton-price|\blanes?\b)/i
const OPERATOR_RE = /\bOperators?\b/
const MEASURE_RE =
  /(\d[\d,.]*\s*(pages?|files?|rows?|bytes?|[KMG]B|seconds?|requests?|records?|matters?)\b|\$\s?\d|\bcount\b[:=]?\s*\d|\d[\d,.]*\s*count\b)/i
const PROBE_REF_RE = /(`[^`]+`|seat-probe|ops-handoff|fleet status|vfy_[0-9A-Z]+|\bmeasured\b)/i

export function isClientPlan(planText) {
  return CLIENT_RE.test(planText) || OPERATOR_RE.test(planText)
}

export function isMeasuredRow(row) {
  return MEASURE_RE.test(row) && PROBE_REF_RE.test(row)
}

const MEASURE_TEACH =
  'Law 16 corollary (docs/doctrine/agent-operating-doctrine.md): a client-work plan needs at least one Premises row MEASURED read-only on the real request and real inputs, with the number and its probe in the row (e.g. "| request names 5 records | measured via `seat-probe ...`: 5 records, 9 files, 4,775 pages unindexed |"); a plan whose premises are all mechanics is not approvable.'

/**
 * Law 18 (2026-11-04, the records job and the jobs-runner rename): nothing
 * runs first on a client seat. A client-work plan whose effect lands on a seat
 * (SEAT_RE) must carry a premise row naming its off-seat rehearsal (the ops
 * workspace, an answer key, smd-staging or the pilot, a lane-proof dry-run), or
 * state the opt-out line with its reason. The 2026-11-02 records job was built
 * and tested on the live client seat for twenty hours (about $58, two wrong
 * emails to the attorney); a $0 rehearsal on copies of the seat's own indexes
 * found four defects in minutes once it was finally run.
 */
const SEAT_RE = /(\bseats?\b|lane-proof|\brelease\b|ashton-price|pilot-smokeball|smd-staging|customer\.yaml)/i
const REHEARSAL_RE = /(rehears|ops workspace|ops-handoff|answer key|smd-staging|pilot-smokeball|lane-proof|dry-run|\$\s?0\b)/i
const REHEARSAL_OPTOUT_RE = /off-seat rehearsal:\s*none\s*\(/i

const REHEARSAL_TEACH =
  'Law 18 (docs/doctrine/agent-operating-doctrine.md): a client-work plan that lands on a seat needs one Premises row naming its off-seat rehearsal, the proof that runs BEFORE the client seat (e.g. "| the lane replays at $0 on copies of the seat indexes in the ops workspace against the answer key | rehearsal via `ops-handoff <client>` |", or staging / pilot-smokeball / a lane-proof dry-run), or the explicit line "Off-seat rehearsal: none (<why>)". Nothing runs first on a client seat.'

export function landsOnSeat(planText) {
  return SEAT_RE.test(planText)
}

export function isRehearsalRow(row) {
  return REHEARSAL_RE.test(row)
}

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
  if (isClientPlan(planText)) {
    const measured = rows.filter(isMeasuredRow).length
    if (measured === 0) {
      return { ok: false, mode: 'client', why: 'client-work premises that are all mechanics: no row carries a number measured on the real request or inputs', teach: MEASURE_TEACH }
    }
    const rehearsed = rows.filter(isRehearsalRow).length
    if (landsOnSeat(planText) && rehearsed === 0 && !REHEARSAL_OPTOUT_RE.test(planText)) {
      return { ok: false, mode: 'rehearsal', why: 'a client plan that lands on a seat with no premise row naming its off-seat rehearsal', teach: REHEARSAL_TEACH }
    }
    return { ok: true, mode: 'table', client: true, measured, rehearsed, rows: rows.length }
  }
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

  const { planText, source } = loadPlanText(payload)
  if (planText === null) {
    // A guessing gate must not claim to enforce.
    log({ event: 'warn-only', why: 'plan text unavailable from tool_input and transcript' })
    return
  }

  const { teach, ...check } = premisesOk(planText)
  log({ event: 'evaluated', source, ...check })
  if (!check.ok) {
    deny(`[premise-gate] This plan has ${check.why}. ${teach ?? TEACH}`)
  }
}

/** Plan text from tool_input, else from the session's own plan file named in
 *  its transcript; { planText: null } when neither is available. */
function loadPlanText(payload) {
  if (typeof payload.tool_input?.plan === 'string' && payload.tool_input.plan.trim() !== '') {
    return { planText: payload.tool_input.plan, source: 'tool_input' }
  }
  if (typeof payload.transcript_path !== 'string') return { planText: null, source: null }
  try {
    const path = planPathFromTranscript(readFileSync(payload.transcript_path, 'utf8'))
    if (path) return { planText: readFileSync(path, 'utf8'), source: 'transcript-derived:' + path }
  } catch {
    /* fall through to warn-only */
  }
  return { planText: null, source: null }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  try {
    main()
  } catch {
    /* fail open */
  }
  process.exit(0)
}
