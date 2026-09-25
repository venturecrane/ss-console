/**
 * Small shared helpers used by the section validators. Each is narrow and
 * stateless; they push to a passed-in errors array and return a typed value
 * (or null on failure). The pattern keeps section validators readable while
 * not requiring exception flow.
 */

import type { SecretFinding } from './secret-detector'
import type { ValidationError, ValidationErrorCode } from './types'

export function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

export function checkRequiredString(
  root: Record<string, unknown>,
  field: string,
  errors: ValidationError[]
): void {
  const v = root[field]
  if (v === undefined || v === null) {
    errors.push({ code: 'MissingField', path: field, message: `${field} is required` })
    return
  }
  if (typeof v !== 'string') {
    errors.push({ code: 'TypeMismatch', path: field, message: `${field} must be a string` })
    return
  }
  if (v.length === 0) {
    errors.push({ code: 'EmptyField', path: field, message: `${field} must not be empty` })
  }
}

/**
 * Like checkRequiredString but the field may be absent. Absent/null is fine
 * (no error); present means it must be a non-empty string. Used for optional
 * top-level scalars such as `escalation_model` (ADR 0049).
 */
export function checkOptionalString(
  root: Record<string, unknown>,
  field: string,
  errors: ValidationError[]
): void {
  const v = root[field]
  if (v === undefined || v === null) return
  if (typeof v !== 'string') {
    errors.push({ code: 'TypeMismatch', path: field, message: `${field} must be a string` })
    return
  }
  if (v.length === 0) {
    errors.push({ code: 'EmptyField', path: field, message: `${field} must not be empty` })
  }
}

// Upstream-pin pattern per ADR 0024 (docs/adr/0024-hermes-consumption-and-update-cadence.md).
// hermes_ref pins an UPSTREAM Hermes release by date-tag AND commit SHA:
//   v{YYYY}.{M}.{D}@{40-hex-sha}
//   (e.g. v2026.5.16@a91a57fa5a13d516c38b07a141a9ce8a3daabeb0)
//   - {YYYY}.{M}.{D} is Hermes' date-based upstream version, present for human
//     readability. Legacy SemVer tags (v0.14.0) are not accepted; Hermes
//     switched to date-based tagging in 2026.
//   - The @{sha} is the immutable pin. A commit SHA is content-addressed, so
//     upstream cannot mutate what it points at — this is the immutability the
//     retired fork only claimed to provide. Carrying the SHA in the ref also
//     means provisioning never resolves it from a live upstream lookup
//     (closes the availability defect documented in ADR 0024).
// ADR 0024 retired the venturecrane/hermes-agent fork and its v...-smd.N tag
// scheme. Bare date-tags (no @sha), bare SHAs (no v-tag), -smd.N fork tags,
// and legacy SemVer tags are all rejected. Security patches are applied in the
// base-image build and tracked by image digest, not by a ref suffix.
const UPSTREAM_PIN_PATTERN = /^v\d{4}\.\d{1,2}\.\d{1,2}@[0-9a-f]{40}$/

export function checkHermesRef(root: Record<string, unknown>, errors: ValidationError[]): void {
  const v = root['hermes_ref']
  // Required-string check already runs upstream of this; no-op cleanly when
  // the field is absent or wrong-typed so we don't duplicate that error.
  if (typeof v !== 'string' || v.length === 0) return
  if (!UPSTREAM_PIN_PATTERN.test(v)) {
    errors.push({
      code: 'InvalidFormat',
      path: 'hermes_ref',
      message:
        'hermes_ref must pin an upstream Hermes release of the form ' +
        'v{YYYY}.{M}.{D}@{40-hex-sha} ' +
        '(e.g. v2026.5.16@a91a57fa5a13d516c38b07a141a9ce8a3daabeb0). ' +
        'Bare tags, bare SHAs, -smd.N fork tags, and legacy SemVer tags are ' +
        'not accepted. See ADR 0024.',
    })
  }
}

export function optionalString(
  rec: Record<string, unknown>,
  key: string,
  path: string,
  errors: ValidationError[]
): string | null {
  const v = rec[key]
  if (v === undefined || v === null) return null
  if (typeof v !== 'string') {
    errors.push({ code: 'TypeMismatch', path, message: `${path} must be a string when present` })
    return null
  }
  return v
}

/**
 * Like {@link optionalString} but rejects empty strings. Use for fields that
 * are optional in the schema but where empty string is always an authoring
 * typo — e.g. bootstrap-populated paths where blank means "the bootstrap
 * script failed half-way." Added by ADR 0022 Stream 1 for the
 * `memory.r2_skill_bodies_*` keys; reusable elsewhere.
 */
export function optionalNonEmptyString(
  rec: Record<string, unknown>,
  key: string,
  path: string,
  errors: ValidationError[]
): string | null {
  const v = rec[key]
  if (v === undefined || v === null) return null
  if (typeof v !== 'string') {
    errors.push({ code: 'TypeMismatch', path, message: `${path} must be a string when present` })
    return null
  }
  if (v.length === 0) {
    errors.push({ code: 'EmptyField', path, message: `${path} must not be empty when present` })
    return null
  }
  return v
}

