/**
 * Guard: the per-customer Machine fly.toml template must set every env var the
 * SMD overlay plugins REQUIRE at register.
 *
 * The overlay plugins (audit, voice, webhook-router, trust, memory-mirror) call
 * `require("SMD_CUSTOMER_SLUG", "SMD_D1_AUDIT_BINDING")` at register and bail to
 * a degraded no-op branch if either is missing. `SMD_CUSTOMER_SLUG` was once
 * omitted from the template (only `CUSTOMER_SLUG` was set), which silently
 * disabled the entire SMD plugin suite on customer-zero — audit emission never
 * wrote, so the ADR 0043 runtime-read seam read a dry well (ss-console#1285).
 * This test fails loudly if a required SMD_* var is dropped from the template.
 */

import { describe, it, expect } from 'vitest'
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const template = readFileSync(resolve(__dirname, '../operator/templates/fly.toml.template'), 'utf8')

// Env vars the overlay plugins require() at register. Dropping any silently
// degrades the whole suite — keep this list in lockstep with the plugins'
// require(...) calls in venturecrane/hermes-smd-overlay.
const REQUIRED_PLUGIN_ENV = ['SMD_CUSTOMER_SLUG', 'SMD_D1_AUDIT_BINDING'] as const

describe('operator fly.toml template — required plugin env', () => {
  for (const key of REQUIRED_PLUGIN_ENV) {
    it(`declares ${key} in [env]`, () => {
      // A key=value assignment for this var must appear in the template.
      expect(template).toMatch(new RegExp(`^\\s*${key}\\s*=`, 'm'))
    })
  }

  it('binds SMD_CUSTOMER_SLUG to the customer slug placeholder', () => {
    expect(template).toMatch(/SMD_CUSTOMER_SLUG\s*=\s*"\{\{CUSTOMER_SLUG\}\}"/)
  })
})

// ss#2612: the [[vm]] block once hardcoded `cpus = 1` while rendering size and
// memory from customer.yaml, so an authored shared-cpu-2x ran on one vCPU. The
// cpu fields are placeholders now, and their values come from the same shell
// function provisioning sources (operator/bin/lib/machine-size.sh), exercised
// here for both seat sizes in use.
const MACHINE_SIZE_LIB = resolve(__dirname, '../operator/bin/lib/machine-size.sh')
function derive(fn: 'machine_cpus' | 'machine_cpu_kind', size: string): string {
  return execFileSync('bash', ['-c', `source "${MACHINE_SIZE_LIB}" && ${fn} "${size}"`], {
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'ignore'],
  }).trim()
}

describe('operator fly.toml template — [[vm]] renders cpus from machine.size (ss#2612)', () => {
  it('renders cpu_kind and cpus from placeholders, never a literal', () => {
    expect(template).toMatch(/^\s*cpu_kind\s*=\s*"\{\{MACHINE_CPU_KIND\}\}"/m)
    expect(template).toMatch(/^\s*cpus\s*=\s*\{\{MACHINE_CPUS\}\}/m)
    expect(template).not.toMatch(/^\s*cpus\s*=\s*\d/m)
  })

  it('provision-customer.sh substitutes both placeholders', () => {
    const provision = readFileSync(
      resolve(__dirname, '../operator/bin/provision-customer.sh'),
      'utf8'
    )
    expect(provision).toContain('{{MACHINE_CPU_KIND}}')
    expect(provision).toContain('{{MACHINE_CPUS}}')
    expect(provision).toContain('lib/machine-size.sh')
  })

  it.each([
    ['shared-cpu-1x', 'shared', '1'],
    ['shared-cpu-2x', 'shared', '2'],
    ['shared-cpu-4x', 'shared', '4'],
    ['shared-cpu-6x', 'shared', '6'],
    ['performance-2x', 'performance', '2'],
  ])('derives %s -> %s x %s', (size, kind, cpus) => {
    expect(derive('machine_cpu_kind', size)).toBe(kind)
    expect(derive('machine_cpus', size)).toBe(cpus)
  })

  it('refuses a size it cannot render instead of falling back to one vCPU', () => {
    expect(() => derive('machine_cpus', 'shared-cpu-3x')).toThrow()
    expect(() => derive('machine_cpus', 'medium')).toThrow()
  })

  it('the A&P seat authors the runner-capable size (ADR 0087)', () => {
    const ap = readFileSync(
      resolve(__dirname, '../operator/customers/ashton-price/customer.yaml'),
      'utf8'
    )
    expect(ap).toMatch(/^machine:\n\s+size: shared-cpu-2x\n\s+memory_mb: 4096/m)
  })
})
