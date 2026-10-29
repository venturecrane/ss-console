/**
 * Situational memory: a saved lesson reaches the agent at the moment it applies.
 *
 * WHY. 2026-10-29 an agent wrote "that's in the range of $20 to $60 a month
 * against the firm's $5,150 monthly fee" and "fold it into the agreement at no
 * added fee". The Captain had corrected exactly this three times, the lesson
 * was saved in feedback_never_call_a_cost_small_against_the_fee.md, and no
 * session saw it: only MEMORY.md auto-loads, and the reply-contract fee rule
 * wanted a minimizing word the sentence did not have. The fix delivers the
 * memory itself, by trigger, at three moments: the Stop hook (the draft reply),
 * the Gmail PreToolUse gate (the draft email) and the prompt primer.
 *
 * The first describe block is the replay of that reply. It fails on main: the
 * module does not exist there, and main's fee rule passes the sentence.
 *
 * Every hook here is EXECUTED against a fixture store (SS_MEMORY_DIR) whose
 * frontmatter mirrors the live memory's triggers. The live store is untracked,
 * so memory-audit runs each live memory's trigger_example / trigger_counter at
 * SessionStart; the self-check test below proves that check can fail.
 */
import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, it, expect, beforeEach, afterEach } from 'vitest'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const HOOKS = join(REPO_ROOT, '.claude', 'hooks')
const REPLY = join(HOOKS, 'reply-contract.mjs')
const GATE = join(HOOKS, 'situational-gate.mjs')
const RECALL = join(HOOKS, 'situational-recall.mjs')
const RECURRENCE = join(HOOKS, 'memory-recurrence.mjs')
const LIB = join(HOOKS, 'lib', 'situational-memory.mjs')
const TRIGGER_TEST = join(HOOKS, 'memory-trigger-test.mjs')

// The live fee memory's triggers, verbatim as single-quoted YAML scalars.
const FEE_A =
  "'(?=.*(?:monthly (?:fee|subscription|rate|payment)|\\bthe (?:firm''s )?fee\\b(?! lien)|\\bthe retainer\\b(?! packet)|(?:\\$[\\d,]+|our|SMD''s) retainer|\\$\\s?5,150\\b))(?=.*(?:\\b(?:against|versus|vs\\.?|share of|fraction|small|tiny|negligible|next to|well within|many times|dwarfs?|percent of|(?:fee|retainer) covers?|covers? (?:it|that|this)|covered by)\\b|\\d\\s?% of))'"
const FEE_B =
  "'\\b(?:(?:at|for) no (?:added|extra|additional) (?:fee|charge)|eat the cost|absorb (?:the|that|this) cost|absorb it ourselves|not worth billing|rounding error|fold (?:it|this|that) into (?:the |our |their |the existing )?(?:agreement|retainer|fee|subscription|engagement))\\b'"
const FEE_P =
  "'\\b(?:pric(?:e|es|ed|ing)|margin|fold (?:it|this|that) in|(?:what|how much) (?:would|will|does|do) [^?.]{0,40}cost|cost of goods|cogs)\\b'"

const FEE_MEMORY = `---
name: feedback_never_call_a_cost_small_against_the_fee
description: "Never set a cost beside the client's fee: report it as ADDED cost of goods with its basis; pricing is the Captain's call."
metadata:
  type: feedback
trigger_reply: ${FEE_A}
trigger_reply: ${FEE_B}
trigger_prompt: ${FEE_P}
trigger_since: '2026-10-29'
---

Report a projected or measured cost as the number, with its basis.

**How to apply:** State the cost as ADDED cost of goods on top of what the engagement already commits. Never set it beside the fee.

Related: nothing.
`

// The two sentences of the 2026-10-29 19:48Z reply, verbatim.
const INCIDENT_1 =
  "At, say, a 20-page scan every working day plus a few invoices a week, that's in the range of $20 to $60 a month against the firm's $5,150 monthly fee."
const INCIDENT_2 = "That's why I'd fold it into the agreement at no added fee."

let scratch: string
let store: string
let board: string
beforeEach(() => {
  scratch = mkdtempSync(join(tmpdir(), 'ss-situational-'))
  store = join(scratch, 'memory')
  board = join(scratch, 'board')
  mkdirSync(store)
  mkdirSync(board)
  writeFileSync(join(store, 'feedback_never_call_a_cost_small_against_the_fee.md'), FEE_MEMORY)
  writeFileSync(
    join(store, 'MEMORY.md'),
    '- [fee](feedback_never_call_a_cost_small_against_the_fee.md)\n'
  )
})
afterEach(() => rmSync(scratch, { recursive: true, force: true }))

