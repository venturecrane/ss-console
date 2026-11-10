/**
 * The jobs path family is spelled in one place: operator/contracts/seat-layout.yaml.
 *
 * Code reads /run/smd-jobs, /opt/data/jobs, /opt/jobs and every path declared
 * under them from the generated layout of its own tree (a Python `seat_layout`
 * module, the shell `${SMD_LAYOUT_*}` file, src/lib/operator/seat-layout.generated.ts),
 * so moving one means editing the yaml and regenerating, not a sweep of the tree.
 * This fails when a code line of a hand-written, non-test file spells one of
 * them, unless the yaml's `restated:` list names the file (with its reason; the
 * generation test pins the spelling to the declared value) or ALLOWED below does.
 *
 * Strict both ways: a listed file that no longer spells one fails too, so the
 * lists shrink when a restatement goes and never become standing permission.
 *
 * Comments, Python docstrings and prose (Markdown) may keep naming the paths: a
 * reader greps for them, and they decide nothing on a seat. Matching is by whole
 * token: /opt/jobs is not /opt/jobsx, and ${root}/opt/jobs still is /opt/jobs.
 *
 * Files are listed by `git grep --untracked` (tracked and new-not-ignored), run
 * with every GIT_* variable stripped (a pre-push hook points GIT_DIR elsewhere).
 */
import { describe, it, expect } from 'vitest'
import { spawnSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { parse } from 'yaml'
import { isGenerated, isTestPath } from '../scripts/sprawl/analyze'
import { trackedFiles } from '../scripts/sprawl/sources'

const ROOTS = ['/run/smd-jobs', '/opt/data/jobs', '/opt/jobs']
const TOKEN = /(?<![\w.-])(\/run\/smd-jobs|\/opt\/data\/jobs|\/opt\/jobs)(?![\w-])/
const DECLARATION = 'operator/contracts/seat-layout.yaml'
const JOBS_IDS_PREFIX = /^(jobs_|records_|demand_|drafting_|litigation_|negotiation_|casefile_)/

/** Why each file may still spell a jobs-family path in code, beyond the yaml's `restated:` list. */
const ALLOWED: Record<string, string> = {
  'scripts/sprawl/snapshot.json':
    'the sprawl census of the tree: data that counts where each literal still appears',
  'scripts/sprawl/literal-baseline.json':
    'the restated-literal ratchet: data that caps each literal at its remaining count (docstrings count there)',
}

/** Data files the census writes: they record literals, they do not use them. */
const NOT_CODE = /\.(md|mdx|txt)$/

function tracked(pattern: string): string[] {
  const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('GIT_')))
  const r = spawnSync('git', ['grep', '--untracked', '-l', '-I', '-E', pattern, '--', '.'], {
    cwd: resolve('.'),
    env,
    encoding: 'utf8',
    maxBuffer: 1 << 26,
  })
  if (r.status !== 0 && r.status !== 1) throw new Error(`git grep failed: ${r.stderr}`)
  return r.stdout.split('\n').filter(Boolean)
}

/** Drop the contents of triple-quoted strings (Python docstrings and the like). */
function withoutTripleQuoted(text: string): string[] {
  const out: string[] = []
  let delim = ''
  for (const line of text.split('\n')) {
    let kept = ''
    let i = 0
    while (i < line.length) {
      if (delim) {
        if (line.startsWith(delim, i)) {
          delim = ''
          i += 3
        } else i += 1
        continue
      }
      if (line.startsWith('"""', i) || line.startsWith("'''", i)) {
        delim = line.slice(i, i + 3)
        i += 3
        continue
      }
      kept += line[i]
      i += 1
    }
    out.push(kept)
  }
  return out
}

/** A `#` comment that starts outside any quote, cut off (shell, Python, YAML, Dockerfile). */
function withoutHashComment(line: string): string {
  let single = false
  let double = false
  for (let i = 0; i < line.length; i++) {
    const c = line[i]
    if (c === "'" && !double) single = !single
    else if (c === '"' && !single) double = !double
    else if (c === '#' && !single && !double && (i === 0 || /\s/.test(line[i - 1]))) {
      return line.slice(0, i)
    }
  }
  return line
}

