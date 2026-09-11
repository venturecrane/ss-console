/**
 * Regression guard: the customer-Machine entrypoint must launch the Workspace
 * broker under a ROOT respawn supervisor (OP-P1-4 follow-up).
 *
 * The broker is the second principal that BOTH the Google capability path AND the
 * append-only audit_append path depend on. If it dies mid-run with no supervisor,
 * audit silently fails OPEN and Google capability stops — with no signal. Only a
 * root process can re-`setpriv` a fresh broker to uid workspace-broker, so the
 * supervisor MUST be forked while the entrypoint is still root, before it
 * exec-drops to the hermes gateway.
 *
 * These assertions lock that shape so a future edit can't quietly regress the
 * broker back to an un-supervised one-shot background launch.
 *
 * @see operator/templates/entrypoint.sh
 * @see docs/security/operator-threat-model.md  (§9 — WS5 / OP-P1-4)
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

const ENTRYPOINT = readFileSync(resolve('operator/templates/entrypoint.sh'), 'utf8')

// Strip `#`-comment lines so assertions match executable content only — the
// supervisor's rationale comments deliberately NAME the failure mode.
const ENTRYPOINT_CODE = ENTRYPOINT.split('\n')
  .filter((l) => !/^\s*#/.test(l))
  .join('\n')

describe('Operator customer Machine entrypoint — broker respawn supervisor', () => {
  it('defines the broker launch once, as a reusable function', () => {
    expect(ENTRYPOINT_CODE).toMatch(/launch_broker\(\)\s*\{/)
    // The broker module is referenced exactly once (inside the function), so the
    // supervisor is the sole launch site — no parallel un-supervised copy.
    const hits = ENTRYPOINT_CODE.match(/-m\s+workspace_broker\.server/g) ?? []
    expect(hits.length, 'broker should be launched from exactly one site').toBe(1)
  })

  it('runs the broker under a backgrounded while-true respawn loop', () => {
    // A subshell loop that re-invokes launch_broker forever, backgrounded so the
    // parent can proceed to the socket-wait and the exec-drop.
    expect(ENTRYPOINT_CODE).toMatch(/while\s+true;?\s*do[\s\S]*?launch_broker[\s\S]*?done\s*\)\s*&/)
    expect(ENTRYPOINT_CODE).toMatch(/SUPERVISOR_PID=\$!/)
  })

  it('never launches the broker as an un-supervised one-shot background job', () => {
    // The exact regression this guards: `... workspace_broker.server &` outside
    // the supervised loop, which can't be respawned after death.
    expect(
      /workspace_broker\.server\s*&/.test(ENTRYPOINT_CODE),
      'broker must not be backgrounded directly; only the supervisor subshell is backgrounded'
    ).toBe(false)
  })

  it('forks the supervisor while still root, BEFORE dropping to the hermes gateway', () => {
    const supervisorIdx = ENTRYPOINT_CODE.indexOf('SUPERVISOR_PID=$!')
    const hermesExec = ENTRYPOINT_CODE.search(/exec\s+setpriv[\s\S]*?--reuid=hermes/)
    expect(supervisorIdx, 'supervisor must be present').toBeGreaterThan(-1)
    expect(hermesExec, 'hermes exec-drop must be present').toBeGreaterThan(-1)
    expect(
      supervisorIdx < hermesExec,
      'the supervisor must be forked before the exec-drop to hermes (else it would not be root)'
    ).toBe(true)
  })

  it('preserves the gateway PID into each respawn (SO_PEERCRED gate survives)', () => {
    // The broker admits only the gateway PID; respawns must carry the same
    // SMD_GATEWAY_PID (the entrypoint PID, preserved across the exec).
    expect(ENTRYPOINT_CODE).toMatch(/SMD_GATEWAY_PID=["']?\$\{SMD_GATEWAY_PID\}/)
  })

  it('tears down the supervisor (not a stale broker PID) if the socket never appears', () => {
    expect(ENTRYPOINT_CODE).toMatch(/kill\s+"\$\{SUPERVISOR_PID\}"/)
    // The old BROKER_PID variable is gone — the supervised loop owns the lifecycle.
    expect(/BROKER_PID=/.test(ENTRYPOINT_CODE)).toBe(false)
  })
})

/**
 * Regression guard: the ADR 0085 establishment spool and root intake launch
 * (ss#2161/#2162).
 *
 * The spool is the trust boundary an admin-instructed voice/shape submission
 * crosses: broker-written staging/runs, root-written results, hermes denied at
 * every level. Every directory must be EXPLICITLY owned and moded — a
 * default-moded ancestor silently widens the whole tree (the spec_applier
 * _harden_ancestors incident). The intake launch must be import-gated so a
 * lagging overlay pin degrades to a loud "not launched" line, never a broken
 * boot.
 */
