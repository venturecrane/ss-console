#!/usr/bin/env tsx
/**
 * Standalone customer.yaml validator wrapper.
 *
 * Replaces the retired `operator/adapter/validate_customer_yaml.py`
 * with an invocation of the canonical TS validator at
 * `src/lib/operator/customer-yaml/` (per ADR 0019: TS is the canonical
 * pre-merge gate; the overlay's bootstrap/validate.py is the runtime
 * re-check; the in-tree Python copy was on a stale schema and is gone).
 *
 * Usage:
 *   npx tsx scripts/validate-customer-yaml.ts <path-to-customer.yaml>
 *
 * Exit codes:
 *   0 — valid
 *   1 — schema validation errors OR a bound skill with no deployable body
 *   2 — file not readable / not parseable YAML
 *
 * Called by `operator/bin/provision-customer.sh` before any Fly action.
 */

import { existsSync, readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parse as parseYaml } from 'yaml'

import { validate } from '../src/lib/operator/customer-yaml'
import {
  armingViolations,
  customerNameFloorViolations,
  parseOutboundBindings,
  parseSendRender,
} from '../src/lib/operator/send-render'

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')

function fail(code: number, message: string): never {
  process.stderr.write(`${message}\n`)
  process.exit(code)
}

/**
 * Every enabled `personas[].skills[]` name, deduped. The runtime pin-resolver
 * (overlay `translate._resolve_skill_pins`) hard-fails boot when a bound skill
 * has no body on the volume, so a missing body is a boot crash-loop (#1206).
 */
function boundSkillNames(value: unknown): string[] {
  const names = new Set<string>()
  const personas = (value as { personas?: unknown }).personas
  if (!Array.isArray(personas)) return []
  for (const persona of personas) {
    const skills = (persona as { skills?: unknown }).skills
    if (!Array.isArray(skills)) continue
    for (const entry of skills) {
      const skill = entry as { name?: unknown; enabled?: unknown }
      if (skill.enabled === true && typeof skill.name === 'string' && skill.name.length > 0) {
        names.add(skill.name)
      }
    }
  }
  return [...names]
}

/**
 * A bound skill is deployable if its body exists either in the shared repo
 * catalog (`operator/skills/<name>/`, baked into the image) or in the
 * customer-local catalog (`<customer.yaml dir>/skills/<name>/`). Mirrors the
 * two lookup locations in `operator/adapter/resolve_skill_pins.py`.
 */
function skillBodyExists(name: string, customerYamlPath: string): boolean {
  const repoSkill = join(REPO_ROOT, 'operator', 'skills', name, 'SKILL.md')
  const customerSkill = join(dirname(customerYamlPath), 'skills', name, 'SKILL.md')
  return existsSync(repoSkill) || existsSync(customerSkill)
}

const argPath = process.argv[2]
if (!argPath) {
  fail(2, 'Usage: validate-customer-yaml.ts <path-to-customer.yaml>')
}

const filePath = resolve(argPath)
let raw: string
try {
  raw = readFileSync(filePath, 'utf8')
} catch (err) {
  fail(2, `cannot read ${filePath}: ${(err as Error).message}`)
}

let parsed: unknown
try {
  parsed = parseYaml(raw)
} catch (err) {
  fail(2, `${filePath} is not valid YAML: ${(err as Error).message}`)
}

const result = validate(parsed)

if (!result.ok) {
  process.stderr.write(`customer.yaml has ${result.errors.length} validation error(s):\n`)
  for (const e of result.errors) {
    process.stderr.write(`  [${e.code}] ${e.path}: ${e.message}\n`)
  }
  process.exit(1)
}

// Author-time guardrail (#1206): catch a bound-but-undeployed skill HERE, at the
// pre-provision gate, instead of as a Machine boot crash-loop. Schema-valid is
// not deploy-safe — `personas[].skills[]` only names a skill; the body must also
// exist in a catalog that reaches the volume.
const missingSkillBodies = boundSkillNames(parsed).filter(
  (name) => !skillBodyExists(name, filePath)
)
if (missingSkillBodies.length > 0) {
  process.stderr.write(
    `customer.yaml binds ${missingSkillBodies.length} skill(s) with no deployable body — ` +
      `each would crash-loop the Machine at boot (#1206):\n`
  )
  for (const name of missingSkillBodies) {
    process.stderr.write(
      `  [skill-body-missing] ${name}: no operator/skills/${name}/SKILL.md ` +
        `(and no customer-local skills/${name}/SKILL.md)\n`
    )
  }
  process.exit(1)
}

// Provision-time arming backstop (outbound-quality track): a cron-armed
// derived-outbound skill must carry a send-render declaration. The merge gate
// (tests/cron-send-arming-gate.test.ts) is primary; this is what makes
// `provision-customer.sh` refuse an armed-undeclared seat even if someone
// pushes around CI. Same join, one implementation (src/lib/operator/send-render.ts).
const seatSlug = result.value.customer_id
const cronRows = result.value.personas.flatMap((persona) =>
  persona.cron.map((row) => ({ skill: row.skill }))
)
if (cronRows.length > 0) {
  const outbound = parseOutboundBindings(
    readFileSync(join(REPO_ROOT, 'operator', 'contracts', 'output-classes.yaml'), 'utf8')
  )
  const renders = parseSendRender(
    readFileSync(join(REPO_ROOT, 'operator', 'contracts', 'send-render.yaml'), 'utf8')
  )
  const violations = armingViolations({
    seat: seatSlug,
    cron: cronRows,
    outbound,
    renders,
    templateExists: (path) => existsSync(join(REPO_ROOT, path)),
  })
  // The floor half (PR #2651 review finding 3): a chase-arming seat whose
  // display name carries a content-floor trigger would have every autonomous
  // chase held as a draft; refused here too so provisioning cannot land it.
  violations.push(
    ...customerNameFloorViolations({
      seat: seatSlug,
      customerName: result.value.customer_name,
      cron: cronRows,
      firmLineAuthored: result.value.personas.some(
        (persona) => typeof persona.signature?.firm_line === 'string'
      ),
    })
  )
  if (violations.length > 0) {
    process.stderr.write(
      `customer.yaml arms ${violations.length} routine(s) to send with no authored ` +
        `render declaration (operator/contracts/send-render.yaml):\n`
    )
    for (const violation of violations) {
      process.stderr.write(`  [${violation.code}] ${violation.message}\n`)
    }
    process.exit(1)
  }
}

process.stdout.write(`${filePath} OK\n`)
process.exit(0)
