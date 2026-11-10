/**
 * Single-owner ADR decisions, enforced by what the code does.
 *
 * ADR 0083 said a drafted document's Word format lives in the firm's template
 * "and nowhere else". On 2026-10-07 #3093 built the drafting lane with its own
 * house style from yaml knobs, and nothing failed, because nothing read code
 * for the capability; a test over a list of known producers would have passed
 * it too, since the new lane was on no list. This test reads every tracked
 * source file for each decision's SIGNATURE (docs/adr/single-owner-registry.yaml)
 * and fails on a hit outside the decision's allowed paths, so a new copy fails
 * here whatever it is called.
 *
 * Guards on the guard:
 *   - every signature must match its own owner, or it measures nothing;
 *   - every entry's `new_copy`, a second copy written fresh, must be caught;
 *   - every known violation must still match, so that list only shrinks;
 *   - every ADR whose text makes an "only in X" style decision must be in the
 *     registry or reviewed out of it, so a new decision cannot skip it.
 *
 * Replay against history (2026-11-09): with known_violation emptied, the 0083
 * entry reports no drafting path on 8ca6641d5^ and reports
 * drafting/firm.py:97-98 (font, size_pt) and drafting/house.py:27
 * (HouseStyle) on 8ca6641d5, the #3093 merge.
 */
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  compile,
  globToRegExp,
  loadRegistry,
  loadSources,
  pathMatches,
  scanEntry,
  strayHits,
  stripCommentLines,
  trackedFiles,
  type RegistryEntry,
} from './_support/single-owner'

const ROOT = join(__dirname, '..')
const registry = loadRegistry(ROOT)
const sources = loadSources(ROOT)
const tracked = trackedFiles(ROOT)

const fmt = (hits: { path: string; line: number; text: string }[]) =>
  hits.map((h) => `  ${h.path}:${h.line}  ${h.text.slice(0, 120)}`).join('\n')

describe('single-owner registry: shape', () => {
  it('has entries, each with a unique id, an existing ADR and a reason on every path', () => {
    expect(registry.entries.length).toBeGreaterThan(0)
    const ids = registry.entries.map((e) => e.id)
    expect(new Set(ids).size).toBe(ids.length)
    for (const e of registry.entries) {
      expect(existsSync(join(ROOT, e.adr)), `${e.id}: ${e.adr} does not exist`).toBe(true)
      expect(e.decision?.trim(), `${e.id}: decision is empty`).toBeTruthy()
      expect(e.signature.match.length, `${e.id}: no signature`).toBeGreaterThan(0)
      expect(e.allowed.length, `${e.id}: no allowed path`).toBeGreaterThan(0)
      for (const p of [...e.allowed, ...(e.known_violation ?? [])]) {
        expect(p.why?.trim(), `${e.id}: ${p.path} has no why`).toBeTruthy()
      }
    }
  })

  it('every allowed path and known violation names a tracked file', () => {
    for (const e of registry.entries) {
      for (const p of [...e.allowed, ...(e.known_violation ?? [])]) {
        const re = globToRegExp(p.path)
        expect(
          tracked.some((f) => re.test(f)),
          `${e.id}: ${p.path} matches no tracked file (renamed? update the registry)`
        ).toBe(true)
      }
    }
  })
})

describe.each(registry.entries.map((e) => [e.id, e] as [string, RegistryEntry]))(
  '%s',
  (_id, entry) => {
    it('its signature matches its own owner (a signature that matches nothing measures nothing)', () => {
      const owners = scanEntry(entry, sources).filter((h) => pathMatches(h.path, entry.allowed))
      expect(
        owners.length,
        `${entry.id}: the signature no longer matches any allowed path`
      ).toBeGreaterThan(0)
    })

    it('a second copy written fresh is caught', () => {
      const { path, code } = entry.new_copy
      expect(
        pathMatches(path, entry.allowed),
        `${entry.id}: new_copy.path is an allowed path`
      ).toBe(false)
      expect(
        pathMatches(path, entry.known_violation),
        `${entry.id}: new_copy.path is a known violation`
      ).toBe(false)
      expect(
        strayHits(entry, [{ path, text: code }]).length,
        `${entry.id}: the signature misses new_copy`
      ).toBeGreaterThan(0)
    })

    it('appears nowhere outside its allowed paths', () => {
      const stray = strayHits(entry, sources)
      expect(
        stray,
        `${entry.adr} gives this capability one owner, and these lines are a second copy:\n${fmt(stray)}\n` +
          `Route the work through the owner, or amend the ADR and its entry in docs/adr/single-owner-registry.yaml.`
      ).toEqual([])
    })

    it('every known violation is still present (the list only shrinks)', () => {
      const hits = scanEntry(entry, sources)
      for (const k of entry.known_violation ?? []) {
        const re = globToRegExp(k.path)
        expect(
          hits.some((h) => re.test(h.path)),
          `${entry.id}: ${k.path} no longer carries the signature; remove it from known_violation`
        ).toBe(true)
      }
    })
  }
)