describe('Operator customer Machine entrypoint — ADR 0085 establishment spool + intake', () => {
  it('creates every spool directory with an explicit owner and mode (no mkdir -p defaults)', () => {
    expect(ENTRYPOINT_CODE).toMatch(
      /install -d -o root -g workspace-broker -m 0750 "\$\{ESTABLISH_SPOOL_DIR\}"/
    )
    for (const child of ['staging', 'runs', 'results']) {
      expect(ENTRYPOINT_CODE).toMatch(
        new RegExp(
          `install -d -o root -g workspace-broker -m 0770 "\\$\\{ESTABLISH_SPOOL_DIR\\}/${child}"`
        )
      )
    }
    // The spool must never be created by a bare mkdir, whose mode is umask luck.
    expect(/mkdir[^\n]*establish-spool/.test(ENTRYPOINT_CODE)).toBe(false)
  })

  it('places the spool OUTSIDE the agent home, which goes 0700 mid-boot', () => {
    // The Hermes gateway chmods /opt/data to 0700 after the entrypoint has
    // granted its group-traverse, so a spool under that tree is reachable by
    // root (every health signal looks fine) and unreachable by the
    // workspace-broker uid that must create staging sets and run dirs. The
    // spool's own dirs are correct at 0770; the ANCESTOR severs them.
    // Live-caught on hermes-pilot-smokeball 2026-08-02, first establishment
    // call: PermissionError on .../establish-spool/staging. Falsifier: this
    // test fails on the pre-fix /opt/data path.
    const m = ENTRYPOINT_CODE.match(/ESTABLISH_SPOOL_DIR="([^"]+)"/)
    expect(m, 'ESTABLISH_SPOOL_DIR must be set').not.toBeNull()
    expect(m![1].startsWith('/opt/data')).toBe(false)
    expect(m![1]).toBe('/var/lib/smd-establish-spool')
  })

  it('exports the spool path and carries it into the broker env allowlist', () => {
    expect(ENTRYPOINT_CODE).toMatch(/export SMD_ESTABLISH_SPOOL_DIR="\$\{ESTABLISH_SPOOL_DIR\}"/)
    // launch_broker runs env -i with a fixed allowlist; the spool var must be
    // named there or the broker boots establishment-disabled forever.
    const launchFn = ENTRYPOINT_CODE.slice(
      ENTRYPOINT_CODE.indexOf('launch_broker()'),
      ENTRYPOINT_CODE.indexOf('-m workspace_broker.server')
    )
    expect(launchFn).toMatch(/SMD_ESTABLISH_SPOOL_DIR="\$\{SMD_ESTABLISH_SPOOL_DIR\}"/)
  })

  it('launches the root intake under an import-gated respawn loop, before the exec-drop', () => {
    // Gated: a lagging overlay (no establish_intake module) must degrade to
    // "not launched", never a broken boot.
    expect(ENTRYPOINT_CODE).toMatch(/python -c "import establish_intake"/)
    expect(ENTRYPOINT_CODE).toMatch(
      /while\s+true;?\s*do[\s\S]*?-m establish_intake[\s\S]*?done\s*\)\s*&/
    )
    const launchIdx = ENTRYPOINT_CODE.indexOf('-m establish_intake')
    const hermesExec = ENTRYPOINT_CODE.search(/exec\s+setpriv[\s\S]*?--reuid=hermes/)
    expect(launchIdx).toBeGreaterThan(-1)
    expect(
      launchIdx < hermesExec,
      'the intake must be forked while still root (it holds the R2 write credential)'
    ).toBe(true)
  })

  it('says so loudly when the intake is NOT launched', () => {
    expect(ENTRYPOINT_CODE).toMatch(/Root establishment intake NOT launched/)
  })
})

