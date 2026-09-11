/**
 * The send-render contract, read the way the arming gate needs it.
 *
 * `operator/contracts/send-render.yaml` declares, per skill, HOW a routine's
 * outbound body is produced: `templated` / `slot-templated` (deterministic
 * render from an authored artifact, hash-verified by the console instrument)
 * or `compositional` (the model composes; covered by cross-run invariants).
 *
 * THE ARMING RULE this module computes: a `personas[].cron[]` entry whose
 * skill is declared `outbound: derived` in output-classes.yaml must be
 * declared in send-render.yaml, and a declaration in a hash-verified mode must
 * name a template that exists. `compositional` is a valid authored state, not
 * a bypass -- the gate refuses the UNDECLARED, the state where a routine is
 * armed to send with nobody having decided how its body comes to exist (the
 * 2026-08-24..31 outbound-quality review: format drift, unstable ACK codes,
 * recipient flapping are all the model re-composing what nobody declared).
 *
 * Two consumers, one join (deliberately -- two hand-rolled copies of a
 * three-file join drift silently):
 *   - tests/cron-send-arming-gate.test.ts, the merge gate (runs in
 *     `npm run verify`, so the exact PR that arms a cron row runs it);
 *   - scripts/validate-customer-yaml.ts, the provision-time backstop, so
 *     `provision-customer.sh` refuses an armed-undeclared seat even if
 *     someone pushes around CI.
 *
 * Not the schema validator (sections-bundles-cron.ts): that one is
 * deliberately pure over one document, and this rule spans three files.
 */

import { parse as parseYaml } from 'yaml'

import { findFloorTrigger } from './floor-triggers'

export type RenderMode = 'templated' | 'slot-templated' | 'compositional'

const RENDER_MODES: readonly RenderMode[] = ['templated', 'slot-templated', 'compositional']

/** Modes whose bodies are hash-verified and therefore need a template artifact. */
const HASH_VERIFIED_MODES: readonly RenderMode[] = ['templated', 'slot-templated']

export interface RenderDecl {
  skill: string
  render: RenderMode
  /** Repo-relative template path; null for `compositional`. */
  template: string | null
}

/**
 * Parse send-render.yaml. Parsed, never grepped; malformed THROWS -- a gate
 * that soldiers past a broken contract file evaluates against an empty
 * declaration set and calls every armed routine undeclared (or worse, none).
 */
export function parseSendRender(yamlText: string): Map<string, RenderDecl> {
  const parsed: unknown = parseYaml(yamlText)
  const skills = (parsed as { skills?: unknown } | null)?.skills
  if (typeof skills !== 'object' || skills === null || Array.isArray(skills)) {
    throw new Error('send-render.yaml: expected a mapping with a `skills` mapping')
  }
  const out = new Map<string, RenderDecl>()
  for (const [skill, entry] of Object.entries(skills as Record<string, unknown>)) {
    if (typeof entry !== 'object' || entry === null) {
      throw new Error(`send-render.yaml: skills.${skill} must be a mapping`)
    }
    const render = (entry as { render?: unknown }).render
    if (typeof render !== 'string' || !(RENDER_MODES as readonly string[]).includes(render)) {
      throw new Error(
        `send-render.yaml: skills.${skill}.render must be one of ${RENDER_MODES.join(' | ')}`
      )
    }
    const template = (entry as { template?: unknown }).template
    if (
      (HASH_VERIFIED_MODES as readonly string[]).includes(render) &&
      (typeof template !== 'string' || template.length === 0)
    ) {
      throw new Error(
        `send-render.yaml: skills.${skill} declares render: ${render} but names no template`
      )
    }
    out.set(skill, {
      skill,
      render: render as RenderMode,
      template: typeof template === 'string' ? template : null,
    })
  }
  return out
}

/**
 * The `skill_bindings.<skill>.outbound` column of output-classes.yaml
 * (`derived` | `none`), the list CI already keeps honest
 * (test_output_class_conformance.py). Malformed THROWS for the same reason as
 * above.
 */
export function parseOutboundBindings(yamlText: string): Map<string, string> {
  const parsed: unknown = parseYaml(yamlText)
  const bindings = (parsed as { skill_bindings?: unknown } | null)?.skill_bindings
  if (typeof bindings !== 'object' || bindings === null || Array.isArray(bindings)) {
    throw new Error('output-classes.yaml: expected a `skill_bindings` mapping')
  }
  const out = new Map<string, string>()
  for (const [skill, entry] of Object.entries(bindings as Record<string, unknown>)) {
    const outbound = (entry as { outbound?: unknown } | null)?.outbound
    out.set(skill, typeof outbound === 'string' ? outbound : '')
  }
  return out
}

export interface ArmingViolation {
  seat: string
  skill: string
  code: 'undeclared-render' | 'missing-template' | 'unknown-outbound' | 'floor-trigger-name'
  message: string
}

/**
 * The skills whose outbound bodies close with the rendered signature block --
 * the shared-chase-voice derivation set (_shared-chase-voice.md "Salutation
 * and signature"). A seat arming any of these renders `customer_name` (or the
 * persona `signature:` override) verbatim into outbound mail, so the name
 * itself must clear the ADR 0031 content floor (see floor-triggers.ts).
 */