function run(script: string, payload: unknown, extra: Record<string, string> = {}) {
  try {
    const stdout = execFileSync('node', [script], {
      input: JSON.stringify(payload),
      encoding: 'utf8',
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env, SS_MEMORY_DIR: store, SS_BOARD_DIR: board, ...extra },
    })
    return { status: 0, stdout, stderr: '' }
  } catch (err) {
    const e = err as { status?: number; stdout?: string; stderr?: string }
    return { status: e.status ?? -1, stdout: e.stdout ?? '', stderr: e.stderr ?? '' }
  }
}

const stop = (msg: string, session = 's1') => ({
  session_id: session,
  cwd: '/tmp/some-tree',
  hook_event_name: 'Stop',
  last_assistant_message: msg,
})

function reason(r: { stdout: string }): string | null {
  if (!r.stdout.trim()) return null
  const out = JSON.parse(r.stdout) as { decision?: string; reason?: string }
  return out.decision === 'block' ? (out.reason ?? '') : null
}

describe('the 2026-10-29 reply is stopped with the memory in front of the agent', () => {
  it('BLOCKS the incident reply and quotes the saved lesson', () => {
    const r = run(REPLY, stop(`${INCIDENT_1} ${INCIDENT_2}`))
    expect(r.status).toBe(0)
    const why = reason(r)
    expect(why).not.toBeNull()
    expect(why).toContain('feedback_never_call_a_cost_small_against_the_fee.md')
    expect(why).toContain('How to apply: State the cost as ADDED cost of goods')
    expect(why).toContain('$20 to $60 a month against')
  })

  it('BLOCKS each incident sentence on its own', () => {
    expect(reason(run(REPLY, stop(INCIDENT_1, 'a')))).toMatch(/saved lesson/)
    expect(reason(run(REPLY, stop(INCIDENT_2, 'b')))).toMatch(/saved lesson/)
  })

  it('BLOCKS it inside a blockquote or quotes too: client drafts sit there', () => {
    expect(reason(run(REPLY, stop(`I would tell Christa:\n\n> ${INCIDENT_2}`, 'c')))).toMatch(
      /saved lesson/
    )
  })

  it('logs the fire by memory name and sentence hash, never the text', () => {
    run(REPLY, stop(INCIDENT_1, 'logged'))
    const log = execFileSync('cat', [join(board, 'situational-memory.log')], { encoding: 'utf8' })
    expect(log).toContain('"memory":"feedback_never_call_a_cost_small_against_the_fee"')
    expect(log).toContain('"stage":"reply"')
    expect(log).not.toContain('scan every working day')
  })
})

describe('what must pass', () => {
  const CLEAN = [
    'The lane costs $20 to $60 a month in model tokens, added cost of goods on top of what the firm already pays for.',
    'A&P pays $5,150 a month by card.',
    'Spend caps stay at $500 a job and $750 a month as runaway guards.',
    'The retainer started on 9/15 and bills on the 15th.',
    'Her signed authorizations sit inside the retainer packet.',
    "I'll fold it in once you send the notes.",
    'Whether to fold the cost into the agreement or price it is your call.',
    `The failing text was:\n\n\`\`\`\n${INCIDENT_1}\n\`\`\``,
  ]
  for (const msg of CLEAN) {
    it(`passes: ${msg.slice(0, 70)}`, () => {
      expect(reason(run(REPLY, stop(msg)))).toBeNull()
    })
  }

  it('is silent with no store at all (fail open)', () => {
    expect(
      reason(run(REPLY, stop(INCIDENT_2), { SS_MEMORY_DIR: join(scratch, 'absent') }))
    ).toBeNull()
  })
})