export function optionalEnum<T extends string>(
  rec: Record<string, unknown>,
  key: string,
  accepted: readonly T[],
  path: string,
  errors: ValidationError[]
): T | null {
  const v = rec[key]
  if (v === undefined || v === null) return null
  if (typeof v !== 'string' || !(accepted as readonly string[]).includes(v)) {
    errors.push({
      code: 'EnumViolation',
      path,
      message: `${path} must be one of: ${accepted.join(', ')}`,
    })
    return null
  }
  return v as T
}

export function requireStringList(
  rec: Record<string, unknown>,
  key: string,
  path: string,
  errors: ValidationError[]
): string[] {
  const v = rec[key]
  if (v === undefined || v === null) {
    errors.push({ code: 'MissingField', path, message: `${path} is required` })
    return []
  }
  if (!Array.isArray(v) || !v.every((x) => typeof x === 'string')) {
    errors.push({ code: 'TypeMismatch', path, message: `${path} must be a list of strings` })
    return []
  }
  return v
}

export function optionalStringList(
  rec: Record<string, unknown>,
  key: string,
  path: string,
  errors: ValidationError[]
): string[] {
  const v = rec[key]
  if (v === undefined || v === null) return []
  if (!Array.isArray(v) || !v.every((x) => typeof x === 'string')) {
    errors.push({
      code: 'TypeMismatch',
      path,
      message: `${path} must be a list of strings when present`,
    })
    return []
  }
  return v
}

/**
 * Convert a SecretFinding (from secret-detector) into a ValidationError.
 * CRITICAL: never includes the matched substring in the message — the
 * detector intentionally drops the value and the validator must not
 * resurrect it from f.reason or f.path.
 */
export function secretFindingToError(f: SecretFinding): ValidationError {
  const code: ValidationErrorCode =
    f.category === 'banned_field_name' ? 'BannedFieldName' : 'SecretDetected'
  const parts: string[] = []
  if (f.line !== null) parts.push(`line ${f.line}`)
  if (f.path !== null) parts.push(`path ${f.path}`)
  const location = parts.length > 0 ? ` (${parts.join(', ')})` : ''
  return {
    code,
    path: f.path ?? (f.line !== null ? `line:${f.line}` : '$'),
    message: `${f.reason}${location}; rotate the value and replace with an infisical: token_ref`,
  }
}

// canonRosterAddress lived in sections-scope.ts until 2026-09-25, when
// sections-staff-send-as.ts (ADR 0089) needed it too and the two section
// validators imported each other (review 2026-09-25, Architecture 5).
/**
 * Characters that disqualify a roster entry outright.
 *
 * THIS SET IS A CROSS-LANGUAGE CONTRACT with the runtime classifier's
 * `_DISQUALIFYING_RE` (`operator/adapter/recipient_classifier.py`). It is spelled
 * out rather than written `\s` because the two languages' whitespace classes are
 * not the same set: `\uFEFF` is whitespace to JavaScript and not to Python;
 * `\x1C`-`\x1F` and `\x85` are the reverse. Arbiter fixture:
 * `operator/contracts/fixtures/roster-canon-cases.json`, loaded by both suites.
 *
 * The control-character ranges are the point, not an oversight: a C0 or C1
 * control inside a local part used to survive canonicalization intact on both
 * sides, and an address is never the place for one. Hence the rule disable.
 */
const DISQUALIFYING =
  // eslint-disable-next-line no-control-regex -- rejecting control characters IS the rule
  /[<>",;\x00-\x20\x7F-\xA0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000\uFEFF]/

/**
 * Canonicalize an outbound-roster address to `@domain` or `local@domain`, or
 * `null` when malformed. Mirrors the runtime classifier's `_canonicalize_roster_entry`
 * (strict: NFC-normalized then lowercased, no display-name/list/whitespace,
 * exact-domain, no plus-tag widening) so the validator's notion of "same address"
 * matches the classifier's.
 *
 * ss#2284: the NFC normalization is not cosmetic. Without it the validator read
 * NFD-`josé@firm.example` and NFC-`josé@firm.example` as two distinct addresses,
 * so its collision rules — no address under two classes, no address in both the
 * outbound roster and `inbound_allow_from` — passed a config the runtime resolves
 * to ONE address holding two exposure classes. The divergence was measured on both
 * real implementations before it was fixed (`vfy_01KZSJWNV9CV6ENHG574A9TZK3`).
 */
export function canonRosterAddress(raw: string): string | null {
  const s = raw.normalize('NFC').trim().toLowerCase()
  if (!s || DISQUALIFYING.test(s)) return null
  if (s.startsWith('@')) {
    const domain = s.slice(1)
    const labels = domain.split('.')
    if (labels.length < 2 || labels.some((l) => l === '')) return null
    return `@${domain}`
  }
  if ((s.match(/@/g) ?? []).length !== 1) return null
  const [local, domain] = s.split('@')
  if (!local || !domain) return null
  const labels = domain.split('.')
  if (labels.length < 2 || labels.some((l) => l === '')) return null
  return `${local}@${domain}`
}
