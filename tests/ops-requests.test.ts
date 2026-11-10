/**
 * Tests for the one-command daily request check: laptop/ops-requests (the
 * transport) and laptop/requests_digest.py (the read, which runs on the seat).
 *
 * Why (2026-11-10): "what did the firm ask its Operator today" was a free-text
 * task handed to a workspace Claude session, which took minutes when it worked
 * and 20 when the session never started. What these pin is the part that
 * matters to the person reading the answer: a day with no requests and a read
 * that failed never look alike.
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { execFileSync, spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const laptop = path.join(process.cwd(), 'operator', 'ops-machine', 'laptop')
const digest = path.join(laptop, 'requests_digest.py')

/** Noon Phoenix on a date, as epoch seconds (Arizona is UTC-7 all year). */
const noon = (day: string): number => Date.parse(`${day}T12:00:00-07:00`) / 1000

let dir: string

/** Build the two seat databases with python's own sqlite, as the seat has them. */
function seed(script: string): void {
  execFileSync('python3', ['-I', '-c', script, dir], { encoding: 'utf8' })
}

const SCHEMA = `
import sqlite3, sys, os
d = sys.argv[1]
r = sqlite3.connect(os.path.join(d, 'requests.db'))
r.execute("create table requests (vendor_message_id text primary key, internet_message_id text, received_at real not null, sender text, subject text, reply_opening text, opening_at real, thread_ref text, request_opening text, request_text text, reply_text text)")
r.execute("create table replies (sent_message_id text primary key, in_reply_to text, sent_at real not null, opening text, text text)")
a = sqlite3.connect(os.path.join(d, 'audit.db'))
a.execute("create table audit_log (ts text, action_type text, actor text, metadata text)")
a.execute("create table medchron_jobs (id text, state text, matter_id text, cents integer, created_at text, updated_at text)")
`

function run(day: string, env: Record<string, string> = {}) {
  return spawnSync('python3', ['-I', digest, day], {
    env: {
      ...process.env,
      SMD_SEAT_LAYOUT: path.join(dir, 'layout.json'),
      ...env,
    },
    encoding: 'utf8',
  })
}

beforeEach(() => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ops-requests-'))
  // The seat's layout file, in the generated file's own shape, pointing at dir.
  fs.writeFileSync(
    path.join(dir, 'layout.json'),
    JSON.stringify({
      paths: {
        requests_db: { path: path.join(dir, 'requests.db') },
        audit_volume_db: { path: path.join(dir, 'audit.db') },
      },
    })
  )
})

describe('requests_digest.py (the read on the seat)', () => {
  it('lists a request with who asked, what, and each reply, and its job', () => {
    seed(`${SCHEMA}
r.execute("insert into requests values ('v1','i1',?, 'pat@firm.example','Chronology please',null,null,'t1',null,'Please update the chronology.',null)", (${noon('2026-11-09')},))
r.execute("insert into replies values ('s1','v1',?, 'On it', 'The update is under way.')", (${noon('2026-11-09')} + 120,))
r.execute("insert into replies values ('s2','t1',?, 'Done', 'The chronology was updated.')", (${noon('2026-11-09')} + 7200,))
a.execute("insert into medchron_jobs values ('J1','held','M-1',2463,'2026-11-09T19:02:00+00:00','2026-11-09T20:00:00+00:00')")
r.commit(); a.commit()`)
    const got = run('2026-11-09')
    expect(got.status).toBe(0)
    expect(got.stdout).toContain('REQUESTS RECEIVED: 1')
    expect(got.stdout).toContain('from pat@firm.example')
    expect(got.stdout).toContain('Asked:   Please update the chronology.')
    expect(got.stdout).toContain('The update is under way.')
    expect(got.stdout).toContain('The chronology was updated.')
    expect(got.stdout).toMatch(/medchron {2}id=J1 {2}state=held.*needs a look/)
    expect(got.stdout).toContain('(1 held or failed)')
  })

  it('says NONE YET for a request with no reply', () => {
    seed(`${SCHEMA}
r.execute("insert into requests values ('v1','i1',?, 'pat@firm.example','Hello',null,null,null,null,'Where is it?',null)", (${noon('2026-11-10')},))
r.commit()`)
    expect(run('2026-11-10').stdout).toContain('Reply:   NONE YET')
  })

  it('reports an empty day with the read check that proves the read worked', () => {
    seed(`${SCHEMA}
r.execute("insert into requests values ('v1','i1',?, 'pat@firm.example','Old',null,null,null,null,'x',null)", (${noon('2026-11-09')},))
r.commit()`)
    const got = run('2026-11-10')
    expect(got.status).toBe(0)
    expect(got.stdout).toContain('REQUESTS RECEIVED: 0')
    expect(got.stdout).toMatch(
      /READ CHECK: 1 requests in the index in all; newest of any date 2026-11-09/
    )
    expect(got.stdout).toContain('the day before had 1')
  })

  it('a missing index is READ FAILED and exit 3, never zero requests', () => {
    const got = run('2026-11-10')
    expect(got.status).toBe(3)
    expect(got.stdout).toContain('READ FAILED')
    expect(got.stdout).toContain('NOT zero requests')
    expect(got.stdout).not.toContain('REQUESTS RECEIVED')
  })

  it('reads both paths from the keys the real seat layout file carries', () => {
    const real = JSON.parse(
      fs.readFileSync(
        path.join(process.cwd(), 'operator', 'templates', 'seat-layout.generated.json'),
        'utf8'
      )
    ) as { paths: Record<string, { path: string }> }
    expect(real.paths.requests_db.path).toMatch(/requests\.db$/)
    expect(real.paths.audit_volume_db.path).toMatch(/audit\.db$/)
  })

  it('a missing seat layout is READ FAILED, never zero requests', () => {
    fs.rmSync(path.join(dir, 'layout.json'))
    const got = run('2026-11-10')
    expect(got.status).toBe(3)
    expect(got.stdout).toContain('could not read the seat layout')
  })

  it('a missing job database is "could not look", never "no jobs"', () => {
    seed(SCHEMA)
    fs.rmSync(path.join(dir, 'audit.db'))
    const got = run('2026-11-10')
    expect(got.status).toBe(0)
    expect(got.stdout).toContain('JOBS: could not look')
  })

  it('names a lane table it does not know instead of skipping it', () => {
    seed(`${SCHEMA}
a.execute("create table brandnew_jobs (id text, state text, created_at text)")
a.commit()`)
    expect(run('2026-11-10').stdout).toMatch(/NOT READ: lane table\(s\).*brandnew_jobs/)
  })

  it('knows every lane job table the Operator code creates', () => {
    // A lane added without a row in JOB_QUERIES would be reported as NOT READ on
    // the seat; this fails at merge instead, where the fix is one line.
    const out = execFileSync(
      'git',
      ['grep', '-hoE', 'CREATE TABLE IF NOT EXISTS [a-z_]+_jobs', '--', 'operator'],
      { encoding: 'utf8', env: stripGit(process.env) }
    )
    const created = [
      ...new Set(
        out
          .split('\n')
          .filter(Boolean)
          .map((l) => l.split(' ').pop())
      ),
    ]
    expect(created.length).toBeGreaterThan(5)
    const source = fs.readFileSync(digest, 'utf8')
    for (const table of created) {
      expect(source, `${table} missing from JOB_QUERIES`).toContain(
        `"${table}": "select * from ${table}"`
      )
    }
  })
})

