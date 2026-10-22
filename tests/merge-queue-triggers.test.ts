/**
 * Main merges through a merge queue (2026-10-21). The queue re-runs every
 * REQUIRED check on the `merge_group` event; a required check whose workflow
 * has no `merge_group` trigger never reports there, and every queued PR waits
 * at "Expected" until the queue times it out. Nothing merges.
 *
 * Falsifier: delete the `merge_group:` block from any workflow below and its
 * case goes red. Adding a required check means adding its workflow here.
 */

import { readdirSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { parse } from 'yaml'

/** Required context -> the workflow that reports it. */
const REQUIRED: Record<string, string> = {
  'Typecheck, Lint, Format, Test': 'verify.yml',
  'Security Summary': 'security.yml',
  substrate: 'operator-substrate.yml',
  'Runtime ACs carry a verification ID': 'runtime-ac-proof.yml',
}

function workflow(file: string): string {
  return readFileSync(resolve(__dirname, '../.github/workflows', file), 'utf8')
}

/** The top-level `on:` block, up to the next top-level key. */
function onBlock(text: string): string {
  const match = text.match(/^on:\n([\s\S]*?)^\S/m)
  return match ? match[1] : ''
}

describe('workflows load at all', () => {
  // A workflow GitHub cannot parse runs on no event, so a required check in it
  // never reports, on a PR or in the queue: every merge freezes. On 2026-10-21
  // an unquoted `run: echo "Queue run: ..."` (a colon inside a plain scalar)
  // made runtime-ac-proof.yml invalid; a text match on the trigger passed it.
  const dir = resolve(__dirname, '../.github/workflows')
  for (const file of readdirSync(dir).filter((f) => /\.ya?ml$/.test(f))) {
    it(`${file} parses as YAML`, () => {
      expect(() => parse(readFileSync(resolve(dir, file), 'utf8'))).not.toThrow()
    })
  }
})

describe('merge queue: every required check reports on merge_group', () => {
  for (const [context, file] of Object.entries(REQUIRED)) {
    it(`${context} (${file})`, () => {
      const text = workflow(file)
      expect(text).toContain(context === 'substrate' ? 'substrate:' : `name: ${context}`)
      expect(onBlock(text)).toMatch(/^ {2}merge_group:\n {4}types: \[checks_requested\]/m)
    })
  }

  it('the Semgrep baseline has a merge_group case (no baseline = a full audit that blocks the queue)', () => {
    const text = workflow('security.yml')
    expect(text).toContain('QUEUE_BASE_SHA: ${{ github.event.merge_group.base_sha }}')
    expect(text).toMatch(/merge_group\)\n\s+baseline=\$\(git merge-base HEAD "\$QUEUE_BASE_SHA"/)
  })

  it('the runtime-AC gate reads the PR body only on pull_request, and passes a queue run', () => {
    const text = workflow('runtime-ac-proof.yml')
    expect(text).toMatch(/if: github\.event_name == 'merge_group'/)
    expect(text).toMatch(
      /name: Inspect the AC status table[^\n]*\n\s+if: github\.event_name == 'pull_request'/
    )
  })
})
