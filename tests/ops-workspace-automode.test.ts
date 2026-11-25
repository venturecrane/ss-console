/**
 * bin/ops-workspace-automode: a client workspace's Claude auto-mode rules come
 * from the repo (2026-11-24, a workspace with no autoMode block had every seat
 * write refused). Runs the real script against a temp HOME and the real
 * template, the way the entrypoint calls it.
 */
import { spawnSync } from 'node:child_process'
import {
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  readFileSync,
  existsSync,
  statSync,
  rmSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, describe, expect, it } from 'vitest'

const OPS = join(fileURLToPath(new URL('..', import.meta.url)), 'operator', 'ops-machine')
const SCRIPT = join(OPS, 'bin', 'ops-workspace-automode')
const TEMPLATE = join(OPS, 'workspace-automode.json')
const dirs: string[] = []
afterEach(() => {
  for (const d of dirs.splice(0)) rmSync(d, { recursive: true, force: true })
})

function home(): string {
  const d = mkdtempSync(join(tmpdir(), 'ws-automode-'))
  dirs.push(d)
  return d
}

function run(settings: string, env: Record<string, string> = {}) {
  return spawnSync('python3', [SCRIPT, TEMPLATE, settings], {
    encoding: 'utf8',
    env: {
      PATH: process.env.PATH ?? '',
      OPS_WORKSPACE: 'example-firm',
      OPS_SEAT_APP: 'hermes-example-firm',
      ...env,
    },
  })
}

type AutoMode = { allow: string[]; soft_deny: string[]; environment: string[] }

describe('ops-workspace-automode', () => {
  it('creates settings.json (mode 0600) with the filled autoMode block when there is none', () => {
    const settings = join(home(), '.claude', 'settings.json')
    const r = run(settings)
    expect(r.status).toBe(0)
    expect(statSync(settings).mode & 0o777).toBe(0o600)
    const out = JSON.parse(readFileSync(settings, 'utf8')) as { autoMode: AutoMode }
    expect(Object.keys(out)).toEqual(['autoMode'])
    const am = out.autoMode
    expect(Object.keys(am).sort()).toEqual(['allow', 'environment', 'soft_deny'])
    expect(am.allow[0]).toBe('$defaults')
    expect(am.soft_deny[0]).toBe('$defaults')
    const text = JSON.stringify(am)
    expect(text).not.toMatch(/\{\{[A-Z_]+\}\}/)
    expect(text).toContain('`hermes-example-firm`')
    expect(text).toContain('fly ssh console -a hermes-example-firm')
    expect(text).toContain('`ops-example-firm`')
    expect(text).toContain('`example-firm`')
  })

  it('keeps every other key and replaces a stale autoMode block', () => {
    const settings = join(home(), 'settings.json')
    writeFileSync(
      settings,
      JSON.stringify({
        model: 'opus',
        permissions: { allow: ['Bash(ls:*)'] },
        autoMode: { allow: ['old rule'], soft_deny: [], environment: [] },
      })
    )
    const r = run(settings, { FLY_APP_NAME: 'ops-renamed' })
    expect(r.status).toBe(0)
    const out = JSON.parse(readFileSync(settings, 'utf8')) as {
      model: string
      permissions: unknown
      autoMode: AutoMode
    }
    expect(out.model).toBe('opus')
    expect(out.permissions).toEqual({ allow: ['Bash(ls:*)'] })
    expect(out.autoMode.allow).not.toContain('old rule')
    expect(out.autoMode.environment[0]).toContain('`ops-renamed`')
    // Idempotent: a second boot writes the same file.
    const first = readFileSync(settings, 'utf8')
    expect(run(settings, { FLY_APP_NAME: 'ops-renamed' }).status).toBe(0)
    expect(readFileSync(settings, 'utf8')).toBe(first)
  })

  it('leaves a malformed settings.json exactly as it is and exits 3', () => {
    const d = home()
    mkdirSync(join(d, '.claude'))
    const settings = join(d, '.claude', 'settings.json')
    for (const bad of ['{"model": "opus",', '["not", "an", "object"]']) {
      writeFileSync(settings, bad)
      const r = run(settings)
      expect(r.status).toBe(3)
      expect(r.stdout).toContain('left as it is')
      expect(readFileSync(settings, 'utf8')).toBe(bad)
    }
  })

  it('installs nothing without the seat app', () => {
    const settings = join(home(), 'settings.json')
    const r = run(settings, { OPS_SEAT_APP: '' })
    expect(r.status).toBe(2)
    expect(existsSync(settings)).toBe(false)
  })

  it('the template names no client and keeps the exact autoMode shape', () => {
    const t = JSON.parse(readFileSync(TEMPLATE, 'utf8')) as AutoMode
    expect(Object.keys(t).sort()).toEqual(['allow', 'environment', 'soft_deny'])
    const text = JSON.stringify(t)
    expect(text).toContain('{{SEAT_APP}}')
    // No listed workspace's slug or app names appears in the shared template.
    const reg = JSON.parse(readFileSync(join(OPS, 'workspaces.json'), 'utf8')) as {
      workspaces: Record<string, { app: string; seat_app: string }>
    }
    for (const [slug, w] of Object.entries(reg.workspaces)) {
      for (const name of [slug, w.app, w.seat_app]) expect(text).not.toContain(name)
    }
  })
})