describe('one bounce per reply, every rule in it', () => {
  it('a second identical reply passes, and logs that it passed under the guard', () => {
    expect(reason(run(REPLY, stop(INCIDENT_1, 'g')))).not.toBeNull()
    expect(reason(run(REPLY, stop(INCIDENT_1, 'g')))).toBeNull()
    const log = execFileSync('cat', [join(board, 'reply-contract.log')], { encoding: 'utf8' })
    expect(log).toContain('passed-under-guard')
  })

  it('another session in the same tree still bounces (the guard no longer keys on cwd)', () => {
    expect(reason(run(REPLY, stop(INCIDENT_1, 'h1')))).not.toBeNull()
    expect(reason(run(REPLY, stop(INCIDENT_2, 'h2')))).not.toBeNull()
  })

  it('the restatement right after a bounce always stands, even without stop_hook_active (no loop)', () => {
    expect(reason(run(REPLY, stop(INCIDENT_1, 'loop')))).not.toBeNull()
    // A different text that still violates, in the same session, inside the cooldown.
    expect(reason(run(REPLY, stop(INCIDENT_2, 'loop')))).toBeNull()
    // After the cooldown the session can be bounced again.
    expect(
      reason(run(REPLY, stop(INCIDENT_2, 'loop'), { SS_REPLY_CONTRACT_COOLDOWN_MS: '0' }))
    ).not.toBeNull()
  })

  it('never logs reply text, including the fee floor sentences', () => {
    run(REPLY, stop('That is small against the $5,000 monthly fee.', 'nolog'))
    const log = execFileSync('cat', [join(board, 'reply-contract.log')], { encoding: 'utf8' })
    expect(log).toContain('"feeSentences":1')
    expect(log).not.toContain('small against')
  })

  it('a memory hit and the hardcoded fee floor land in ONE block', () => {
    const why = reason(run(REPLY, stop('That is small against the $5,000 monthly fee.', 'i')))
    expect(why).toContain('saved lesson')
    expect(why).toContain('ADDED cost of goods')
  })
})

describe('the Gmail draft gate', () => {
  const draft = (body: string, session = 'd1') => ({
    session_id: session,
    cwd: '/tmp/some-tree',
    tool_name: 'mcp__claude_ai_Gmail__create_draft',
    tool_input: { to: ['x@example.com'], subject: 'Invoices', body },
  })

  it('DENIES a draft carrying the incident sentence, with the memory text', () => {
    const r = run(GATE, draft(`Hi Christa,\n\n${INCIDENT_2}\n\nThanks,\nScott`))
    expect(r.status).toBe(2)
    expect(r.stderr).toContain('feedback_never_call_a_cost_small_against_the_fee.md')
    expect(r.stderr).toContain('How to apply')
  })

  it('denies once per content, then lets the same draft through', () => {
    expect(run(GATE, draft(INCIDENT_1, 'd2')).status).toBe(2)
    expect(run(GATE, draft(INCIDENT_1, 'd2')).status).toBe(0)
  })

  it('reads htmlBody as text', () => {
    const p = draft('', 'd3')
    p.tool_input = {
      ...p.tool_input,
      body: '',
      htmlBody: `<p>${INCIDENT_2}</p>`,
    } as typeof p.tool_input
    expect(run(GATE, p).status).toBe(2)
  })

  it('passes a clean draft', () => {
    expect(run(GATE, draft('The October invoice is attached.', 'd4')).status).toBe(0)
  })
})

describe('the prompt stage', () => {
  const prompt = (text: string, session = 'p1') => ({
    session_id: session,
    cwd: '/tmp/some-tree',
    prompt: text,
  })

  it('shows the memory when the Captain raises cost, once per session', () => {
    const first = run(RECALL, prompt('what would the invoice lane cost us per month?'))
    expect(first.stdout).toContain('[memory]')
    expect(first.stdout).toContain('feedback_never_call_a_cost_small_against_the_fee.md')
    expect(run(RECALL, prompt('and the pricing for it?')).stdout).toBe('')
    expect(run(RECALL, prompt('and the pricing for it?', 'p2')).stdout).toContain('[memory]')
  })

  it('is silent on an unrelated prompt (discharge, in charge)', () => {
    expect(run(RECALL, prompt('pull the discharge summary for Cook', 'p3')).stdout).toBe('')
    expect(run(RECALL, prompt('who is in charge of the release?', 'p4')).stdout).toBe('')
  })

  it('reaches context through reflex-primer.sh', () => {
    const out = execFileSync('bash', [join(HOOKS, 'reflex-primer.sh')], {
      input: JSON.stringify(prompt('what would this cost the firm?', 'p5')),
      encoding: 'utf8',
      env: { ...process.env, SS_MEMORY_DIR: store, SS_BOARD_DIR: board },
    })
    expect(out).toContain('[memory]')
  })
})

