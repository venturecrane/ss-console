/**
 * no-history-in-file-bodies: a file body says WHY a line exists; WHAT HAPPENED and WHEN
 * belongs to git.
 *
 * Until 2026-10-22 operator/templates/Dockerfile carried 1,161 lines of dated pin changelog
 * (129 entries) above `ARG OVERLAY_REF`, and operator/contracts/overlay-pairs.json carried a
 * 70,371-character `overlayRefNote` ledger. Every overlay pin bump appended to both, so every
 * bump from every concurrent session collided on the same lines (105 of 442 commits in the
 * 30 days before touched the Dockerfile), and a reader looking for the current contract had
 * to scroll past four months of narrative. The seat-architecture review (2026-10-22) named
 * this the top textual-conflict source for parallel agents.
 *
 * The rule, mechanically: in the guarded files, no comment line may START a paragraph with a
 * date (2026-10-20, 2026-10-20d), a short commit sha followed by a separator (e031c09 —),
 * "Pinned at <sha>", or a version token (v0.4.5, v2026.9.14). A date in the middle of a
 * sentence, or a wrapped line that happens to begin with one, is not an entry: the check
 * looks at the line before, and only a line that opens a paragraph (after a blank, a
 * non-comment, or a sentence that ended) counts. Rationale that cites a date stays legal.
 *
 * Two surfaces are ratcheted rather than zeroed, with the only-tightens idiom from
 * tests/operator-module-size.test.ts (grow fails; shrink fails until regenerated):
 *   - live seats' customer.yaml, whose bytes reach every seat on publish and are rewritten
 *     by the Wave 2 compiled seat spec, so their three existing entries are pinned, not purged;
 *   - note-like string fields in operator/contracts JSON (syncNote and friends), whose total
 *     size per file may only shrink; overlayRefNote itself is capped at 400 characters.
 *
 * Regenerate the baseline (after a deliberate reduction):
 *   UPDATE_DATED_ENTRY_BASELINE=1 npx vitest run tests/no-history-in-file-bodies.test.ts
 */
import { describe, expect, it } from 'vitest'
import { readFileSync, writeFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'

const ROOT = resolve(__dirname, '..')
const BASELINE_PATH = resolve(ROOT, 'operator/contracts/dated-entry-baseline.json')
const REGENERATE = process.env.UPDATE_DATED_ENTRY_BASELINE === '1'
const REGENERATE_HINT =
  'UPDATE_DATED_ENTRY_BASELINE=1 npx vitest run tests/no-history-in-file-bodies.test.ts'

/** A comment line whose text opens with a history marker. */
const ENTRY =
  /^\s*#\s*(?:20\d\d-[01]\d-[0-3]\d[a-z]?\s*[:(]|[0-9a-f]{7,8}\s+(?:—|-|–|->|:)|Pinned at [0-9a-f]{7,8}\b|v\d+\.\d+\.\d+\b)/
const COMMENT = /^\s*#/

/** The line before closed its sentence (or was not a comment), so this line opens a paragraph. */
function opensParagraph(prev: string | undefined): boolean {
  if (prev === undefined) return true
  const t = prev.trimEnd()
  if (!t || !COMMENT.test(t)) return true
  if (/^\s*#\s*$/.test(t)) return true // a bare "#" separator line opens a paragraph
  return /[.)!?;]$/.test(t)
}

export function datedEntries(text: string): Array<{ line: number; text: string }> {
  const lines = text.split('\n')
  const hits: Array<{ line: number; text: string }> = []
  lines.forEach((l, i) => {
    if (ENTRY.test(l) && opensParagraph(i ? lines[i - 1] : undefined))
      hits.push({ line: i + 1, text: l.trim() })
  })
  return hits
}

function walk(dir: string, pred: (p: string) => boolean, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    const st = statSync(p)
    if (st.isDirectory()) {
      if (name === 'node_modules' || name.startsWith('.')) continue
      walk(p, pred, out)
    } else if (pred(p)) out.push(p)
  }
  return out
}

const isShell = (p: string) =>
  p.endsWith('.sh') || p.endsWith('Dockerfile') || p.endsWith('.template')
const isYaml = (p: string) => p.endsWith('.yaml') || p.endsWith('.yml')

/** Zero-tolerance set: templates, operator tooling, CI scripts, fleet and role config, seat scaffolds. */
function zeroToleranceFiles(): string[] {
  const files = [
    ...walk(resolve(ROOT, 'operator/templates'), (p) => isShell(p) || isYaml(p)),
    ...walk(resolve(ROOT, 'operator/bin'), isShell),
    ...walk(resolve(ROOT, 'operator/runners'), isShell),
    ...walk(resolve(ROOT, 'scripts'), isShell),
    ...walk(resolve(ROOT, 'operator/fleet'), isYaml),
    ...walk(resolve(ROOT, 'operator/roles'), isYaml),
  ]
  for (const name of readdirSync(resolve(ROOT, 'operator/customers'))) {
    if (name.startsWith('_')) files.push(...walk(resolve(ROOT, 'operator/customers', name), isYaml))
  }
  return files.map((p) => relative(ROOT, p)).sort()
}

/** Ratcheted set: live seats' authored yaml. */
function liveSeatYaml(): string[] {
  const out: string[] = []
  for (const name of readdirSync(resolve(ROOT, 'operator/customers'))) {
    if (name.startsWith('_') || name.includes('.decommissioned.')) continue
    const dir = resolve(ROOT, 'operator/customers', name)
    if (!statSync(dir).isDirectory()) continue
    for (const f of ['customer.yaml', 'routine-grid.yaml', 'seat.yaml']) {
      try {
        statSync(join(dir, f))
        out.push(relative(ROOT, join(dir, f)))
      } catch {
        /* absent */
      }
    }
  }
  return out.sort()
}

