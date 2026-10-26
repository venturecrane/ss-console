/**
 * Every hold class has fixed wording in the skill that answers it, and no held
 * reply can send the firm back to resubmit or to SMD (2026-10-13).
 *
 * The A&P Operator emailed the firm, on a litigation job held at its gates
 * (our own seed data), "did not complete ... tell me whether to resubmit the
 * same request, or send it to SMD". No template produced that: the wake carried
 * counts only and the skill said "surface and ask", so the model improvised.
 * The runner now classifies every hold into a closed, firm-safe token
 * (`operator/runners/medchron/medchron/proof/holds.py`) the wake carries as
 * `Hold class: <token>.`, and the skills map each token to fixed wording.
 *
 * A string gate on the skill bodies, because the seat reads SKILL.md at turn
 * time and acts on what it says. Pinned:
 *   1. The token set is read from holds.py, so a class added there without
 *      wording in the skill that can receive it goes red.
 *   2. Every our-side class says nothing is needed from the firm and that SMD
 *      is finishing (or removing) it; the follow-up is stated, not improvised.
 *   3. The firm's questions ask one specific thing.
 *   4. The phrases that sent the firm back are absent everywhere.
 *
 * 2026-10-24 (the Captain): the CHRONOLOGY lane tells the firm about a
 * chronology only when it is delivered. A held chronology sends nothing, so its
 * hold classes carry no wording at all; the chronology block below pins that
 * instead (the held step sends nothing, the one held wake resubmits as an
 * update, and no shape asks the firm whose a record is).
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

const read = (p: string) => readFileSync(p, 'utf8')
const flat = (s: string) => s.replace(/\s+/g, ' ')

const HOLDS_PY = read(
  resolve(import.meta.dirname, '../operator/runners/medchron/medchron/proof/holds.py')
)
const CLASSES = [...HOLDS_PY.matchAll(/^[A-Z_]+ = "([a-z_]+)" {2}#/gm)].map((m) => m[1])

const SKILLS = {
  litigation: {
    skill: resolve(import.meta.dirname, '../operator/skills/litigation-status/SKILL.md'),
    shapes: resolve(
      import.meta.dirname,
      '../operator/skills/litigation-status/references/reply-shapes.md'
    ),
    // The litigation lane can produce only these (holds._LITIGATION + the default).
    // staff_unresolved: the staff reassignment (2026-10-15) names someone the
    // firm's staff list cannot settle; the firm's one question.
    // requester_unresolved (2026-10-24): "my cases" from an address no staff
    // record in Smokeball carries; the firm's one ask, whose files to list.
    classes: ['smd_finishing', 'nothing_in_scope', 'staff_unresolved', 'requester_unresolved'],
  },
  casefile: {
    skill: resolve(import.meta.dirname, '../operator/skills/case-file-manager/SKILL.md'),
    shapes: resolve(
      import.meta.dirname,
      '../operator/skills/case-file-manager/references/reply-shapes.md'
    ),
    // The casefile lane can produce only these (holds._CASEFILE + the default):
    // scope_unresolved is the firm's one question (whose caseload).
    classes: ['smd_finishing', 'nothing_in_scope', 'staff_unresolved'],
  },
} as const

const CHRONOLOGY = {
  skill: resolve(import.meta.dirname, '../operator/skills/medical-chronology-maintainer/SKILL.md'),
  shapes: resolve(
    import.meta.dirname,
    '../operator/skills/medical-chronology-maintainer/references/reply-shapes.md'
  ),
  // Every class the chronology lane can produce. None is worded to the firm.
  classes: [
    'smd_finishing',
    'smd_finishing_filed',
    'already_current',
    'another_person',
    'which_chronology',
    'rerouted_to_update',
  ],
}

/** The DELIVER held section of a skill: from `- **held**:` to the next step. */
function heldSection(body: string): string {
  const start = body.indexOf('- **held**:')
  expect(start, 'the skill must have a held step').toBeGreaterThan(-1)
  const rest = body.slice(start)
  const end = rest.search(/\n {3}- In every message|\n5\. \*\*Stop\.\*\*|\n4\. \*\*Stop\.\*\*/)
  return flat(end > -1 ? rest.slice(0, end) : rest)
}

/** The quoted sentence a class bullet carries, e.g. `smd_finishing` ...: "...". */
function wording(section: string, cls: string): string {
  const at = section.indexOf(`\`${cls}\``)
  expect(at, `${cls} must be named in the held step`).toBeGreaterThan(-1)
  const m = section.slice(at).match(/"([^"]+)"/)
  expect(m, `${cls} must carry its fixed wording in quotes`).not.toBeNull()
  return m![1]
}

