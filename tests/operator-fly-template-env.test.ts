/**
 * The per-customer Machine fly.toml, rendered for real by the one renderer
 * (operator/bin/lib/render-fly-toml.sh, sourced the way provision-customer.sh
 * sources it) and parsed as TOML.
 *
 * The overlay plugins (audit, voice, webhook-router, trust, memory-mirror) call
 * `require("SMD_CUSTOMER_SLUG", "SMD_D1_AUDIT_BINDING")` at register and bail to
 * a degraded no-op branch if either is missing. `SMD_CUSTOMER_SLUG` was once
 * omitted from the template (only `CUSTOMER_SLUG` was set), which silently
 * disabled the entire SMD plugin suite on customer-zero (ss-console#1285).
 *
 * ss#2612: the [[vm]] block once hardcoded `cpus = 1` while rendering size and
 * memory from customer.yaml, so an authored shared-cpu-2x ran on one vCPU. The
 * cpu fields come from operator/bin/lib/machine-size.sh, exercised here for
 * every seat size in use and through the render.
 */

import { describe, it, expect } from 'vitest'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { parse as parseToml } from 'smol-toml'
import { parse as parseYaml } from 'yaml'

// Env vars the overlay plugins require() at register. Dropping any silently
// degrades the whole suite.
const REQUIRED_PLUGIN_ENV = ['SMD_CUSTOMER_SLUG', 'SMD_D1_AUDIT_BINDING'] as const

const MACHINE_SIZE_LIB = resolve(__dirname, '../operator/bin/lib/machine-size.sh')
const RENDER_LIB = resolve(__dirname, '../operator/bin/lib/render-fly-toml.sh')
const TEMPLATE_DIR = resolve(__dirname, '../operator/templates')

function derive(fn: 'machine_cpus' | 'machine_cpu_kind', size: string): string {
  return execFileSync('bash', ['-c', `source "${MACHINE_SIZE_LIB}" && ${fn} "${size}"`], {
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'ignore'],
  }).trim()
}

interface FlyToml {
  kill_signal?: string
  kill_timeout?: number
  build?: Record<string, unknown>
  env: Record<string, string>
  vm: Array<{ size: string; memory: string; cpu_kind: string; cpus: number }>
}

/** Render the template the way provisioning does, then parse it. */
function render(slug: string, size: string): FlyToml {
  const out = join(mkdtempSync(join(tmpdir(), 'smd-flytoml-')), 'fly.toml')
  execFileSync(
    'bash',
    [
      '-c',
      'set -euo pipefail; source "$1"; source "$2"; ' +
        'MACHINE_CPU_KIND="$(machine_cpu_kind "$MACHINE_SIZE")"; ' +
        'MACHINE_CPUS="$(machine_cpus "$MACHINE_SIZE")"; render_fly_toml "$3"',
      'render',
      MACHINE_SIZE_LIB,
      RENDER_LIB,
      out,
    ],
    {
      encoding: 'utf8',
      env: {
        ...process.env,
        SLUG: slug,
        FLY_REGION: 'phx',
        MACHINE_SIZE: size,
        MEMORY_MB: '4096',
        R2_BUCKET_CONFIG: 'smd-customer-config',
        TEMPLATE_DIR,
      },
    }
  )
  return parseToml(readFileSync(out, 'utf8')) as unknown as FlyToml
}

describe('operator fly.toml: required plugin env', () => {
  const toml = render('acme-law', 'shared-cpu-2x')

  for (const key of REQUIRED_PLUGIN_ENV) {
    it(`renders ${key} in [env]`, () => {
      expect(toml.env[key], key).toBeTruthy()
    })
  }

  it('binds SMD_CUSTOMER_SLUG to the seat slug', () => {
    expect(toml.env.SMD_CUSTOMER_SLUG).toBe('acme-law')
  })
})

describe('operator fly.toml: [[vm]] renders cpus from machine.size (ss#2612)', () => {
  it.each([
    ['shared-cpu-2x', 'shared', 2],
    ['shared-cpu-4x', 'shared', 4],
    ['performance-2x', 'performance', 2],
  ])('a %s seat renders cpu_kind %s and %s cpus', (size, kind, cpus) => {
    const [vm] = render('acme-law', size).vm
    expect(vm.size).toBe(size)
    expect(vm.cpu_kind).toBe(kind)
    expect(vm.cpus).toBe(cpus)
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
})

describe('operator fly.toml: one image for every seat (fleet release, 2026-10-19)', () => {
  const toml = render('acme-law', 'shared-cpu-2x')

  it('gives the supervisor its stop allowance: SIGTERM and 300 s', () => {
    // Fly's default (SIGINT, 5 s) killed every running job on every deploy.
    expect(toml.kill_signal).toBe('SIGTERM')
    expect(toml.kill_timeout).toBe(300)
  })

  it('passes no per-seat build args (the slug and the Hermes pin never reach the image)', () => {
    // [build] stays only for the --build fallback.
    expect(toml.build).toBeDefined()
    expect(toml.build?.args).toBeUndefined()
    expect(Object.keys(toml.env).filter((k) => /^HERMES_(REF|UPSTREAM_)/.test(k))).toEqual([])
  })

  it('the A&P seat authors the runner-capable size (ADR 0087)', () => {
    const ap = parseYaml(
      readFileSync(resolve(__dirname, '../operator/customers/ashton-price/customer.yaml'), 'utf8')
    ) as { machine: { size: string; memory_mb: number } }
    expect(ap.machine).toMatchObject({ size: 'shared-cpu-4x', memory_mb: 8192 })
  })
})