/**
 * ss#2614 (routine 11): the chronology runner daemon. Same invariants as the
 * appliers — forked while still root, under an import-gated respawn loop, and
 * loud when it is not launched — plus the tree it depends on: a root-owned
 * queue the broker uid can write, a root-only jobs dir, both reached through a
 * bind mount (the /opt/data mid-boot chmod severs a child dir's group
 * traverse, the audit-ledger lesson), the queue path carried into the broker's
 * env -i allowlist, and the firm config fetched from the vault fail-static.
 */
describe('entrypoint.sh: the chronology runner daemon (ss#2614)', () => {
  it('converges the queue and jobs dirs with explicit owners and modes, then binds them', () => {
    expect(ENTRYPOINT_CODE).toMatch(
      /install -d -o root -g workspace-broker -m 0770 "\$\{MEDCHRON_DATA_DIR\}\/queue"/
    )
    expect(ENTRYPOINT_CODE).toMatch(
      // 0710 group medchron: the driver child traverses to its own job dir but
      // cannot list or open siblings (live-caught 2026-08-31 at 0700).
      /install -d -o root -g medchron -m 0710 "\$\{MEDCHRON_DATA_DIR\}\/jobs"/
    )
    expect(ENTRYPOINT_CODE).toMatch(
      /mount --bind "\$\{MEDCHRON_DATA_DIR\}" "\$\{MEDCHRON_RUN_DIR\}"/
    )
    // Pruned from the hermes chown sweep like the audit subtree.
    expect(ENTRYPOINT_CODE).toMatch(/-o -path "\$\{MEDCHRON_DATA_DIR\}" \\\) -prune/)
  })

  it('carries the queue path into the broker env allowlist', () => {
    const launchFn = ENTRYPOINT_CODE.slice(
      ENTRYPOINT_CODE.indexOf('launch_broker()'),
      ENTRYPOINT_CODE.indexOf('-m workspace_broker.server')
    )
    expect(launchFn).toMatch(/SMD_MEDCHRON_QUEUE_DIR="\$\{SMD_MEDCHRON_QUEUE_DIR\}"/)
  })

  it('fetches the firm config from the vault without a FATAL arm and fences it to root:medchron', () => {
    expect(ENTRYPOINT_CODE).toMatch(/vaults\/\$\{CUSTOMER_SLUG\}\/medchron-firm\.yaml/)
    expect(ENTRYPOINT_CODE).toMatch(/chown root:medchron "\$\{MEDCHRON_FIRM_CONFIG\}"/)
    expect(ENTRYPOINT_CODE).toMatch(/chmod 0640 "\$\{MEDCHRON_FIRM_CONFIG\}"/)
    expect(ENTRYPOINT_CODE).toMatch(/the chronology runner will refuse jobs/)
  })

  // 2026-09-04: the classifier's controls and the ICD tables are INSTALL-level
  // (a job's data_root is fresh per job and can never carry them), so the
  // entrypoint seeds them from the vault on every boot, fail-static like the
  // firm config, into a root-owned tree the medchron uid can read and not
  // write. Boot smoke asserts the result on the seat; this pins the shape.
  it('seeds the medchron controls tree from the vault on every boot, root-owned and medchron-readable, without a FATAL arm', () => {
    expect(ENTRYPOINT_CODE).toMatch(/MEDCHRON_CONTROLS_DIR="\$\{MEDCHRON_DATA_DIR\}\/controls"/)
    expect(ENTRYPOINT_CODE).toMatch(
      /aws s3 cp[\s\S]*?--recursive[\s\S]*?"s3:\/\/\$\{R2_BUCKET_CONFIG\}\/vaults\/\$\{CUSTOMER_SLUG\}\/medchron-controls\/"/
    )
    // The swap is gated on the authored index arriving: an empty prefix or a
    // partial copy never replaces a good tree.
    expect(ENTRYPOINT_CODE).toMatch(
      /&& \[ -f "\$\{MEDCHRON_CONTROLS_DIR\}\.r2\.tmp\/controls\.json" \]; then/
    )
    expect(ENTRYPOINT_CODE).toMatch(/chown -R root:medchron "\$\{MEDCHRON_CONTROLS_DIR\}"/)
    expect(ENTRYPOINT_CODE).toMatch(
      /find "\$\{MEDCHRON_CONTROLS_DIR\}" -type d -exec chmod 0750 \{\} \+/
    )
    expect(ENTRYPOINT_CODE).toMatch(
      /find "\$\{MEDCHRON_CONTROLS_DIR\}" -type f -exec chmod 0640 \{\} \+/
    )
    expect(ENTRYPOINT_CODE).toMatch(
      /the chronology runner will refuse the classify stage of every job/
    )
    // Seeded AFTER the data dir is established and BEFORE the daemon forks.
    const seedIdx = ENTRYPOINT_CODE.indexOf('medchron-controls/')
    expect(seedIdx).toBeGreaterThan(
      ENTRYPOINT_CODE.indexOf('install -d -o root -g root -m 0755 "${MEDCHRON_DATA_DIR}"')
    )
    expect(seedIdx).toBeLessThan(ENTRYPOINT_CODE.indexOf('-m medchron.daemon'))
    // No FATAL arm anywhere in the block.
    const block = ENTRYPOINT_CODE.slice(
      seedIdx,
      ENTRYPOINT_CODE.indexOf('SMOKEBALL_TOKEN_DIR="/opt/data/.smokeball-mcp"')
    )
    expect(block).not.toMatch(/FATAL/)
  })

  it('launches the daemon from its own venv under an import-gated respawn loop, before the exec-drop', () => {
    expect(ENTRYPOINT_CODE).toMatch(
      /\/opt\/medchron\/\.venv\/bin\/python -c "import medchron\.daemon"/
    )
    expect(ENTRYPOINT_CODE).toMatch(
      /while\s+true;?\s*do[\s\S]*?-m medchron\.daemon[\s\S]*?done\s*\)\s*&/
    )
    const launchIdx = ENTRYPOINT_CODE.indexOf('-m medchron.daemon')
    const hermesExec = ENTRYPOINT_CODE.search(/exec\s+setpriv[\s\S]*?--reuid=hermes/)
    expect(launchIdx).toBeGreaterThan(-1)
    expect(launchIdx < hermesExec, 'the daemon must be forked while still root').toBe(true)
    expect(ENTRYPOINT_CODE).toMatch(/Root medchron daemon NOT launched/)
  })

  it('shares the Smokeball token with the medchron uid through a setgid dir, never by widening the file to world', () => {
    expect(ENTRYPOINT_CODE).toMatch(/chmod 2770 "\$\{SMOKEBALL_TOKEN_DIR\}"/)
    expect(ENTRYPOINT_CODE).toMatch(/chmod 0660 "\$\{SMOKEBALL_TOKEN_DIR\}\/refresh_token"/)
    expect(ENTRYPOINT_CODE).not.toMatch(
      /chmod 0?66[4-7] "\$\{SMOKEBALL_TOKEN_DIR\}\/refresh_token"/
    )
  })
})