describe('single-owner registry: completeness', () => {
  it('every ADR that makes an "only in X" decision is registered or reviewed out', () => {
    const phrasing = compile(registry.phrasing, 'i')
    const covered = new Set([
      ...registry.entries.map((e) => e.adr),
      ...registry.reviewed_not_code.map((r) => r.adr),
    ])
    const adrs = readdirSync(join(ROOT, 'docs/adr'))
      .filter((f) => /^\d{4}-.*\.md$/.test(f))
      .map((f) => `docs/adr/${f}`)
    const unregistered = adrs.filter(
      (a) => phrasing.test(readFileSync(join(ROOT, a), 'utf8')) && !covered.has(a)
    )
    expect(
      unregistered,
      `These ADRs read as single-owner decisions and are in neither entries nor reviewed_not_code ` +
        `of docs/adr/single-owner-registry.yaml:\n  ${unregistered.join('\n  ')}`
    ).toEqual([])
  })

  it('every reviewed-out ADR exists, carries a reason, and still uses the phrasing', () => {
    const phrasing = compile(registry.phrasing, 'i')
    for (const r of registry.reviewed_not_code) {
      expect(existsSync(join(ROOT, r.adr)), `${r.adr} does not exist`).toBe(true)
      expect(r.why?.trim(), `${r.adr} has no why`).toBeTruthy()
      expect(
        phrasing.test(readFileSync(join(ROOT, r.adr), 'utf8')),
        `${r.adr} no longer matches; drop it`
      ).toBe(true)
    }
  })
})

describe('single-owner scanner', () => {
  const format = registry.entries.find((e) => e.id === 'word-format-lives-in-the-firm-template')!

  it('catches the #3093 shape: a lane building HouseStyle from its own firm yaml', () => {
    const house3093 = [
      'def house_for(fmt, cls):',
      '    from smokeball_connector.docx_classes import HouseStyle',
      '    lay = dict((fmt.get("layout") or {}).get(cls) or {})',
      '    return HouseStyle(',
      '        font=str(fmt["font"]),',
      '        size_pt=float(fmt["size_pt"]),',
      '        body_line_spacing=lay.get("line_spacing"),',
      '    )',
    ].join('\n')
    const firm3093 = [
      'FORMAT = {',
      '    "font": ("str", True),',
      '    "size_pt": ("float", True),',
      '}',
    ].join('\n')
    const stray = strayHits(format, [
      { path: 'operator/runners/jobs/jobs/briefs/house.py', text: house3093 },
      { path: 'operator/runners/jobs/jobs/briefs/firm.py', text: firm3093 },
    ])
    expect(new Set(stray.map((h) => h.path))).toEqual(
      new Set([
        'operator/runners/jobs/jobs/briefs/house.py',
        'operator/runners/jobs/jobs/briefs/firm.py',
      ])
    )
  })

  it('ignores a comment that names the capability', () => {
    expect(stripCommentLines('# run.font.name = "x"\n  // HouseStyle(\nreal = 1')).toBe(
      '\n\nreal = 1'
    )
    expect(strayHits(format, [{ path: 'operator/x.py', text: '# HouseStyle(font="x")' }])).toEqual(
      []
    )
  })

  it('globs: ** crosses directories, * does not', () => {
    expect(globToRegExp('src/lib/**').test('src/lib/a/b.ts')).toBe(true)
    expect(globToRegExp('a/docx_*.py').test('a/docx_base.py')).toBe(true)
    expect(globToRegExp('a/docx_*.py').test('a/sub/docx_base.py')).toBe(false)
  })
})
