/**
 * Public surface for the customer.yaml schema validator + secret detector.
 *
 *   import { validate, scanRawYaml } from '@/lib/operator/customer-yaml'
 *
 * The validator is consumed by:
 *   - CI workflow at the canonical configs repo (per ADR 0012 §5; lands in
 *     a follow-on PR alongside the repo itself)
 *   - Pre-commit hook authors wiring in their dev environment
 *   - tests/customer-yaml-validator.test.ts and
 *     tests/customer-yaml-secret-detector.test.ts
 *
 * See docs/specs/operator/customer-yaml-schema.md for the contract.
 */

export {
  scanParsedValue,
  scanRawYaml,
  SECRET_DETECTOR_INTERNALS,
  type ScanOptions,
  type SecretFinding,
  type SecretPatternCategory,
} from './secret-detector'

export {
  validate,
  ACCEPTED_VERTICALS,
  ACCEPTED_ADDONS,
  ACCEPTED_EXPOSURE_CEILINGS,
  ACCEPTED_USER_ROLES,
  ACCEPTED_PERSONA_STATUSES,
  ACCEPTED_PRONOUNS,
  ACCEPTED_LOG_LEVELS,
  ACCEPTED_LOG_SHIPS,
  ACCEPTED_BACKEND_PREFIXES,
  ACCEPTED_GOOGLE_AUTH_MODES,
  ACCEPTED_SCHEMA_VERSIONS,
  ACCEPTED_WAKE_POLICIES,
  AUDIT_LOG_DAYS_MAX,
  SEMVER_PATTERN,
  SYNC_SOURCES,
  VERTICAL_AUDIT_LOG_DAYS_DEFAULTS,
  WEBHOOK_URL_PATTERN,
  isAcceptedCronSchedule,
  type AddonSpec,
  type SyncSource,
  type ValidateOptions,
  type ValidationResult,
  type ValidationError,
  type ValidationErrorCode,
  type CustomerYaml,
  type Persona,
  type PersonaSkill,
  type PersonaStatus,
  type PersonaSendAs,
  type PersonaChannelBinding,
  type PersonaBundle,
  type PersonaCron,
  type WebhookTrigger,
  type WakePolicy,
  type User,
  type UserRole,
  type Connector,
  type GoogleAuth,
  type ManagedMailbox,
  type Scope,
  type Escalation,
  type SendPolicy,
  type SendPolicyHeldRelease,
  type SendPolicyReply,
  type Memory,
  type MemoryRetention,
  type BusinessHours,
  type Logging,
  type Pause,
  type MachineSpec,
  type CostEstimate,
  type Relationship,
  type RelationshipPerson,
  type Vertical,
  type ExposureCeiling,
  type SkillInitiation,
  type PersonaEntitlements,
  type AuthoredExposureActionClass,
  type Pronouns,
  type LogLevel,
  type LogShip,
  type SchemaVersion,
} from './validator'
