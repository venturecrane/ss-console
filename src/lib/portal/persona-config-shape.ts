/**
 * The read-side check on a projected `personas_json` column: parse-and-validate
 * into `PersonaConfig[]`, never cast.
 *
 * `projectRow` (./customer-config.ts) read this column as
 * `JSON.parse(value) as PersonaConfig[]` until 2026-09-25, while the forward
 * projection's header (./customer-config-projection.ts) already promised the
 * reader "THROWS on a shape mismatch". It threw on JSON syntax only; a row of
 * the wrong shape reached the portal typed as right (review 2026-09-25, Code
 * Quality 2). This module makes the promise true: every field the portal reads
 * is checked against the shape `toPersonaConfig` writes, and the first
 * mismatch is named.
 *
 * Tolerances are exactly the ones the type already declares: `cron` and a
 * skill's `settings` may be absent (rows projected before those fields), and a
 * `send_as` may carry only the legacy `agentmail_identity`. An exposure key
 * this build does not know is skipped rather than refused, so a projection
 * written by a newer schema never 500s an older reader; a known key with an
 * unknown value is refused.
 */

import { isRecord } from '../api/helpers'
import {
  ACCEPTED_EXPOSURE_CEILINGS,
  EXPOSURE_ACTION_CLASSES,
  type AuthoredExposureActionClass,
  type ExposureCeiling,
  type PersonaEntitlements,
  type SkillInitiation,
} from '../operator/customer-yaml/types'
import type {
  PersonaChannelBinding,
  PersonaConfig,
  PersonaCronEntry,
  PersonaSendAs,
  PersonaSkill,
} from './customer-config'

/** Thrown with the path of the first field that does not match. */
export class PersonaShapeError extends Error {}

function fail(path: string, expected: string): never {
  throw new PersonaShapeError(`${path} is not ${expected}`)
}

function str(value: unknown, path: string): string {
  return typeof value === 'string' ? value : fail(path, 'a string')
}

function nullableStr(value: unknown, path: string): string | null {
  return value == null ? null : str(value, path)
}

function bool(value: unknown, path: string): boolean {
  return typeof value === 'boolean' ? value : fail(path, 'a boolean')
}

function rec(value: unknown, path: string): Record<string, unknown> {
  return isRecord(value) ? value : fail(path, 'an object')
}

function list(value: unknown, path: string): unknown[] {
  return Array.isArray(value) ? value : fail(path, 'an array')
}

function strList(value: unknown, path: string): string[] {
  return list(value, path).map((v, i) => str(v, `${path}[${i}]`))
}

const isCeiling = (v: unknown): v is ExposureCeiling =>
  typeof v === 'string' && (ACCEPTED_EXPOSURE_CEILINGS as readonly string[]).includes(v)

function exposureMap(
  value: unknown,
  path: string
): Partial<Record<AuthoredExposureActionClass, ExposureCeiling>> {
  const raw = rec(value, path)
  const out: Partial<Record<AuthoredExposureActionClass, ExposureCeiling>> = {}
  for (const cls of EXPOSURE_ACTION_CLASSES) {
    const v = raw[cls]
    if (v === undefined) continue
    if (!isCeiling(v)) fail(`${path}.${cls}`, `one of ${ACCEPTED_EXPOSURE_CEILINGS.join(', ')}`)
    out[cls] = v
  }
  return out
}

function entitlements(value: unknown, path: string): PersonaEntitlements {
  const raw = rec(value, path)
  const out: PersonaEntitlements = { exposure: exposureMap(raw.exposure, `${path}.exposure`) }
  if (raw.exposure_ceiling !== undefined) {
    out.exposure_ceiling = exposureMap(raw.exposure_ceiling, `${path}.exposure_ceiling`)
  }
  return out
}

function sendAs(value: unknown, path: string): PersonaSendAs | null {
  if (value == null) return null
  const raw = rec(value, path)
  const out: PersonaSendAs = {}
  if (raw.send_identity !== undefined) {
    const id = rec(raw.send_identity, `${path}.send_identity`)
    const provider = id.provider
    if (provider !== 'agentmail' && provider !== 'msgraph')
      fail(`${path}.send_identity.provider`, 'agentmail or msgraph')
    out.send_identity = { provider, address: str(id.address, `${path}.send_identity.address`) }
  }
  if (raw.agentmail_identity !== undefined) {
    out.agentmail_identity = str(raw.agentmail_identity, `${path}.agentmail_identity`)
  }
  return out
}

function initiation(value: unknown, path: string): SkillInitiation {
  const raw = rec(value, path)
  return {
    manual: bool(raw.manual, `${path}.manual`),
    scheduled: bool(raw.scheduled, `${path}.scheduled`),
    webhook: bool(raw.webhook, `${path}.webhook`),
  }
}

function settings(value: unknown, path: string): Record<string, string | number | boolean> {
  const out: Record<string, string | number | boolean> = {}
  for (const [key, v] of Object.entries(rec(value, path))) {
    if (typeof v !== 'string' && typeof v !== 'number' && typeof v !== 'boolean')
      fail(`${path}.${key}`, 'a string, number, or boolean')
    out[key] = v
  }
  return out
}

function skill(value: unknown, path: string): PersonaSkill {
  const raw = rec(value, path)
  const out: PersonaSkill = {
    name: str(raw.name, `${path}.name`),
    initiation: initiation(raw.initiation, `${path}.initiation`),
  }
  if (raw.settings !== undefined) out.settings = settings(raw.settings, `${path}.settings`)
  return out
}

function cronEntry(value: unknown, path: string): PersonaCronEntry {
  const raw = rec(value, path)
  return { skill: str(raw.skill, `${path}.skill`), schedule: str(raw.schedule, `${path}.schedule`) }
}

function channelBinding(value: unknown, path: string): PersonaChannelBinding {
  const raw = rec(value, path)
  return {
    integration: str(raw.integration, `${path}.integration`),
    channels: strList(raw.channels, `${path}.channels`),
  }
}

function persona(value: unknown, path: string): PersonaConfig {
  const raw = rec(value, path)
  const status = raw.status
  if (status !== 'active' && status !== 'archived') fail(`${path}.status`, 'active or archived')
  const out: PersonaConfig = {
    slug: str(raw.slug, `${path}.slug`),
    status,
    name: str(raw.name, `${path}.name`),
    title: nullableStr(raw.title, `${path}.title`),
    signature_html: nullableStr(raw.signature_html, `${path}.signature_html`),
    tone: strList(raw.tone, `${path}.tone`),
    send_as: sendAs(raw.send_as, `${path}.send_as`),
    entitlements: entitlements(raw.entitlements, `${path}.entitlements`),
    skills: list(raw.skills, `${path}.skills`).map((s, i) => skill(s, `${path}.skills[${i}]`)),
    channel_bindings: list(raw.channel_bindings, `${path}.channel_bindings`).map((c, i) =>
      channelBinding(c, `${path}.channel_bindings[${i}]`)
    ),
  }
  if (raw.cron !== undefined) {
    out.cron = list(raw.cron, `${path}.cron`).map((c, i) => cronEntry(c, `${path}.cron[${i}]`))
  }
  return out
}

/**
 * Read a parsed `personas_json` value. Throws PersonaShapeError naming the
 * first field that does not match the projected shape.
 */
export function readPersonaConfigs(value: unknown): PersonaConfig[] {
  return list(value, 'personas').map((p, i) => persona(p, `personas[${i}]`))
}
