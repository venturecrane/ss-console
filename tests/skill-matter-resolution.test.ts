import { readFileSync, readdirSync, existsSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

/**
 * Every skill that tells the Operator to write on a matter also tells it how
 * to open that write (2026-11-04, the model-as-logic audit, mechanism 2).
 *
 * The Smokeball connector refuses a create or render on a matter the model
 * chose unless the call carries a `matter_resolution` minted for that matter
 * (operator_connector_sdk.resolution, declared per tool at registration). A
 * skill that names such a tool and never names a resolver sends the model to a
 * write it cannot make, and in enforce mode the firm's work stops at a refusal
 * nobody wrote down a way past. This test is grep-driven, the shape of
 * tests/forbidden-strings.test.ts: the gated set is read from the connector's
 * own conformance pin (EXPECTED_RESOLUTION_GATED), so a tool gated tomorrow is
 * checked here the day it is gated, with no second list to keep in step.
 */

const SKILLS = 'operator/skills'
const CONFORMANCE = 'operator/connectors/smokeball/tests/test_conformance.py'
const RESOLVERS = /\b(resolve_matter_by_id|resolve_invoice_matter)\b/
/** The two writes that check a resolution in their own bodies (not declared). */
const SELF_CHECKING = ['stage_vendor_invoice', 'file_attachment_pages_to_matter']

function gatedTools(): string[] {
  const source = readFileSync(CONFORMANCE, 'utf8')
  const block = /EXPECTED_RESOLUTION_GATED: set\[str\] = \{([\s\S]*?)\n\}/.exec(source)
  if (!block) throw new Error(`${CONFORMANCE}: EXPECTED_RESOLUTION_GATED not found`)
  const names = [...block[1].matchAll(/"([a-z0-9_]+)"/g)].map((m) => m[1])
  return [...names, ...SELF_CHECKING]
}

/** The whole file: a write named anywhere in it, frontmatter included, needs its door. */
function body(path: string): string {
  return readFileSync(path, 'utf8')
}

const skillFiles = readdirSync(SKILLS)
  .map((name) => `${SKILLS}/${name}/SKILL.md`)
  .filter((path) => existsSync(path))

describe('skills that write on a matter name the resolver that opens the write', () => {
  const gated = gatedTools()
  const naming = new RegExp(`\\b(${gated.join('|')})\\b`)

  it('reads the gated set from the connector pin', () => {
    expect(gated).toContain('create_task')
    expect(gated).toContain('create_memo')
    expect(gated).toContain('render_firm_form_letter')
    expect(gated.length).toBeGreaterThanOrEqual(19)
  })

  it('finds the skills that name a gated write', () => {
    const writers = skillFiles.filter((path) => naming.test(body(path)))
    expect(writers.length).toBeGreaterThan(30)
  })

  for (const path of skillFiles) {
    const text = body(path)
    const tool = naming.exec(text)?.[1]
    if (!tool) continue
    it(`${path} names ${tool} and a resolver`, () => {
      expect(RESOLVERS.test(text), `${path} names ${tool} but neither resolver`).toBe(true)
    })
  }

  it('the router runs the conflict check as the one tool call', () => {
    const router = readFileSync(`${SKILLS}/matter-inbox-router/SKILL.md`, 'utf8')
    expect(router).toMatch(/one `conflict_check` call/)
    expect(router).not.toMatch(/create the ONE fixed-shape clearance task \(`create_task`\)/)
  })
})
