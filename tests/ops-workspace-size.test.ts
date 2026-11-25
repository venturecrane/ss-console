/**
 * A client ops workspace runs at the size authored for it in
 * operator/ops-machine/workspaces.json, and only there.
 *
 * On 2026-11-24 the A&P workspace (authored performance-8x, 16 GB) ran at 2
 * shared CPUs and 4 GB all afternoon under hand drafting. `ops-workspace update`
 * had passed the authored size to `fly deploy`, but flyctl gives --vm-size to a
 * new Machine only, and fly.toml's [[vm]] (smd-ops's size) set the existing one.
 * bin/ops-workspace-size is now the one reader of the size: `want` refuses a
 * workspace without one, `check` compares each Machine's guest with it, and
 * `fix` resizes a Machine that differs. bin/ops-workspace refuses to deploy
 * without a size.
 *
 * These tests run the real scripts with `fly` (and `infisical`) replaced by
 * PATH shims that print canned `fly machine list --json` output and log every
 * call, against a temporary workspaces.json (OPS_WORKSPACES_JSON).
 */
import { execFileSync, spawnSync } from 'node:child_process'
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterEach, describe, expect, it } from 'vitest'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const BIN = join(REPO_ROOT, 'operator', 'ops-machine', 'bin')
const SIZE = join(BIN, 'ops-workspace-size')
const WORKSPACE = join(BIN, 'ops-workspace')

const dirs: string[] = []
afterEach(() => {
  for (const d of dirs.splice(0)) rmSync(d, { recursive: true, force: true })
})

interface Guest {
  id: string
  state: string
  cpu_kind: string
  cpus: number
  memory_mb: number
}

const machine = (g: Guest) => ({
  id: g.id,
  state: g.state,
  config: { guest: { cpu_kind: g.cpu_kind, cpus: g.cpus, memory_mb: g.memory_mb } },
})

const AUTHORED = {
  'ashton-price': {
    app: 'ops-ashton-price',
    seat_app: 'hermes-ashton-price',
    region: 'sjc',
    volume_gb: 150,
    vm_size: 'performance-8x',
    memory_mb: 16384,
  },
}

interface Setup {
  workspaces?: Record<string, Record<string, unknown>>
  machines?: Guest[] | 'error'
  /** What a `fly machine update` makes the Machine report afterwards. */
  afterUpdate?: Guest[]
}

/**
 * The fake fly: `machine list` prints machines.json (or fails), `machine update`
 * logs and swaps in after-update.json when there is one. Every call goes to
 * calls.log, with whether FLY_API_TOKEN was set (never its value).
 */
function setup(s: Setup) {
  const root = mkdtempSync(join(tmpdir(), 'ops-ws-size-'))
  dirs.push(root)
  const shims = join(root, 'shims')
  mkdirSync(shims)
  const reg = join(root, 'workspaces.json')
  writeFileSync(reg, JSON.stringify({ workspaces: s.workspaces ?? AUTHORED }))
  if (s.machines === 'error') writeFileSync(join(root, 'list-fails'), '')
  else writeFileSync(join(root, 'machines.json'), JSON.stringify((s.machines ?? []).map(machine)))
  if (s.afterUpdate)
    writeFileSync(join(root, 'after-update.json'), JSON.stringify(s.afterUpdate.map(machine)))
  writeFileSync(
    join(shims, 'fly'),
    `#!/bin/bash
R=${JSON.stringify(root)}
echo "fly $* token=\${FLY_API_TOKEN:+set}" >> "$R/calls.log"
if [ "$1 $2" = "machine list" ]; then
  [ -f "$R/list-fails" ] && { echo "Error: unauthorized" >&2; exit 1; }
  cat "$R/machines.json"; exit 0
fi
if [ "$1 $2" = "machine update" ]; then
  [ -f "$R/after-update.json" ] && cp "$R/after-update.json" "$R/machines.json"
  exit 0
fi
exit 0
`
  )
  chmodSync(join(shims, 'fly'), 0o755)
  writeFileSync(
    join(shims, 'infisical'),
    `#!/bin/bash\necho "infisical $*" >> ${JSON.stringify(join(root, 'calls.log'))}\nexit 0\n`
  )
  chmodSync(join(shims, 'infisical'), 0o755)
  const env: Record<string, string> = {}
  for (const [k, v] of Object.entries(process.env))
    if (
      v !== undefined &&
      !k.startsWith('GIT_') &&
      !k.startsWith('OPS_FLY_TOKEN__') &&
      k !== 'FLY_API_TOKEN'
    )
      env[k] = v
  env.PATH = `${shims}:${process.env.PATH ?? '/usr/bin:/bin'}`
  env.OPS_WORKSPACES_JSON = reg
  const run = (script: string, args: string[], extra: Record<string, string> = {}) => {
    const r = spawnSync('bash', [script, ...args], { env: { ...env, ...extra }, encoding: 'utf8' })
    return { rc: r.status ?? -1, out: r.stdout, err: r.stderr }
  }
  const calls = () =>
    existsSync(join(root, 'calls.log'))
      ? readFileSync(join(root, 'calls.log'), 'utf8').trim().split('\n')
      : []
  return { run, calls }
}

