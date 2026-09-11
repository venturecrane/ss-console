/**
 * Guard 2 (audit Wave 0) — CONSUMER-WITHOUT-PRODUCER guard.
 *
 * THE INERT-CONTROL CLASS THIS CLOSES
 * -----------------------------------
 * `ACCEPTED_ACTION_TYPES` (operator/adapter/audit_log.py) is a closed enum.
 * Its TS mirror (`AUDIT_ACTION_TYPES` in src/lib/portal/operator/audit.ts) is
 * parity-tested, and the values are CONSUMED widely: the audit viewer filters
 * on them, src/lib/portal/operator/compliance.ts rolls them up, and
 * operator/adapter/evidence/packet.py `_count(...)`s them into the compliance
 * evidence packet. Nothing asserted that each consumed value has a RUNTIME
 * PRODUCER. An action_type can be in the enum, mirrored to TS, and counted by
 * the compliance packet while being emitted by NOBODY — a metric that is
 * structurally always-zero, indistinguishable from "genuinely zero events".
 * That is the inert-control class for the audit vocabulary.
 *
 * WHAT THIS GUARD ASSERTS (three properties)
 * ------------------------------------------
 * The producer manifest (operator/contracts/audit-action-type-producers.json)
 * classifies every action_type as produced in `ss-console` (this repo) or
 * `overlay` (venturecrane/hermes-smd-overlay, the runtime that ships on
 * customer Machines — a separate pinned repo not checked out in this CI).
 *
 *   1. COVERAGE — manifest keys == ACCEPTED_ACTION_TYPES exactly. A new
 *      action_type with no manifest entry fails CI, forcing a conscious
 *      producer classification. This is the gate that would have caught a
 *      consumed-but-never-produced type.
 *   2. ss-console PRODUCERS ARE REAL — every `ss-console` entry names a
 *      producerFile that exists and contains the action_type token in code.
 *      Deleting the only producer of a still-consumed type fails CI here.
 *   3. overlay ENTRIES DOCUMENT THE BOUNDARY — every `overlay` entry carries
 *      `overlayProducer` + `reason`. The overlay repo is not present in this
 *      CI, so the gate cannot grep its producers; it forces the human to NAME
 *      the cross-repo producer instead (the same shape the overlay-pairs drift
 *      gate uses).
 *
 * This is ORTHOGONAL to the TS<->Python parity test
 * (tests/portal-operator-audit.test.ts). Parity proves the two enums agree;
 * this proves each member has a producer.
 *
 * A dashboard-consumed-field check rides along: the aliveness header consumes
 * `stickyStopLevel` from the Hermes bridge; the guard asserts the consumer's
 * level vocabulary stays anchored to the substrate's StickyStopLevel so a
 * renamed level cannot silently make the chip dead.
 */

import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

const ENUM_SOURCE = resolve('operator/adapter/audit_log.py')
const MANIFEST_PATH = resolve('operator/contracts/audit-action-type-producers.json')

type ProducerSide = 'ss-console' | 'overlay' | 'deferred'

interface ProducerEntry {
  side: ProducerSide
  producerFile?: string
  overlayProducer?: string
  producerIntent?: string
  reason?: string
  note?: string
}

const KNOWN_SIDES: readonly ProducerSide[] = ['ss-console', 'overlay', 'deferred']

interface Manifest {
  producers: Record<string, ProducerEntry>
}

/**
 * Parse ACCEPTED_ACTION_TYPES out of audit_log.py. Mirrors the extraction the
 * existing parity test (tests/portal-operator-audit.test.ts) performs, so the
 * two gates read the enum the same way.
 */
function parseAcceptedActionTypes(): string[] {
  const py = readFileSync(ENUM_SOURCE, 'utf-8')
  const start = py.indexOf('ACCEPTED_ACTION_TYPES = frozenset(')
  if (start < 0) throw new Error('ACCEPTED_ACTION_TYPES not found in audit_log.py')
  // Bound the block at the closing `\n)` of frozenset(...), NOT the first `}` —
  // the comment bodies inside the enum contain `{"wakeAgent": false}` braces
  // that would truncate the parse early (this is exactly how SUPPRESSED_WAKE,
  // the last member, gets dropped). Mirrors the extraction in
  // tests/portal-operator-audit.test.ts so both gates read the enum the same.
  const end = py.indexOf('\n)', start)
  if (end < 0) throw new Error('could not find the closing ) of the frozenset literal')
  const body = py.slice(start, end)
  // Only string-literal members; the block is interleaved with comments, but
  // comment prose never contains a bare "ALL_CAPS" quoted token by convention.
  const members = [...body.matchAll(/"([A-Z0-9_]+)"/g)].map((m) => m[1])
  return [...new Set(members)].sort()
}