const NOTE_KEY = /(?:^|[a-z])[Nn]otes?$/

/** Total characters held by note-like string fields anywhere in a JSON document. */
function noteChars(value: unknown, key = ''): number {
  if (typeof value === 'string') return NOTE_KEY.test(key) ? value.length : 0
  if (Array.isArray(value)) return value.reduce<number>((a, v) => a + noteChars(v, key), 0)
  if (value && typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>).reduce(
      (a, [k, v]) => a + noteChars(v, k),
      0
    )
  }
  return 0
}

interface Baseline {
  datedEntries: Record<string, number>
  noteChars: Record<string, number>
}

function contractsJson(): string[] {
  return (
    readdirSync(resolve(ROOT, 'operator/contracts'))
      // Generated snapshots (gate coverage, policy inputs) are produced by their own tests and
      // regenerate when a seat is added; their note text is not hand-written history.
      .filter(
        (n) =>
          n.endsWith('.json') &&
          n !== 'dated-entry-baseline.json' &&
          !/^(gate-coverage-snapshot|policy-inputs|policy-matrix)/.test(n)
      )
      .map((n) => `operator/contracts/${n}`)
      .sort()
  )
}

function measure(): Baseline {
  const datedEntriesCount: Record<string, number> = {}
  for (const f of liveSeatYaml()) {
    const n = datedEntries(readFileSync(resolve(ROOT, f), 'utf-8')).length
    if (n) datedEntriesCount[f] = n
  }
  const chars: Record<string, number> = {}
  for (const f of contractsJson()) {
    const n = noteChars(JSON.parse(readFileSync(resolve(ROOT, f), 'utf-8')))
    if (n) chars[f] = n
  }
  return { datedEntries: datedEntriesCount, noteChars: chars }
}

function loadBaseline(): Baseline {
  return JSON.parse(readFileSync(BASELINE_PATH, 'utf-8')) as Baseline
}

describe('no history in file bodies', () => {
  it('guarded templates, tooling, scripts and config carry no dated changelog entries', () => {
    const offenders: string[] = []
    for (const f of zeroToleranceFiles()) {
      for (const hit of datedEntries(readFileSync(resolve(ROOT, f), 'utf-8'))) {
        offenders.push(`${f}:${hit.line}: ${hit.text.slice(0, 100)}`)
      }
    }
    expect(
      offenders,
      'A comment that opens a paragraph with a date, a commit sha, "Pinned at", or a version is history. ' +
        'Put what changed and why in the commit message and the PR; keep the comment to why the line exists now.\n' +
        offenders.join('\n')
    ).toEqual([])
  })

  it('the overlay-pairs manifest holds no pin ledger', () => {
    const manifest = JSON.parse(
      readFileSync(resolve(ROOT, 'operator/contracts/overlay-pairs.json'), 'utf-8')
    ) as {
      overlayRefNote: string
    }
    expect(
      manifest.overlayRefNote.length,
      'overlayRefNote is a pointer, not a ledger'
    ).toBeLessThanOrEqual(400)
    expect(
      datedEntries(manifest.overlayRefNote.replace(/^/gm, '# ')),
      'no dated entries inside overlayRefNote'
    ).toEqual([])
  })

  it('live seat yaml and contract note fields only tighten (regenerate after a deliberate reduction)', () => {
    const current = measure()
    if (REGENERATE) {
      writeFileSync(BASELINE_PATH, JSON.stringify(current, null, 2) + '\n')
      return
    }
    const baseline = loadBaseline()
    const problems: string[] = []
    const keys = new Set([
      ...Object.keys(baseline.datedEntries),
      ...Object.keys(current.datedEntries),
    ])
    for (const k of keys) {
      const was = baseline.datedEntries[k] ?? 0
      const now = current.datedEntries[k] ?? 0
      if (now > was)
        problems.push(
          `${k}: ${now} dated entries, baseline ${was}. History goes in the commit, not the yaml.`
        )
      else if (now < was)
        problems.push(
          `${k}: ${now} dated entries, baseline ${was}. Tightened; regenerate: ${REGENERATE_HINT}`
        )
    }
    const nkeys = new Set([...Object.keys(baseline.noteChars), ...Object.keys(current.noteChars)])
    for (const k of nkeys) {
      const was = baseline.noteChars[k] ?? 0
      const now = current.noteChars[k] ?? 0
      if (now > was)
        problems.push(
          `${k}: note fields hold ${now} chars, baseline ${was}. A note states the current decision, not its history.`
        )
      else if (now < was)
        problems.push(
          `${k}: note fields hold ${now} chars, baseline ${was}. Tightened; regenerate: ${REGENERATE_HINT}`
        )
    }
    expect(problems, problems.join('\n')).toEqual([])
  })

  it('detects an entry at a paragraph start and ignores a wrapped date mid-sentence', () => {
    const body = [
      '# This behaviour was live-probed on the seat (the cgroup layout is',
      '# 2026-08-31: v2 mounted bare), so the check reads the v1 mount.',
      '',
      '# 2026-10-20 (3d64eda0, overlay #533): a reply held in a wake turn names the job.',
      'ARG X=1',
      '# e031c09 — registry entry for the connector (overlay#180).',
      '# v0.4.5 adds the Telegram allowlist. Superset of v0.4.4.',
    ].join('\n')
    expect(datedEntries(body).map((h) => h.line)).toEqual([4, 6, 7])
  })
})
