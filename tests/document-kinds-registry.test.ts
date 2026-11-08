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
import { readdirSync, readFileSync } from 'fs'
import { parse as parseYaml } from 'yaml'

interface Kind {
  key: string
  library_title: string
  variants: string[]
  produced_by: string[]
  reader: string
  variant_readers?: Record<string, string>
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

const PY_KIND_VARIANTS = /^KIND_VARIANTS: dict\[str, tuple\[str, \.\.\.\]\] = \{([\s\S]*?)^\}/m

/** The writer's kind -> variants map, read from its source. */
function kindVariants(path: string): Record<string, string[]> {
  const m = read(path).match(PY_KIND_VARIANTS)
  if (!m) throw new Error(`KIND_VARIANTS not found in ${path}`)
  const out: Record<string, string[]> = {}
  for (const x of m[1].matchAll(/"([a-z_]+)":\s*\(([^)]*)\)/g))
    out[x[1]] = [...x[2].matchAll(DQ)].map((y) => y[1])
  return out
}

/**
 * A ledger slot's kind is ``<kind>`` or ``<kind>.<variant>`` (``library_ledger.slot_name``).
 * Why a slot-name literal in source names a variant the registry does not list,
 * or null. A file name (``memo.docx``) is not a slot.
 */
const FILE_SUFFIXES = new Set([
  'doc',
  'docx',
  'dotx',
  'pdf',
  'md',
  'json',
  'yaml',
  'txt',
  'xlsx',
  'opus',
])
function slotLiteralProblem(literal: string): string | null {
  const m = literal.match(/^["']([a-z_]+)\.([a-z_]+)["']$/)
  if (!m || !KINDS.includes(m[1]) || FILE_SUFFIXES.has(m[2])) return null
  const listed = registry.kinds.find((k) => k.key === m[1])?.variants ?? []
  return listed.includes(m[2]) ? null : `${literal}: ${m[1]} lists ${JSON.stringify(listed)}`
}

function pyFiles(dir: string): string[] {
  return (readdirSync(dir, { recursive: true }) as string[])
    .filter(
      (f) =>
        f.endsWith('.py') &&
        !f.split('/').some((part) => part.startsWith('.') || part === 'node_modules')
    )
    .map((f) => `${dir}/${f}`)
}

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

  it('names each kind its reader, from the wording rules readers or a letter addressee', () => {
    const readers = members(
      'operator/runners/jobs/jobs/wording_rules_store.py',
      /^READERS: tuple\[str, \.\.\.\] = \(([\s\S]*?)\)/m,
      DQ
    )
    for (const k of registry.kinds) {
      const keys = Object.keys(k).filter((x) => x !== 'variant_readers')
      expect(keys.sort(), k.key).toEqual([
        'key',
        'library_title',
        'produced_by',
        'reader',
        'variants',
      ])
      expect([...readers, 'addressee'], k.key).toContain(k.reader)
      for (const [variant, reader] of Object.entries(k.variant_readers ?? {})) {
        expect(k.variants, `${k.key}.${variant}`).toContain(variant)
        expect(readers, `${k.key}.${variant}`).toContain(reader)
      }
    }
    expect(registry.kinds.find((k) => k.key === 'letter')?.reader).toBe('addressee')
  })

  it("the resolver's reader copy is the registry's, and its readers are the wording rules'", () => {
    const READERS_PY = 'operator/connectors/_sdk/operator_connector_sdk/library/readers.py'
    const src = read(READERS_PY)
    const kindReaders = src.match(/^KIND_READERS: dict\[str, str\] = \{([\s\S]*?)\}/m)
    if (!kindReaders) throw new Error('KIND_READERS not found')
    const pairs = [...kindReaders[1].matchAll(/"([a-z_]+)":\s*"([a-z_]+)"/g)].map((x) => [
      x[1],
      x[2],
    ])
    expect(pairs).toEqual(registry.kinds.map((k) => [k.key, k.reader]))
    const variantReaders = src.match(
      /^VARIANT_READERS: dict\[str, dict\[str, str\]\] = \{([\s\S]*?)^\}/m
    )
    if (!variantReaders) throw new Error('VARIANT_READERS not found')
    const got: Record<string, Record<string, string>> = {}
    for (const x of variantReaders[1].matchAll(/"([a-z_]+)":\s*\{([^}]*)\}/g))
      got[x[1]] = Object.fromEntries(
        [...x[2].matchAll(/"([a-z_]+)":\s*"([a-z_]+)"/g)].map((y) => [y[1], y[2]])
      )
    const want = Object.fromEntries(
      registry.kinds.filter((k) => k.variant_readers).map((k) => [k.key, k.variant_readers])
    )
    expect(got).toEqual(want)
    const tuple = /^READERS: tuple\[str, \.\.\.\] = \(([\s\S]*?)\)/m
    expect(members(READERS_PY, tuple, DQ)).toEqual(
      members('operator/runners/jobs/jobs/wording_rules_store.py', tuple, DQ)
    )
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

  it("the library acceptance writer's DOCUMENT_CLASSES is the registry, in its order", () => {
    const ACCEPT = 'operator/hermes-smd-overlay/establish_intake/library_accept.py'
    expect(disagreement(members(ACCEPT, PY_DOCUMENT_CLASSES, DQ), KINDS, 'ordered')).toBeNull()
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

  it("the acceptance writer's variants are the registry's, so a slot is kind, variant and attorney", () => {
    const ACCEPT = 'operator/hermes-smd-overlay/establish_intake/library_accept.py'
    const declared = Object.fromEntries(
      registry.kinds.filter((k) => k.variants.length).map((k) => [k.key, k.variants])
    )
    expect(kindVariants(ACCEPT)).toEqual(declared)
  })

  it('every slot-name literal in the operator source names a variant the registry lists', () => {
    // Tests are left out: they carry unlisted slots on purpose, to prove the
    // writer and the ledger refuse them (test_library_accept, test_library_caller).
    const problems: string[] = []
    for (const file of pyFiles('operator').filter((f) => !/\/tests?\/|\/test_[^/]*\.py$/.test(f))) {
      for (const lit of read(file).match(/["'][a-z_]+\.[a-z_]+["']/g) ?? []) {
        const why = slotLiteralProblem(lit)
        if (why) problems.push(`${file}: ${why}`)
      }
    }
    expect(problems).toEqual([])
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

  it('names a slot for a variant the registry does not list, and passes a listed one or a file name', () => {
    expect(slotLiteralProblem('"demand_letter.settlement"')).toMatch(/demand_letter lists/)
    expect(slotLiteralProblem('"memo.pre_suit"')).toMatch(/memo lists \[\]/)
    expect(slotLiteralProblem('"demand_letter.pre_suit"')).toBeNull()
    expect(slotLiteralProblem('"memo.docx"')).toBeNull()
  })
})