/** A pre-push hook exports GIT_DIR and friends; a test's git must not inherit them. */
function stripGit(env: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  return Object.fromEntries(Object.entries(env).filter(([k]) => !k.startsWith('GIT_')))
}

describe('ops-requests (the laptop command)', () => {
  let home: string

  /** A stub fly whose workspace answers with whatever WORKSPACE_SAYS holds. */
  function call(args: string[], workspaceSays: string): { status: number; stdout: string } {
    home = fs.mkdtempSync(path.join(os.tmpdir(), 'ops-requests-home-'))
    const bin = path.join(home, 'bin')
    const tools = path.join(home, 'tools')
    fs.mkdirSync(bin)
    fs.mkdirSync(tools)
    fs.mkdirSync(path.join(home, '.claude'))
    fs.copyFileSync(
      path.join(process.cwd(), 'operator', 'ops-machine', 'workspaces.json'),
      path.join(home, '.claude', 'smd-ops-workspaces.json')
    )
    fs.writeFileSync(path.join(home, 'says'), workspaceSays)
    fs.writeFileSync(
      path.join(bin, 'fly'),
      `#!/bin/bash
case "$*" in
  *"machine list"*) echo '[{"id":"m1","state":"started"}]' ;;
  *"bash -s"*) cat > "${home}/sent.sh"; cat "${home}/says" ;;
  *) : ;;
esac
exit 0
`,
      { mode: 0o755 }
    )
    for (const name of ['ops', 'ops-requests', 'requests_digest.py']) {
      fs.copyFileSync(path.join(laptop, name), path.join(tools, name))
      fs.chmodSync(path.join(tools, name), 0o755)
    }
    const got = spawnSync(path.join(tools, 'ops-requests'), args, {
      env: { ...process.env, HOME: home, PATH: `${bin}:${process.env.PATH}` },
      encoding: 'utf8',
    })
    return { status: got.status ?? -1, stdout: got.stdout }
  }

  it('prints the digest and exits 0 when the read check came back', () => {
    const got = call(['ap'], 'REQUESTS RECEIVED: 0\nREAD CHECK: 61 requests\nops-requests-exit=0\n')
    expect(got.status).toBe(0)
    expect(got.stdout).toContain('REQUESTS RECEIVED: 0')
    expect(got.stdout).not.toContain('ops-requests-exit')
  })

  it('sends the digest script itself and runs it on the seat as hermes, with the day', () => {
    call(['ap', '2026-11-09'], 'READ CHECK: x\nops-requests-exit=0\n')
    const sent = fs.readFileSync(path.join(home, 'sent.sh'), 'utf8')
    expect(sent).toContain('runuser -u hermes -- python3 - 2026-11-09')
    const b64 = sent.match(/printf %s '([A-Za-z0-9+/=]+)'/)?.[1] ?? ''
    expect(Buffer.from(b64, 'base64').toString('utf8')).toBe(fs.readFileSync(digest, 'utf8'))
  })

  it('exits 1 and says it is not zero requests when the read never finished', () => {
    // The seat was unreachable: output, an exit code even, but no READ CHECK.
    const got = call(['ap'], 'Error: app not found\nops-requests-exit=1\n')
    expect(got.status).toBe(1)
    expect(got.stdout).toContain('REQUEST CHECK FAILED')
    expect(got.stdout).toContain('NOT zero requests')
  })

  it('exits 1 when the workspace returned nothing at all', () => {
    const got = call(['ap'], '')
    expect(got.status).toBe(1)
    expect(got.stdout).toContain('REQUEST CHECK FAILED')
  })

  it('does not trust a 0 exit that arrives without the read check', () => {
    const got = call(['ap'], 'REQUESTS RECEIVED: 0\nops-requests-exit=0\n')
    expect(got.status).toBe(1)
  })

  it('refuses a date that is not YYYY-MM-DD before reaching the workspace', () => {
    const got = call(['ap', "x'; rm -rf ~; '"], '')
    expect(got.status).toBe(2)
    expect(fs.existsSync(path.join(home, 'sent.sh'))).toBe(false)
  })
})