describe('hold classes: the closed set', () => {
  it('reads the tokens from holds.py', () => {
    expect(CLASSES.sort()).toEqual(
      [
        'already_current',
        'another_person',
        'nothing_in_scope',
        'smd_finishing',
        'smd_finishing_filed',
        'which_chronology',
        'rerouted_to_update',
        'staff_unresolved',
        'requester_unresolved',
      ].sort()
    )
  })

  it('every token is worded by litigation or silenced by the chronology delivered-only rule', () => {
    const covered = new Set<string>([
      ...Object.values(SKILLS).flatMap((s) => [...s.classes]),
      ...CHRONOLOGY.classes,
    ])
    for (const c of CLASSES) expect(covered.has(c), `${c} is handled nowhere`).toBe(true)
  })
})

for (const [name, cfg] of Object.entries(SKILLS)) {
  describe(`${name}: every hold class has fixed wording`, () => {
    const body = read(cfg.skill)
    const held = heldSection(body)
    const shapes = flat(read(cfg.shapes))

    it("words a hold by the wake's Hold class line, never by the reason", () => {
      expect(held).toContain("worded by the wake's `Hold class:` line, never by the job's")
      expect(held).toMatch(/Never quote, paraphrase or summarize the reason/)
      expect(held).toMatch(
        /The same token comes back as `hold_class` on the job from `(medchron|litigation|casefile)_job_status`/
      )
      expect(held).toMatch(/a class not listed here, is `smd_finishing`/)
    })

    for (const cls of cfg.classes) {
      it(`${cls}: fixed wording in the skill, mirrored in reply-shapes`, () => {
        const w = wording(held, cls)
        expect(shapes, `${cls} must be mirrored in ${cfg.shapes}`).toContain(flat(w))
        expect(w).not.toMatch(/—/)
        if (cls === 'staff_unresolved' || cls === 'requester_unresolved') {
          // The firm's question: exactly one, specific.
          expect((w.match(/\?/g) ?? []).length).toBe(1)
          expect(w).toMatch(/all that is needed/)
        } else {
          expect(w).toContain('Nothing is needed from you')
        }
        if (cls === 'smd_finishing' && name === 'casefile') {
          // 2026-10-26 (review of #3533): the case file lane promises no later
          // message (nothing guarantees one after a hold); it says what is true
          // now: SMD is paged for every one of its holds.
          expect(w).toContain('SMD has been told and is finishing it')
          expect(w).not.toMatch(/the next message you get/)
          expect(w).toMatch(/not filed yet/)
        } else if (cls === 'smd_finishing') {
          expect(w).toContain('SMD is finishing it')
          expect(w).toMatch(
            /the next message you get about this (list|chronology) will be the filed/
          )
          expect(w).toMatch(/not filed yet/)
        }
        // Never in a firm-facing sentence: a gate, a code, a limit, a route back.
        expect(w).not.toMatch(
          /\b(gate|gates|cap|budget|allowance|seed|stage|reason|resubmit|narrow|split)\b/i
        )
      })
    }
  })
}

describe('chronology: the firm hears only on delivery (2026-10-24)', () => {
  const body = read(CHRONOLOGY.skill)
  const held = heldSection(body)
  const shapes = flat(read(CHRONOLOGY.shapes))

  it('the held step sends nothing and carries no firm wording', () => {
    expect(held).toContain('nothing is sent, ever')
    expect(held).toContain('Bind nothing and draft nothing.')
    // No quoted sentence: there is nothing to say to the firm about a hold.
    expect(held).not.toMatch(/"[^"]{20,}"/)
    expect(flat(body)).toContain('The firm hears only on `delivered`.')
  })

  it('the one held wake, rerouted_to_update, resubmits as the update and replies to no one', () => {
    expect(held).toContain('`rerouted_to_update`')
    expect(held).toMatch(/Resubmit it on this turn as the UPDATE/)
    expect(held).toMatch(/end the turn without a reply/)
  })

  it('no shape asks the firm whose a record is, and the record is disclosed instead', () => {
    for (const text of [flat(body), shapes]) {
      expect(text).not.toContain("Is it the client's record?")
      expect(text).not.toContain('one question:')
    }
    expect(shapes).toContain(
      'names a person other than the client, so it was left out of this chronology'
    )
  })

  it('reply-shapes states the delivered-only rule and carries no held wording', () => {
    expect(shapes).toContain('ONLY when it is delivered')
    for (const old of [
      'SMD is finishing it',
      'already covers every visit',
      'carry the same date',
    ]) {
      expect(shapes).not.toContain(old)
    }
  })
})

