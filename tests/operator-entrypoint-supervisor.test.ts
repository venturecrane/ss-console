/**
 * The entrypoint's Machine-stop supervisor (fleet job-safe restarts), run for
 * real: bash sourcing operator/runners/supervisor/entrypoint-supervisor.sh,
 * python stand-ins for the medchron daemon, a job child and the gateway, and
 * the stop delivered the way tini `-g` delivers it, to the entrypoint's whole
 * process group.
 *
 * What it proves, in the order a stop happens: the quiesce file is written;
 * the daemon is signalled and the supervisor waits for its drain BEFORE the
 * gateway is touched; a job child in its own session survives the group signal;
 * the gateway gets SIGTERM only then, and the supervisor exits with its code.
 * Plus the held-gateway contract: the gateway's pid IS SMD_GATEWAY_PID, it runs
 * in its own session, and its environment is the entrypoint's FINAL one (an
 * unset after the fork never reaches it).
 *
 * The falsifier runs the pre-change shape (exec into the gateway, job child in
 * the shared group) under the same group signal and shows the job child dying
 * at once and the gateway stopping before the daemon drained.
 *
 * `setpriv` and `setsid` are stubbed on PATH (no root here, and macOS has no
 * setsid): the setsid stub calls os.setsid() and execs, keeping the pid, which
 * is the property under test.
 */
import { describe, it, expect } from 'vitest'
import { spawn, spawnSync } from 'child_process'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'fs'
import { tmpdir } from 'os'
import { join, resolve } from 'path'

const SUPERVISOR = resolve('operator/runners/supervisor/entrypoint-supervisor.sh')
const ENTRYPOINT = readFileSync(resolve('operator/templates/entrypoint.sh'), 'utf8')

const GATEWAY_PY = `
import json, os, signal, sys, time
out = os.environ["OUT"]
mode = os.environ.get("GATEWAY_MODE", "drain")
def rec(name, **kw):
    with open(os.path.join(out, "events"), "a") as f:
        f.write(json.dumps({"e": name, "t": time.time(), **kw}) + "\\n")
def on_term(*_):
    rec("gateway-term", daemon_exited=os.path.exists(os.path.join(out, "daemon-exited")))
    if mode == "deaf":
        return
    time.sleep(0.2)  # Hermes' own stop
    sys.exit(0)
signal.signal(signal.SIGTERM, on_term)
with open(os.path.join(out, "gateway.json"), "w") as f:
    json.dump({"pid": os.getpid(), "sid": os.getsid(0), "env_pid": os.environ.get("SMD_GATEWAY_PID"),
               "late": os.environ.get("LATE_VAR"), "dropped": os.environ.get("DROPPED_SECRET")}, f)
if mode == "crash":
    time.sleep(0.3)
    sys.exit(7)
while True:
    time.sleep(0.1)
`

// A daemon that, like medchron.daemon, starts a job child (in its own session
// unless OLD_SHAPE), drains for a while on SIGTERM, then exits 130.
const DAEMON_PY = `
import json, os, signal, subprocess, sys, time
out = os.environ["OUT"]
def rec(name, **kw):
    with open(os.path.join(out, "events"), "a") as f:
        f.write(json.dumps({"e": name, "t": time.time(), **kw}) + "\\n")
child = subprocess.Popen([sys.executable, os.path.join(out, "job.py")],
                         start_new_session=not os.environ.get("OLD_SHAPE"))
with open(os.path.join(out, "job.pid"), "w") as f:
    f.write(str(child.pid))
stop = {"at": None}
def on_term(*_):
    stop["at"] = stop["at"] or time.time()
    rec("daemon-term")
signal.signal(signal.SIGTERM, on_term)
while stop["at"] is None or time.time() - stop["at"] < 0.8:
    time.sleep(0.05)
open(os.path.join(out, "daemon-exited"), "w").close()
rec("daemon-exit")
sys.exit(130)
`

const JOB_PY = `
import json, os, signal, time
out = os.environ["OUT"]
def on_term(*_):
    with open(os.path.join(out, "events"), "a") as f:
        f.write(json.dumps({"e": "job-term", "t": time.time()}) + "\\n")
    os._exit(143)
signal.signal(signal.SIGTERM, on_term)
open(os.path.join(out, "job-ready"), "w").close()
time.sleep(30)
`