const AT_SIZE: Guest = {
  id: 'm1',
  state: 'started',
  cpu_kind: 'performance',
  cpus: 8,
  memory_mb: 16384,
}
// What the Machine ran on 2026-11-24 after release v8.
const TODAY: Guest = { id: 'm1', state: 'started', cpu_kind: 'shared', cpus: 2, memory_mb: 4096 }

describe('ops-workspace-size want', () => {
  it('reads the size and splits it into kind and CPU count', () => {
    const { run } = setup({})
    const r = run(SIZE, ['want', 'ashton-price'])
    expect(r.rc).toBe(0)
    expect(r.out.trim()).toBe('performance-8x 16384 performance 8')
  })

  it('refuses a workspace with no size: there is no default', () => {
    const { app, seat_app } = AUTHORED['ashton-price']
    const { run } = setup({ workspaces: { 'ashton-price': { app, seat_app } } })
    const r = run(SIZE, ['want', 'ashton-price'])
    expect(r.rc).toBe(2)
    expect(r.out).toBe('')
    expect(r.err).toContain('never sized by default')
  })

  it.each([
    [{ vm_size: 'a100-40gb', memory_mb: 16384 }, 'is not shared-cpu'],
    [{ vm_size: 'performance-8x', memory_mb: 1000 }, 'not a whole number of 256 MB'],
    [{ vm_size: 'shared-cpu-x', memory_mb: 4096 }, 'names no CPU count'],
  ])('refuses a size Fly cannot run as authored: %j', (size, said) => {
    const { run } = setup({ workspaces: { w: { app: 'ops-w', seat_app: 'hermes-w', ...size } } })
    const r = run(SIZE, ['want', 'w'])
    expect(r.rc).toBe(2)
    expect(r.err).toContain(said)
  })

  it('every workspace authored in the repo has a size it accepts', () => {
    const real = join(REPO_ROOT, 'operator', 'ops-machine', 'workspaces.json')
    const slugs = Object.keys(JSON.parse(readFileSync(real, 'utf8')).workspaces)
    expect(slugs.length).toBeGreaterThan(0)
    for (const slug of slugs) {
      const out = execFileSync('bash', [SIZE, 'want', slug], {
        env: { ...process.env, OPS_WORKSPACES_JSON: real },
        encoding: 'utf8',
      })
      expect(out).toMatch(/^(shared-cpu|performance)-\d+x \d+ (shared|performance) \d+\n$/)
    }
  })
})

