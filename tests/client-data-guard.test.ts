/**
 * Tests for operator/ops-machine/laptop/client-data-guard.mjs, the user-level
 * PreToolUse hook that stops laptop sessions from pulling client documents
 * (Captain, 2026-10-17: client documents are opened only in that client's ops
 * workspace). Run as Claude Code runs it: node with the payload on stdin; exit 2
 * blocks, exit 0 allows. The workspace marker is pointed at a temp path so the
 * suite behaves the same on a laptop, in CI, and on a workspace.
 */
import { describe, it, expect } from 'vitest'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const script = path.join(
  process.cwd(),
  'operator',
  'ops-machine',
  'laptop',
  'client-data-guard.mjs'
)
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'cdg-'))
const absentMarker = path.join(tmp, 'no-marker')

function run(toolInput: Record<string, string>, env: Record<string, string> = {}): number {
  try {
    execFileSync('node', [script], {
      input: JSON.stringify({
        hook_event_name: 'PreToolUse',
        tool_name: 'Bash',
        tool_input: toolInput,
      }),
      env: {
        ...process.env,
        HOME: tmp,
        SMD_OPS_WORKSPACE_MARKER: absentMarker,
        SS_ALLOW_CLIENT_DATA_ON_LAPTOP: '',
        ...env,
      },
      stdio: ['pipe', 'ignore', 'ignore'],
    })
    return 0
  } catch (err) {
    return (err as { status?: number }).status ?? -1
  }
}
const bash = (command: string, env?: Record<string, string>) => run({ command }, env)

describe('client-data-guard: blocks document work on a laptop', () => {
  it.each([
    'python3 operator/customers/ashton-price/tools/medchron/download.py 201504',
    'cd tools/medchron && SMD_MC_DATA=/tmp/x python3 download.py 201504',
    './mint_small.sh 201504',
    'bash run_seat.sh list 201504',
    'ls ~/smd-medchron-data/irma',
    'du -sh /Users/scottdurgan/smd-filereview-data',
    'export SMD_MC_DATA=$HOME/work && python3 assemble.py',
    'fly ssh sftp get -a hermes-ashton-price /opt/data/x.pdf',
    'operator/bin/seat-probe.sh ashton-price download 201504',
  ])('blocks: %s', (cmd) => {
    expect(bash(cmd)).toBe(2)
  })

  it('blocks a write into a data folder', () => {
    expect(run({ file_path: path.join(tmp, 'smd-medchron-data', 'irma', 'notes.md') })).toBe(2)
  })
})

describe('client-data-guard: leaves product and seat work alone', () => {
  it.each([
    'git diff operator/customers/ashton-price/tools/medchron/download.py',
    'python3 -m pytest tools/medchron/tests/test_download.py -q',
    'rg -n smd-medchron-data operator/',
    'bash operator/bin/lane-proof.sh ashton-price demand',
    'fly status -a hermes-ashton-price',
    'npm run verify',
  ])('allows: %s', (cmd) => {
    expect(bash(cmd)).toBe(0)
  })

  it('allows editing tool code', () => {
    expect(
      run({
        file_path: '/x/engagements/operator/customers/ashton-price/tools/medchron/download.py',
      })
    ).toBe(0)
  })
})

describe('client-data-guard: the tools that take documents OFF a laptop', () => {
  it.each([
    'OPS_APP=ops-ashton-price bash operator/ops-machine/move-by-folder.sh ~/smd-medchron-data mac23/smd-medchron-data /tmp/list.txt',
    'bash /x/operator/ops-machine/move-to-ops.sh /Users/scott/smd-medchron-data mac23/smd-medchron-data ./run-1',
    'python3 operator/ops-machine/laptop-offboard.py --host mac23 --from ops-ashton-price --execute',
  ])('allows: %s', (cmd) => {
    expect(bash(cmd)).toBe(0)
  })

  it.each([
    'bash operator/ops-machine/move-to-ops.sh ~/smd-medchron-data x ./a; cat ~/smd-medchron-data/irma/a.pdf',
    'bash operator/ops-machine/move-to-ops.sh ~/smd-medchron-data x ./a && ls ~/smd-medchron-data',
    'bash operator/ops-machine/move-to-ops.sh $(ls ~/smd-medchron-data)',
    'cp ~/smd-medchron-data/a.pdf /tmp # operator/ops-machine/move-to-ops.sh',
  ])('still blocks anything chained to one: %s', (cmd) => {
    expect(bash(cmd)).toBe(2)
  })
})

describe('client-data-guard: deleting documents from a laptop', () => {
  it.each([
    'rm -rf /Users/scottdurgan/smd-medchron-data/_parity-check-2026-10-14',
    'rm ~/smd-filereview-data/irma/a.pdf ~/smd-filereview-data/irma/b.pdf',
  ])('allows: %s', (cmd) => {
    expect(bash(cmd)).toBe(0)
  })

  it.each([
    'rm -rf ~/smd-medchron-data/x; cat ~/smd-medchron-data/y.pdf',
    'rm -rf ~/smd-medchron-data/x && python3 download.py 1',
    'rm ~/smd-medchron-data/*.tmp',
    'rm $(ls ~/smd-medchron-data)',
    'cp ~/smd-medchron-data/a.pdf /tmp/rm',
  ])('still blocks anything more than a deletion: %s', (cmd) => {
    expect(bash(cmd)).toBe(2)
  })
})

describe('client-data-guard: workspace and break-glass', () => {
  it('allows everything on a workspace (marker present)', () => {
    const marker = path.join(tmp, 'marker')
    fs.writeFileSync(marker, 'ashton-price\n')
    expect(bash('python3 download.py 201504', { SMD_OPS_WORKSPACE_MARKER: marker })).toBe(0)
  })

  it('allows with a stated reason and audits it', () => {
    expect(
      bash('python3 download.py 201504', {
        SS_ALLOW_CLIENT_DATA_ON_LAPTOP: 'workspace down, client waiting',
      })
    ).toBe(0)
    const log = fs.readFileSync(path.join(tmp, '.claude', 'client-data-guard-audit.log'), 'utf8')
    expect(log).toContain('workspace down, client waiting')
  })
})