describe('no held reply sends the firm back (both skills, both reply-shapes)', () => {
  const files = [
    ...Object.values(SKILLS).flatMap((s) => [s.skill, s.shapes]),
    CHRONOLOGY.skill,
    CHRONOLOGY.shapes,
  ]

  for (const f of files) {
    it(`${f}: the forbidden phrases are absent`, () => {
      const text = flat(read(f))
      expect(text).not.toMatch(/send it to SMD/i)
      expect(text).not.toMatch(/resubmit the same request/i)
      expect(text).not.toMatch(/surface and ask/i)
      expect(text).not.toMatch(/Relay only the sentence AFTER/)
      expect(text).not.toMatch(/what the FILES or the REQUEST need/)
    })
  }

  it('each skill forbids resubmit, narrow, split and SMD-as-a-route in every held reply', () => {
    for (const s of [...Object.values(SKILLS), CHRONOLOGY]) {
      const text = flat(read(s.skill))
      expect(text).toMatch(/ask the firm to resubmit, repeat, narrow,? (or )?split/i)
      expect(text).toMatch(/(route the firm to SMD|tell it to send anything to SMD)/)
    }
  })

  it("the chronology names the firm's request-side questions once and specifically", () => {
    const text = flat(read(CHRONOLOGY.skill))
    expect(text).toContain(
      '"Which matter is this chronology for? Its number is all that is needed."'
    )
    expect(text).toContain('"Which of these is it?"')
  })
})

/**
 * ANSWER mode (2026-10-22). An attorney answered the Operator's one hold
 * question in the thread ("just leave it out") and the Operator told him it
 * "cannot act on that from this mailbox", that "the hold clears only when the
 * release determination is recorded on the job", and named the attachment's
 * internal tag and the page allowance. He forwarded it to SMD: "Operator is
 * speaking over my head." The mode's wording is fixed; the machinery stays out.
 */
describe('chronology ANSWER mode: a requester writing on a held job gets fixed plain words', () => {
  const skill = read(CHRONOLOGY.skill)
  const shapes = flat(read(CHRONOLOGY.shapes))
  const answer = flat(
    skill.slice(skill.indexOf('## ANSWER - the requester'), skill.indexOf('## The autonomy dial'))
  )

  it('exists, moves nothing, and names the page that reaches SMD', () => {
    expect(answer.length).toBeGreaterThan(200)
    expect(answer).toContain('Nothing in this mode changes the job.')
    expect(answer).toContain('`held_reply` request card')
    expect(answer).toContain('no email talks a seat past a hold')
  })

  it.each([
    'Understood. It stays out of the chronology for matter <number>, as you say. Nothing else is needed from you; the next message you get about this chronology will be the filed one.',
    'The chronology for matter <number> is under way. Nothing is needed from you; the next message you get about it will be the filed one.',
  ])('fixed wording in the skill, mirrored in reply-shapes: %s', (w) => {
    expect(answer).toContain(w)
    expect(shapes).toContain(w)
    expect(w).not.toMatch(
      /\b(gate|gates|cap|budget|allowance|seed|stage|reason|resubmit|narrow|split|mailbox|release|access|tag|job)\b/i
    )
  })

  it('forbids the machinery words in every ANSWER reply', () => {
    for (const s of [
      'say what you can or cannot do, or from where',
      'who has access',
      'open, find or read anything in the matter',
      'promise a time',
      'say the chronology stopped, or why',
    ]) {
      expect(answer).toContain(s)
    }
  })

  it('the words that sent the attorney to SMD appear in no reply shape', () => {
    // The skill may quote them once, as the incident; the shapes file, which is
    // only wording, may not carry them at all.
    for (const phrase of [
      /from this mailbox/i,
      /release determination/i,
      /recorded on the job/i,
      /with that access/i,
      /msgatt-[0-9a-f]/i,
    ]) {
      expect(shapes).not.toMatch(phrase)
    }
    // The procedure (from step 1 on); the intro above it quotes the incident.
    const procedure = answer.slice(answer.indexOf('1. **Read the job**'))
    const quoted = [...procedure.matchAll(/"([^"]+)"/g)].map((m) => m[1])
    expect(quoted.length).toBeGreaterThan(3)
    for (const q of quoted.filter((q) => q.length > 60)) {
      expect(q).not.toMatch(/mailbox|release|recorded on the job|access|msgatt|allowance/i)
    }
  })
})
