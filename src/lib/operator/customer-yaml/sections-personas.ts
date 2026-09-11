/**
 * Personas section validator for customer.yaml. Splits the recursive
 * per-persona / per-skill / per-channel-binding checks into small functions
 * so each stays under the 75-line / 15-complexity ceilings.
 */

import {
  ACCEPTED_PERSONA_STATUSES,
  ACCEPTED_PRONOUNS,
  SLUG_PATTERN,
  type Persona,
  type PersonaChannelBinding,
  type PersonaSignature,
  type PersonaStatus,
  type ValidationError,
} from './types'
import { isPlainObject, optionalEnum, optionalString } from './helpers'
import { findFloorTrigger } from '../floor-triggers'
import { checkSendAs } from './sections-personas-send-as'
import { checkBundles, checkCron } from './sections-bundles-cron'
import { checkPersonaEntitlements, checkPersonaSkills } from './sections-persona-skills'

export function checkPersonas(root: Record<string, unknown>, errors: ValidationError[]): Persona[] {
  const raw = root['personas']
  if (raw === undefined || raw === null) {
    errors.push({ code: 'MissingField', path: 'personas', message: 'personas is required' })
    return []
  }
  if (!Array.isArray(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'personas',
      message: 'personas must be a list (ADR 0011)',
    })
    return []
  }
  if (raw.length === 0) {
    errors.push({
      code: 'EmptyList',
      path: 'personas',
      message: 'personas must contain at least one entry (ADR 0011)',
    })
    return []
  }
  return buildPersonas(raw, errors)
}

function buildPersonas(raw: unknown[], errors: ValidationError[]): Persona[] {
  const out: Persona[] = []
  const seenSlugs = new Set<string>()
  let activeCount = 0
  for (let i = 0; i < raw.length; i++) {
    const built = checkOnePersona(raw[i], i, seenSlugs, errors)
    if (built !== null) {
      out.push(built)
      if (built.status === 'active') activeCount += 1
    }
  }
  if (activeCount === 0 && out.length > 0) {
    errors.push({
      code: 'MissingActivePersona',
      path: 'personas',
      message: 'at least one persona must have status: active',
    })
  }
  return out
}

function checkOnePersona(
  p: unknown,
  i: number,
  seenSlugs: Set<string>,
  errors: ValidationError[]
): Persona | null {
  if (!isPlainObject(p)) {
    errors.push({
      code: 'TypeMismatch',
      path: `personas[${i}]`,
      message: 'personas entries must be objects',
    })
    return null
  }
  const slug = checkPersonaSlug(p['slug'], i, seenSlugs, errors)
  const status = checkPersonaStatus(p['status'], i, errors)
  const name = checkPersonaName(p['name'], i, errors)
  checkPersonaTone(p['tone'], i, errors)
  const skills = checkPersonaSkills(p['skills'], `personas[${i}].skills`, errors)
  const channelBindings = checkChannelBindings(
    p['channel_bindings'],
    `personas[${i}].channel_bindings`,
    errors
  )
  const bundles = checkBundles(p['bundles'], `personas[${i}].bundles`, skills, errors)
  const cron = checkCron(p['cron'], `personas[${i}].cron`, skills, errors)
  const title = optionalString(p, 'title', `personas[${i}].title`, errors)
  const signatureHtml = optionalString(p, 'signature_html', `personas[${i}].signature_html`, errors)
  const signature = checkPersonaSignature(p['signature'], `personas[${i}].signature`, errors)
  const avatar = optionalString(p, 'avatar_url', `personas[${i}].avatar_url`, errors)
  const pronouns = optionalEnum(p, 'pronouns', ACCEPTED_PRONOUNS, `personas[${i}].pronouns`, errors)
  const sendAs = checkSendAs(p['send_as'], `personas[${i}].send_as`, errors)
  const entitlements = checkPersonaEntitlements(
    p['entitlements'],
    `personas[${i}].entitlements`,
    errors
  )
  return {
    slug,
    status,
    name,
    title,
    signature_html: signatureHtml,
    signature,
    avatar_url: avatar,
    tone: extractToneList(p['tone']),
    pronouns: pronouns,
    send_as: sendAs,
    entitlements,
    skills,
    voice_overrides: checkOverrideBlob(
      p['voice_overrides'],
      `personas[${i}].voice_overrides`,
      errors
    ),
    escalation_overrides: checkOverrideBlob(
      p['escalation_overrides'],
      `personas[${i}].escalation_overrides`,
      errors
    ),
    channel_bindings: channelBindings,
    bundles,
    cron,
  }
}

/**
 * The authored chase-mail signature block (outbound-quality track): an
 * optional mapping with optional `firm_line` / `closing` strings, nothing
 * else. Additive and optional on purpose -- unauthored degrades to
 * `customer_name` alone (ADR 0035, no imposed defaults) -- but a MALFORMED
 * authoring is an error, never a silent null: a firm that authored a
 * signature and got the shape wrong must hear about it at validation time,
 * not discover its sign-off missing in a client's mailbox.
 */