interface Run {
  dir: string
  code: number | null
  events: { e: string; t: number; daemon_exited?: boolean }[]
  gateway: {
    pid: number
    sid: number
    env_pid: string
    late: string | null
    dropped: string | null
  } | null
  log: string
}

function stubs(dir: string): string {
  const bin = `${dir}/bin`
  mkdirSync(bin, { recursive: true })
  // setpriv: drop the options, ignore the bootstrap path, exec the test gateway.
  writeFileSync(
    `${bin}/setpriv`,
    `#!/bin/sh\nexec ${process.env.PYTHON ?? 'python3'} "$OUT/gateway.py"\n`,
    { mode: 0o755 }
  )
  writeFileSync(
    `${bin}/setsid`,
    `#!/usr/bin/env python3\nimport os, sys\nos.setsid()\nos.execvp(sys.argv[1], sys.argv[1:])\n`,
    { mode: 0o755 }
  )
  return bin
}

function scenario(dir: string, body: string, env: Record<string, string> = {}) {
  const bin = stubs(dir)
  writeFileSync(`${dir}/gateway.py`, GATEWAY_PY)
  writeFileSync(`${dir}/daemon.py`, DAEMON_PY)
  writeFileSync(`${dir}/job.py`, JOB_PY)
  mkdirSync(`${dir}/run`, { recursive: true })
  const script = [
    'set -euo pipefail',
    `log() { echo "$*" >> "${dir}/log"; }`,
    `export OUT="${dir}" TMPDIR="${dir}" PATH="${bin}:$PATH"`,
    `MEDCHRON_RUN_DIR="${dir}/run"`,
    body,
  ].join('\n')
  writeFileSync(`${dir}/entry.sh`, script)
  // nosemgrep: javascript.lang.security.audit.dangerous-spawn-shell.dangerous-spawn-shell -- argv is bash plus a script this test wrote into its own mkdtemp dir; no input reaches it
  return spawn('bash', [`${dir}/entry.sh`], {
    detached: true, // its own process group, as tini's child is
    stdio: 'ignore',
    env: { ...process.env, ...env },
  })
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

async function until(pred: () => boolean, ms = 10000): Promise<void> {
  const end = Date.now() + ms
  while (!pred()) {
    if (Date.now() > end) throw new Error('timed out waiting')
    await sleep(25)
  }
}

function alive(pid: number): boolean {
  try {
    process.kill(pid, 0)
    return true
  } catch {
    return false
  }
}

/** Alive and not a zombie: a killed process its new parent has not reaped yet
 * still answers kill(pid, 0). Same test as the supervisor's own _smd_alive. */
function running(pid: number): boolean {
  if (!alive(pid)) return false
  const stat = spawnSync('ps', ['-o', 'stat=', '-p', String(pid)], { encoding: 'utf8' }).stdout
  return !stat.trim().startsWith('Z')
}

async function runStop(
  body: string,
  env: Record<string, string> = {},
  signalGroup = true
): Promise<Run> {
  const dir = mkdtempSync(join(tmpdir(), 'smd-supervisor-'))
  const child = scenario(dir, body, env)
  const exited = new Promise<number | null>((r) =>
    child.on('exit', (code, sig) => r(code ?? (sig ? 128 : null)))
  )
  await until(() => existsSync(`${dir}/gateway.json`) && existsSync(`${dir}/job-ready`))
  if (signalGroup) process.kill(-child.pid!, 'SIGTERM') // tini -g
  const code = await exited
  const events = existsSync(`${dir}/events`)
    ? readFileSync(`${dir}/events`, 'utf8')
        .trim()
        .split('\n')
        .map((l) => JSON.parse(l))
    : []
  const gateway = JSON.parse(readFileSync(`${dir}/gateway.json`, 'utf8'))
  return {
    dir,
    code,
    events,
    gateway,
    log: existsSync(`${dir}/log`) ? readFileSync(`${dir}/log`, 'utf8') : '',
  }
}

const NEW_SHAPE = (gatewayMode = 'drain') => `
export GATEWAY_MODE=${gatewayMode}
SMD_SUPERVISOR_DAEMON_WAIT_SECONDS=5 SMD_SUPERVISOR_GATEWAY_WAIT_SECONDS=1
export DROPPED_SECRET=leaks-if-the-fork-env-is-used
. "${SUPERVISOR}"
smd_supervisor_boot
( _mc_stop=""; trap '_mc_stop=1' TERM INT
  while true; do
    ${process.env.PYTHON ?? 'python3'} "$OUT/daemon.py" && rc=0 || rc=$?
    { [ "\${rc}" = 130 ] || [ -n "\${_mc_stop}" ]; } && break
  done ) &
SMD_MEDCHRON_LOOP_PID=$!
unset DROPPED_SECRET
export LATE_VAR=late
smd_supervise
`

const bashOk =
  spawnSync('bash', ['-c', 'command -v ps && command -v pkill && command -v python3'], {
    encoding: 'utf8',
  }).status === 0

describe.skipIf(!bashOk)('entrypoint supervisor: the Machine stop drains before it stops', () => {
  it('quiesces, drains the daemon, spares the job child, THEN stops the gateway and exits with its code', async () => {
    const r = await runStop(NEW_SHAPE())
    const order = r.events.map((e) => e.e)
    expect(r.code, r.log).toBe(0) // the gateway's own exit code
    expect(order.indexOf('daemon-term')).toBeGreaterThan(-1)
    expect(order.indexOf('daemon-exit')).toBeGreaterThan(order.indexOf('daemon-term'))
    expect(
      order.indexOf('gateway-term'),
      'the gateway is stopped only after the daemon drained'
    ).toBeGreaterThan(order.indexOf('daemon-exit'))
    expect(r.events.find((e) => e.e === 'gateway-term')?.daemon_exited).toBe(true)
    expect(order, 'a job child in its own session never sees the group signal').not.toContain(
      'job-term'
    )
    const jobPid = Number(readFileSync(`${r.dir}/job.pid`, 'utf8'))
    expect(alive(jobPid)).toBe(true)
    process.kill(jobPid, 'SIGKILL')
    const q = JSON.parse(readFileSync(`${r.dir}/run/quiesce`, 'utf8'))
    expect(q.by).toBe('machine-stop')
    expect(q.until).toBeGreaterThan(Date.now() / 1000 + 500)
    expect(r.log).toMatch(/medchron daemon drained/)
  }, 30000)

  it('runs the gateway as the held pid, in its own session, with the FINAL environment', async () => {
    const r = await runStop(NEW_SHAPE())
    const g = r.gateway!
    expect(String(g.pid)).toBe(g.env_pid) // SMD_GATEWAY_PID is the gateway itself (SO_PEERCRED)
    expect(g.sid).toBe(g.pid) // its own session: tini's group signal cannot reach it
    expect(g.late).toBe('late') // exported after the fork, delivered at release
    expect(g.dropped).toBeNull() // unset after the fork, never delivered
    process.kill(Number(readFileSync(`${r.dir}/job.pid`, 'utf8')), 'SIGKILL')
  }, 30000)

  it('the broker survives the group stop, outlives the gateway, then is killed with no extra wait', async () => {
    // 2026-10-20: the broker loop ignores TERM (entrypoint.sh) so the gateway
    // and the daemon drain can still record; the supervisor KILLs it once the
    // gateway has exited, so the stop takes no longer than before.
    const py = process.env.PYTHON ?? 'python3'
    const broker = `( trap '' TERM INT
  while true; do
    ${py} -c 'import os, time; open(os.environ["OUT"] + "/broker.pids", "a").write(f"{os.getpid()}\\n"); time.sleep(120)'
    sleep 0.2
  done ) &
SUPERVISOR_PID=$!
`
    const started = Date.now()
    const r = await runStop(NEW_SHAPE().replace('smd_supervise\n', `${broker}smd_supervise\n`))
    expect(r.code, r.log).toBe(0)
    const pids = readFileSync(`${r.dir}/broker.pids`, 'utf8').trim().split('\n').map(Number)
    expect(pids, 'one broker for the whole stop: the group signal did not kill it').toHaveLength(1)
    // SIGKILL is asynchronous, and the broker's parent loop dies in the same
    // kill, so the corpse is reaped by whoever inherits it. Probing the pid the
    // instant the supervisor exits raced that (red on main 2026-10-20). Wait on
    // the observable event, the process gone or a zombie, with a deadline far
    // below the 120 s the broker would otherwise sleep: a supervisor that never
    // kills it still fails here.
    await until(() => !running(pids[0]), 5000).catch(() => {
      throw new Error(`broker ${pids[0]} still running 5 s after the stop: never killed`)
    })
    expect(Date.now() - started).toBeLessThan(20000)
  }, 30000)

  it('exits with the gateway code when the gateway exits on its own (crash semantics kept)', async () => {
    const r = await runStop(NEW_SHAPE('crash'), {}, false)
    expect(r.code).toBe(7)
    expect(r.log).toMatch(/gateway exited on its own \(7\)/)
    process.kill(Number(readFileSync(`${r.dir}/job.pid`, 'utf8')), 'SIGKILL')
  }, 30000)

  it('SIGKILLs a gateway that outlives its stop window', async () => {
    const r = await runStop(NEW_SHAPE('deaf'))
    expect(r.code).toBe(137)
    expect(r.log).toMatch(/SIGKILL/)
    process.kill(Number(readFileSync(`${r.dir}/job.pid`, 'utf8')), 'SIGKILL')
  }, 30000)

  it('FALSIFIER: the pre-change shape (exec into the gateway, shared group) kills the job child at once', async () => {
    const r = await runStop(
      `
export OLD_SHAPE=1
( while true; do ${process.env.PYTHON ?? 'python3'} "$OUT/daemon.py" && rc=0 || rc=$?; [ "\${rc}" = 130 ] && break; done ) &
exec ${process.env.PYTHON ?? 'python3'} "$OUT/gateway.py"
`
    )
    const order = r.events.map((e) => e.e)
    expect(order).toContain('job-term')
    // and the gateway (the container's main process) stopped before the daemon drained
    expect(r.events.find((e) => e.e === 'gateway-term')?.daemon_exited).toBe(false)
  }, 30000)
})

describe.skipIf(!bashOk)('entrypoint supervisor: the held gateway and the quiesce file', () => {
  it('a boot that dies before the release execs no gateway', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'smd-supervisor-'))
    const child = scenario(
      dir,
      `. "${SUPERVISOR}"\nsmd_supervisor_boot\necho "$SMD_GATEWAY_PID" > "$OUT/held.pid"\nexit 3`
    )
    const code = await new Promise<number | null>((r) => child.on('exit', (c) => r(c)))
    expect(code).toBe(3) // the boot gates' refusal code passes through
    await sleep(500)
    expect(existsSync(`${dir}/gateway.json`)).toBe(false)
    const held = Number(readFileSync(`${dir}/held.pid`, 'utf8'))
    if (alive(held)) process.kill(held, 'SIGKILL') // blocked in open(); a real container dies with tini
  }, 20000)

  it('clears the previous Machine stop quiesce at boot and keeps anyone else’s', () => {
    for (const [by, kept] of [
      ['machine-stop', false],
      ['fleet:ops@laptop', true],
    ] as const) {
      const dir = mkdtempSync(join(tmpdir(), 'smd-quiesce-'))
      mkdirSync(`${dir}/run`)
      writeFileSync(`${dir}/run/quiesce`, `{"until": 9999999999, "by": "${by}", "reason": "x"}\n`)
      const r = spawnSync(
        'bash',
        [
          '-c',
          `set -euo pipefail\nlog() { :; }\nMEDCHRON_RUN_DIR="${dir}/run"\n. "${SUPERVISOR}"\nsmd_quiesce_clear_stale`,
        ],
        { encoding: 'utf8' }
      )
      expect(r.status, r.stderr).toBe(0)
      expect(existsSync(`${dir}/run/quiesce`)).toBe(kept)
    }
  })

  it('a root poller does not respawn on 130 or after the stop, and does after a crash', () => {
    const src = readFileSync(SUPERVISOR, 'utf8')
    const start = src.indexOf('smd_root_poller() {')
    const fn = src
      .slice(start, src.indexOf('\n}\n', start) + 3)
      .replace('/opt/hermes/.venv/bin/python -m "$1"', '"$STUB"')
      .replace('sleep 5', 'sleep 0')
    const run = (codes: number[]) => {
      const dir = mkdtempSync(join(tmpdir(), 'smd-poller-'))
      const stub = `${dir}/stub.sh`
      writeFileSync(
        stub,
        `#!/bin/bash\nn=$(cat "${dir}/n" 2>/dev/null || echo 0)\necho $((n+1)) > "${dir}/n"\ncodes=(${codes.join(' ')})\nexit \${codes[$n]:-130}\n`,
        { mode: 0o755 }
      )
      const r = spawnSync(
        'bash',
        ['-c', `set -e\nlog() { :; }\nSTUB="${stub}"\n${fn}\nsmd_root_poller x x\nwait`],
        {
          encoding: 'utf8',
          timeout: 10000,
        }
      )
      expect(r.status, r.stderr).toBe(0)
      return Number(readFileSync(`${dir}/n`, 'utf8').trim())
    }
    expect(run([130]), 'a stop is final').toBe(1)
    expect(run([1, 130]), 'a crash is respawned').toBe(2)
    expect(run([0, 1, 130]), 'a clean exit is respawned').toBe(3)
  })
})

