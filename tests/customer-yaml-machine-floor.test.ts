/**
 * No seat is authored on one shared CPU.
 *
 * On 2026-11-09 captain-mark (shared-cpu-1x, copied from the couples preset)
 * sat at 84% CPU steal after a routine release; its gateway could not finish
 * startup inside hermes' 3-minute loop watchdog and was killed twice (exit 75)
 * while the couple's assistant went unanswered. The memory floor added after
 * the 2026-09-01 pilot loop never covered CPU, and every preset defaulted to
 * the smallest size. These pin the CPU floor in the validator and on every
 * seat, preset and template a new seat is made from.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, existsSync } from 'node:fs'
import { join } from 'node:path'
import { parse as parseYaml } from 'yaml'
import {
  seatSizeCpus,
  MIN_SEAT_SHARED_CPUS,
} from '../src/lib/operator/customer-yaml/sections-identity'
import { validate } from '../src/lib/operator/customer-yaml'

const ROOT = join(__dirname, '..')
const CUSTOMERS = join(ROOT, 'operator', 'customers')
const PRESETS = join(CUSTOMERS, '_template', 'presets')

function sizeOf(path: string): string | undefined {
  const doc = parseYaml(readFileSync(path, 'utf8')) as { machine?: { size?: string } }
  return doc?.machine?.size
}

const authored = [
  ...readdirSync(CUSTOMERS)
    .map((slug) => join(CUSTOMERS, slug, 'customer.yaml'))
    .filter((p) => existsSync(p)),
  ...readdirSync(PRESETS)
    .map((p) => join(PRESETS, p, 'customer.yaml'))
    .filter((p) => existsSync(p)),
  join(ROOT, 'operator', 'templates', 'customer-no-pm-system.yaml'),
]

describe('seat CPU floor', () => {
  it('reads a Fly size name', () => {
    expect(seatSizeCpus('shared-cpu-1x')).toEqual({ kind: 'shared', cpus: 1 })
    expect(seatSizeCpus('performance-cpu-1x')).toEqual({ kind: 'performance', cpus: 1 })
    expect(seatSizeCpus('nonsense')).toBeNull()
  })

  it.each(authored.map((p) => [p.slice(ROOT.length + 1), p]))(
    '%s is authored at two shared CPUs or more',
    (_label, path) => {
      const size = sizeOf(path)
      if (size === undefined) return // pilot-style files without a machine block are covered by the validator
      const parsed = seatSizeCpus(size)
      expect(parsed, `${size} is not a Fly size`).not.toBeNull()
      if (parsed!.kind === 'shared')
        expect(parsed!.cpus).toBeGreaterThanOrEqual(MIN_SEAT_SHARED_CPUS)
    }
  )

  it('the validator refuses a one-CPU seat and accepts two', () => {
    const doc = parseYaml(
      readFileSync(join(CUSTOMERS, 'captain-mark', 'customer.yaml'), 'utf8')
    ) as Record<string, unknown> & { machine: { size: string } }
    const sizeErrors = (size: string) => {
      const r = validate({ ...doc, machine: { ...doc.machine, size } })
      return r.ok ? [] : r.errors.filter((e) => e.path === 'machine.size')
    }
    expect(sizeErrors('shared-cpu-1x')).toHaveLength(1)
    expect(sizeErrors('shared-cpu-2x')).toHaveLength(0)
    expect(sizeErrors('performance-cpu-1x')).toHaveLength(0)
  })
})
