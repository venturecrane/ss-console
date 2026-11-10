/**
 * The premise gate blocks evidence-free plans and refuses to guess.
 *
 * The credibility properties, asserted by EXECUTING the hook:
 *   1. It denies a plan with no ## Premises section (Law 12 control: the
 *      case built to fail -- if this passes, the instrument is broken).
 *   2. It passes evidenced tables and the explicit opt-out.
 *   3. When it cannot determine the plan text it ALLOWS (warn-only) --
 *      a guessing gate must not claim to enforce (2026-08-01 critique
 *      consensus: the cross-session false-PASS/false-BLOCK is the failure).
 *   4. Exit 0 on every path.
 */
import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { isRehearsalRow, landsOnSeat } from '../.claude/hooks/plan-premise-gate.mjs'
import { INVENTORY_SEAT_PURPOSES } from '../scripts/lib/seat-purposes.generated.mjs'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const HOOK = join(REPO_ROOT, '.claude', 'hooks', 'plan-premise-gate.mjs')

let dir: string
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'ss-premise-gate-'))
})
afterEach(() => {
  rmSync(dir, { recursive: true, force: true })
})

function runHook(payload: unknown, env: Record<string, string> = {}) {
  try {
    const stdout = execFileSync('node', [HOOK], {
      input: typeof payload === 'string' ? payload : JSON.stringify(payload),
      encoding: 'utf8',
      env: { ...process.env, SS_BOARD_DIR: dir, ...env },
    })
    return { stdout, status: 0 }
  } catch (err) {
    const e = err as { status?: number; stdout?: string }
    return { stdout: e.stdout ?? '', status: e.status ?? -1 }
  }
}

function permission(r: { stdout: string }): string | null {
  if (!r.stdout.trim()) return null
  const parsed = JSON.parse(r.stdout) as { hookSpecificOutput?: { permissionDecision?: string } }
  return parsed.hookSpecificOutput?.permissionDecision ?? null
}

const GOOD_PLAN = [
  '# Some Plan',
  '## Premises',
  '| Premise | Evidence |',
  '|---|---|',
  '| board dir writable | live probe: `mkdir -p ~/.claude/ss-board` OK |',
  '| hook events registered | read `.claude/settings.json` |',
  '## Workstreams',
  'Build the thing.',
].join('\n')

const BARE_PLAN = [
  '# Some Plan',
  '## Workstreams',
  'Build the thing with no premises stated.',
].join('\n')

const HOLLOW_PLAN = [
  '# Some Plan',
  '## Premises',
  '| Premise | Evidence |',
  '|---|---|',
  '| the API supports it | TODO |',
].join('\n')

const OPTOUT_PLAN = [
  '# Tiny Plan',
  'Premises: none (no external premises)',
  'Rename the variable.',
].join('\n')

const payload = (extra: Record<string, unknown> = {}) => ({
  session_id: 's',
  cwd: '/tmp/t',
  hook_event_name: 'PreToolUse',
  tool_name: 'ExitPlanMode',
  ...extra,
})

