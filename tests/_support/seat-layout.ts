/**
 * Read an Operator shell script the way the seat runs it: every
 * `${SMD_LAYOUT_<ID>}` replaced by the value operator/templates/_seat-layout.generated.sh
 * assigns it (generated from operator/contracts/seat-layout.yaml).
 *
 * The scripts name seat paths through the generated layout. Tests that assert
 * on a script's text (a command, a mode, a path) assert on the expanded text, so
 * they keep saying what the seat executes, and a wrong layout value still fails
 * them. An id the generated file does not assign throws: under `set -u` that
 * name would kill the boot.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const GENERATED = resolve('operator/templates/_seat-layout.generated.sh')

export function seatLayoutValues(): Record<string, string> {
  const text = readFileSync(GENERATED, 'utf8')
  return Object.fromEntries(
    [...text.matchAll(/^SMD_LAYOUT_([A-Z0-9_]+)='([^']*)'$/gm)].map((m) => [m[1], m[2]])
  )
}

export function expandSeatLayout(text: string): string {
  const values = seatLayoutValues()
  return text.replace(/\$\{SMD_LAYOUT_([A-Z0-9_]+)\}/g, (_all, id: string) => {
    const value = values[id]
    if (value === undefined) throw new Error(`SMD_LAYOUT_${id} is not assigned in ${GENERATED}`)
    return value
  })
}

/** readFileSync(path) with the seat layout expanded. */
export function readSeatScript(path: string): string {
  return expandSeatLayout(readFileSync(resolve(path), 'utf8'))
}
