/**
 * Regression guard: the authored-spec tree is delivered ROOT-OWNED, and the
 * Machine refuses to boot if it is not (ss ADR 0083, #2084).
 *
 * WHY A GUARD RATHER THAN A COMMENT. An authored spec enters the drafting
 * context by being READ, and `read_file` is READ-class: unfenced, always
 * allowed, and it does not taint the session. A spec the hermes uid could WRITE
 * would therefore be a persistent, untainted, self-authored instruction channel
 * that survives restarts — strictly worse than a tainted inbound email, which at
 * least fences the turn it arrives on. This repo has already paid for the general
 * form once: the keystone comment at the top of entrypoint.sh records the
 * self-loopback proven live on hermes-smd-staging 2026-06-15, where an
 * agent-writable customer.yaml let one `sed` flip external_send from
 * draft_for_review to autonomous. The fix was root ownership. These assertions
 * lock that shape so a future edit cannot quietly move the spec tree onto the
 * agent-writable volume "for convenience".
 *
 * @see operator/templates/entrypoint.sh
 * @see operator/safety_substrate/invariants/spec_dir_ownership.py
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'
import { parse as parseYaml } from 'yaml'

const ENTRYPOINT = readFileSync(resolve('operator/templates/entrypoint.sh'), 'utf8')

// Strip `#`-comment lines so assertions match executable content only — the
// rationale comments deliberately name the failure mode and its keywords.
const CODE = ENTRYPOINT.split('\n')
  .filter((l) => !/^\s*#/.test(l))
  .join('\n')

describe('Operator entrypoint — authored-spec tree delivery', () => {
  it('places the spec dir under the ROOT-owned config dir, never on /opt/data', () => {
    expect(CODE).toMatch(/SPEC_DIR="\$\{CONFIG_DIR\}\/specs"/)
    expect(CODE).toMatch(/export SMD_SPEC_DIR="\$\{SPEC_DIR\}"/)
    // /opt/data is the agent-writable Fly volume — the keystone hole's address.
    expect(/SPEC_DIR="?\/opt\/data/.test(CODE)).toBe(false)
  })

  it('owns the spec dir root:root with 0755 dirs and 0644 files', () => {
    // The asymmetry IS the security property: the agent must read these (an
    // unread spec fails its send gate) and must never write them.
    expect(CODE).toMatch(/chown root:root "\$\{SPEC_DIR\}"/)
    expect(CODE).toMatch(/chmod 0755 "\$\{SPEC_DIR\}"/)
    expect(CODE).toMatch(/find "\$\{SPEC_DIR\}" -type d -exec chmod 0755/)
    expect(CODE).toMatch(/find "\$\{SPEC_DIR\}" -type f -exec chmod 0644/)
  })

  it('runs the boot fetch synchronously BEFORE the privilege drop', () => {
    const fetchIdx = CODE.indexOf('-m spec_applier --once')
    const dropIdx = CODE.search(/exec\s+setpriv[\s\S]*?--reuid=hermes/)
    expect(fetchIdx, 'boot fetch must be present').toBeGreaterThan(-1)
    expect(dropIdx, 'hermes exec-drop must be present').toBeGreaterThan(-1)
    // bootstrap.sh runs translate.py right after the drop, and translate renders
    // each profile's SKILL.md spec pointer from the installed manifest. Fetch
    // after the drop would stamp nothing on a first boot.
    expect(fetchIdx).toBeLessThan(dropIdx)
  })

  it('forks the spec poller as a supervised root child before the drop', () => {
    expect(CODE).toMatch(/while\s+true;\s*do[\s\S]*?-m spec_applier[\s\S]*?done\s*\)\s*&/)
    const forkIdx = CODE.indexOf('python -m spec_applier || true')
    const dropIdx = CODE.search(/exec\s+setpriv[\s\S]*?--reuid=hermes/)
    expect(forkIdx).toBeGreaterThan(-1)
    expect(forkIdx).toBeLessThan(dropIdx)
  })

  it('never lets a missing or refused spec object brick the boot', () => {
    // Fail-static: an un-adoptable spec costs the seat its update, never its
    // uptime. The runtime gate — not the boot — decides whether an output whose
    // declared spec never arrived may be produced.
    const fetchBlock = CODE.slice(
      CODE.indexOf('-m spec_applier --once') - 200,
      CODE.indexOf('-m spec_applier --once') + 400
    )
    expect(fetchBlock).toMatch(/WARN/)
    expect(/spec_applier --once[\s\S]{0,200}?exit 1/.test(CODE)).toBe(false)
  })
})

describe('Operator entrypoint — spec-dir ownership boot gate', () => {
  it('refuses to boot when the ownership check module is missing', () => {
    // Fail-closed on a degraded substrate, matching the invariant_7 gate: a
    // missing or unimportable module is itself a refusal, never a silent skip.
    expect(CODE).toMatch(/SPEC_OWNERSHIP_CHECK="[^"]*spec_dir_ownership\.py"/)
    expect(CODE).toMatch(/if \[ ! -f "\$\{SPEC_OWNERSHIP_CHECK\}" \][\s\S]{0,300}?exit 3/)
  })

  it('refuses to boot when the check itself fails', () => {
    expect(CODE).toMatch(
      // PYTHONPATH=/app: the check imports by package name (packaging stage 4).
      /if PYTHONPATH=\/app \/opt\/hermes\/\.venv\/bin\/python3 "\$\{SPEC_OWNERSHIP_CHECK\}"[\s\S]{0,600}?exit 3/
    )
  })

  it('runs the gate before the exec-drop, after the spec tree is installed', () => {
    const installIdx = CODE.indexOf('-m spec_applier --once')
    const gateIdx = CODE.indexOf('SPEC_OWNERSHIP_CHECK="')
    const dropIdx = CODE.search(/exec\s+setpriv[\s\S]*?--reuid=hermes/)
    expect(installIdx).toBeLessThan(gateIdx)
    expect(gateIdx).toBeLessThan(dropIdx)
  })
})

describe('Operator contracts — SMD_SPEC_DIR is declared', () => {
  const envContract = parseYaml(
    readFileSync(resolve('operator/contracts/env-consumption.yaml'), 'utf8')
  ) as { vars: Record<string, Record<string, string>> }

  it('declares SMD_SPEC_DIR as an agent-stage var that is HELD, not stripped', () => {
    const entry = envContract.vars['SMD_SPEC_DIR']
    expect(entry, 'SMD_SPEC_DIR must be declared').toBeTruthy()
    expect(entry.stage).toBe('agent')
    // The headline env invariant: a var the agent reads may never be stripped.
    // The trust plugin's read-mark and translate.py's pointer stamp both read it.
    expect(entry.agent_env).toBe('held')
    expect(entry.custody).toBe('infra')
  })
})

describe('Operator contracts — the spec controls are registered', () => {
  const registry = parseYaml(
    readFileSync(resolve('operator/contracts/runtime-controls.yaml'), 'utf8')
  ) as { controls: Record<string, Record<string, string>> }

  it('registers the spec applier and the read mark', () => {
    expect(registry.controls['spec_applier']).toBeTruthy()
    expect(registry.controls['spec_read_mark']).toBeTruthy()
  })

  it('declares an honest status with an owner and a tracking item', () => {
    // Honest status is the registry's whole purpose, and honesty cuts both
    // ways: claiming `enforced` without a probe would make this file another
    // instance of the class it exists to expose, and holding `unprobed` after
    // a control has demonstrably fired is the same lie in the other direction.
    //
    // spec_applier is still unprobed — nothing yet demonstrates on a live seat
    // that a published spec arrives, that a rejected one is refused, or that
    // the tree survives a restart.
    //
    // spec_read_mark is ENFORCED as of 2026-08-10, on an observation nobody
    // wanted: it fired on every autonomous staff send on pilot-smokeball from
    // 08-04 to 08-09 (SPEC_GATE_TRIGGERED, reason spec_not_read, output_class
    // staff — ss#2228, vfy_01KZP6JY5481272X5GZZDW5XT7). Six days of a control
    // working exactly as written, doing the wrong thing.
    const expected: Record<string, string> = {
      spec_applier: 'unprobed',
      spec_read_mark: 'enforced',
    }
    for (const [name, status] of Object.entries(expected)) {
      const entry = registry.controls[name]
      expect(entry.status).toBe(status)
      expect(entry.owner).toBeTruthy()
      expect(entry.tracking).toBe('#2084')
      expect(entry.note).toBeTruthy()
    }
  })
})