describe('premise-gate: via tool_input.plan', () => {
  it('DENIES a plan with no premises (Law 12 control)', () => {
    const r = runHook(payload({ tool_input: { plan: BARE_PLAN } }))
    expect(r.status).toBe(0)
    expect(permission(r)).toBe('deny')
    expect(r.stdout).toMatch(/## Premises/)
  })

  it('denies hollow evidence (TODO rows)', () => {
    expect(permission(runHook(payload({ tool_input: { plan: HOLLOW_PLAN } })))).toBe('deny')
  })

  it('passes an evidenced premise table', () => {
    expect(permission(runHook(payload({ tool_input: { plan: GOOD_PLAN } })))).toBeNull()
  })

  it('passes the explicit opt-out', () => {
    expect(permission(runHook(payload({ tool_input: { plan: OPTOUT_PLAN } })))).toBeNull()
  })
})

// Law 16 corollary (2026-11-03, the A&P matter-records lane): a client-work
// plan whose premises are all mechanics is bounced until one row carries a
// number measured on the real request or the real inputs.
const MECHANICS_CLIENT_PLAN = [
  '# Matter records lane for the ashton-price seat',
  '## Premises',
  '| Premise | Evidence |',
  '|---|---|',
  '| the lane spool dir is writable | read `operator/runners/jobs/jobs/records_lane.py:40` |',
  '| the seat grants Smokeball file reads | read `operator/customers/ashton-price/customer.yaml:120` |',
  '## Workstreams',
  'Build the lane.',
].join('\n')

const MEASURED_ROW =
  '| the real request names 5 records across 9 candidate files | measured via `seat-probe ashton-price`: 5 records, 9 files, 4,775 pages unindexed |'

// Law 18 (2026-11-04): a client plan that lands on a seat also names its
// off-seat rehearsal, so the measured plan below carries one.
const REHEARSAL_ROW =
  '| the lane replays at $0 on copies of the seat indexes in the ops workspace against the 11-02 answer key | rehearsal via `ops-handoff ashton-price` |'

const MEASURED_ONLY_SEAT_PLAN = MECHANICS_CLIENT_PLAN.replace(
  '## Workstreams',
  `${MEASURED_ROW}\n## Workstreams`
)

const MEASURED_CLIENT_PLAN = MECHANICS_CLIENT_PLAN.replace(
  '## Workstreams',
  `${MEASURED_ROW}\n${REHEARSAL_ROW}\n## Workstreams`
)

const REHEARSAL_OPTOUT_PLAN = MEASURED_ONLY_SEAT_PLAN.replace(
  '## Workstreams',
  'Off-seat rehearsal: none (a config-only change; the seat self-test is the proof)\n## Workstreams'
)

const NUMBER_WITHOUT_PROBE_PLAN = MECHANICS_CLIENT_PLAN.replace(
  '## Workstreams',
  '| the matter is about 4,775 pages | read the dossier |\n## Workstreams'
)

const CLIENT_OPTOUT_PLAN = [
  '# Rename a variable in the seat helper',
  'Premises: none (no external premises)',
].join('\n')

describe('premise-gate: Law 16 corollary, client plans need a measured premise', () => {
  it('DENIES a client plan whose premises are all mechanics (control)', () => {
    const r = runHook(payload({ tool_input: { plan: MECHANICS_CLIENT_PLAN } }))
    expect(r.status).toBe(0)
    expect(permission(r)).toBe('deny')
    expect(r.stdout).toMatch(/Law 16 corollary/)
  })

  it('passes the same plan once one row is measured', () => {
    expect(permission(runHook(payload({ tool_input: { plan: MEASURED_CLIENT_PLAN } })))).toBeNull()
  })

  it('a number with no probe reference does not count as measured', () => {
    expect(permission(runHook(payload({ tool_input: { plan: NUMBER_WITHOUT_PROBE_PLAN } })))).toBe(
      'deny'
    )
  })

  it('a non-client plan with mechanics-only premises is unaffected', () => {
    expect(GOOD_PLAN).not.toMatch(/\d+\s*(pages|files|records)/)
    expect(permission(runHook(payload({ tool_input: { plan: GOOD_PLAN } })))).toBeNull()
  })

  it('"no matter" and "client-side" do not make a plan client work', () => {
    const plan = GOOD_PLAN.replace(
      'Build the thing.',
      'No matter the order, render it client-side.'
    )
    expect(permission(runHook(payload({ tool_input: { plan } })))).toBeNull()
  })

  it('"Premises: none" still passes on a client plan', () => {
    expect(permission(runHook(payload({ tool_input: { plan: CLIENT_OPTOUT_PLAN } })))).toBeNull()
  })
})

// Law 18 (2026-11-04): nothing runs first on a client seat. A client plan that
// lands on a seat names its off-seat rehearsal or states the opt-out line.
describe('premise-gate: Law 18, seat plans name their off-seat rehearsal', () => {
  it('DENIES a measured seat plan with no rehearsal row (control)', () => {
    const r = runHook(payload({ tool_input: { plan: MEASURED_ONLY_SEAT_PLAN } }))
    expect(r.status).toBe(0)
    expect(permission(r)).toBe('deny')
    expect(r.stdout).toMatch(/Law 18/)
  })

  it('passes the same plan once a row names the ops-workspace rehearsal', () => {
    expect(permission(runHook(payload({ tool_input: { plan: MEASURED_CLIENT_PLAN } })))).toBeNull()
  })

  it('passes with the explicit "Off-seat rehearsal: none (<why>)" line', () => {
    expect(permission(runHook(payload({ tool_input: { plan: REHEARSAL_OPTOUT_PLAN } })))).toBeNull()
  })

  it('a client plan that lands on no seat is unaffected', () => {
    const plan = [
      '# Quote page for a matter in the client portal',
      '## Premises',
      '| Premise | Evidence |',
      '|---|---|',
      '| the quote table carries the columns the page renders | read `src/lib/db/quotes.ts:12` |',
      '| the matter lists 3 records today | measured via `register list`: 3 records |',
      '## Workstreams',
      'Build the page.',
    ].join('\n')
    expect(plan).not.toMatch(
      /\bseats?\b|lane-proof|\brelease\b|ashton-price|pilot-smokeball|smd-staging|customer\.yaml/
    )
    expect(permission(runHook(payload({ tool_input: { plan } })))).toBeNull()
  })
})

// ADR 0107: the seat names in the Law 18 check come from the seat inventory,
// never slugs written into the hook, so a renamed or added seat needs no edit.
describe('premise-gate: Law 18 seat names come from each seat.yaml purpose', () => {
  const where = (purpose: string) =>
    INVENTORY_SEAT_PURPOSES.filter(([, p]) => p === purpose).map(([slug]) => slug)

  it('every seat named lands a plan on a seat', () => {
    for (const [slug] of INVENTORY_SEAT_PURPOSES) {
      expect(landsOnSeat(`change the persona on ${slug}`), String(slug)).toBe(true)
    }
  })

  it('naming a release twin or a demo seat is a rehearsal; naming a client seat is not', () => {
    const rehearsals = [...where('release-twin'), ...where('demo')]
    expect(rehearsals.length).toBeGreaterThan(0)
    for (const slug of rehearsals) {
      expect(isRehearsalRow(`| proven first on ${slug} | probe |`), String(slug)).toBe(true)
    }
    for (const slug of where('client')) {
      expect(isRehearsalRow(`| proven first on ${slug} | probe |`), String(slug)).toBe(false)
    }
    expect(isRehearsalRow('| proven on the release twin of the client seat | probe |')).toBe(true)
  })
})

describe('premise-gate: via transcript-derived plan file', () => {
  it('finds the session plan file named in the transcript and gates on it', () => {
    const plansDir = join(dir, '.claude', 'plans')
    mkdirSync(plansDir, { recursive: true })
    const planPath = join(plansDir, 'test-plan.md')
    writeFileSync(planPath, BARE_PLAN)
    const transcriptPath = join(dir, 'transcript.jsonl')
    writeFileSync(
      transcriptPath,
      JSON.stringify({ text: `Plan File Info: create your plan at ${planPath} using Write` }) + '\n'
    )
    const r = runHook(payload({ transcript_path: transcriptPath }))
    expect(permission(r)).toBe('deny')
    writeFileSync(planPath, GOOD_PLAN)
    expect(permission(runHook(payload({ transcript_path: transcriptPath })))).toBeNull()
  })
})

describe('premise-gate: refuses to guess, fails open', () => {
  it('ALLOWS when plan text is unavailable (warn-only, never deny on a guess)', () => {
    const r = runHook(payload({ transcript_path: join(dir, 'missing.jsonl') }))
    expect(r.status).toBe(0)
    expect(permission(r)).toBeNull()
  })

  it('allows when escape hatch set', () => {
    expect(
      permission(
        runHook(payload({ tool_input: { plan: BARE_PLAN } }), { SS_SKIP_PREMISE_GATE: '1' })
      )
    ).toBeNull()
  })

  it('exits 0 on malformed stdin', () => {
    expect(runHook('{{{').status).toBe(0)
  })

  it('ignores non-ExitPlanMode tool calls', () => {
    expect(
      permission(runHook(payload({ tool_name: 'Write', tool_input: { plan: BARE_PLAN } })))
    ).toBeNull()
  })
})

// Wave 2 PR 2 (2026-11-09): the measured-row unit list rejected real units.
// The wave-2 plan's own measured premise counted PRs, transcripts and tokens.
describe('premise-gate: measured rows accept the units a probe counts', () => {
  const plan = (row: string) =>
    [
      '# Lane report for the client seats',
      '## Premises',
      '| Premise | Evidence |',
      '|---|---|',
      row,
      '| the release ran first on the pilot | rehearsal via `lane-proof dry-run` |',
      '## Workstreams',
      'Build it.',
    ].join('\n')

  for (const row of [
    '| bodies name the firm | measured via `gh pr list --limit 1000`: 346 PRs |',
    '| sessions pass 250K | measured via `jq` over 48 transcripts |',
    '| median first call | measured via `jq .usage`: 193K tokens |',
    '| skills changed mid-session | `git log --oneline`: 21 commits |',
    '| long foreground waits | measured via `jq`: 630 calls over 270s |',
  ]) {
    it(`counts as measured: ${row}`, () => {
      expect(permission(runHook(payload({ tool_input: { plan: plan(row) } })))).toBeNull()
    })
  }

  it('a bare number with a probe and no unit still does not count (control)', () => {
    const r = runHook(payload({ tool_input: { plan: plan('| some count | `ls`: 12 |') } }))
    expect(permission(r)).toBe('deny')
    expect(r.stdout).toMatch(/Law 16 corollary/)
  })
})

// Wave 2 PR 2 (2026-11-09, PREV-3): when /critique saved critics beside the
// plan file, the plan must carry a "## Critic findings not adopted" section
// naming each of them with its own verdict. The control fixture is modeled on
// docs/plans/document-library/design-2026-11-06.md:124, which overruled its
// critique in a "Not adopted:" list inside a revision-history section.
describe('premise-gate: overruled critic findings are shown in the plan', () => {
  const EVIDENCED = [
    '## Premises',
    '| Premise | Evidence |',
    '|---|---|',
    '| the resolver reads the library | read `operator/runners/jobs/jobs/example.py:12` |',
  ].join('\n')

  const MODELED_ON_LIBRARY_PLAN = [
    '# Document library design',
    '## What a firm experiences',
    'Prose.',
    EVIDENCED,
    '## What changed in revision 2, and what was not adopted',
    '1. **Proofs that could not fail were replaced** (critique).',
    'Not adopted:',
    '- **Deferring the plain letter until asked** (critique). The acceptance test names it.',
    '- **Dropping two pieces as ahead of the evidence** (critique). Asked for by name.',
    '## For the list',
    'Nothing here.',
  ].join('\n')

  const SECTION = [
    '## Critic findings not adopted',
    '| Critic | Verdict | Finding not adopted | Reply |',
    '|---|---|---|---|',
    "| Devil's Advocate | Proceed with fixes | Deferring the plain letter | The acceptance test names it. |",
    '| Simplifier | Reconsider approach | Dropping two pieces | Asked for by name. |',
  ].join('\n')

  const CRITICS: Record<string, string> = {
    'devils-advocate.md':
      "## Devil's Advocate Critique\n\n**Strengths**\n- ok\n\n**Issues Found**\n1. Defer the letter\n\n**Verdict**: Proceed with fixes\n",
    'simplifier.md':
      '## Simplifier Critique\n\n1. Drop two pieces\n\n**Verdict**: Reconsider approach. Fewer pieces.\n',
  }

  function writePlan(text: string, critics: Record<string, string> | null) {
    const planPath = join(dir, 'plans', 'library-design.md')
    mkdirSync(join(dir, 'plans'), { recursive: true })
    writeFileSync(planPath, text)
    if (critics) {
      const cdir = join(dir, 'plans', 'library-design.critics')
      mkdirSync(cdir, { recursive: true })
      for (const [f, t] of Object.entries(critics)) writeFileSync(join(cdir, f), t)
    }
    return planPath
  }

  const viaPath = (planPath: string, plan?: string) =>
    runHook(payload({ tool_input: { planFilePath: planPath, ...(plan ? { plan } : {}) } }))

  it('DENIES the library-shaped plan whose critique was overruled out of sight (control)', () => {
    const planPath = writePlan(MODELED_ON_LIBRARY_PLAN, CRITICS)
    const r = viaPath(planPath)
    expect(r.status).toBe(0)
    expect(permission(r)).toBe('deny')
    expect(r.stdout).toMatch(/Critic findings not adopted/)
  })

  it('the same plan with no critics directory is unaffected', () => {
    const planPath = writePlan(MODELED_ON_LIBRARY_PLAN, null)
    expect(permission(viaPath(planPath))).toBeNull()
  })

  it('passes once the section names every critic with its own verdict', () => {
    const planPath = writePlan(`# Document library design\n${SECTION}\n${EVIDENCED}`, CRITICS)
    expect(permission(viaPath(planPath))).toBeNull()
  })

  it('denies a section that omits one critic', () => {
    const oneRow = SECTION.split('\n').slice(0, 4).join('\n')
    const planPath = writePlan(`# Plan\n${oneRow}\n${EVIDENCED}`, CRITICS)
    const r = viaPath(planPath)
    expect(permission(r)).toBe('deny')
    expect(r.stdout).toMatch(/omits critic\(s\): simplifier/)
  })

  it("denies a row that paraphrases the critic's verdict", () => {
    const paraphrased = SECTION.replace('Reconsider approach', 'Revise; fewer pieces')
    const planPath = writePlan(`# Plan\n${paraphrased}\n${EVIDENCED}`, CRITICS)
    const r = viaPath(planPath)
    expect(permission(r)).toBe('deny')
    expect(r.stdout).toMatch(/simplifier \(reconsider approach\)/)
  })

  it('passes the exact none line', () => {
    const planPath = writePlan(`# Plan\nCritic findings not adopted: none\n${EVIDENCED}`, CRITICS)
    expect(permission(viaPath(planPath))).toBeNull()
  })

  it('reads the plan text from tool_input.plan and the critics beside planFilePath', () => {
    const planPath = writePlan('# stale copy on disk', CRITICS)
    expect(permission(viaPath(planPath, MODELED_ON_LIBRARY_PLAN))).toBe('deny')
    expect(permission(viaPath(planPath, `# Plan\n${SECTION}\n${EVIDENCED}`))).toBeNull()
  })

  it('finds the plan file through the transcript when planFilePath is absent', () => {
    const plansDir = join(dir, '.claude', 'plans')
    mkdirSync(join(plansDir, 'p.critics'), { recursive: true })
    const planPath = join(plansDir, 'p.md')
    writeFileSync(planPath, MODELED_ON_LIBRARY_PLAN)
    for (const [f, t] of Object.entries(CRITICS)) writeFileSync(join(plansDir, 'p.critics', f), t)
    const transcriptPath = join(dir, 't.jsonl')
    writeFileSync(transcriptPath, JSON.stringify({ text: `Plan File Info: ${planPath}` }) + '\n')
    expect(permission(runHook(payload({ transcript_path: transcriptPath })))).toBe('deny')
    writeFileSync(planPath, `# Plan\n${SECTION}\n${EVIDENCED}`)
    expect(permission(runHook(payload({ transcript_path: transcriptPath })))).toBeNull()
  })

  it('reports a premises failure and a critics failure together, in one bounce', () => {
    const planPath = writePlan('# Plan with nothing', CRITICS)
    const r = viaPath(planPath)
    expect(r.stdout).toMatch(/## Premises/)
    expect(r.stdout).toMatch(/Critic findings not adopted/)
  })
})