describe('refused triggers and the live-store self-check', () => {
  it('refuses triggers that match everything, nest quantifiers, or are not single-quoted', async () => {
    const { compileTrigger, parseMemory } = await import(LIB)
    expect(compileTrigger('').error).toBeTruthy()
    expect(compileTrigger('x?').error).toMatch(/empty string/)
    expect(compileTrigger('(a+)+$').error).toMatch(/nested quantifier/)
    expect(compileTrigger('\\bwidget\\b').re).toBeTruthy()
    const mem = parseMemory(
      'm.md',
      '---\nname: m\ntrigger_reply: |\ntrigger_reply: "widget"\n---\nbody\n'
    )
    expect(mem.triggers.reply).toHaveLength(0)
    expect(mem.invalid).toHaveLength(2)
  })

  it('an empty-matching trigger in the store never bounces a reply', () => {
    writeFileSync(
      join(store, 'feedback_bad.md'),
      "---\nname: feedback_bad\ntrigger_reply: '.*'\n---\nbody\n"
    )
    expect(reason(run(REPLY, stop('Yes, merged as #2140.')))).toBeNull()
  })

  it('selfCheck names an example that stopped firing and a counter that started', async () => {
    const { parseMemory, selfCheck } = await import(LIB)
    const good = parseMemory(
      'g.md',
      "---\nname: g\ntrigger_reply: '\\bwidget\\b'\ntrigger_example: 'a widget'\ntrigger_counter: 'a gadget'\n---\n"
    )
    expect(selfCheck([good])).toEqual([])
    const rotten = parseMemory(
      'r.md',
      "---\nname: r\ntrigger_reply: '\\bwidget\\b'\ntrigger_example: 'a gizmo'\ntrigger_counter: 'two widgets, one widget'\n---\n"
    )
    const problems = selfCheck([rotten])
    expect(problems.join('\n')).toMatch(/trigger_example no longer matches/)
    expect(problems.join('\n')).toMatch(/trigger_counter now matches/)
  })
})

describe('memory-recurrence', () => {
  it('self-test: a fixture stumble counts as ESCAPED, a logged one as BOUNCED, a clean window as zero', () => {
    const out = execFileSync('node', [RECURRENCE, '--self-test'], { encoding: 'utf8' })
    expect(out).toContain('PASS')
  })

  it('counts the incident as ESCAPED when no hook caught it, and BOUNCED when one did', () => {
    const tdir = join(scratch, 'transcripts')
    mkdirSync(tdir)
    const rec = (type: string, text: string, ts: string) =>
      JSON.stringify({ type, timestamp: ts, message: { content: [{ type: 'text', text }] } })
    writeFileSync(
      join(tdir, 'incident.jsonl'),
      [
        rec(
          'user',
          'is this lane priced into what they pay? again, how many times',
          '2026-10-29T19:40:00Z'
        ),
        rec('assistant', `${INCIDENT_1} ${INCIDENT_2}`, '2026-10-29T19:48:37Z'),
      ].join('\n')
    )
    const scanOnce = () => {
      try {
        execFileSync('node', [RECURRENCE, '--json'], {
          encoding: 'utf8',
          env: {
            ...process.env,
            SS_MEMORY_DIR: store,
            SS_BOARD_DIR: board,
            SS_TRANSCRIPT_DIR: tdir,
          },
        })
        return { status: 0, r: null as unknown }
      } catch (err) {
        const e = err as { status: number; stdout: string }
        return { status: e.status, r: JSON.parse(e.stdout) }
      }
    }
    const before = scanOnce()
    expect(before.status).toBe(1)
    const row = (before.r as { rows: { escaped: number; corrections: number }[] }).rows[0]
    expect(row.escaped).toBe(1)
    expect(row.corrections).toBe(1)

    // The same reply, caught by the Stop hook in that session, is a bounce.
    run(REPLY, stop(`${INCIDENT_1} ${INCIDENT_2}`, 'incident'))
    const after = scanOnce()
    const row2 = (after.r as { rows: { escaped: number; bounced: number }[] }).rows[0]
    expect(row2.bounced).toBe(1)
    expect(row2.escaped).toBe(0)

    // A restatement that repeats the bounced sentence reached the Captain: one
    // fire catches one occurrence, so the repeat is ESCAPED, not a second bounce.
    writeFileSync(
      join(tdir, 'incident.jsonl'),
      [
        rec('assistant', `${INCIDENT_1} ${INCIDENT_2}`, '2026-10-29T19:48:37Z'),
        rec('assistant', `${INCIDENT_1} ${INCIDENT_2}`, '2026-10-29T19:49:10Z'),
      ].join('\n')
    )
    const repeat = scanOnce()
    const row3 = (repeat.r as { rows: { escaped: number; bounced: number }[] }).rows[0]
    expect(row3.bounced).toBe(1)
    expect(row3.escaped).toBe(1)

    // A fire from a different session catches nothing here.
    writeFileSync(
      join(tdir, 'other.jsonl'),
      rec('assistant', `${INCIDENT_1} ${INCIDENT_2}`, '2026-10-29T20:00:00Z')
    )
    const other = scanOnce()
    const row4 = (other.r as { rows: { escaped: number; bounced: number }[] }).rows[0]
    expect(row4.escaped).toBe(2)
  })
})

