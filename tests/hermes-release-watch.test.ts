/**
 * The upstream read in the Hermes release-watch, run rather than described.
 *
 * Run #6 (2026-08-17) turned one bad second into a critical alert: a single
 * `gh api .../releases` came back empty, the step exited 1, and a weekly signal
 * job paged. The identical call returns v2026.8.18 today, so nothing upstream
 * was actually wrong.
 *
 * The fix retries, then falls back to a second endpoint. The risk in that fix is
 * the obvious one — make a watcher tolerant enough and it stops watching. So the
 * test that matters here is the LAST one: both endpoints permanently unreadable
 * must still fail the run, loudly, naming what it saw. A release-watch that
 * exits 0 on an unreadable upstream lets the fleet pin rot invisibly, which is
 * the drift ADR 0024 wrote this job to prevent and the exact shape of ss#2441.
 *
 * These execute the REAL step body extracted from the YAML, under the shell
 * GitHub uses, against a `gh` stub whose per-call behaviour the test chooses.
 * `sleep` is stubbed to a no-op so the production backoff stays real code rather
 * than a test-only knob. Asserting on the body's text would only restate the
 * wiring; running it is what can fail.
 */
import { execFileSync } from 'node:child_process'
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterEach, describe, expect, it } from 'vitest'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const WORKFLOW = join(REPO_ROOT, '.github', 'workflows', 'hermes-release-watch.yml')
const STEP = 'Compare upstream latest vs blessed fleet pin'

/** The `run:` body of a named step, dedented, exactly as GitHub runs it. */
const stepBody = (name: string): string => {
  const source = readFileSync(WORKFLOW, 'utf8')
  const start = source.indexOf(`      - name: ${name}`)
  expect(start).toBeGreaterThan(-1)
  const body = source.slice(source.indexOf('run: |', start) + 'run: |\n'.length)
  const lines: string[] = []
  for (const line of body.split('\n')) {
    if (line.trim() !== '' && !line.startsWith('          ')) break
    lines.push(line.slice(10))
  }
  return lines.join('\n')
}

/**
 * `releases` / `tags` are the tag each endpoint yields, or '' for an empty read.
 * `releasesFailFor` makes the first N releases calls come back empty, so a
 * transient blip is distinguishable from a broken endpoint.
 */
interface Scenario {
  releases?: string
  tags?: string
  releasesFailFor?: number
  blessed?: string
}

describe('the release-watch survives a blip and still fails on a dead upstream', () => {
  let dir: string | undefined

  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true })
    dir = undefined
  })

  const runStep = (s: Scenario) => {
    dir = mkdtempSync(join(tmpdir(), 'hermes-watch-'))
    const bin = join(dir, 'bin')
    mkdirSync(bin, { recursive: true })

    // The Dockerfile grep the step opens first. Its own failure path is already
    // explicit in the step; this just lets the upstream read be what is tested.
    mkdirSync(join(dir, 'operator', 'templates'), { recursive: true })
    writeFileSync(
      join(dir, 'operator', 'templates', 'Dockerfile'),
      `ARG HERMES_UPSTREAM_TAG=${s.blessed ?? 'v2026.7.1'} \n`
    )

    const counter = join(dir, 'releases-calls')
    writeFileSync(counter, '0')

    writeFileSync(
      join(bin, 'gh'),
      [
        '#!/usr/bin/env bash',
        // `gh api <endpoint> --jq <filter>` — the only calls under test. Every
        // other subcommand (issue list/create, label create) succeeds quietly so
        // the drift branch can run to completion.
        'if [ "$1" = "api" ]; then',
        '  case "$2" in',
        '    *releases*)',
        `      n=$(cat ${counter}); n=$((n + 1)); echo "$n" > ${counter}`,
        `      if [ "$n" -le ${s.releasesFailFor ?? 0} ]; then exit 0; fi`,
        `      printf '%s' '${s.releases ?? ''}'; [ -n '${s.releases ?? ''}' ] && echo`,
        '      exit 0 ;;',
        `    *tags*) printf '%s' '${s.tags ?? ''}'; [ -n '${s.tags ?? ''}' ] && echo; exit 0 ;;`,
        '  esac',
        'fi',
        'if [ "$1" = "issue" ] && [ "$2" = "list" ]; then echo 0; exit 0; fi',
        'exit 0',
      ].join('\n')
    )
    chmodSync(join(bin, 'gh'), 0o755)

    // No-op backoff: the retry timing is real production code, but a test should
    // not spend 30s proving it.
    writeFileSync(join(bin, 'sleep'), '#!/usr/bin/env bash\nexit 0\n')
    chmodSync(join(bin, 'sleep'), 0o755)

    writeFileSync(join(dir, 'step.sh'), stepBody(STEP))

    // Merged streams: the step's diagnostics (which attempt saw what, the
    // fallback notice) go to stderr on purpose, and they are half of what this
    // change is for — a failure that cannot be read is the ss#2440 defect.
    let stdout: string
    let failed = false
    try {
      stdout = execFileSync('bash', ['-c', `bash "${join(dir, 'step.sh')}" 2>&1`], {
        cwd: dir,
        encoding: 'utf-8',
        stdio: ['ignore', 'pipe', 'pipe'],
        env: { ...process.env, PATH: `${bin}:${process.env.PATH ?? ''}` },
      })
    } catch (err) {
      failed = true
      const e = err as { stdout?: string; stderr?: string }
      stdout = `${e.stdout ?? ''}${e.stderr ?? ''}`
    }
    return { stdout, failed, releasesCalls: Number(readFileSync(counter, 'utf-8').trim()) }
  }

  it('reads the latest tag on a clean first attempt', () => {
    const run = runStep({ releases: 'v2026.8.18' })
    expect(run.failed).toBe(false)
    expect(run.stdout).toContain('blessed=v2026.7.1 latest=v2026.8.18')
    expect(run.releasesCalls).toBe(1)
  })

  it('recovers from the blip that killed run #6 instead of paging', () => {
    // Two empty reads, then the same value the endpoint returns today.
    const run = runStep({ releases: 'v2026.8.18', releasesFailFor: 2 })
    expect(run.failed).toBe(false)
    expect(run.stdout).toContain('latest=v2026.8.18')
    expect(run.releasesCalls).toBe(3)
  })

  it('falls back to tags when releases is the endpoint that is broken', () => {
    const run = runStep({ releases: '', tags: 'v2026.8.18' })
    expect(run.failed).toBe(false)
    expect(run.stdout).toContain('latest=v2026.8.18')
    expect(run.stdout).toContain('falling back to tags')
  })

  it('reports no action when the fleet is already current', () => {
    const run = runStep({ releases: 'v2026.7.1', blessed: 'v2026.7.1' })
    expect(run.failed).toBe(false)
    expect(run.stdout).toContain('Fleet is current with upstream')
  })

  it('STILL fails, and says what it saw, when upstream is genuinely unreadable', () => {
    // The falsifier for the whole change. If retries and a fallback ever turn a
    // dead upstream green, the watcher has become the thing it watches for.
    const run = runStep({ releases: '', tags: '' })
    expect(run.failed).toBe(true)
    expect(run.stdout).toContain('could not read latest upstream release tag')
    expect(run.stdout).toContain('<empty>')
    // Three on releases, three on tags — it gave up, it did not give up early.
    expect(run.releasesCalls).toBe(3)
  })
})
