/**
 * The document-kind registry (operator/contracts/document-kinds.yaml) is the
 * one list of kinds of document the Operator makes in a firm's Word files.
 * Every other copy of that list is read here, out of its own file, and must
 * agree with it: equal where the copy means "every kind", a subset where the
 * copy is one path's kinds (the drafting lane makes five of the seven; the
 * demand lane and the chat tool make the others).
 *
 * Each pinned copy is read from source, never restated here, so editing any one
 * of them to disagree fails this file. The last block proves the comparison
 * itself can fail.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { parse as parseYaml } from 'yaml'

interface Kind {
  key: string
  library_title: string
  variants: string[]
  produced_by: string[]
}

const PRODUCERS = ['chat_tool', 'drafting_job', 'demand_job'] as const

const read = (p: string) => readFileSync(p, 'utf-8')

const registry = parseYaml(read('operator/contracts/document-kinds.yaml')) as {
  version: number
  kinds: Kind[]
}
const KINDS = registry.kinds.map((k) => k.key)
const byProducer = (p: string) =>
  registry.kinds.filter((k) => k.produced_by.includes(p)).map((k) => k.key)

/** The quoted members of the first match of ``list`` (a literal regex whose group 1 is the list body). */
function members(path: string, list: RegExp, quote: RegExp): string[] {
  const m = read(path).match(list)
  if (!m) throw new Error(`${list} not found in ${path}`)
  return [...m[1].matchAll(quote)].map((x) => x[1])
}

const DQ = /"([a-z_]+)"/g
const PY_DOCUMENT_CLASSES = /^DOCUMENT_CLASSES = \(([\s\S]*?)\)/m
const PY_VARIANTS = /^VARIANTS = \(([\s\S]*?)\)/m
const PY_CLASS_TITLES = /^CLASS_TITLES: dict\[str, str\] = \{([\s\S]*?)\}/m
const TS_DOCUMENT_CLASSES = /^const DOCUMENT_CLASSES = \[([\s\S]*?)\] as const/m

function classTitles(path: string): Array<[string, string]> {
  const m = read(path).match(PY_CLASS_TITLES)
  if (!m) throw new Error(`CLASS_TITLES not found in ${path}`)
  return [...m[1].matchAll(/"([a-z_]+)":\s*"([^"]+)"/g)].map((x) => [x[1], x[2]])
}

/**
 * Why ``got`` disagrees with ``want``, or null. ``equal``: same members, same
 * order when ``ordered``. ``subset``: every member of ``got`` is in ``want``.
 */
function disagreement(
  got: readonly string[],
  want: readonly string[],
  mode: 'equal' | 'ordered' | 'subset'
): string | null {
  const extra = got.filter((k) => !want.includes(k))
  const missing = mode === 'subset' ? [] : want.filter((k) => !got.includes(k))
  if (extra.length || missing.length)
    return `extra ${JSON.stringify(extra)}, missing ${JSON.stringify(missing)}`
  if (new Set(got).size !== got.length) return `duplicates in ${JSON.stringify(got)}`
  if (mode === 'ordered' && got.join() !== want.join())
    return `order ${JSON.stringify(got)} != ${JSON.stringify(want)}`
  return null
}

describe('the registry itself', () => {
  it('declares seven kinds, each once, each with a known producer', () => {
    expect(registry.version).toBe(1)
    expect(KINDS).toHaveLength(7)
    expect(new Set(KINDS).size).toBe(7)
    for (const k of registry.kinds) {
      expect(k.library_title, k.key).toMatch(/^[A-Z][A-Za-z ]+$/)
      expect(Array.isArray(k.variants), k.key).toBe(true)
      expect(k.produced_by.length, k.key).toBeGreaterThan(0)
      for (const p of k.produced_by)
        expect(PRODUCERS as readonly string[], `${k.key}: ${p}`).toContain(p)
    }
  })

  it('carries no reader or audience field until something reads one', () => {
    for (const k of registry.kinds) {
      expect(Object.keys(k).sort(), k.key).toEqual([
        'key',
        'library_title',
        'produced_by',
        'variants',
      ])
    }
  })
})

