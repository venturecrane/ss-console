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
  '| the lane spool dir is writable | read `operator/runners/medchron/medchron/records_lane.py:40` |',
  '| the seat grants Smokeball file reads | read `operator/customers/ashton-price/customer.yaml:120` |',
  '## Workstreams',
  'Build the lane.',
].join('\n')

const MEASURED_ROW =
  '| the real request names 5 records across 9 candidate files | measured via `seat-probe ashton-price`: 5 records, 9 files, 4,775 pages unindexed |'

const MEASURED_CLIENT_PLAN = MECHANICS_CLIENT_PLAN.replace(
  '## Workstreams',
  `${MEASURED_ROW}\n## Workstreams`
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