describe('draft-only triggers (trigger_draft)', () => {
  // "We", never "I checked", is a rule about what a client reads; a chat reply
  // to the Captain saying "I checked the log" is fine. So the trigger fires on
  // a Gmail draft and never on a reply.
  const WE_MEMORY = [
    '---',
    'name: feedback_we',
    'description: "Client emails say we; never I checked."',
    "trigger_draft: '(?<![\"“])\\bI (?:checked|reviewed)\\b'",
    "trigger_example: 'I checked the file.'",
    "trigger_counter: 'We checked the file.'",
    '---',
    '',
    '**How to apply:** Write the work as we.',
    '',
  ].join('\n')
  beforeEach(() => writeFileSync(join(store, 'feedback_we.md'), WE_MEMORY))

  const gmail = (body: string, session: string) => ({
    session_id: session,
    cwd: '/tmp/some-tree',
    tool_input: { body },
  })

  it('denies a Gmail draft that says "I checked"', () => {
    const r = run(GATE, gmail('I checked the file and the bill is there.', 'w1'))
    expect(r.status).toBe(2)
    expect(r.stderr).toContain('feedback_we.md')
  })

  it('never bounces a chat reply that says "I checked"', () => {
    expect(reason(run(REPLY, stop('I checked the log and the job finished.', 'w2')))).toBeNull()
  })

  it('lets a draft quote the client saying it', () => {
    const r = run(GATE, gmail('Her reply says: "I reviewed the package and found nothing."', 'w3'))
    expect(r.status).toBe(0)
  })
})

describe('memory-trigger-test', () => {
  function runTest(tdir: string) {
    try {
      const stdout = execFileSync(
        'node',
        [
          TRIGGER_TEST,
          'feedback_never_call_a_cost_small_against_the_fee.md',
          '--since',
          '2026-01-01',
        ],
        { encoding: 'utf8', env: { ...process.env, SS_MEMORY_DIR: store, SS_TRANSCRIPT_DIR: tdir } }
      )
      return { status: 0, stdout }
    } catch (err) {
      const e = err as { status: number; stdout: string }
      return { status: e.status, stdout: e.stdout }
    }
  }

  it('sweeps history per stage and passes a memory whose self-check holds', () => {
    const tdir = join(scratch, 'tt')
    mkdirSync(tdir)
    const rec = (type: string, content: unknown, ts: string) =>
      JSON.stringify({ type, timestamp: ts, message: { content } })
    const draft = {
      type: 'tool_use',
      name: 'mcp__claude_ai_Gmail__create_draft',
      input: { body: INCIDENT_2 },
    }
    writeFileSync(
      join(tdir, 's.jsonl'),
      [
        rec('user', 'what would this lane cost us?', '2026-10-29T19:40:00Z'),
        rec('assistant', [{ type: 'text', text: INCIDENT_1 }], '2026-10-29T19:48:37Z'),
        rec('assistant', [draft], '2026-10-29T19:50:00Z'),
        rec('assistant', [{ type: 'text', text: 'A clean reply.' }], '2026-10-29T19:51:00Z'),
      ].join('\n')
    )
    writeFileSync(
      join(store, 'feedback_never_call_a_cost_small_against_the_fee.md'),
      FEE_MEMORY.replace(
        "trigger_since: '2026-10-29'",
        "trigger_since: '2026-10-29'\ntrigger_example: 'That''s why I''d fold it into the agreement at no added fee.'"
      )
    )
    const r = runTest(tdir)
    expect(r.stdout).toMatch(/reply: 1 hit\(s\) in 2 assistant replies/)
    expect(r.stdout).toMatch(/draft: 1 hit\(s\)/)
    expect(r.stdout).toMatch(/prompt: 1 hit\(s\) in 1 Captain prompts/)
    expect(r.status).toBe(0)
  })

  it('fails a memory with a reply trigger and no example (untested is not tested)', () => {
    const tdir = join(scratch, 'tt2')
    mkdirSync(tdir)
    const r = runTest(tdir)
    expect(r.stdout).toContain('no trigger_example')
    expect(r.status).toBe(1)
  })
})