/**
 * Regression guard: the entrypoint must run the ADR 0009 cross-machine
 * isolation boot check (invariant_7 `verify_at_boot`) before it hands off to
 * the agent runtime, and must fail closed — including when the invariant module
 * itself is missing or unimportable (SEC-22).
 *
 * @see operator/safety-substrate/invariants/invariant_7.py
 * @see docs/adr/0009-cross-machine-query-prohibition.md
 */
describe('Operator customer Machine entrypoint — sftp staging directory', () => {
  it('creates the staging dir hermes-owned, with an explicit mode', () => {
    // `fly ssh sftp put` runs as ROOT. If the directory does not exist, the
    // first push creates it root-owned, and hermes then cannot unlink there
    // (unlink permission comes from the DIRECTORY, not the file), so every
    // transfer copy stays until someone runs a root op. Live-caught on
    // hermes-ashton-price 2026-08-26: root:root 0755, 18 files, 157 MiB of
    // client medical material, `PermissionError` on a hermes write probe.
    expect(ENTRYPOINT_CODE).toMatch(/STAGE_DIR="\/opt\/data\/tmp-deliverable"/)
    expect(ENTRYPOINT_CODE).toMatch(/install -d -o hermes -g hermes -m 0700 "\$\{STAGE_DIR\}"/)
    // Never a bare mkdir, whose mode is umask luck.
    expect(/mkdir[^\n]*tmp-deliverable/.test(ENTRYPOINT_CODE)).toBe(false)
  })

  it('creates it AFTER the chown sweep, so the sweep cannot be relied on instead', () => {
    // The boot sweep re-owns everything under /opt/data, which is why this
    // looked self-healing and was not: the sweep runs at boot, and the
    // directory is created by a push that happens later. Ordering it after
    // the sweep makes the establishment unconditional rather than incidental.
    // Falsifier: this fails if the install line is moved above the sweep.
    const sweep = ENTRYPOINT_CODE.indexOf('-prune -o -print0 | xargs -0 -r chown hermes:hermes')
    const stage = ENTRYPOINT_CODE.indexOf('install -d -o hermes -g hermes -m 0700 "${STAGE_DIR}"')
    expect(sweep, 'chown sweep must be present').toBeGreaterThan(-1)
    expect(stage, 'staging dir establishment must be present').toBeGreaterThan(-1)
    expect(stage).toBeGreaterThan(sweep)
  })
})

