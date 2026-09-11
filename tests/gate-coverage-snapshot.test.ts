/**
 * Gate-coverage snapshot: the console half of the additive-gate assertion.
 *
 * WHAT THIS PINS. Two fields in every seat's customer.yaml decide whether the
 * distillation gates have anything to act on: `voice_library` (is there a corpus
 * pointer at all) and `output_classes` (which classes declare that a voice or
 * format spec is EXPECTED). The overlay asserts, per seat, that turning a gate
 * on is ADDITIVE — that no seat silently loses a control when the gate repoints.
 * It can only assert that against a picture of what each seat declares, and the
 * picture has to come from somewhere. This file is where it comes from.
 *
 * WHY A CHECKED-IN SNAPSHOT AND NOT A LIVE READ. The overlay cannot read this
 * repo at test time, and a fixture it maintains by hand drifts the moment a seat
 * is edited here — silently, and in the direction that matters, because a seat
 * that GAINS `output_classes: {staff: {voice_spec: expected}}` is exactly the
 * change an additive-gate assertion must see. Regenerating from the real
 * customer.yaml files and failing on drift makes the two repos move together:
 * editing a gate-relevant field here fails this test until the snapshot is
 * regenerated, and regenerating changes the pinned hash, which fails the
 * overlay's test until its copy is updated too. Neither side can move alone.
 *
 * THE FIELD NAME IS A TRAP WORTH NAMING. `voice_library_nonempty` is a claim
 * about the customer.yaml BLOCK — the mapping exists and has at least one key.
 * It is NOT a claim that the seat has voice samples. ashton-price's own
 * customer.yaml says "voice_library below is empty until then" while carrying a
 * populated `voice_library:` block; the comment means the R2 vault is empty, and
 * the two senses of "empty" sit fourteen lines apart in the same file. This
 * snapshot records the config-plane fact only. Corpus emptiness is the
 * compilers' business at establishment time (spec_leak_check and voice_profile
 * both exit nonzero on an empty corpus), not a thing this repo can see.
 *
 * TWO ARTIFACTS, ONE GENERATOR. `gate-coverage-snapshot.json` is the full record
 * (voice AND format, with absence distinguished from `none`).
 * `gate-coverage-snapshot.overlay.json` is the same data projected onto the
 * voice axis, in the overlay fixture's exact merged shape, ready to be copied
 * verbatim into hermes-smd-overlay `tests/contract/seat_gate_binding_snapshot.json`.
 * That fixture's own header says "REGENERATION IS CONSOLE-SIDE" and nothing
 * console-side regenerated it, so it would have gone stale on the next seat
 * edit with both repos green. Emitting it here is what makes the sentence true.
 *
 * REGENERATE (writes both files):
 *
 *   UPDATE_GATE_COVERAGE_SNAPSHOT=1 npx vitest run tests/gate-coverage-snapshot.test.ts
 *
 * When the drift test fires: regenerate, then COPY
 * `operator/contracts/gate-coverage-snapshot.overlay.json` over the overlay's
 * fixture in the SAME change that edits the seat configs. The overlay has no
 * way to pull it.
 *
 * @see operator/contracts/gate-coverage-snapshot.json (the artifact)
 * @see operator/contracts/gate-coverage-snapshot.overlay.json (the copy-me twin)
 * @see operator/contracts/output-classes.yaml (what the classes MEAN)
 * @see tests/customer-yaml-parity-contract.test.ts (the pinned-hash precedent)
 */

import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { parse as parseYaml } from 'yaml'

const CUSTOMERS_DIR = 'operator/customers'
const SNAPSHOT_PATH = 'operator/contracts/gate-coverage-snapshot.json'

/**
 * Canonical-content hash of the `seats` object (sorted keys, compact
 * separators) — independent of file formatting, so prettier cannot break it.
 *
 * MUST equal the constant the overlay's additive-gate test pins. Update in BOTH
 * repos when seat coverage changes; `vitest -u`-style regeneration deliberately
 * does NOT update this line, so the cross-repo half stays a human edit.
 */
const PINNED_SEATS_SHA256 = '13cb962ebe7f095fdf5967abf4b19e24e54bed07effda422d32e84f0bcdedbfc'