describe('ops-workspace-size check', () => {
  it('passes a Machine at its authored size, with one read-only list call', () => {
    const { run, calls } = setup({ machines: [AT_SIZE] })
    const r = run(SIZE, ['check'])
    expect(r.rc).toBe(0)
    expect(r.out).toContain('PASS size ops-ashton-price m1 (started): performance-8x, 16384 MB')
    expect(calls()).toEqual(['fly machine list -a ops-ashton-price --json token='])
  })

  it('fails the 2026-11-24 Machine, naming what it runs, what is authored, and the fix', () => {
    const { run } = setup({ machines: [TODAY] })
    const r = run(SIZE, ['check', 'ashton-price'])
    expect(r.rc).toBe(1)
    expect(r.out).toContain(
      'FAIL size ops-ashton-price m1 (started): runs shared/2 CPU/4096 MB, authored performance-8x/16384 MB'
    )
    expect(r.out).toContain('ops-workspace resize ashton-price')
  })

  it.each([
    ['the CPU kind', { cpu_kind: 'shared' }],
    ['the CPU count', { cpus: 4 }],
    ['the memory', { memory_mb: 8192 }],
  ])('fails when only %s differs', (_what, diff) => {
    const { run } = setup({ machines: [{ ...AT_SIZE, ...diff }] })
    expect(run(SIZE, ['check']).rc).toBe(1)
  })

  it('checks a stopped Machine too: it starts at the size it holds', () => {
    const { run } = setup({ machines: [{ ...TODAY, state: 'stopped' }] })
    const r = run(SIZE, ['check'])
    expect(r.rc).toBe(1)
    expect(r.out).toContain('m1 (stopped)')
  })

  it('fails an app it cannot read, never reporting it as at size', () => {
    const { run } = setup({ machines: 'error' })
    const r = run(SIZE, ['check'])
    expect(r.rc).toBe(1)
    expect(r.out).toContain(
      'FAIL size ops-ashton-price: could not read the Machines of ops-ashton-price'
    )
    expect(r.out).toContain('unauthorized')
    expect(r.out).not.toContain('PASS')
  })

  it('fails an app with no Machine', () => {
    const { run } = setup({ machines: [] })
    const r = run(SIZE, ['check'])
    expect(r.rc).toBe(1)
    expect(r.out).toContain('FAIL size ops-ashton-price: no Machine')
  })

  it('fails a workspace with no size instead of checking it against a default', () => {
    const { app, seat_app } = AUTHORED['ashton-price']
    const { run, calls } = setup({
      workspaces: { 'ashton-price': { app, seat_app } },
      machines: [TODAY],
    })
    const r = run(SIZE, ['check'])
    expect(r.rc).toBe(1)
    expect(r.out).toContain('FAIL size ashton-price:')
    expect(calls()).toEqual([])
  })

  it("uses the workspace's own sweep token when the run holds one", () => {
    const { run, calls } = setup({ machines: [AT_SIZE] })
    run(SIZE, ['check'], { OPS_FLY_TOKEN__ASHTON_PRICE: 'FlyV1 fake' })
    expect(calls()).toEqual(['fly machine list -a ops-ashton-price --json token=set'])
  })
})

describe('ops-workspace-size fix', () => {
  it('resizes a Machine that differs to the authored size, then reads it back', () => {
    const { run, calls } = setup({ machines: [TODAY], afterUpdate: [AT_SIZE] })
    const r = run(SIZE, ['fix', 'ashton-price'])
    expect(r.rc).toBe(0)
    expect(calls()).toEqual([
      'fly machine list -a ops-ashton-price --json token=',
      'fly machine update m1 -a ops-ashton-price --vm-size performance-8x --vm-memory 16384 --yes token=',
      'fly machine list -a ops-ashton-price --json token=',
    ])
    expect(r.out).toContain('PASS size ops-ashton-price m1')
  })

  it('leaves a Machine at size alone: no restart', () => {
    const { run, calls } = setup({ machines: [AT_SIZE] })
    expect(run(SIZE, ['fix', 'ashton-price']).rc).toBe(0)
    expect(calls().filter((c) => c.includes('machine update'))).toEqual([])
  })

  it('keeps a stopped Machine stopped', () => {
    const stopped = { ...TODAY, state: 'stopped' }
    const { run, calls } = setup({
      machines: [stopped],
      afterUpdate: [{ ...AT_SIZE, state: 'stopped' }],
    })
    expect(run(SIZE, ['fix', 'ashton-price']).rc).toBe(0)
    expect(calls()[1]).toContain('--skip-start')
  })

  it('fails when the Machine still differs after the update', () => {
    const { run } = setup({ machines: [TODAY] })
    const r = run(SIZE, ['fix', 'ashton-price'])
    expect(r.rc).toBe(1)
    expect(r.out).toContain('FAIL size ops-ashton-price m1')
  })
})

describe('ops-workspace sizes only from workspaces.json', () => {
  it.each(['update', 'create', 'resize'])(
    'refuses %s for a workspace with no size, before the vault or Fly is touched',
    (cmd) => {
      const { app, seat_app } = AUTHORED['ashton-price']
      const { run, calls } = setup({ workspaces: { 'ashton-price': { app, seat_app } } })
      const r = run(WORKSPACE, [cmd, 'ashton-price'])
      expect(r.rc).toBe(2)
      expect(r.err).toContain('never sized by default')
      expect(calls()).toEqual([])
    }
  )

  it('resize puts the Machine at the authored size without a rebuild', () => {
    const { run, calls } = setup({ machines: [TODAY], afterUpdate: [AT_SIZE] })
    const r = run(WORKSPACE, ['resize', 'ashton-price'])
    expect(r.rc).toBe(0)
    expect(calls().some((c) => c.startsWith('fly deploy'))).toBe(false)
    expect(calls()).toContain(
      'fly machine update m1 -a ops-ashton-price --vm-size performance-8x --vm-memory 16384 --yes token='
    )
  })
})