function codeLines(path: string, text: string): string[] {
  if (NOT_CODE.test(path)) return []
  if (/\.(ts|tsx|js|mjs|cjs|astro)$/.test(path)) {
    return text.split('\n').filter((l) => !/^\s*(\/\/|\/\*|\*)/.test(l))
  }
  if (/\.json$/.test(path)) return text.split('\n')
  if (/\.sql$/.test(path)) return text.split('\n').filter((l) => !/^\s*--/.test(l))
  const lines = path.endsWith('.py') ? withoutTripleQuoted(text) : text.split('\n')
  return lines.map(withoutHashComment)
}

function restatedFiles(): Set<string> {
  const doc = parse(readFileSync(resolve(DECLARATION), 'utf8')) as {
    restated?: Array<{ file: string; ids: string[] }>
  }
  return new Set(
    (doc.restated ?? [])
      .filter((row) => row.ids.some((id) => JOBS_IDS_PREFIX.test(id)))
      .map((row) => row.file)
  )
}

function spellers(): string[] {
  const all = tracked(ROOTS.map((r) => r.replace(/[.]/g, '\\.')).join('|'))
  // isGenerated wants the generator to be a tracked file, so it gets the whole tree.
  const trackedSet = new Set(trackedFiles())
  return all
    .filter((p) => p !== DECLARATION && !isTestPath(p))
    .filter((p) => {
      let text: string
      try {
        text = readFileSync(resolve(p), 'utf8')
      } catch {
        return false // a tracked symlink to a directory, or a file deleted in the working tree
      }
      if (isGenerated(text, trackedSet)) return false
      return codeLines(p, text).some((l) => TOKEN.test(l))
    })
    .sort()
}

describe('the jobs path family is spelled only in the seat layout', () => {
  it('no code line outside the declaration, the restated files and ALLOWED spells one', () => {
    const allowed = new Set([...restatedFiles(), ...Object.keys(ALLOWED)])
    const stray = spellers().filter((p) => !allowed.has(p))
    expect(
      stray,
      `read the path from the generated seat layout (operator/contracts/seat-layout.yaml), or restate it there with a reason`
    ).toEqual([])
  })

  it('every listed file still spells one (the lists shrink, never stand as permission)', () => {
    const found = new Set(spellers())
    const stale = [...restatedFiles(), ...Object.keys(ALLOWED)].filter((p) => !found.has(p))
    expect(stale, 'remove these from the yaml restated: list or ALLOWED').toEqual([])
  })

  it('matches whole tokens and code lines only (falsifiers)', () => {
    expect(TOKEN.test('x = "/opt/jobs"')).toBe(true)
    expect(TOKEN.test('"${_SMD_BT}/opt/jobs"')).toBe(true)
    expect(TOKEN.test('/run/smd-jobs/demand-queue')).toBe(true)
    expect(TOKEN.test('/opt/data/jobs/records-state')).toBe(true)
    expect(TOKEN.test('/opt/jobsx')).toBe(false)
    expect(TOKEN.test('/run/smd-jobs-old')).toBe(false)
    expect(TOKEN.test('/tmp/opt/jobs')).toBe(false)
    expect(codeLines('a.py', '"""/opt/jobs is the runner."""\nX = 1  # /opt/jobs\n')).toEqual([
      '',
      'X = 1  ',
      '',
    ])
    expect(codeLines('a.py', 'X = "/opt/jobs"  # the runner\n').some((l) => TOKEN.test(l))).toBe(
      true
    )
    expect(codeLines('a.sh', 'echo "a # /opt/jobs"\n').some((l) => TOKEN.test(l))).toBe(true)
    expect(codeLines('a.ts', '// /opt/jobs\nconst x = 1\n').some((l) => TOKEN.test(l))).toBe(false)
    expect(codeLines('a.md', 'at /opt/jobs').length).toBe(0)
  })
})