describe('every copy of the kind list agrees with the registry', () => {
  const DOCX = 'operator/connectors/smokeball/smokeball_connector/docx_format.py'
  const LIBRARY = 'operator/connectors/smokeball/smokeball_connector/library.py'
  const LEDGER = 'operator/workspace_broker/drafting_ledger.py'
  const LANE = 'operator/hermes-smd-overlay/plugins/hermes-smd-lanes/drafting.py'

  it("the renderer's DOCUMENT_CLASSES is the registry, in its order", () => {
    expect(disagreement(members(DOCX, PY_DOCUMENT_CLASSES, DQ), KINDS, 'ordered')).toBeNull()
  })

  it("the library's CLASS_TITLES names every kind by its library title", () => {
    const titles = classTitles(LIBRARY)
    expect(
      disagreement(
        titles.map(([k]) => k),
        KINDS,
        'ordered'
      )
    ).toBeNull()
    for (const [key, title] of titles) {
      expect(title, key).toBe(registry.kinds.find((k) => k.key === key)?.library_title)
    }
  })

  it('every kind is made by the chat tool, which accepts the renderer list', () => {
    expect(disagreement(byProducer('chat_tool'), KINDS, 'equal')).toBeNull()
  })

  it("the drafting ledger's kinds are exactly the registry's drafting_job kinds, a subset of all", () => {
    // A subset by design: the drafting lane makes five kinds; a demand letter
    // is the demand lane's and a letter is made only in a chat turn.
    const ledger = members(LEDGER, PY_DOCUMENT_CLASSES, DQ)
    expect(disagreement(ledger, KINDS, 'subset')).toBeNull()
    expect(disagreement(ledger, byProducer('drafting_job'), 'equal')).toBeNull()
  })

  it("the overlay's drafting lane copies the ledger's list in its order", () => {
    expect(
      disagreement(
        members(LANE, PY_DOCUMENT_CLASSES, DQ),
        members(LEDGER, PY_DOCUMENT_CLASSES, DQ),
        'ordered'
      )
    ).toBeNull()
  })

  it("the demand lane makes the demand letter only, and its variants are the registry's", () => {
    expect(byProducer('demand_job')).toEqual(['demand_letter'])
    const variants = members('operator/runners/jobs/jobs/demand/firm.py', PY_VARIANTS, DQ)
    const declared = registry.kinds.find((k) => k.key === 'demand_letter')?.variants ?? []
    expect(disagreement(variants, declared, 'ordered')).toBeNull()
    for (const k of registry.kinds.filter((x) => x.key !== 'demand_letter'))
      expect(k.variants, k.key).toEqual([])
  })

  it("the library-sequence test's renderer list is every kind", () => {
    expect(
      disagreement(
        members('tests/self-initiation-sequence.test.ts', TS_DOCUMENT_CLASSES, /'([a-z_]+)'/g),
        KINDS,
        'equal'
      )
    ).toBeNull()
  })

  it("the drafting discipline's document_class sentence names every kind", () => {
    const text = read('operator/templates/drafting/drafting-discipline.md').replace(/\s+/g, ' ')
    const m = text.match(/with a `document_class` \(([^)]*)\)/)
    if (!m) throw new Error('the document_class sentence is gone from drafting-discipline.md')
    const listed = [...m[1].matchAll(/`([a-z_]+)`/g)].map((x) => x[1])
    expect(disagreement(listed, KINDS, 'equal')).toBeNull()
  })

  it("the establishment skill's class table and its description name every kind", () => {
    const skill = read('operator/skills/document-library-establishment/SKILL.md')
    const table = [...skill.matchAll(/^\| `([a-z_]+)`\s+\|/gm)].map((x) => x[1])
    expect(disagreement(table, KINDS, 'ordered')).toBeNull()
    const desc = skill.replace(/\s+/g, ' ').match(/drafting renderer knows \(([^)]*)\)/)
    if (!desc) throw new Error('the class list is gone from the skill description')
    const names = desc[1].split(',').map((s) => s.trim())
    const titles = registry.kinds.map((k) => k.library_title.toLowerCase())
    expect(disagreement(names, titles, 'ordered')).toBeNull()
  })
})

describe('the comparison can fail', () => {
  it('names a missing kind, an extra kind, a duplicate and a wrong order', () => {
    expect(
      disagreement(
        KINDS.filter((k) => k !== 'depo_outline'),
        KINDS,
        'equal'
      )
    ).toMatch(/missing \["depo_outline"\]/)
    expect(disagreement([...KINDS, 'pleading'], KINDS, 'equal')).toMatch(/extra \["pleading"\]/)
    expect(disagreement(['memo', 'memo'], KINDS, 'subset')).toMatch(/duplicates/)
    expect(disagreement([...KINDS].reverse(), KINDS, 'ordered')).toMatch(/^order/)
    expect(disagreement(['memo', 'pleading'], KINDS, 'subset')).toMatch(/extra \["pleading"\]/)
  })
})