export const CHASE_SIGNATURE_SKILLS: readonly string[] = [
  'client-verification-tracker',
  'medical-records-chaser',
  'lien-ledger-tracker',
  'discovery-response-tracker',
]

/**
 * A `customer_name` carrying a floor-trigger word, on a seat whose cron arms a
 * chase-signature skill: every autonomous chase from that seat would render
 * the name into its sign-off and be silently held as a draft by the runtime
 * floor (PR #2651 review, finding 3). Refused where authored, with the word
 * named. Seats arming no chase-signature skill are untouched -- their name
 * reaches no chase body.
 */
export function customerNameFloorViolations(input: {
  seat: string
  customerName: string
  cron: readonly { skill: string }[]
  /**
   * True when an authored `personas[].signature.firm_line` exists (itself
   * floor-checked by the schema validator): the override is what renders, so
   * the display name never reaches a chase body and is not gated.
   */
  firmLineAuthored?: boolean
}): ArmingViolation[] {
  if (input.firmLineAuthored === true) return []
  const armedChase = input.cron
    .map((row) => row.skill)
    .filter((skill) => CHASE_SIGNATURE_SKILLS.includes(skill))
  if (armedChase.length === 0) return []
  const trigger = findFloorTrigger(input.customerName)
  if (trigger === null) return []
  return [
    {
      seat: input.seat,
      skill: armedChase[0],
      code: 'floor-trigger-name',
      message:
        `${input.seat}: customer_name "${input.customerName}" contains "${trigger}", a ` +
        'content-floor trigger word (ADR 0031), and this seat arms chase skills ' +
        `(${armedChase.join(', ')}) whose signature block renders the name into every ` +
        'outbound chase body -- the floor would hold each one as a draft. Author a ' +
        'floor-clean display name, or author personas[].signature.firm_line without ' +
        'the word (see _shared-chase-voice.md).',
    },
  ]
}

export interface ArmingInput {
  seat: string
  /** The seat's cron rows (all personas, already schema-validated upstream). */
  cron: readonly { skill: string }[]
  outbound: Map<string, string>
  renders: Map<string, RenderDecl>
  templateExists: (path: string) => boolean
}

/**
 * The three-file join. Only `outbound: derived` skills are gated: an
 * `outbound: none` routine sends nothing by construction, and gating it would
 * force declarations about bodies that cannot exist.
 */
export function armingViolations(input: ArmingInput): ArmingViolation[] {
  const violations: ArmingViolation[] = []
  const seen = new Set<string>()
  for (const row of input.cron) {
    if (seen.has(row.skill)) continue
    seen.add(row.skill)
    if (!input.outbound.has(row.skill)) {
      violations.push({
        seat: input.seat,
        skill: row.skill,
        code: 'unknown-outbound',
        message:
          `${input.seat}: cron arms "${row.skill}" but output-classes.yaml has no ` +
          'skill_bindings entry for it, so whether it sends cannot be evaluated ' +
          '(cannot-evaluate must never read as permitted)',
      })
      continue
    }
    if (input.outbound.get(row.skill) !== 'derived') continue
    const decl = input.renders.get(row.skill)
    if (decl === undefined) {
      violations.push({
        seat: input.seat,
        skill: row.skill,
        code: 'undeclared-render',
        message:
          `${input.seat}: "${row.skill}" is cron-armed and outbound: derived but ` +
          'operator/contracts/send-render.yaml does not declare it -- armed to send ' +
          'with no authored render declaration (declare it templated, slot-templated, ' +
          'or explicitly compositional)',
      })
      continue
    }
    if (
      (HASH_VERIFIED_MODES as readonly string[]).includes(decl.render) &&
      (decl.template === null || !input.templateExists(decl.template))
    ) {
      violations.push({
        seat: input.seat,
        skill: row.skill,
        code: 'missing-template',
        message:
          `${input.seat}: "${row.skill}" is declared render: ${decl.render} but its ` +
          `template artifact (${decl.template ?? 'unnamed'}) does not exist on disk`,
      })
    }
  }
  return violations
}

/**
 * Contract hygiene, independent of any seat: every hash-verified declaration's
 * template must exist. Checked by the merge gate so a template deletion cannot
 * strand a declaration.
 *
 * @public Merge-gate check. tests/cron-send-arming-gate.test.ts imports it and runs it over
 * the shipped declarations. No runtime caller, by design.
 */
export function templateHygieneViolations(
  renders: Map<string, RenderDecl>,
  templateExists: (path: string) => boolean
): ArmingViolation[] {
  const violations: ArmingViolation[] = []
  for (const decl of renders.values()) {
    if (!(HASH_VERIFIED_MODES as readonly string[]).includes(decl.render)) continue
    if (decl.template === null || !templateExists(decl.template)) {
      violations.push({
        seat: '(contract)',
        skill: decl.skill,
        code: 'missing-template',
        message:
          `send-render.yaml declares "${decl.skill}" render: ${decl.render} but its ` +
          `template artifact (${decl.template ?? 'unnamed'}) does not exist on disk`,
      })
    }
  }
  return violations
}