describe.skipIf(!bashOk)('a broker launched under the loop survives tini -g', () => {
  async function brokerAfterGroupTerm(trap: string): Promise<boolean> {
    const dir = mkdtempSync(join(tmpdir(), 'smd-broker-term-'))
    const py = process.env.PYTHON ?? 'python3'
    const script = `
( ${trap}
  /usr/bin/env -i ${py} -c 'import os, time; open("${dir}/pid", "w").write(str(os.getpid())); time.sleep(30)' ) &
wait
`
    writeFileSync(`${dir}/loop.sh`, script)
    // nosemgrep: javascript.lang.security.audit.dangerous-spawn-shell.dangerous-spawn-shell -- argv is bash plus a script this test wrote into its own mkdtemp dir; no input reaches it
    const child = spawn('bash', [`${dir}/loop.sh`], { detached: true, stdio: 'ignore' })
    await until(() => existsSync(`${dir}/pid`) && readFileSync(`${dir}/pid`, 'utf8').length > 0)
    const brokerPid = Number(readFileSync(`${dir}/pid`, 'utf8'))
    process.kill(-child.pid!, 'SIGTERM') // tini -g
    await sleep(300)
    const survived = alive(brokerPid)
    for (const pid of [brokerPid, -child.pid!]) {
      try {
        process.kill(pid, 'SIGKILL')
      } catch {
        // already gone
      }
    }
    return survived
  }

  it('a group SIGTERM leaves an env -i python child of an ignoring subshell running', async () => {
    expect(await brokerAfterGroupTerm("trap '' TERM INT")).toBe(true)
  }, 15000)

  it('FALSIFIER: without the trap the same child dies with the group', async () => {
    expect(await brokerAfterGroupTerm(':')).toBe(false)
  }, 15000)
})

