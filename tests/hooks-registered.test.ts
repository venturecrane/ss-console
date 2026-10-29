/**
 * Every hook script in .claude/hooks/ is registered in .claude/settings.json,
 * or is named here with the reason it is reached another way.
 *
 * Why (code review 2026-09-10, Architecture 15): knip declares
 * `.claude/hooks/*.mjs` as entry files, so a hook that stops being registered
 * is invisible to every gate in the repo. An unregistered guard is a guard
 * that silently stopped enforcing, which is the failure that matters here,
 * so registration is pinned by test rather than by convention.
 *
 * The exempt set is pruned by its own test: an entry whose file is gone, or
 * whose file became registered after all, fails.
 */

import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, existsSync } from 'node:fs'
import { resolve } from 'node:path'

const HOOKS_DIR = resolve('.claude/hooks')
const SETTINGS = resolve('.claude/settings.json')

/** Hook scripts that are deliberately not wired as hooks, with the reason. */
const EXEMPT: Record<string, string> = {
  'trap-recurrence.mjs':
    'a Captain CLI, invoked through .claude/bin/trap-recurrence to answer the memory traps review; not a harness hook',
  'memory-recurrence.mjs':
    'a CLI, invoked through .claude/bin/memory-recurrence by /eos to say whether a triggered memory recurred; not a harness hook',
  'situational-recall.mjs':
    'run by reflex-primer.sh (UserPromptSubmit) with the payload it already captured, so the memory line rides the one primer hook',
}

function hookScripts(): string[] {
  return readdirSync(HOOKS_DIR)
    .filter((name) => /\.(mjs|sh)$/.test(name))
    .sort()
}

function registered(settingsText: string, name: string): boolean {
  return settingsText.includes(`.claude/hooks/${name}`)
}

describe('.claude/hooks registration', () => {
  const settingsText = readFileSync(SETTINGS, 'utf8')

  it('every hook script is registered in settings.json or exempt with a reason', () => {
    const unregistered = hookScripts().filter(
      (name) => !registered(settingsText, name) && !(name in EXEMPT)
    )
    expect(unregistered).toEqual([])
  })

  it('every exempt entry still exists, carries a reason, and is not also registered', () => {
    for (const [name, reason] of Object.entries(EXEMPT)) {
      expect(existsSync(resolve(HOOKS_DIR, name)), `${name} listed as exempt but missing`).toBe(
        true
      )
      expect(reason.length, `${name} needs a reason`).toBeGreaterThan(20)
      expect(registered(settingsText, name), `${name} is registered; drop it from EXEMPT`).toBe(
        false
      )
    }
  })

  it('the exempt CLI is reachable through the wrapper it names', () => {
    const wrapper = readFileSync(resolve('.claude/bin/trap-recurrence'), 'utf8')
    expect(wrapper).toContain('hooks/trap-recurrence.mjs')
  })

  it('the deciding guards block when they cannot run (onFailure: "block")', () => {
    // Without it, a guard that fails to start or times out lets the tool call
    // through, so "could not evaluate" reads as "permitted". Each script still
    // fails open on errors it catches itself; this covers only the cases where
    // the script never got to decide.
    const settings = JSON.parse(settingsText) as {
      hooks: Record<string, { hooks: { command?: string; onFailure?: string }[] }[]>
    }
    const handlers = Object.values(settings.hooks).flatMap((groups) =>
      groups.flatMap((g) => g.hooks)
    )
    for (const name of ['worktree-guard.mjs', 'engagement-guard.mjs', 'plan-premise-gate.mjs']) {
      const handler = handlers.find((h) => h.command?.includes(`.claude/hooks/${name}`))
      expect(handler, `${name} not registered`).toBeDefined()
      expect(handler?.onFailure, `${name} must set onFailure: "block"`).toBe('block')
    }
  })

  it('the scan sees the hooks that matter', () => {
    const names = hookScripts()
    expect(names).toContain('worktree-guard.mjs')
    expect(names).toContain('reflex-primer.sh')
    expect(names.length).toBeGreaterThanOrEqual(10)
  })
})
