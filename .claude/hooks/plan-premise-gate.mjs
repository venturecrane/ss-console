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
 * Since 2026-11-09 it also checks overruled critic findings: when /critique
 * saved its critics beside the plan file (`<plan>.critics/`), the plan must
 * carry a "## Critic findings not adopted" section naming each of them.
 *
 * Plan text source, in order (per the 2026-08-01 critique consensus -- a
 * cross-session file hunt can silently gate one session on another session's
 * plan, and a guessing gate must not claim to enforce):
 *   1. tool_input.plan, when this harness provides it
 *   2. tool_input.planFilePath (ExitPlanMode's own field), else the
 *      session's OWN plan file, parsed from its transcript ("Plan File
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
import { readFileSync, mkdirSync, appendFileSync, existsSync, readdirSync, statSync } from 'node:fs'
import { join, basename } from 'node:path'
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
// The unit list was widened 2026-11-09 (wave 2 PR 2) after it rejected a real
// measured row ("1000 records ... 590 files (64 main + 526 subagent)" passed only
// on its records/files; "346 PRs", "48 transcripts", "193K tokens" did not).
// Units are things a probe counts; a bare number still never qualifies.
const MEASURE_UNITS =
  'pages?|files?|rows?|bytes?|[KMG]B|seconds?|requests?|records?|matters?|' +
  'PRs?|pull requests?|transcripts?|tokens?|commits?|calls?|sessions?|issues?|' +
  'tests?|lines?|jobs?|runs?|emails?|messages?|documents?|minutes?|hours?|days?|' +
  'plans?|hooks?|skills?|seats?|clients?|events?|entries|items?'
const MEASURE_RE = new RegExp(
  `(\\d[\\d,.]*[KMk]?\\s*(${MEASURE_UNITS})\\b|\\$\\s?\\d|\\bcount\\b[:=]?\\s*\\d|\\d[\\d,.]*\\s*count\\b)`,
  'i'
)
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

/**
 * Overruled critic findings (2026-11-09, wave 2 PR 2; retrospective PREV-3).
 * The document-library plan of 2026-11-06 overruled its critique in a bullet
 * list inside "What changed in revision 2" (docs/plans/document-library/
 * design-2026-11-06.md:124), where nobody approving the plan would look. Since
 * /critique now saves each critic's raw output beside the plan file, in
 * `<plan without .md>.critics/<perspective>.md`, the gate can see what the
 * critics said and check that the plan shows it: a section headed
 * `## Critic findings not adopted` naming every critic, carrying each critic's
 * own verdict when it is one of the three /critique verdicts, or the exact line
 * "Critic findings not adopted: none". No critics directory, no check: a plan
 * that was never critiqued is unaffected.
 */
const CRITIC_NONE_RE = /^\s*Critic findings not adopted:\s*none\.?\s*$/im
const CRITIC_SECTION_RE =
  /^(#{1,6})\s*Critic findings not adopted\b[^\n]*\n([\s\S]*?)(?=^#{1,6}\s|(?![\s\S]))/im
const CANONICAL_VERDICTS = ['proceed as-is', 'proceed with fixes', 'reconsider approach']

const CRITIC_TEACH =
  "Overruled critic findings (wave 2, PREV-3): /critique saved its critics beside this plan, so the plan must show what they said. Add as the plan's FIRST section:\n" +
  '## Critic findings not adopted\n' +
  "| Critic | Verdict (the critic's own line) | Finding not adopted | Reply |\n|---|---|---|---|\n| <perspective> | <its Verdict line, verbatim> | <its top issue title> | <why not adopted> |\n" +
  'one row per file in the .critics/ directory, or the exact line "Critic findings not adopted: none" when every finding was adopted.'

/** Lowercase letters and digits only, so "Devil's Advocate" meets "devils-advocate". */
function squash(text) {
  return String(text ?? '')
    .toLowerCase()
    .replace(/[^a-z0-9]/g, '')
}

/** The critics directory that /critique writes beside a plan file. */
export function criticsDirFor(planPath) {
  return String(planPath).replace(/\.md$/i, '') + '.critics'
}

/** One critic's verdict, when its text carries one of the three canonical verdicts. */
export function canonicalVerdict(criticText) {
  const line = String(criticText ?? '')
    .split('\n')
    .find((l) => /\bverdict\b/i.test(l))
  if (!line) return null
  const after = line.replace(/^[\s\S]*?\bverdict\b[*_`\s]*[:-]?[*_`\s]*/i, '').toLowerCase()
  return CANONICAL_VERDICTS.find((v) => after.startsWith(v)) ?? null
}

/** The critic files in a .critics directory: `{ name, title, verdict }` each. */
export function readCritics(dir) {
  return readdirSync(dir)
    .filter((f) => /\.md$/i.test(f) && statSync(join(dir, f)).isFile())
    .sort()
    .map((f) => {
      const text = readFileSync(join(dir, f), 'utf8')
      const heading = text.match(/^#{1,6}\s*(.+?)\s*$/m)?.[1] ?? ''
      return {
        name: basename(f, '.md'),
        title: heading.replace(/\bcritique\b/i, '').trim(),
        verdict: canonicalVerdict(text),
      }
    })
}

/**
 * The plan shows every critic: the none line, or a findings section in which
 * each critic is named (by its file name or its heading) and, where the critic
 * gave a canonical verdict, a line naming that critic carries the verdict.
 * @param {string} planText
 * @param {{ name: string, title?: string, verdict?: string | null }[]} critics
 */
export function criticFindingsOk(planText, critics) {
  if (!critics || critics.length === 0) return { ok: true, mode: 'no-critics' }
  if (CRITIC_NONE_RE.test(planText)) {
    return { ok: true, mode: 'critics-none', critics: critics.length }
  }
  const m = planText.match(CRITIC_SECTION_RE)
  if (!m) {
    return {
      ok: false,
      mode: 'critics',
      why: `${critics.length} critic file(s) beside the plan and no "## Critic findings not adopted" section (nor the line "Critic findings not adopted: none")`,
    }
  }
  const lines = m[2].split('\n').map(squash)
  const missing = []
  const noVerdict = []
  for (const c of critics) {
    const keys = [squash(c.name), squash(c.title)].filter((k) => k.length >= 3)
    const naming = lines.filter((l) => keys.some((k) => l.includes(k)))
    if (naming.length === 0) {
      missing.push(c.name)
      continue
    }
    if (c.verdict && !naming.some((l) => l.includes(squash(c.verdict)))) {
      noVerdict.push(`${c.name} (${c.verdict})`)
    }
  }
  if (missing.length > 0) {
    return {
      ok: false,
      mode: 'critics',
      why: `a critic findings section that omits critic(s): ${missing.join(', ')}`,
    }
  }
  if (noVerdict.length > 0) {
    return {
      ok: false,
      mode: 'critics',
      why: `a critic findings section without the critic's own verdict for: ${noVerdict.join(', ')}`,
    }
  }
  return { ok: true, mode: 'critics-section', critics: critics.length }
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

  const critic = criticCheck(planText, planFilePath(payload))
  if (critic) log({ event: 'critics-evaluated', ...critic })

  const reasons = []
  if (!check.ok) reasons.push(`This plan has ${check.why}. ${teach ?? TEACH}`)
  if (critic && !critic.ok) reasons.push(`This plan has ${critic.why}. ${CRITIC_TEACH}`)
  if (reasons.length > 0) deny(`[premise-gate] ${reasons.join('\n\n')}`)
}

/** The critic check, or null when there is no plan path or no critics beside it. */
function criticCheck(planText, planPath) {
  if (!planPath) return null
  const dir = criticsDirFor(planPath)
  try {
    if (!existsSync(dir) || !statSync(dir).isDirectory()) return null
    return { dir, ...criticFindingsOk(planText, readCritics(dir)) }
  } catch {
    return null
  }
}

/** The plan FILE path: tool_input.planFilePath (ExitPlanMode's own field),
 *  else the session's plan file named in its transcript; null when neither. */
function planFilePath(payload) {
  const given = payload.tool_input?.planFilePath
  if (typeof given === 'string' && given.trim() !== '') return given
  if (typeof payload.transcript_path !== 'string') return null
  try {
    return planPathFromTranscript(readFileSync(payload.transcript_path, 'utf8'))
  } catch {
    return null
  }
}

/** Plan text from tool_input, else from the plan file (tool_input.planFilePath
 *  or the session's own plan file named in its transcript); { planText: null }
 *  when neither is available. */
function loadPlanText(payload) {
  if (typeof payload.tool_input?.plan === 'string' && payload.tool_input.plan.trim() !== '') {
    return { planText: payload.tool_input.plan, source: 'tool_input' }
  }
  try {
    const path = planFilePath(payload)
    if (path) return { planText: readFileSync(path, 'utf8'), source: 'plan-file:' + path }
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
