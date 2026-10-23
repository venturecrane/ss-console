/**
 * Adapter ↔ overlay paired-file drift gate (2026-06-12 code review;
 * runtime-side coverage added 2026-06-16, SEC-32; both sides hashed in place
 * since the 2026-10-22 fold, ADR 0095).
 *
 * Several modules exist as deliberate ported pairs: the console copy
 * (`operator/adapter/*`, `operator/workspace_broker/*`) is the control-plane
 * substrate consumed by `operator/bin` lifecycle tooling, and the overlay copy
 * (`operator/hermes-smd-overlay/{plugins,shared}/*`) is the runtime that ships on
 * customer Machines. Nothing previously enforced that an edit to one side was
 * made with the other in mind — the pairs were already diverging (SentItem vs
 * SentMessage, executor contracts) with no signal.
 *
 * The manifest (`operator/contracts/overlay-pairs.json`) pins, per pair:
 *   - `sha256`        : the console-side file's hash.
 *   - `overlaySha256` : the overlay runtime file's hash.
 *
 * Until the fold the overlay half could only be checked by a network step in
 * `operator-substrate` that fetched the overlay repo at a pinned commit, because
 * this suite is hermetic and the runtime lived in another repository. The runtime
 * is in this tree now, so both halves are plain file reads here and the pin
 * fields are gone. Editing either side without updating its manifest hash fails
 * CI. Updating a hash is the conscious act: record in `syncNote` whether the
 * change is paired or deliberately one-sided.
 */

import { describe, it, expect } from 'vitest'
import { createHash } from 'crypto'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

interface PairEntry {
  adapterPath: string
  overlayPath: string
  sha256: string
  overlaySha256: string
  syncNote: string
}

interface Manifest {
  pairs: PairEntry[]
}

const MANIFEST_PATH = resolve('operator/contracts/overlay-pairs.json')
const OVERLAY_ROOT = resolve('operator/hermes-smd-overlay')

function loadManifest(): Manifest {
  const raw: unknown = JSON.parse(readFileSync(MANIFEST_PATH, 'utf-8'))
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new Error('overlay-pairs.json must be an object { pairs[] }')
  }
  const m = raw as Manifest
  if (!Array.isArray(m.pairs)) throw new Error('overlay-pairs.json: `pairs` must be an array')
  return m
}

function sha256(path: string): string {
  return createHash('sha256').update(readFileSync(path)).digest('hex')
}

describe('adapter ↔ overlay paired-file drift gate', () => {
  const manifest = loadManifest()

  it('manifest is well-formed (non-empty pairs, both hashes, a note)', () => {
    expect(manifest.pairs.length).toBeGreaterThan(0)
    for (const entry of manifest.pairs) {
      expect(entry.adapterPath, 'adapterPath required').toBeTruthy()
      expect(entry.overlayPath, 'overlayPath required').toBeTruthy()
      expect(entry.sha256, `sha256 malformed for ${entry.adapterPath}`).toMatch(/^[0-9a-f]{64}$/)
      expect(entry.overlaySha256, `overlaySha256 malformed for ${entry.overlayPath}`).toMatch(
        /^[0-9a-f]{64}$/
      )
      expect(entry.syncNote, `syncNote required for ${entry.adapterPath}`).toBeTruthy()
    }
  })

  it('every manifest entry points at an existing file on both sides', () => {
    for (const entry of manifest.pairs) {
      expect(existsSync(resolve(entry.adapterPath)), `${entry.adapterPath} missing`).toBe(true)
      expect(
        existsSync(resolve(OVERLAY_ROOT, entry.overlayPath)),
        `operator/hermes-smd-overlay/${entry.overlayPath} missing`
      ).toBe(true)
    }
  })

  for (const entry of loadManifest().pairs) {
    it(`${entry.adapterPath} matches its recorded hash`, () => {
      const actual = sha256(resolve(entry.adapterPath))
      expect(
        actual,
        `${entry.adapterPath} changed without a manifest update.\n` +
          `Its runtime twin is operator/hermes-smd-overlay/${entry.overlayPath}.\n` +
          `Decide whether this change needs the twin changed too, then update the\n` +
          `sha256 to ${actual} and record the decision in syncNote\n` +
          `(operator/contracts/overlay-pairs.json).`
      ).toBe(entry.sha256)
    })

    it(`operator/hermes-smd-overlay/${entry.overlayPath} matches its recorded hash`, () => {
      const actual = sha256(resolve(OVERLAY_ROOT, entry.overlayPath))
      expect(
        actual,
        `operator/hermes-smd-overlay/${entry.overlayPath} changed without a manifest update.\n` +
          `Its console twin is ${entry.adapterPath}.\n` +
          `Decide whether this change needs the twin changed too, then update the\n` +
          `overlaySha256 to ${actual} and record the decision in syncNote\n` +
          `(operator/contracts/overlay-pairs.json).`
      ).toBe(entry.overlaySha256)
    })
  }
})
