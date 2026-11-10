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
import { homedir } from 'os'
import { resolve } from 'path'

const ROOT = process.cwd()
const SKILLS = ['sos', 'eos', 'code-review', 'wired', 'critique', 'auto-build'] as const

/** Literal paths, not composed ones: the set is closed and the scanner is right
 * to distrust a path built from a variable. */
const SKILL_PATHS: Record<(typeof SKILLS)[number], string> = {
  sos: resolve(ROOT, '.claude/skills/sos/SKILL.md'),
  eos: resolve(ROOT, '.claude/skills/eos/SKILL.md'),
  'code-review': resolve(ROOT, '.claude/skills/code-review/SKILL.md'),
  wired: resolve(ROOT, '.claude/skills/wired/SKILL.md'),
  critique: resolve(ROOT, '.claude/skills/critique/SKILL.md'),
  'auto-build': resolve(ROOT, '.claude/skills/auto-build/SKILL.md'),
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

describe('client-confidentiality fences in the session skills', () => {
  // Session transcripts persist under ~/.claude and go to model providers, and
  // this repo is public: client text must never be printed into one or pasted here.
  it('/sos never prints obligation text into the transcript', () => {
    expect(skillBody('sos')).toMatch(/no `what` field and there must not be/)
  })

  it("/wired cites what the client was told and never pastes the client's text into this repo", () => {
    expect(skillBody('wired')).toMatch(/Never paste the client's text into this repo/)
  })
})

describe('/sos points at the stance surfaces instead of restating the laws', () => {
  it('no doctrine primer_line is pasted into /sos (no second rulebook to drift)', () => {
    const body = skillBody('sos')
    const doctrine = readFileSync(
      resolve(ROOT, 'docs/doctrine/agent-operating-doctrine.md'),
      'utf8'
    )
    const primerLines = [...doctrine.matchAll(/^primer_line: ['"](.+)['"]$/gm)].map((m) => m[1])
    expect(primerLines.length).toBeGreaterThan(0)
    expect(primerLines.filter((line) => body.includes(line))).toEqual([])
  })
})

/**
 * /wired defines done as READY, and docs/doctrine/wired-contract.md carries the
 * same definition word for word (fork, 2026-11-09). The parity must fail on
 * crane's unmodified copy, or it measures nothing (Law 12); that half runs only
 * where a crane-console checkout sits at ~/dev/crane-console.
 */
const CRANE_WIRED = resolve(homedir(), 'dev/crane-console/.agents/skills/wired/SKILL.md')

const WIRED_CONTRACT = resolve(ROOT, 'docs/doctrine/wired-contract.md')

/** The READY bullet, which the skill and the doctrine page carry word for word. */
function readyBullet(body: string): string | null {
  const m = body.match(/^- \*\*READY\*\* - .+$/m)
  return m ? m[0] : null
}

function readyMatchesContract(body: string): boolean {
  const ready = readyBullet(body)
  return ready !== null && ready === readyBullet(readFileSync(WIRED_CONTRACT, 'utf8'))
}

describe('/wired and /critique agree with what reads them (fork, 2026-11-09)', () => {
  it('/wired defines done as READY, word for word with docs/doctrine/wired-contract.md', () => {
    expect(readyMatchesContract(skillBody('wired'))).toBe(true)
  })

  it('/critique writes what the plan gate reads: the same directory, heading and none line', async () => {
    const gate = await import('../.claude/hooks/plan-premise-gate.mjs')
    const body = skillBody('critique')
    const none = body.match(/^Critic findings not adopted: none$/m)?.[0] ?? ''
    const section = body.match(/^## Critic findings not adopted\n\n(\|.*\n)+/m)?.[0] ?? ''
    const critics = [
      { name: 'devils-advocate', title: "Devil's Advocate", verdict: 'proceed with fixes' },
      { name: 'simplifier', title: 'Simplifier', verdict: 'reconsider approach' },
    ]
    expect(gate.criticsDirFor('/p/plans/x.md')).toBe('/p/plans/x.critics')
    expect(gate.criticFindingsOk(none, critics).ok).toBe(true)
    expect(gate.criticFindingsOk(section, critics).ok).toBe(true)
    expect(gate.criticFindingsOk('## Something else\n', critics).ok).toBe(false)
  })
})

describe.skipIf(!existsSync(CRANE_WIRED))("the READY parity fails against crane's copy", () => {
  it("crane's /wired does not carry the contract's READY definition", () => {
    expect(readyMatchesContract(readFileSync(CRANE_WIRED, 'utf8'))).toBe(false)
  })
})