describe('Operator customer Machine entrypoint — ADR 0009 / SEC-22 isolation boot check', () => {
  it('invokes the invariant_7 boot check', () => {
    expect(ENTRYPOINT_CODE).toMatch(/safety-substrate\/invariants\/invariant_7\.py/)
    // Runs the module directly (its __main__ shim == verify_at_boot).
    expect(ENTRYPOINT_CODE).toMatch(/python3\s+"\$\{INVARIANT7_BOOT_CHECK\}"/)
  })

  it('fails closed on a missing invariant module (degraded substrate is a refusal, not a skip)', () => {
    // `[ ! -f "${INVARIANT7_BOOT_CHECK}" ]` -> exit 3, before the check can run.
    expect(ENTRYPOINT_CODE).toMatch(
      /\[\s*!\s+-f\s+"\$\{INVARIANT7_BOOT_CHECK\}"\s*\][\s\S]*?exit 3/
    )
  })

  it('refuses the boot (exit 3) on any non-zero check result — violation OR import error', () => {
    // The if/else around the python invocation must exit 3 in the else branch,
    // so an unimportable module (non-zero exit, no verify_at_boot pass) also
    // fails closed rather than falling through to the exec-drop.
    expect(ENTRYPOINT_CODE).toMatch(
      /if\s+\/opt\/hermes\/\.venv\/bin\/python3\s+"\$\{INVARIANT7_BOOT_CHECK\}";\s*then[\s\S]*?else[\s\S]*?exit 3[\s\S]*?fi/
    )
  })

  it('runs the boot check BEFORE the exec-drop to the hermes gateway', () => {
    const checkIdx = ENTRYPOINT_CODE.indexOf('INVARIANT7_BOOT_CHECK=')
    const hermesExec = ENTRYPOINT_CODE.search(/exec\s+setpriv[\s\S]*?--reuid=hermes/)
    expect(checkIdx, 'the boot check must be present').toBeGreaterThan(-1)
    expect(hermesExec, 'hermes exec-drop must be present').toBeGreaterThan(-1)
    expect(
      checkIdx < hermesExec,
      'isolation must be verified before the Machine serves any agent turn'
    ).toBe(true)
  })
})