/** A seat's gate-relevant declaration, as the overlay consumes it. */
interface SeatCoverage {
  /**
   * The customer.yaml `voice_library` mapping exists and has ≥1 key. NOT a
   * statement about the R2 vault's contents — see the header.
   */
  readonly voice_library_nonempty: boolean
  /**
   * Declared output classes. `null` for a property the class does not declare,
   * never the string 'none': `none` is an authored choice that a spec is not
   * expected, and absence is a class nobody has decided about yet. The registry
   * is built so those cannot be confused (operator/contracts/output-classes.yaml).
   */
  readonly output_classes: Record<
    string,
    { readonly voice_spec: string | null; readonly format_spec: string | null }
  >
}

/**
 * Scaffold directories, excluded from the snapshot. They are provisioning
 * templates rather than seats: nothing boots from them, so an additive-gate
 * assertion over them would be asserting against a form, not a deployment.
 */
const isScaffold = (dirName: string): boolean => dirName.startsWith('_')

function seatDirs(): string[] {
  return readdirSync(resolve(CUSTOMERS_DIR), { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .map((d) => d.name)
    .sort()
}

function readSeat(slug: string): SeatCoverage {
  const yamlPath = resolve(CUSTOMERS_DIR, slug, 'customer.yaml')
  const doc = parseYaml(readFileSync(yamlPath, 'utf8')) as Record<string, unknown>

  const lib = doc.voice_library
  const voice_library_nonempty =
    typeof lib === 'object' && lib !== null && !Array.isArray(lib) && Object.keys(lib).length > 0

  const declared = doc.output_classes
  const output_classes: SeatCoverage['output_classes'] = {}
  if (typeof declared === 'object' && declared !== null && !Array.isArray(declared)) {
    for (const className of Object.keys(declared).sort()) {
      const props = (declared as Record<string, unknown>)[className]
      const asRecord =
        typeof props === 'object' && props !== null && !Array.isArray(props)
          ? (props as Record<string, unknown>)
          : {}
      const asStringOrNull = (v: unknown): string | null => (typeof v === 'string' ? v : null)
      output_classes[className] = {
        voice_spec: asStringOrNull(asRecord.voice_spec),
        format_spec: asStringOrNull(asRecord.format_spec),
      }
    }
  }

  return { voice_library_nonempty, output_classes }
}

/** Regenerate the whole `seats` object from the real customer.yaml files. */
function regenerateSeats(): Record<string, SeatCoverage> {
  const out: Record<string, SeatCoverage> = {}
  for (const slug of seatDirs()) {
    if (isScaffold(slug)) continue
    out[slug] = readSeat(slug)
  }
  return out
}

/** Stable, formatting-independent serialization: keys sorted recursively,
 *  compact separators. Matches Python's json.dumps(sort_keys, separators). */
function stableStringify(v: unknown): string {
  if (Array.isArray(v)) return '[' + v.map(stableStringify).join(',') + ']'
  if (v !== null && typeof v === 'object') {
    const entries = Object.keys(v as Record<string, unknown>)
      .sort()
      .map((k) => JSON.stringify(k) + ':' + stableStringify((v as Record<string, unknown>)[k]))
    return '{' + entries.join(',') + '}'
  }
  return JSON.stringify(v)
}

const seatsSha256 = (seats: Record<string, SeatCoverage>): string =>
  createHash('sha256').update(stableStringify(seats), 'utf8').digest('hex')

const SNAPSHOT_NOTE =
  'Gate-relevant customer.yaml coverage, regenerated by tests/gate-coverage-snapshot.test.ts ' +
  'and consumed by the overlay additive-gate assertion. voice_library_nonempty describes the ' +
  'customer.yaml BLOCK, not the R2 vault contents. A null voice_spec/format_spec means the ' +
  'class does not declare that property; the string "none" means it declares that no spec is ' +
  'expected. Do not hand-edit: regenerate with UPDATE_GATE_COVERAGE_SNAPSHOT=1. The sibling ' +
  'gate-coverage-snapshot.overlay.json is the SAME data in the overlay fixture format; when this ' +
  'file changes, copy that one into hermes-smd-overlay tests/contract/' +
  'seat_gate_binding_snapshot.json in the same change as the seat config edit.'

function renderSnapshot(seats: Record<string, SeatCoverage>): string {
  return JSON.stringify({ note: SNAPSHOT_NOTE, seats }, null, 2) + '\n'
}

// ---------------------------------------------------------------------------
// The overlay's own fixture, emitted by the SAME generator.
// ---------------------------------------------------------------------------

/**
 * The overlay's merged fixture (tests/contract/seat_gate_binding_snapshot.json)
 * says "REGENERATION IS CONSOLE-SIDE" — and until now nothing console-side
 * regenerated it, so it would have gone stale on the next seat-config change
 * with both repos' tests still green. Emitting it from this generator is what
 * makes that sentence true.
 *
 * IT IS A DIFFERENT SHAPE, NOT A RENAMED ONE, and the difference is narrower
 * than it looks: the overlay fixture is VOICE-gate-specific. It carries
 * `voice_spec` and nothing else — the merged file omits `format_spec` even for
 * smd-staging, which declares `format_spec: expected`. Its consumer
 * (test_voice_gate_binding_coverage.py) reads exactly `voice_library_authored`
 * and `output_classes.<cls>.voice_spec`. So this is a projection of our snapshot
 * onto the voice axis, not a reformatting of it, and the two files are both kept
 * because ours is the full record and theirs is the consumed slice.
 */
const OVERLAY_SNAPSHOT_PATH = 'operator/contracts/gate-coverage-snapshot.overlay.json'
const OVERLAY_FIXTURE_PATH = 'tests/contract/seat_gate_binding_snapshot.json'

/** Byte-identical to the merged overlay fixture's `_comment`, plus the one
 *  sentence that tells the next person how the file gets there. */
const OVERLAY_COMMENT =
  "Snapshot of every REAL seat's voice-gate-relevant config fields, generated from ss-console " +
  'operator/customers/*/customer.yaml (underscore-prefixed template dirs excluded). Consumed by ' +
  'tests/test_voice_gate_binding_coverage.py to assert the ss#2086 per-class gate repoint is ' +
  'ADDITIVE: no (seat x class) loses its downgrade relative to the pre-repoint voice_library ' +
  "binding. REGENERATION IS CONSOLE-SIDE: ss-console's drift test (ss#2086 plan C3, following " +
  'the validator_parity_fixtures precedent) regenerates this file when seat configs change; do ' +
  'not hand-edit. The generator emits it at ss-console ' +
  "operator/contracts/gate-coverage-snapshot.overlay.json; when ss-console's drift test fires, " +
  'copy that file over this one in the same change that edits the seat configs.'

/**
 * Provenance only, and deliberately NOT part of the drift comparison — see the
 * drift test for why a commit-stamped field cannot be.
 *
 * `origin/main` rather than `HEAD`, because this string is read in the OTHER
 * repo: this venture squash-merges, so a feature-branch sha is a dead reference
 * the moment the PR lands, and someone dating a stale fixture would find
 * nothing. origin/main names a commit that still exists. (It is also what the
 * merged overlay fixture already carries.) HEAD is the fallback for a checkout
 * with no origin/main ref.
 */
function consoleSha(): string {
  for (const rev of ['origin/main', 'HEAD']) {
    try {
      return execFileSync('git', ['rev-parse', rev], { encoding: 'utf8', stdio: 'pipe' }).trim()
    } catch {
      continue
    }
  }
  return 'unknown'
}

const GENERATED_FROM_KEY = 'generated_from'
const GENERATED_FROM_PATTERN = /^venturecrane\/ss-console@[0-9a-f]{40}$/

function renderOverlaySnapshot(
  seats: Record<string, SeatCoverage>,
  excluded: string[],
  generatedFrom: string
): string {
  const overlaySeats: Record<string, unknown> = {}
  for (const slug of Object.keys(seats).sort()) {
    const seat = seats[slug]
    const classes: Record<string, Record<string, string>> = {}
    for (const className of Object.keys(seat.output_classes).sort()) {
      const declared: Record<string, string> = {}
      const voiceSpec = seat.output_classes[className].voice_spec
      // Omitted, not nulled: the consumer does decl.get("voice_spec", ""), and
      // an explicit null would read as a declaration of nothing rather than as
      // the absence of one.
      if (voiceSpec !== null) declared.voice_spec = voiceSpec
      classes[className] = declared
    }
    overlaySeats[slug] = {
      voice_library_authored: seat.voice_library_nonempty,
      output_classes: classes,
    }
  }
  // Key order is load-bearing: the emitted file is copied verbatim into the
  // overlay, and a reordered object would show up as a whole-file diff there.
  return (
    JSON.stringify(
      {
        _comment: OVERLAY_COMMENT,
        [GENERATED_FROM_KEY]: generatedFrom,
        excluded_template_dirs: excluded,
        seats: overlaySeats,
      },
      null,
      2
    ) + '\n'
  )
}

/** Replace the provenance line so two renderings compare on CONTENT. */
function normalizeGeneratedFrom(json: string): string {
  return json.replace(
    new RegExp(`("${GENERATED_FROM_KEY}": )"[^"]*"`),
    '$1"<provenance-normalized>"'
  )
}

const LIVE_SEATS = regenerateSeats()
const EXCLUDED_DIRS = seatDirs().filter(isScaffold)

describe('gate-coverage snapshot', () => {
  it('excludes scaffold directories, and there are some to exclude', () => {
    // Asserted separately, and in both directions, because an exclusion rule
    // that filters nothing is indistinguishable from no rule at all. If
    // `_template` were ever renamed, the filter would go quietly inert and this
    // test is what notices.
    const all = seatDirs()
    const scaffolds = all.filter(isScaffold)
    expect(
      scaffolds,
      'the underscore-prefixed provisioning scaffolds must still exist, or the exclusion this ' +
        'snapshot depends on is filtering nothing'
    ).not.toEqual([])
    expect(
      scaffolds.filter((s) => s in LIVE_SEATS),
      'a scaffold reached the snapshot; it is a provisioning form, not a deployed seat'
    ).toEqual([])
    expect(
      all.filter((d) => !isScaffold(d) && !(d in LIVE_SEATS)),
      'every non-scaffold customer dir must appear in the snapshot'
    ).toEqual([])
  })

  it('reads a customer.yaml from every seat it lists', () => {
    // Guards the silent-empty case: a glob that matches nothing produces an
    // empty snapshot that agrees with an empty checked-in file forever.
    expect(Object.keys(LIVE_SEATS).length).toBeGreaterThan(0)
    for (const slug of Object.keys(LIVE_SEATS)) {
      expect(
        existsSync(resolve(CUSTOMERS_DIR, slug, 'customer.yaml')),
        `${slug} customer.yaml`
      ).toBe(true)
    }
  })

  it('matches the checked-in snapshot', () => {
    const rendered = renderSnapshot(LIVE_SEATS)
    const snapshotFile = resolve(SNAPSHOT_PATH)

    if (process.env.UPDATE_GATE_COVERAGE_SNAPSHOT === '1') {
      writeFileSync(snapshotFile, rendered, 'utf8')
      console.warn(
        `[gate-coverage] rewrote ${SNAPSHOT_PATH}. Seats hash is now ${seatsSha256(LIVE_SEATS)} — ` +
          'update PINNED_SEATS_SHA256 here AND the overlay constant.'
      )
      return
    }

    expect(existsSync(snapshotFile), `${SNAPSHOT_PATH} is missing`).toBe(true)
    expect(
      readFileSync(snapshotFile, 'utf8'),
      `${SNAPSHOT_PATH} is stale. A gate-relevant customer.yaml field changed. Regenerate with ` +
        '`UPDATE_GATE_COVERAGE_SNAPSHOT=1 npx vitest run tests/gate-coverage-snapshot.test.ts`, ' +
        'then update PINNED_SEATS_SHA256 below AND the matching constant in the overlay, so the ' +
        'additive-gate assertion is re-decided rather than silently re-based.'
    ).toBe(rendered)
  })

  it('matches the checked-in overlay fixture, ignoring provenance', () => {
    // ONE generator, two artifacts. The comparison normalizes `generated_from`
    // because a HEAD-stamped field compared byte-for-byte fails on the very next
    // commit, and a gate everyone learns to regenerate past has stopped gating.
    // Its well-formedness is asserted separately below; the overlay's consumer
    // never reads it.
    const rendered = renderOverlaySnapshot(
      LIVE_SEATS,
      EXCLUDED_DIRS,
      `venturecrane/ss-console@${consoleSha()}`
    )
    const overlayFile = resolve(OVERLAY_SNAPSHOT_PATH)

    if (process.env.UPDATE_GATE_COVERAGE_SNAPSHOT === '1') {
      writeFileSync(overlayFile, rendered, 'utf8')
      console.warn(
        `[gate-coverage] rewrote ${OVERLAY_SNAPSHOT_PATH}. Copy it over hermes-smd-overlay ` +
          `${OVERLAY_FIXTURE_PATH} in the same change as the seat config edit.`
      )
      return
    }

    expect(existsSync(overlayFile), `${OVERLAY_SNAPSHOT_PATH} is missing`).toBe(true)
    expect(
      normalizeGeneratedFrom(readFileSync(overlayFile, 'utf8')),
      `${OVERLAY_SNAPSHOT_PATH} is stale. Regenerate with ` +
        '`UPDATE_GATE_COVERAGE_SNAPSHOT=1 npx vitest run tests/gate-coverage-snapshot.test.ts`, ' +
        `then COPY it over hermes-smd-overlay ${OVERLAY_FIXTURE_PATH} in the same change that ` +
        'edits the seat configs. The overlay cannot regenerate it — its own header says ' +
        'regeneration is console-side, and this generator is what makes that true.'
    ).toBe(normalizeGeneratedFrom(rendered))
  })

  it('emits the overlay fixture in the shape its consumer reads', () => {
    // Structural, not textual, so a future field addition on our side cannot
    // silently reach the overlay in a shape test_voice_gate_binding_coverage.py
    // does not parse. It reads exactly voice_library_authored and
    // output_classes.<cls>.voice_spec.
    const parsed = JSON.parse(readFileSync(resolve(OVERLAY_SNAPSHOT_PATH), 'utf8')) as {
      _comment: string
      generated_from: string
      excluded_template_dirs: string[]
      seats: Record<string, { voice_library_authored: boolean; output_classes: object }>
    }

    expect(Object.keys(parsed)).toEqual([
      '_comment',
      'generated_from',
      'excluded_template_dirs',
      'seats',
    ])
    expect(
      parsed.generated_from,
      'provenance must name a real console commit, so a stale fixture can be dated'
    ).toMatch(GENERATED_FROM_PATTERN)
    expect(parsed.excluded_template_dirs).toEqual(EXCLUDED_DIRS)
    expect(parsed.excluded_template_dirs.length).toBeGreaterThan(0)

    for (const [slug, seat] of Object.entries(parsed.seats)) {
      expect(Object.keys(seat), `${slug} keys`).toEqual([
        'voice_library_authored',
        'output_classes',
      ])
      expect(typeof seat.voice_library_authored, `${slug} voice_library_authored`).toBe('boolean')
      for (const [cls, decl] of Object.entries(seat.output_classes)) {
        // The merged overlay fixture carries NO format_spec anywhere, including
        // for smd-staging, which declares `format_spec: expected`. That file is
        // the voice axis only. Leaking format_spec into it would be a shape the
        // consumer never asked for and a diff nobody authored.
        expect(
          Object.keys(decl as object),
          `${slug}.${cls} declares only voice_spec`
        ).not.toContain('format_spec')
      }
    }
  })

  it('pins the seats hash the overlay mirrors', () => {
    // The cross-repo half. Regeneration deliberately does not touch this
    // constant: a snapshot change that nobody carried to the overlay should
    // fail here, in this repo, with the new value printed.
    expect(
      seatsSha256(LIVE_SEATS),
      'Seat coverage changed. Update this constant AND the overlay additive-gate test, together.'
    ).toBe(PINNED_SEATS_SHA256)
  })
})