function loadManifest(): Manifest {
  const raw: unknown = JSON.parse(readFileSync(MANIFEST_PATH, 'utf-8'))
  if (typeof raw !== 'object' || raw === null || !('producers' in raw)) {
    throw new Error('audit-action-type-producers.json must have a "producers" object')
  }
  return raw as Manifest
}

/**
 * A producer is "real" iff the named source file exists and contains the
 * action_type token as a quoted literal in a non-comment line. The token-in-
 * code requirement is what makes the gate bite when a producer is deleted: an
 * entry pointing at a file that no longer emits the type fails.
 */
function fileEmitsToken(file: string, token: string): boolean {
  const abs = resolve(file)
  if (!existsSync(abs)) return false
  const src = readFileSync(abs, 'utf-8')
  for (const rawLine of src.split('\n')) {
    const line = rawLine.trim()
    if (line.startsWith('#') || line.startsWith('*') || line.startsWith('//')) continue
    if (line.includes(`"${token}"`) || line.includes(`'${token}'`)) return true
  }
  return false
}

describe('audit action_type consumer-without-producer guard', () => {
  const enumTypes = parseAcceptedActionTypes()
  const manifest = loadManifest()
  const manifestKeys = Object.keys(manifest.producers).sort()

  it('extracted enum is non-trivial', () => {
    expect(enumTypes.length).toBeGreaterThan(20)
  })

  it('manifest covers ACCEPTED_ACTION_TYPES exactly (no drift either way)', () => {
    const missing = enumTypes.filter((t) => !manifestKeys.includes(t))
    const extra = manifestKeys.filter((t) => !enumTypes.includes(t))
    expect(
      { missingFromManifest: missing, staleManifestEntries: extra },
      'Every action_type in ACCEPTED_ACTION_TYPES must have a producer-manifest entry, ' +
        'and the manifest must not list types no longer in the enum. A NEW action_type ' +
        'with no manifest entry is the consumer-without-producer hole — classify it as ' +
        'ss-console (name the producerFile) or overlay (name overlayProducer + reason) ' +
        'in operator/contracts/audit-action-type-producers.json.'
    ).toEqual({ missingFromManifest: [], staleManifestEntries: [] })
  })

  it('every ss-console producer file exists and emits its token', () => {
    const broken: string[] = []
    for (const [type, entry] of Object.entries(manifest.producers)) {
      if (entry.side !== 'ss-console') continue
      if (!entry.producerFile) {
        broken.push(`${type}: side=ss-console but no producerFile named`)
        continue
      }
      if (!existsSync(resolve(entry.producerFile))) {
        broken.push(`${type}: producerFile ${entry.producerFile} does not exist`)
        continue
      }
      if (!fileEmitsToken(entry.producerFile, type)) {
        broken.push(
          `${type}: producerFile ${entry.producerFile} no longer contains the token in code ` +
            `(producer removed? this action_type is now consumed-but-not-produced)`
        )
      }
    }
    expect(broken, broken.join('\n')).toEqual([])
  })

  it('every overlay entry names its cross-repo producer and a reason', () => {
    const underdocumented: string[] = []
    for (const [type, entry] of Object.entries(manifest.producers)) {
      if (entry.side !== 'overlay') continue
      if (!entry.overlayProducer || entry.overlayProducer.trim() === '') {
        underdocumented.push(`${type}: side=overlay but no overlayProducer named`)
      }
      if (!entry.reason || entry.reason.trim() === '') {
        underdocumented.push(`${type}: side=overlay but no reason given`)
      }
    }
    expect(
      underdocumented,
      'Overlay-produced action_types must NAME the runtime producer (the overlay repo is ' +
        'not in this CI, so the gate cannot grep it) and state why no ss-console producer ' +
        'exists. This is the cross-repo human decision, mirroring overlay-pairs.json.\n' +
        underdocumented.join('\n')
    ).toEqual([])
  })

  it('every deferred entry names the intended producer, a reason, and is the rare exception', () => {
    const underdocumented: string[] = []
    const deferred: string[] = []
    for (const [type, entry] of Object.entries(manifest.producers)) {
      if (entry.side !== 'deferred') continue
      deferred.push(type)
      if (!entry.producerIntent || entry.producerIntent.trim() === '') {
        underdocumented.push(`${type}: side=deferred but no producerIntent named`)
      }
      if (!entry.reason || entry.reason.trim() === '') {
        underdocumented.push(`${type}: side=deferred but no reason given`)
      }
    }
    expect(
      underdocumented,
      'A "deferred" action_type is the live inert-control instance: in the vocabulary, ' +
        'consumed, but with no row-producer yet. Each must name producerIntent (where the ' +
        'producer WILL live / the structured emitter awaiting persistence) and a reason that ' +
        'references the unblocking issue.\n' +
        underdocumented.join('\n')
    ).toEqual([])

    // Guardrail on the guardrail: "deferred" is meant to document a real,
    // tracked gap — not to become a dumping ground that quietly drains the
    // producer requirement. If this count climbs, the inert-control class is
    // spreading, not shrinking. Tighten or resolve before raising this bound.
    expect(
      deferred.length,
      `deferred (consumed-but-not-produced) action_types: ${deferred.join(', ')}. ` +
        'This is the inert-control class the audit found; keep it shrinking, not growing.'
    ).toBeLessThanOrEqual(1)
  })

  it('side is always one of the known values', () => {
    for (const [type, entry] of Object.entries(manifest.producers)) {
      expect(KNOWN_SIDES, `${type}: unknown side ${entry.side}`).toContain(entry.side)
    }
  })

  // Proof the producer-existence check bites: fileEmitsToken must distinguish
  // a real emit from a comment-only or absent mention. (This predicate is what
  // caught RBAC_EVENT during authoring — it appears only in comments + the
  // enum mirror, never as a written row, so it is classified `deferred`.)
  it('producer-existence predicate distinguishes real emit from comment-only mention', () => {
    // A genuine ss-console producer: token written in code.
    expect(fileEmitsToken('operator/safety-substrate/sticky_stop.py', 'AGENT_STOPPED')).toBe(true)
    // A type that exists ONLY in comments + the enum mirror, never written as a
    // row in rbac-audit.ts — the predicate must NOT count it as produced there.
    expect(fileEmitsToken('src/lib/portal/operator/rbac-audit.ts', 'RBAC_EVENT')).toBe(false)
    // A token absent from a file is not produced there.
    expect(fileEmitsToken('operator/safety-substrate/sticky_stop.py', 'VOICE_GATE_PASSED')).toBe(
      false
    )
  })
})

