/**
 * Tests for .claude/hooks/ops-tools-sync.sh and laptop/install.sh --check: the
 * laptop's INSTALLED ops tools converge on origin/main at every session start.
 *
 * Why (2026-11-10): a fix to ops-handoff merged on 11-09 and the laptop still
 * ran the 10-27 copy the next morning, which cost the Captain 20 minutes on a
 * one-minute check. tests/ops-handoff.test.ts runs the repo's file, so it
 * could never see a stale installed copy. These run the hook against a real
 * git repository and a real (temporary) HOME, and read the installed files
 * back.
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { execFileSync, spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const root = process.cwd()
const hook = path.join(root, '.claude', 'hooks', 'ops-tools-sync.sh')
const laptopRel = path.join('operator', 'ops-machine', 'laptop')
const TOOLS = ['ops', 'ops-handoff', 'ops-requests', 'requests_digest.py']

/** A pre-push hook exports GIT_DIR and friends; nothing here may inherit them. */
const clean = (extra: Record<string, string> = {}): NodeJS.ProcessEnv => ({
  ...Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('GIT_'))),
  ...extra,
})

let repo: string
let home: string

function git(...args: string[]): string {
  return execFileSync('git', ['-C', repo, ...args], { encoding: 'utf8', env: clean() })
}

/** Commit the working tree and point origin/main at it, as a fetch would. */
function publish(message: string): void {
  git('add', '-A')
  git('-c', 'user.name=t', '-c', 'user.email=t@example.com', 'commit', '-q', '-m', message)
  git('update-ref', 'refs/remotes/origin/main', 'HEAD')
}

function runHook(): { stdout: string; status: number } {
  const got = spawnSync('bash', [hook], {
    env: clean({ HOME: home, CLAUDE_PROJECT_DIR: repo, OPS_TOOLS_SYNC_NO_FETCH: '1' }),
    encoding: 'utf8',
  })
  return { stdout: got.stdout, status: got.status ?? -1 }
}

const installed = (name: string): string => path.join(home, '.local', 'bin', name)
const tracked = (name: string): string => path.join(repo, laptopRel, name)

beforeEach(() => {
  repo = fs.mkdtempSync(path.join(os.tmpdir(), 'ops-tools-repo-'))
  home = fs.mkdtempSync(path.join(os.tmpdir(), 'ops-tools-home-'))
  fs.mkdirSync(path.join(repo, laptopRel), { recursive: true })
  for (const name of fs.readdirSync(path.join(root, laptopRel))) {
    fs.copyFileSync(path.join(root, laptopRel, name), tracked(name))
  }
  fs.copyFileSync(
    path.join(root, 'operator', 'ops-machine', 'workspaces.json'),
    path.join(repo, 'operator', 'ops-machine', 'workspaces.json')
  )
  execFileSync('git', ['init', '-q', '-b', 'main', repo], { env: clean() })
  publish('tools')
  // A laptop that has been set up: run the real installer once.
  execFileSync('bash', [tracked('install.sh')], { env: clean({ HOME: home }), encoding: 'utf8' })
})

describe('install.sh --check', () => {
  const check = () =>
    spawnSync('bash', [tracked('install.sh'), '--check'], {
      env: clean({ HOME: home }),
      encoding: 'utf8',
    })

  it('is silent and exits 0 right after an install', () => {
    const got = check()
    expect(got.status).toBe(0)
    expect(got.stdout).toBe('')
  })

  it('installs every tool the laptop runs, and the request digest beside ops-requests', () => {
    for (const name of TOOLS) expect(fs.existsSync(installed(name)), name).toBe(true)
  })

  it('names each stale or missing file and exits 1, installing nothing', () => {
    fs.appendFileSync(installed('ops-handoff'), '\n# an old copy\n')
    fs.rmSync(installed('requests_digest.py'))
    const got = check()
    expect(got.status).toBe(1)
    expect(got.stdout.trim().split(' ').sort()).toEqual(['ops-handoff', 'requests_digest.py'])
    expect(fs.existsSync(installed('requests_digest.py'))).toBe(false)
  })
})

describe('ops-tools-sync.sh', () => {
  it('says nothing when the installed tools already equal origin/main', () => {
    expect(runHook()).toEqual({ stdout: '', status: 0 })
  })

  it('reinstalls a stale tool from origin/main and says which one', () => {
    // The 2026-11-10 case: the fix is on main, the installed copy is older.
    fs.writeFileSync(installed('ops-handoff'), '#!/bin/bash\necho the 10-27 copy\n')
    const got = runHook()
    expect(got.status).toBe(0)
    expect(got.stdout).toMatch(
      /were stale \(ops-handoff\); reinstalled from origin\/main @ [0-9a-f]+/
    )
    expect(fs.readFileSync(installed('ops-handoff'), 'utf8')).toBe(
      fs.readFileSync(tracked('ops-handoff'), 'utf8')
    )
  })

  it('installs origin/main, never the work in progress in this tree', () => {
    const main = fs.readFileSync(tracked('ops'), 'utf8')
    fs.appendFileSync(tracked('ops'), '\n# unmerged edit on a branch\n')
    fs.writeFileSync(installed('ops'), '#!/bin/bash\necho old\n', { mode: 0o755 })
    runHook()
    expect(fs.readFileSync(installed('ops'), 'utf8')).toBe(main)
  })

  it('leaves a laptop that was never set up for ops alone', () => {
    fs.rmSync(path.join(home, '.local'), { recursive: true })
    expect(runHook()).toEqual({ stdout: '', status: 0 })
    expect(fs.existsSync(installed('ops'))).toBe(false)
  })

  it('says loudly that the tools are stale when the reinstall cannot fix it', () => {
    fs.writeFileSync(installed('ops-handoff'), '#!/bin/bash\necho old\n')
    // The installed path cannot be written: a directory sits where a file goes.
    fs.rmSync(installed('ops-requests'))
    fs.mkdirSync(installed('ops-requests'))
    fs.writeFileSync(path.join(installed('ops-requests'), 'x'), '')
    const got = runHook()
    expect(got.status).toBe(0)
    expect(got.stdout).toContain('THE INSTALLED LAPTOP OPS TOOLS ARE STALE')
  })
})