function checkPersonaSignature(
  value: unknown,
  path: string,
  errors: ValidationError[]
): PersonaSignature | null {
  if (value === undefined || value === null) return null
  if (!isPlainObject(value)) {
    errors.push({
      code: 'TypeMismatch',
      path,
      message: `${path} must be a mapping with optional firm_line / closing strings`,
    })
    return null
  }
  for (const key of Object.keys(value)) {
    if (key !== 'firm_line' && key !== 'closing') {
      errors.push({
        code: 'TypeMismatch',
        path: `${path}.${key}`,
        message: `${path}.${key}: unknown key (only firm_line and closing are authored here)`,
      })
      return null
    }
  }
  const firmLine = optionalString(value, 'firm_line', `${path}.firm_line`, errors)
  const closing = optionalString(value, 'closing', `${path}.closing`, errors)
  // The block renders VERBATIM into every chase body, which the ADR 0031
  // content floor re-scans before delivery (overlay shared/content_floor.py).
  // A trigger word authored here would silently downgrade every autonomous
  // chase to a held draft, so it is refused at authoring time with the word
  // named (PR #2651 review, finding 3). Shared vocabulary + drift guard:
  // src/lib/operator/floor-triggers.ts.
  for (const [key, text] of [
    ['firm_line', firmLine],
    ['closing', closing],
  ] as const) {
    const trigger = text === null ? null : findFloorTrigger(text)
    if (trigger !== null) {
      errors.push({
        code: 'TypeMismatch',
        path: `${path}.${key}`,
        message:
          `${path}.${key} contains "${trigger}", a content-floor trigger word (ADR 0031): ` +
          'the signature renders into every outbound chase body, and the floor would hold ' +
          'each one as a draft. Use the floor-clean substitution from ' +
          '_shared-chase-voice.md instead of this word.',
      })
      return null
    }
  }
  return { firm_line: firmLine, closing }
}

/**
 * Validate a free-form per-persona override blob. Absent → null. A plain
 * object is carried verbatim (internal shape is intentionally open). Any other
 * authored value (string, number, array) is a malformed override → push an
 * error and coerce to null rather than silently passing a scalar through, which
 * the prior `unknown` typing allowed.
 */
function checkOverrideBlob(
  value: unknown,
  path: string,
  errors: ValidationError[]
): Record<string, unknown> | null {
  if (value === undefined || value === null) return null
  if (isPlainObject(value)) return value
  errors.push({
    code: 'TypeMismatch',
    path,
    message: `${path} must be a mapping (object) or absent`,
  })
  return null
}

function checkPersonaSlug(
  slug: unknown,
  i: number,
  seenSlugs: Set<string>,
  errors: ValidationError[]
): string {
  if (typeof slug !== 'string' || slug.length === 0) {
    errors.push({
      code: 'MissingField',
      path: `personas[${i}].slug`,
      message: 'personas[].slug is required',
    })
    return ''
  }
  if (!SLUG_PATTERN.test(slug)) {
    errors.push({
      code: 'InvalidSlug',
      path: `personas[${i}].slug`,
      message: 'personas[].slug must match ^[a-z0-9][a-z0-9-]{0,31}$',
    })
    return ''
  }
  if (seenSlugs.has(slug)) {
    errors.push({
      code: 'DuplicatePersonaSlug',
      path: `personas[${i}].slug`,
      message: `personas[].slug "${slug}" is duplicated`,
    })
    return slug
  }
  seenSlugs.add(slug)
  return slug
}

function checkPersonaStatus(status: unknown, i: number, errors: ValidationError[]): PersonaStatus {
  if (
    typeof status !== 'string' ||
    !(ACCEPTED_PERSONA_STATUSES as readonly string[]).includes(status)
  ) {
    errors.push({
      code: 'EnumViolation',
      path: `personas[${i}].status`,
      message: `personas[].status must be one of: ${ACCEPTED_PERSONA_STATUSES.join(', ')}`,
    })
    return 'archived'
  }
  return status as PersonaStatus
}

function checkPersonaName(name: unknown, i: number, errors: ValidationError[]): string {
  if (typeof name !== 'string' || name.length === 0) {
    errors.push({
      code: 'MissingField',
      path: `personas[${i}].name`,
      message: 'personas[].name is required',
    })
    return ''
  }
  return name
}

function checkPersonaTone(tone: unknown, i: number, errors: ValidationError[]): void {
  if (!Array.isArray(tone)) {
    errors.push({
      code: 'MissingField',
      path: `personas[${i}].tone`,
      message: 'personas[].tone must be a list of strings (3-5 entries)',
    })
  }
}

function extractToneList(tone: unknown): string[] {
  if (!Array.isArray(tone)) return []
  return tone.filter((t): t is string => typeof t === 'string')
}

function checkChannelBindings(
  raw: unknown,
  path: string,
  errors: ValidationError[]
): PersonaChannelBinding[] {
  if (raw === undefined || raw === null) return []
  if (!Array.isArray(raw)) {
    errors.push({ code: 'TypeMismatch', path, message: `${path} must be a list` })
    return []
  }
  const out: PersonaChannelBinding[] = []
  for (let i = 0; i < raw.length; i++) {
    const b = checkOneChannelBinding(raw[i], `${path}[${i}]`, errors)
    if (b !== null) out.push(b)
  }
  return out
}

function checkOneChannelBinding(
  raw: unknown,
  path: string,
  errors: ValidationError[]
): PersonaChannelBinding | null {
  if (!isPlainObject(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path,
      message: 'channel_bindings entries must be objects',
    })
    return null
  }
  const integration = raw['integration']
  const channels = raw['channels']
  if (typeof integration !== 'string' || integration.length === 0) {
    errors.push({
      code: 'MissingField',
      path: `${path}.integration`,
      message: 'integration is required',
    })
    return null
  }
  if (!Array.isArray(channels) || !channels.every((c) => typeof c === 'string')) {
    errors.push({
      code: 'TypeMismatch',
      path: `${path}.channels`,
      message: 'channels must be a list of strings',
    })
    return null
  }
  return { integration, channels: channels }
}
