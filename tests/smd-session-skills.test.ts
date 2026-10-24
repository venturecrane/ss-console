/**
 * SMD's forked /sos and /eos (2026-09-17).
 *
 * These were crane-console enterprise skills, byte-identical across ke, dfg, sc
 * and ss. ss-console is the primary venture, so the session lifecycle is now
 * authored here rather than inherited, and the obligation register is folded in:
 * /sos reports what we owe, /eos Check I captures what the session promised.
 *
 * WHAT THIS FILE ACTUALLY GUARDS is the placement, because placement is the only
 * part that can silently revert. Two separate crane mechanisms write into this
 * repo:
 *
 *   - `crane ss` runs syncClaudeAssets on EVERY launch, copying crane's
 *     `.claude/commands/*.md` over ours (launch-lib/skill-sync.ts).
 *   - `sync-commands.sh` copies AND prunes: it deletes any `.md` in a venture's
 *     `.claude/commands/` that crane does not have.
 *
 * Neither touches `.claude/skills/`, which is why the forks live there. A fork
 * placed in `.claude/commands/` would be overwritten at the Captain's next
 * launch with no diff, no error, and no way to notice -- and worse, the same
 * directory is gitignored here, so the loss would not even show in `git status`.
 * That is the regression this file exists to catch.
 */

import { describe, expect, it } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { execFileSync } from 'child_process'
import { resolve } from 'path'

const ROOT = process.cwd()
const SKILLS = ['sos', 'eos', 'code-review'] as const

/** Literal paths, not composed ones: the set is closed and the scanner is right
 * to distrust a path built from a variable. */
const SKILL_PATHS: Record<(typeof SKILLS)[number], string> = {
  sos: resolve(ROOT, '.claude/skills/sos/SKILL.md'),
  eos: resolve(ROOT, '.claude/skills/eos/SKILL.md'),
  'code-review': resolve(ROOT, '.claude/skills/code-review/SKILL.md'),
}

function skillPath(name: (typeof SKILLS)[number]): string {
  return SKILL_PATHS[name]
}

function skillBody(name: (typeof SKILLS)[number]): string {
  return readFileSync(skillPath(name), 'utf8')
}

describe('the forked session skills live where crane cannot reach them', () => {
  for (const name of SKILLS) {
    it(`${name} exists at .claude/skills/${name}/SKILL.md`, () => {
      expect(existsSync(skillPath(name))).toBe(true)
    })

    it(`${name} is tracked by git`, () => {
      // `.claude/commands/` is gitignored (.gitignore:57). A fork that landed
      // there would be untracked, unreviewed, and invisible when overwritten.
      const tracked = execFileSync('git', ['ls-files', `.claude/skills/${name}/SKILL.md`], {
        cwd: ROOT,
        encoding: 'utf8',
      }).trim()
      expect(tracked).not.toBe('')
    })

    it(`${name} records its fork point so the diff against crane stays possible`, () => {
      const body = skillBody(name)
      expect(body).toMatch(/Forked from crane-console on \d{4}-\d{2}-\d{2}/)
      // Naming the commit is what makes "what have we changed since?" answerable.
      expect(body).toMatch(/fork point is commit `[0-9a-f]{7,40}`/)
    })

    it(`${name} declares skill frontmatter`, () => {
      const body = skillBody(name)
      expect(body.startsWith('---\n')).toBe(true)
      expect(body).toMatch(new RegExp(`^name: ${name}$`, 'm'))
      expect(body).toMatch(/^description: .+/m)
    })
  }

  it('neither fork is checked in under a crane-synced directory', () => {
    // The mutation: move either SKILL.md to `.claude/commands/`. It would be
    // clobbered on the next `crane ss` and pruned by the next sync-commands.sh.
    const tracked = execFileSync('git', ['ls-files'], { cwd: ROOT, encoding: 'utf8' })
      .split('\n')
      .filter(Boolean)
    const synced = tracked.filter(
      (p) =>
        (p.startsWith('.claude/commands/') ||
          p.startsWith('.agents/skills/') ||
          p.startsWith('.gemini/commands/')) &&
        SKILLS.some((s) => p.includes(s))
    )
    expect(synced).toEqual([])
  })
})