describe('the root audit-loss directory is made by root before the agent runs', () => {
  const src = readFileSync(SUPERVISOR, 'utf8')
  it('is the first thing smd_supervisor_boot does, and replaces anything that is not root’s', () => {
    // 2026-10-20 review: /opt/data is the agent's; a planted .smd-root (its own
    // dir or a symlink) is removed, never followed, and root's is created 0755.
    const boot = src.slice(src.indexOf('smd_supervisor_boot() {'))
    expect(boot.split('\n')[1].trim()).toBe('smd_root_loss_dir')
    expect(src).toMatch(
      /\[ -L "\$\{_d\}" \] \|\| \[ ! -d "\$\{_d\}" \] \|\| \[ "\$\(stat -c %u "\$\{_d\}"\)" != 0 \]/
    )
    expect(src).toContain('rm -rf -- "${_d}"')
    expect(src).toContain('install -d -o root -g root -m 0755 "${_d}"')
  })

  it('the boot chown of /opt/data leaves .smd-root alone, or root’s counts reset every boot', () => {
    const line = ENTRYPOINT.split('\n').find((l) => l.startsWith('find /opt/data')) ?? ''
    const pruned = /-o -path ("[^"]*\.smd-root") \\\) -prune/.exec(line)?.[1]
    const created = /local _d=("[^"]*")/.exec(readFileSync(SUPERVISOR, 'utf8'))?.[1]
    expect(pruned, 'the prune names .smd-root').toBeDefined()
    expect(created).toBeDefined()
    // Same path from the same variable, whatever HERMES_HOME is.
    for (const home of ['', '/opt/data', '/srv/elsewhere']) {
      const env: NodeJS.ProcessEnv = { ...process.env, HERMES_HOME: home }
      if (home === '') delete env.HERMES_HOME
      const sh = (expr: string) =>
        // nosemgrep: javascript.lang.security.audit.dangerous-spawn-shell.dangerous-spawn-shell -- expr is a quoted path expression read from this repo's own shell source by a closed regex; no external input reaches it
        spawnSync('bash', ['-c', `printf '%s' ${expr}`], { env, encoding: 'utf8' }).stdout
      expect(sh(pruned!), `HERMES_HOME=${home}`).toBe(sh(created!))
    }
  })
})