/**
 * Dashboard-consumed-field check: the aliveness header consumes
 * `stickyStopLevel` (OK or HARD_STOP) from the Hermes
 * bridge. That vocabulary mirrors the substrate's StickyStopLevel
 * (operator/safety-substrate/sticky_stop.py). If a level is renamed on the
 * substrate side without updating the consumer, the chip silently stops
 * recognising a stopped agent (a renamed level falls through to 'OK'). The
 * guard anchors the consumer vocabulary to the producer enum.
 */
describe('dashboard sticky-stop consumer is anchored to its producer enum', () => {
  it('aliveness consumes exactly the StickyStopLevel vocabulary', () => {
    const ts = readFileSync(resolve('src/lib/portal/operator/aliveness.ts'), 'utf-8')
    // The consumer's declared union, e.g. 'OK' | 'WARN' | 'SOFT_STOP' | 'HARD_STOP'
    const m = ts.match(/stickyStopLevel:\s*((?:'[A-Z_]+'\s*\|?\s*)+)/)
    expect(m, 'stickyStopLevel union not found in aliveness.ts').toBeTruthy()
    const consumerLevels = [...m![1].matchAll(/'([A-Z_]+)'/g)].map((x) => x[1]).sort()

    const py = readFileSync(resolve('operator/safety-substrate/sticky_stop.py'), 'utf-8')
    // Read the StickyStopLevel enum body: members declared as NAME = "VALUE".
    const enumStart = py.indexOf('class StickyStopLevel(')
    expect(enumStart, 'StickyStopLevel enum not found in sticky_stop.py').toBeGreaterThan(-1)
    // Bound the class body at the next top-level `class ` / `def ` declaration.
    const rest = py.slice(enumStart + 1)
    const nextDecl = rest.search(/\n(class |def |_LEVEL_ORDER)/)
    const enumBody = nextDecl > -1 ? rest.slice(0, nextDecl) : rest
    const producerLevels = [...enumBody.matchAll(/^\s+([A-Z_]+)\s*=\s*"[A-Z_]+"/gm)]
      .map((m) => m[1])
      .sort()

    // >= 2, not > 2: the ladder is OK / HARD_STOP since the 2026-09-02
    // collapse. The old bound quietly encoded the four-state shape, so it
    // would have failed the removal rather than checked it -- while still
    // catching the case this guard is for, an enum that parsed to nothing.
    expect(
      producerLevels.length,
      'expected StickyStopLevel to declare members'
    ).toBeGreaterThanOrEqual(2)
    expect(
      consumerLevels,
      'aliveness.ts stickyStopLevel union must match the substrate StickyStopLevel ' +
        'vocabulary; a renamed/added level silently degrades a stopped agent to OK (dead chip).'
    ).toEqual(producerLevels)
  })
})
