/**
 * What a seat is for, console-side: the capabilities a customer.yaml authors,
 * and the blocks that belong to one (ADR 0100).
 *
 * There is one Operator. A seat is the core every Operator has plus the
 * capabilities its engagement authored, and "authored" is DERIVED from config
 * the seat already carries:
 *
 * - a job lane is authored when a persona enables the lane's skill;
 * - a connector capability is authored when an enabled connector binds one of
 *   its backends.
 *
 * This file MIRRORS operator/hermes-smd-overlay/shared/capabilities.py, the one
 * source boot, the gateway, translate and the release controller ask. The
 * tables and the predicate are pinned to the Python on every repo seat by
 * tests/customer-yaml-capabilities.test.ts, which runs the Python itself.
 *
 * BLOCK OWNERSHIP. A config block whose only readers are one capability's code
 * belongs to that capability (operator/contracts/customer-yaml-blocks.yaml,
 * `owner:`). A seat that authors such a block without authoring its owner is
 * refused: the values would sit in the file read by nothing, and the reader
 * the author believed in is not running. Blocks read by core code stay core
 * and are never listed here. Nothing reads `vertical`, `seat.product` or a
 * slug.
 */

import type { ValidationError } from './types'
import { isPlainObject } from './helpers'

/** Job lane -> the skill whose being enabled authors it (capabilities.py JOB_LANES). */
export const JOB_LANES = {
  medchron: 'medical-chronology-maintainer',
  demand: 'demand-letter-drafter',
  drafting: 'document-drafter',
  litigation: 'litigation-status',
  casefile: 'case-file-manager',
  negotiation: 'negotiation-watch',
  note: 'smd-note',
} as const satisfies Record<string, string>
export type JobLane = keyof typeof JOB_LANES

/** Connector capability -> the backends that author it (capabilities.py CONNECTOR_BACKENDS). */
export const CONNECTOR_BACKENDS: Record<string, readonly string[]> = {
  smokeball: ['mcp:smokeball'],
}

/**
 * Top-level block -> the capability whose code is its only reader. Pinned to
 * the registry's `owner:` fields by tests/customer-yaml-capabilities.test.ts.
 */
export const BLOCK_OWNERS: Record<string, string> = {
  firm_identity: 'smokeball',
  vendor_invoice_intake: 'smokeball',
  combined_post_intake: 'smokeball',
  smokeball_layouts: 'smokeball',
  form_letters: 'smokeball',
  records_orders: 'smokeball',
}

/** Skill names any persona enables (`enabled` absent counts as enabled). */
export function enabledSkills(root: Record<string, unknown>): Set<string> {
  const names = new Set<string>()
  const personas = root['personas']
  if (!Array.isArray(personas)) return names
  for (const persona of personas) {
    if (!isPlainObject(persona) || !Array.isArray(persona['skills'])) continue
    for (const skill of persona['skills']) {
      if (isPlainObject(skill) && skill['enabled'] !== false && typeof skill['name'] === 'string') {
        names.add(skill['name'].trim())
      }
    }
  }
  return names
}

/** The job lanes this seat authors, in JOB_LANES order. */
export function authoredLanes(root: Record<string, unknown>): JobLane[] {
  const skills = enabledSkills(root)
  return (Object.keys(JOB_LANES) as JobLane[]).filter((lane) => skills.has(JOB_LANES[lane]))
}

/** True iff an enabled connector binds one of `name`'s backends. */
export function authorsConnector(root: Record<string, unknown>, name: string): boolean {
  const backends = CONNECTOR_BACKENDS[name]
  if (!backends) throw new Error(`unknown connector capability ${name}`)
  const connectors = root['connectors']
  if (!isPlainObject(connectors)) return false
  return Object.values(connectors).some(
    (c) =>
      isPlainObject(c) &&
      c['enabled'] !== false &&
      typeof c['backend'] === 'string' &&
      backends.includes(c['backend'])
  )
}

/** True iff the seat authors `capability` (a job lane or a connector capability). */
export function authorsCapability(root: Record<string, unknown>, capability: string): boolean {
  if (capability in JOB_LANES) return authoredLanes(root).includes(capability as JobLane)
  if (capability in CONNECTOR_BACKENDS) return authorsConnector(root, capability)
  throw new Error(`unknown capability ${capability}`)
}

/** Refuse a block whose owner capability this seat does not author. */
export function checkCapabilityOwnedBlocks(
  root: Record<string, unknown>,
  errors: ValidationError[]
): void {
  for (const [block, owner] of Object.entries(BLOCK_OWNERS)) {
    if (root[block] === undefined || root[block] === null) continue
    if (authorsCapability(root, owner)) continue
    errors.push({
      code: 'CapabilityNotAuthored',
      path: block,
      message:
        `${block} is read only by the ${owner} capability, which this seat does not author ` +
        `(${owner in JOB_LANES ? `no persona enables ${JOB_LANES[owner as JobLane]}` : `no enabled connector binds ${CONNECTOR_BACKENDS[owner]?.join(' or ')}`}); ` +
        `remove the block or author the capability`,
    })
  }
}