describe('the register is wired into both skills', () => {
  it('/sos reads the register and composes an [owed] line', () => {
    const body = skillBody('sos')
    expect(body).toMatch(/register list --json/)
    expect(body).toMatch(/\[owed\]/)
  })

  it('/sos leads with what is ours and never folds waiting rows into it', () => {
    // 2026-10-22: "[owed] 53 open" read 50 for Ashton & Price when 15 were ours.
    const body = skillBody('sos')
    expect(body).toMatch(/counting only `ours` rows/)
    expect(body).toMatch(/Never fold the two into one number/)
  })

  it('/eos Check I moves rows a letter handed to the client', () => {
    const body = skillBody('eos')
    expect(body).toMatch(/\*\*Awaited, too\.\*\*/)
    expect(body).toMatch(/register await --client/)
  })

  it('/sos never prints obligation text into the transcript', () => {
    // The line lands in a session transcript; transcripts persist under
    // ~/.claude and go to model providers, and this repo is public.
    const body = skillBody('sos')
    expect(body).toMatch(/no `what` field and there must not be/)
  })

  it('/sos distinguishes an empty register from an unreachable one', () => {
    // Silence for both is the cadence-engine failure: healthy and broken look
    // identical from outside.
    const body = skillBody('sos')
    expect(body).toMatch(/register unreachable/)
    expect(body).toMatch(/Do not omit it silently/)
  })

  it('/eos Check I captures what the session committed us to', () => {
    const body = skillBody('eos')
    expect(body).toMatch(/\*\*I\. Client obligations/)
    expect(body).toMatch(/register add --kind none/)
  })

  it('/eos Check I records but never blocks', () => {
    // If Check I entered the Ship Gate, every client promise would become a
    // reason a session cannot close, and people would invent blockers to escape
    // it. The distinction has to survive future edits to this skill.
    const body = skillBody('eos')
    expect(body).toMatch(/RECORDS; it never BLOCKS|RECORDS, never blocks/)
    expect(body).toMatch(/not\*\* a Ship Gate item/)
  })

  it('/eos Check I is reachable from the audit, not orphaned prose', () => {
    // The anti-fabrication gate enumerates what may be listed as a loose end.
    // A check absent from that list produces items the gate then discards --
    // built, not wired, in miniature.
    const body = skillBody('eos')
    expect(body).toMatch(/from Check I/)
    expect(body).toMatch(/If NO to all ten/)
  })

  it('/eos states the blind spot rather than implying full coverage', () => {
    // read-tracker.mjs is PostToolUse on `Read` only: a letter read via Bash,
    // Grep, or pasted into the prompt is invisible. A negative result here
    // means nothing, and the skill has to say so.
    expect(skillBody('eos')).toMatch(/blind spot/i)
  })
})

describe('/code-review covers the Operator and ends in fixes (fork, 2026-10-17)', () => {
  it('reviews the overlay, not only the repo it runs in', () => {
    // The inherited skill reviewed ss-console only; the overlay, the Operator's
    // runtime on every seat, had never been reviewed. Since ADR 0095 the overlay
    // is a path in this repo, so the skill must name that path as an area of the
    // review, and must not send the reviewer to clone a second repo or bump a pin
    // that no longer exists.
    const body = skillBody('code-review')
    expect(body).toMatch(
      /`operator\/hermes-smd-overlay\/` \| the Operator and hosted agent runtime/
    )
    expect(body).not.toMatch(/gh repo clone/)
    expect(body).not.toMatch(/OVERLAY_REF/)
  })

  it('tells a probe to look where the code runs', () => {
    // F1 was carried open for two reviews by a probe that searched only
    // ss-console for a control that is wired in the overlay.
    expect(skillBody('code-review')).toMatch(/A probe looks where the code runs/)
  })

  it('fixes what it finds instead of asking which subset to fix', () => {
    const body = skillBody('code-review')
    expect(body).toMatch(/## Step 9 — Fix everything, now/)
    expect(body).toMatch(/does not wait for a go-ahead/)
    // The inherited closing step that produced lists must not come back.
    expect(body).not.toMatch(/\*\*ask\*\* before creating GitHub issues/)
  })

  it('counts a fix done only when the seats run it', () => {
    const body = skillBody('code-review')
    expect(body).toMatch(/## Step 10 — Done means the seats run it/)
    expect(body).toMatch(/Never kill a running job/)
  })
})

describe('the skunkworks stance is the default (folded 2026-10-24)', () => {
  // A PLACEMENT guard, like the rest of this file: it proves the text is where
  // a session will read it, not that agents behave. The behavior-carrying
  // surface is the per-turn mission suffix, executed in
  // tests/board-and-freshness.test.ts; the measurement is the
  // skunkworks-default-stance mechanism in the doctrine registry.
  it('/sos records where the stance came from', () => {
    const body = skillBody('sos')
    expect(body).toMatch(/## The stance \(skunkworks, folded 2026-10-24\)/)
    expect(body).toMatch(/skunkworks.*fork point is commit `6e2af4c8`/s)
  })

  it('/sos asks for a winning line the agent drafts itself, then moves', () => {
    const body = skillBody('sos')
    expect(body).toMatch(/<outcome>, via <task>/)
    expect(body).toMatch(/Draft it yourself/)
    expect(body).toMatch(/Product work never names a client/)
  })

  it('/sos keeps speed off client, paid and seat work', () => {
    // Settled with the Captain 2026-10-24: speed is for reversible repo work;
    // everything that reaches a client or costs money proves the path first.
    expect(skillBody('sos')).toMatch(/Law 16 path proof before the first long run/)
  })

  it('/sos sends the stance into subagent briefs', () => {
    expect(skillBody('sos')).toMatch(/Subagent briefs carry it/)
  })

  it('/sos points at the stance surfaces instead of restating the laws', () => {
    // No second rulebook: a primer_line pasted here would drift from doctrine.
    const body = skillBody('sos')
    expect(body).toMatch(/board\.mjs`, `STANCE`/)
    const doctrine = readFileSync(
      resolve(ROOT, 'docs/doctrine/agent-operating-doctrine.md'),
      'utf8'
    )
    const primerLines = [...doctrine.matchAll(/^primer_line: ['"](.+)['"]$/gm)].map((m) => m[1])
    expect(primerLines.length).toBeGreaterThan(0)
    expect(primerLines.filter((line) => body.includes(line))).toEqual([])
  })

  it('/eos grades the session against its mission, with a citation', () => {
    const body = skillBody('eos')
    expect(body).toMatch(/\*\*Against the mission:\*\*/)
    expect(body).toMatch(/An uncited verdict is written as \*\*not shown\*\*/)
  })
})