describe('entrypoint.sh wires the supervisor', () => {
  const code = ENTRYPOINT.split('\n')
    .filter((l) => !/^\s*#/.test(l))
    .join('\n')

  it('sources the supervisor and forks the held gateway before the broker starts', () => {
    const boot = code.indexOf('smd_supervisor_boot')
    expect(code).toContain('. /app/runners/supervisor/entrypoint-supervisor.sh')
    expect(boot).toBeGreaterThan(-1)
    expect(boot).toBeLessThan(code.indexOf('launch_broker() {'))
    expect(code).not.toMatch(/export SMD_GATEWAY_PID="\$\$"/)
  })

  it('records the daemon loop pid and ends in smd_supervise, never an exec into bootstrap', () => {
    expect(code).toMatch(/done \) &\n\s+SMD_MEDCHRON_LOOP_PID=\$!/)
    expect(code.trimEnd().endsWith('smd_supervise')).toBe(true)
    expect(code).not.toMatch(/exec\s+setpriv/)
  })

  it('launches the skills applier before the drop and the appliers through the 130-aware poller', () => {
    expect(code).toContain('smd_skills_applier')
    expect(code.indexOf('smd_skills_applier')).toBeLessThan(code.indexOf('\nsmd_supervise'))
    expect(code).toContain('smd_root_poller config_applier "config applier"')
    expect(code).toContain('smd_root_poller spec_applier "spec applier"')
    expect(code).not.toMatch(/-m (config|spec)_applier \|\| true/)
  })

  it('the broker respawn loop ignores the group stop signal, so the broker outlives the gateway', () => {
    // 2026-10-20: tini -g signals this group at a Machine stop; the broker has
    // no handler and died at once while the gateway and the medchron daemon
    // drained for minutes with nothing to record to.
    expect(code).toMatch(/\( trap '' TERM INT\n\s+while true; do\n\s+if launch_broker; then/)
  })

  it('keeps every boot gate before the release', () => {
    const release = code.lastIndexOf('\nsmd_supervise')
    for (const gate of [
      'INVARIANT7_BOOT_CHECK=',
      'SPEC_OWNERSHIP_CHECK=',
      'unset AGENTMAIL_SEND_API_KEY',
    ]) {
      expect(code.indexOf(gate), gate).toBeGreaterThan(-1)
      expect(code.indexOf(gate), gate).toBeLessThan(release)
    }
  })
})

/**
 * bootstrap.sh: skills delivered as a release. When the root skills applier
 * installed /var/lib/smd-skills/current, the volume is seeded from it by the
 * overlay's skills_applier.sync; with no release, no module (an image from an
 * older overlay), or a failed sync, the image-catalog seed below runs as before.
 * The block is cut at its markers and run with the paths and python stubbed.
 */
describe('bootstrap.sh: the skills release seed and its fallback', () => {
  const src = readFileSync(resolve('operator/templates/bootstrap.sh'), 'utf8')
  const begin = src.indexOf('# skills-release-seed:begin')
  const end = src.indexOf('# skills-release-seed:end')

  const run = (opts: { release: boolean; module: boolean; syncRc: number }) => {
    const dir = mkdtempSync(join(tmpdir(), 'smd-skills-seed-'))
    const release = `${dir}/smd-skills/current`
    if (opts.release) mkdirSync(`${dir}/smd-skills/v7`, { recursive: true })
    if (opts.release) spawnSync('ln', ['-s', `${dir}/smd-skills/v7`, release])
    const py = `${dir}/python3`
    writeFileSync(
      py,
      `#!/bin/bash\nif [ "$1" = -c ]; then exit ${opts.module ? 0 : 1}; fi\necho "$@" > "${dir}/sync-args"\nexit ${opts.syncRc}\n`,
      { mode: 0o755 }
    )
    const block = src
      .slice(begin, end)
      .replace('_skills_release=/var/lib/smd-skills/current', `_skills_release="${release}"`)
      .replaceAll('/opt/hermes/.venv/bin/python3', py)
    const r = spawnSync(
      'bash',
      [
        '-c',
        `set -euo pipefail\nlog() { echo "$*"; }\nHERMES_HOME="${dir}/home"\nCUSTOMER_YAML="${dir}/customer.yaml"\n${block}\necho "FROM_RELEASE=\${_skills_from_release}"`,
      ],
      { encoding: 'utf8' }
    )
    expect(r.status, r.stderr).toBe(0)
    const args = existsSync(`${dir}/sync-args`) ? readFileSync(`${dir}/sync-args`, 'utf8') : ''
    return { fromRelease: /FROM_RELEASE=1/.test(r.stdout), args, dir, out: r.stdout }
  }

  it('has its markers, before the image seed', () => {
    expect(begin).toBeGreaterThan(-1)
    expect(end).toBeGreaterThan(begin)
    expect(end).toBeLessThan(src.indexOf('# retired-skill-prune:begin'))
  })

  it('seeds from the installed release through skills_applier.sync with the agreed flags', () => {
    const r = run({ release: true, module: true, syncRc: 0 })
    expect(r.fromRelease).toBe(true)
    expect(r.args).toContain('-m skills_applier.sync')
    expect(r.args).toContain(`--dest ${r.dir}/home/skills`)
    expect(r.args).toContain('--retired-file /app/retired-skills.txt')
    expect(r.args).toContain(`--enabled-skills-from ${r.dir}/customer.yaml`)
    // the resolved version dir's skills/, never the symlink; plus the release's own retired list
    expect(r.args).toMatch(/--source \S*smd-skills\/v7\/skills /)
    expect(r.args).toMatch(/--retired-file \S*smd-skills\/v7\/retired\.txt /)
  })

  it('falls back to the image seed when there is no release, no module, or a failed sync', () => {
    expect(run({ release: false, module: true, syncRc: 0 }).fromRelease).toBe(false)
    expect(run({ release: true, module: false, syncRc: 0 }).fromRelease).toBe(false)
    const failed = run({ release: true, module: true, syncRc: 1 })
    expect(failed.fromRelease).toBe(false)
    expect(failed.out).toMatch(/seeding from the image catalog instead/)
  })

  it('skips the image seed and both image prunes only when the release seeded', () => {
    const tail = src.slice(end)
    expect(tail).toMatch(
      /if \[ "\$\{_skills_from_release\}" = 1 \]; then\n\s+:[^\n]*\nelif \[ -d \/app\/skills \]; then/
    )
    expect(tail).toMatch(
      /if \[ "\$\{_skills_from_release\}" != 1 \]; then\n# retired-skill-prune:begin/
    )
    expect(tail).toMatch(/# omitted-skill-prune:end\nfi /)
  })
})