/**
 * Regression guard: the root-side gateway liveness supervisor (P0 ss#2488).
 *
 * On 2026-08-20 the paying client's seat wedged for 33 minutes. Hermes' own
 * loop-liveness watchdog logged "...exiting with code 75 so the service
 * supervisor can restart it" and then did not exit — and there was no service
 * supervisor to restart it: entrypoint EXECS the gateway as the container's main
 * process, the respawner above covers the BROKER, and Fly does not restart a
 * Machine on a failing health check.
 *
 * These are SHAPE locks only. The state machine itself — arming, the recovery
 * re-check, the kill ledger, profile resolution — is driven for real in
 * operator/templates/tests/test_gateway_liveness_supervisor.py, which extracts
 * this block and runs it. A grep-only guard would have passed against the very
 * defect that opened the issue: a recovery path that logs its intent and then
 * never performs it.
 *
 * @see operator/templates/entrypoint.sh
 * @see operator/templates/tests/test_gateway_liveness_supervisor.py
 * @see docs/runbooks/operator/incidents/2026-08-20-gateway-wedge-no-restart.md
 */
describe('Operator customer Machine entrypoint — gateway liveness supervisor (ss#2488)', () => {
  it('runs the watcher in a backgrounded while-true subshell', () => {
    expect(ENTRYPOINT_CODE).toMatch(
      /\(\s*set \+e[\s\S]*?while\s+true;?\s*do[\s\S]*?gateway_liveness_escalate[\s\S]*?done\s*\)\s*&/
    )
  })

  it('disables the INHERITED errexit inside the subshell', () => {
    // entrypoint.sh runs under `set -euo pipefail` and a backgrounded subshell
    // inherits it, so ONE failed probe — a heartbeat read mid-replace, a `kill`
    // on a pid that just died — would end the supervisor for the life of the
    // container, silently, leaving the seat with no recovery at all. The broker
    // loop above pays for the same hazard with an `if` guard; this one turns
    // errexit off outright, because nearly every line in it is a probe that is
    // allowed to fail.
    const block = ENTRYPOINT_CODE.slice(
      ENTRYPOINT_CODE.indexOf('gateway_heartbeat_path()'),
      ENTRYPOINT_CODE.indexOf('Root gateway liveness supervisor launched')
    )
    expect(block.length, 'supervisor block must be present').toBeGreaterThan(0)
    expect(block).toMatch(/\(\s*\n\s*set \+e/)
  })

  it('forks the supervisor while still root, BEFORE the exec-drop to hermes', () => {
    const supervisorIdx = ENTRYPOINT_CODE.indexOf('gateway_liveness_escalate()')
    const hermesExec = ENTRYPOINT_CODE.search(/exec\s+setpriv[\s\S]*?--reuid=hermes/)
    expect(supervisorIdx, 'supervisor must be present').toBeGreaterThan(-1)
    expect(
      supervisorIdx < hermesExec,
      'only a root process forked before the exec-drop can signal the gateway'
    ).toBe(true)
  })

  it('is forked AFTER SMD_GATEWAY_PID is exported (its kill target)', () => {
    const pidExport = ENTRYPOINT_CODE.indexOf('export SMD_GATEWAY_PID=')
    const supervisorIdx = ENTRYPOINT_CODE.indexOf('gateway_heartbeat_path()')
    expect(pidExport).toBeGreaterThan(-1)
    expect(pidExport < supervisorIdx).toBe(true)
  })

  it('escalates SIGTERM before SIGKILL, and kills the container main process', () => {
    // SIGTERM does nothing for a genuinely wedged loop — its handler would have
    // to run ON that loop. It is there for the false-positive case, where it
    // buys a clean shutdown (audit WAL flushed) instead of a hard kill.
    expect(ENTRYPOINT_CODE).toMatch(/kill -TERM "\$\{SMD_GATEWAY_PID\}"/)
    expect(ENTRYPOINT_CODE).toMatch(/kill -KILL "\$\{SMD_GATEWAY_PID\}"/)
    const term = ENTRYPOINT_CODE.indexOf('kill -TERM "${SMD_GATEWAY_PID}"')
    const hard = ENTRYPOINT_CODE.indexOf('kill -KILL "${SMD_GATEWAY_PID}"')
    expect(term < hard, 'SIGTERM must precede SIGKILL').toBe(true)
  })

  it('bounds restarts with a ledger on the VOLUME, so a flapping seat stops', () => {
    // The ledger has to survive the restart it records, or it bounds nothing.
    expect(ENTRYPOINT_CODE).toMatch(/GATEWAY_LIVENESS_LEDGER_DIR="\/opt\/data\//)
    expect(ENTRYPOINT_CODE).toMatch(/gateway_liveness_kill_budget_ok/)
    expect(ENTRYPOINT_CODE).toMatch(/REFUSING to restart/)
  })

  it('never arms on a heartbeat it has not seen fresh this boot', () => {
    // /opt/data persists, so a beat from the PREVIOUS boot is on disk at every
    // cold start. Arming on it would SIGKILL the container on every boot.
    expect(ENTRYPOINT_CODE).toMatch(/armed=0/)
    expect(ENTRYPOINT_CODE).toMatch(/NOT arming/)
  })

  it('gates the SIGUSR2 stack dump on the handler actually being registered', () => {
    // SIGUSR2's default disposition is TERMINATE. If a future pin drops
    // faulthandler.register, an unguarded send stops being a diagnostic and
    // becomes an unlogged kill that skips the recovery re-check and the ledger.
    expect(ENTRYPOINT_CODE).toMatch(/grep -q 'faulthandler\.register'/)
  })

  it('resolves the profile from the gateway argv, never by mtime ordering', () => {
    // A seat may carry several persona homes (ADR 0011); only one is the
    // gateway's. "Newest mtime" identifies it only while it is healthy — the
    // exact assumption that stops holding in the scenario this watches for.
    expect(ENTRYPOINT_CODE).toMatch(
      /\$\{GATEWAY_LIVENESS_PROC_DIR\}\/\$\{SMD_GATEWAY_PID\}\/cmdline/
    )
    expect(
      /ls\s+-1t[\s\S]{0,120}gateway\.heartbeat/.test(ENTRYPOINT_CODE),
      'must not pick the heartbeat by mtime'
    ).toBe(false)
  })

  it('proves its own liveness with a per-iteration tick, not a pid file', () => {
    // boot-smoke-test.sh Step 11 already records why: a check that confirmed a
    // process EXISTED rather than that it WORKED is how the 2026-07-16
    // scheduler outage ran eight days green.
    expect(ENTRYPOINT_CODE).toMatch(/touch "\$\{GATEWAY_LIVENESS_RUN_DIR\}\/tick"/)
    expect(
      /supervisor\.pid/.test(ENTRYPOINT_CODE),
      'a pid file would prove a number was written once, not that the loop turns'
    ).toBe(false)
  })

  it('publishes its state machine as one word the webhook gate can read (part 2)', () => {
    // The gate is the hermes uid and the one process that survives a wedge; it
    // ships these on the control-plane heartbeat so a refusing or never-armed
    // supervisor reaches an inbox instead of only `fly logs`. Read-only for the
    // agent is the whole security property: root-owned dirs with no group/other
    // write bit, so the agent can neither forge, edit nor unlink a line.
    expect(ENTRYPOINT_CODE).toMatch(
      /install -d -o root -g root -m 0755 "\$\{GATEWAY_LIVENESS_RUN_DIR\}"/
    )
    expect(ENTRYPOINT_CODE).toMatch(
      /install -d -o root -g root -m 0755 "\$\{GATEWAY_LIVENESS_LEDGER_DIR\}"/
    )
    expect(
      /install -d -o root -g root -m 0700 "\$\{GATEWAY_LIVENESS_(RUN|LEDGER)_DIR\}"/.test(
        ENTRYPOINT_CODE
      ),
      'a 0700 dir would make every gate-derived heartbeat field NULL forever, and NULL is a hold'
    ).toBe(false)
    for (const word of ['not-armed', 'armed', 'inert', 'not-watching', 'refusing']) {
      expect(ENTRYPOINT_CODE, `state transition "${word}" must be written`).toMatch(
        new RegExp(`gateway_liveness_state ${word}\\b`)
      )
    }
  })
})
