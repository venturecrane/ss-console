/**
 * bin/ops-home-bin-sync: a ~/bin copy of an image script must not shadow the
 * image's own (2026-11-09, smd-ops ran a 10-16 claude shim from ~/bin and
 * missed the two-account switch). Runs the real script against temp dirs.
 */
import { execFileSync } from 'node:child_process'
import {
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  readFileSync,
  readlinkSync,
  lstatSync,
  rmSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, describe, expect, it } from 'vitest'

const SYNC = join(
  fileURLToPath(new URL('..', import.meta.url)),
  'operator',
  'ops-machine',
  'bin',
  'ops-home-bin-sync'
)
const dirs: string[] = []
afterEach(() => {
  for (const d of dirs.splice(0)) rmSync(d, { recursive: true, force: true })
})

function setup(): { img: string; home: string } {
  const root = mkdtempSync(join(tmpdir(), 'home-bin-'))
  dirs.push(root)
  const img = join(root, 'img')
  const home = join(root, 'home-bin')
  mkdirSync(img)
  mkdirSync(home)
  writeFileSync(join(img, 'claude'), 'new shim\n')
  writeFileSync(join(img, 'ss-env'), 'new ss-env\n')
  return { img, home }
}

describe('ops-home-bin-sync', () => {
  it('points a stale ~/bin copy at the image script and leaves the operator’s own files', () => {
    const { img, home } = setup()
    writeFileSync(join(home, 'claude'), 'old shim\n')
    writeFileSync(join(home, 'ops-crane-probe'), 'mine\n')
    const out = execFileSync('bash', [SYNC, img, home], { encoding: 'utf8' })
    expect(out).toContain('claude now points at')
    expect(readlinkSync(join(home, 'claude'))).toBe(join(img, 'claude'))
    expect(readFileSync(join(home, 'claude'), 'utf8')).toBe('new shim\n')
    expect(lstatSync(join(home, 'ops-crane-probe')).isSymbolicLink()).toBe(false)
    expect(readFileSync(join(home, 'ops-crane-probe'), 'utf8')).toBe('mine\n')
  })

  it('creates nothing for an image script with no ~/bin copy, and is quiet on a second run', () => {
    const { img, home } = setup()
    writeFileSync(join(home, 'claude'), 'old shim\n')
    execFileSync('bash', [SYNC, img, home])
    expect(() => lstatSync(join(home, 'ss-env'))).toThrow()
    expect(execFileSync('bash', [SYNC, img, home], { encoding: 'utf8' })).toBe('')
  })
})
