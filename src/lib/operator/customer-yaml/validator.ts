/**
 * customer.yaml structural validator.
 *
 * Consumes the parsed YAML as an `unknown` (the consumer chooses its YAML
 * parser — portal and Hermes each parse independently per ADR 0012 §4) and
 * returns a tagged-union ValidationResult: `ok` carries a typed CustomerYaml,
 * `err` carries a list of ValidationError entries.
 *
 * Hand-rolled rather than zod/valibot for three reasons:
 *   1. The contract is small enough that explicit checks read better than a
 *      schema declaration whose semantics the reader translates back to docs.
 *   2. The error shape we need (typed `code`, JSONPath, no-echo for secrets)
 *      is awkward to retrofit onto a schema library's error format.
 *   3. Zero dependencies. The Worker bundle already has no schema lib.
 *
 * Section validators live in sections-*.ts to keep individual files under the
 * 500/75/15 ceiling. This file is the entrypoint and the orchestrator.
 *
 * Aligned with docs/specs/operator/customer-yaml-schema.md (#790).
 */

import { scanParsedValue, scanRawYaml, type ScanOptions } from './secret-detector'
import {
  checkHermesRef,
  checkOptionalString,
  checkRequiredString,
  isPlainObject,
  secretFindingToError,
} from './helpers'
import {
  type AddonSpec,
  type CustomerYaml,
  type GoogleAuth,
  type MachineSpec,
  type Memory,
  type Observability,
  type SchemaVersion,
  type ValidationError,
  type ValidationResult,
  type Vertical,
} from './types'
import {
  checkCustomerId,
  checkMachine,
  checkPracticeAreas,
  checkSchemaVersion,
  checkUsers,
} from './sections-identity'
import { checkPersonas } from './sections-personas'
import { checkConnectors } from './sections-connectors'
import { checkCustodyExceptions, checkCustodyGuard } from './sections-custody-guard'
import { checkScope } from './sections-scope'
import { checkStaffMailboxReads } from './sections-staff-send-as'
import {
  checkBusinessHours,
  checkDigest,
  checkComplianceEnabled,
  checkLogging,
  checkMemory,
  checkPause,
  checkVoiceLibrary,
} from './sections-other'
import { checkEscalation } from './sections-escalation'
import { checkSendPolicy } from './sections-send-policy'
import { checkCredentialCustodyDefault } from './sections-connectors'
import { checkGmailPush } from './sections-gmail-push'
import { checkFirmIdentity } from './sections-firm-identity'
import { checkTelegram } from './sections-telegram'
import { checkObservability } from './sections-observability'
import { checkVoiceCohorts } from './sections-voice'
import { checkSeat } from './sections-seat'
import { checkOutputClasses } from './sections-output-classes'
import { checkWebhookTriggers } from './sections-webhook-triggers'
import { checkExtendsReserved, checkVerticalPinned } from './sections-vertical'
import { checkAddons } from './sections-addons'
import { checkMcpConnector } from './sections-mcp-connector'
import { checkGoogleAuth } from './sections-google-auth'
import { checkAuthority } from './sections-authority'
import { checkRelationship } from './sections-relationship'
import type { AuthorityPosture } from '../authority'
import type { CredentialCustody } from '../credential-custody'

export type {
  CustomerYaml,
  ValidationError,
  ValidationErrorCode,
  ValidationResult,
  Persona,
  PersonaSkill,
  PersonaStatus,
  PersonaSendAs,
  PersonaChannelBinding,
  PersonaBundle,
  PersonaCron,
  WebhookTrigger,
  WakePolicy,
  User,
  UserRole,
  Connector,
  GoogleAuth,
  ManagedMailbox,
  Scope,
  Escalation,
  SendPolicy,
  SendPolicyHeldRelease,
  SendPolicyReply,
  Memory,
  MemoryRetention,
  VoiceLibrary,
  VoiceCohorts,
  BaseVoiceCohort,
  BusinessHours,
  Logging,
  Pause,
  MachineSpec,
  CostEstimate,
  Observability,
  McpConnector,
  McpConnectorAccess,
  McpIssuancePolicy,
  Relationship,
  RelationshipPerson,
  DataPosture,
  Vertical,
  ExposureCeiling,
  SkillInitiation,
  PersonaEntitlements,
  AuthoredExposureActionClass,
  Pronouns,
  LogLevel,
  LogShip,
  SchemaVersion,
} from './types'
export {
  ACCEPTED_VERTICALS,
  ACCEPTED_ADDONS,
  ACCEPTED_EXPOSURE_CEILINGS,
  ACCEPTED_USER_ROLES,
  ACCEPTED_DATA_POSTURES,
  ACCEPTED_PERSONA_STATUSES,
  ACCEPTED_PRONOUNS,
  ACCEPTED_LOG_LEVELS,
  ACCEPTED_LOG_SHIPS,
  ACCEPTED_BACKEND_PREFIXES,
  ACCEPTED_GOOGLE_AUTH_MODES,
  ACCEPTED_SCHEMA_VERSIONS,
  ACCEPTED_WAKE_POLICIES,
  AUDIT_LOG_DAYS_MAX,
  BASE_VOICE_COHORTS,
  OBSERVABILITY_DEFAULTS,
  SEMVER_PATTERN,
  SYNC_SOURCES,
  VERTICAL_AUDIT_LOG_DAYS_DEFAULTS,
  WEBHOOK_URL_PATTERN,
  isAcceptedCronSchedule,
  type AddonSpec,
  type SyncSource,
} from './types'
export { resolveCohortVocabulary } from './sections-voice'
export {
  ACCEPTED_AUTHORITY_DEFAULTS,
  ACCEPTED_AUTHORITY_HOLDERS,
  ALL_AUTHORITY_DOMAINS,
  DEFAULT_AUTHORITY_POSTURE,
  SMD_ONLY_AUTHORITY_DOMAINS,
  SWITCHABLE_AUTHORITY_DOMAINS,
  canClientRead,
  isClientOperable,
  isSwitchableDomain,
  parseAuthorityPosture,
  resolveAllDomains,
  resolveDomainAuthority,
  type AuthorityDefault,
  type AuthorityDomain,
  type AuthorityHolder,
  type AuthorityPosture,
  type SmdOnlyAuthorityDomain,
  type SwitchableAuthorityDomain,
} from '../authority'
export {
  ACCEPTED_CREDENTIAL_CUSTODY,
  DEFAULT_CREDENTIAL_CUSTODY,
  parseCredentialCustody,
  resolveCredentialCustody,
  smdCanReachSecret,
  type CredentialCustody,
} from '../credential-custody'

export interface ValidateOptions {
  /** Raw YAML text. When provided, scanRawYaml runs as the first pass so a
   * malformed structural shape still fails closed on a leaked secret. */
  rawText?: string
  /** Forwarded to the secret detector. */
  scanOptions?: ScanOptions
}

/**
 * Validate a parsed customer.yaml object.
 *
 * The validator never throws. All structural and policy violations are
 * collected and returned as a flat list. Authors see every error in one
 * pass rather than fixing one and re-running.
 */
export function validate(input: unknown, options: ValidateOptions = {}): ValidationResult {
  const errors: ValidationError[] = []

  if (typeof options.rawText === 'string') {
    for (const f of scanRawYaml(options.rawText, options.scanOptions)) {
      errors.push(secretFindingToError(f))
    }
  }

  if (!isPlainObject(input)) {
    errors.push({
      code: 'TypeMismatch',
      path: '$',
      message: 'customer.yaml must parse to an object at the root',
    })
    return { ok: false, errors }
  }

  for (const f of scanParsedValue(input, '', options.scanOptions)) {
    errors.push(secretFindingToError(f))
  }

  const parsed = validateSections(input, errors)
  if (errors.length > 0) {
    return { ok: false, errors }
  }
  return { ok: true, value: assembleCustomerYaml(input, parsed) }
}

interface ParsedSections {
  schemaVersion: number
  customerId: string | null
  vertical: Vertical | null
  verticalVersion: string | null
  addons: AddonSpec[]
  practiceAreas: string[]
  machine: MachineSpec | null
  users: ReturnType<typeof checkUsers>
  personas: ReturnType<typeof checkPersonas>
  connectors: ReturnType<typeof checkConnectors>
  googleAuth: GoogleAuth | null
  scope: ReturnType<typeof checkScope>
  escalation: ReturnType<typeof checkEscalation>
  sendPolicy: ReturnType<typeof checkSendPolicy>
  memory: Memory | null
  voiceLibrary: ReturnType<typeof checkVoiceLibrary>
  voiceCohorts: ReturnType<typeof checkVoiceCohorts>
  seat: ReturnType<typeof checkSeat>
  outputClasses: ReturnType<typeof checkOutputClasses>
  businessHours: ReturnType<typeof checkBusinessHours>
  digest: ReturnType<typeof checkDigest>
  logging: ReturnType<typeof checkLogging>
  pause: ReturnType<typeof checkPause>
  observability: Observability
  webhookTriggers: ReturnType<typeof checkWebhookTriggers>
  custodyExceptions: ReturnType<typeof checkCustodyExceptions>
  complianceEnabled: boolean
  authority: AuthorityPosture
  credentialCustodyDefault: CredentialCustody
  mcpConnector: ReturnType<typeof checkMcpConnector>
  relationship: ReturnType<typeof checkRelationship>
}

function validateSections(
  root: Record<string, unknown>,
  errors: ValidationError[]
): ParsedSections {
  const schemaVersion = checkSchemaVersion(root, errors)
  const customerId = checkCustomerId(root, errors)
  checkRequiredString(root, 'customer_name', errors)
  const verticalResult = checkVerticalPinned(root, errors)
  const addons = checkAddons(root, errors)
  checkExtendsReserved(root, errors)
  const practiceAreas = checkPracticeAreas(root, verticalResult.vertical, errors)
  checkRequiredString(root, 'fly_region', errors)
  checkRequiredString(root, 'model', errors)
  checkOptionalString(root, 'escalation_model', errors) // ADR 0049 — escalate-up model
  checkRequiredString(root, 'hermes_ref', errors)
  checkHermesRef(root, errors)
  const machine = checkMachine(root, errors)
  const users = checkUsers(root, errors)
  const personas = checkPersonas(root, errors)
  const connectors = checkConnectors(root, customerId, errors)
  const googleAuth = checkGoogleAuth(root, errors)
  const scope = checkScope(root, errors)
  checkStaffMailboxReads(root, scope.staff_send_as, errors) // ADR 0089 5a; validate-only
  checkTelegram(root, errors) // optional telegram block; validate-only (ADR 0033)
  checkGmailPush(root, errors) // optional gmail_push block; validate-only
  checkFirmIdentity(root, errors) // optional firm_identity (letterhead); validate-only
  const escalation = checkEscalation(root, errors)
  const sendPolicy = checkSendPolicy(root, errors)
  const memory = checkMemory(root, customerId, verticalResult.vertical, errors)
  const webhookTriggers = checkWebhookTriggers(root, personas, connectors, errors)
  const mcpConnector = checkMcpConnector(root, users, personas, errors)
  // ADR 0044 Decision 8 / ADR 0045 §7 (#1841): code_execution vs gateway-held creds
  const custodyExceptions = checkCustodyExceptions(root, errors)
  checkCustodyGuard(root, personas, connectors, custodyExceptions, errors)
  return {
    schemaVersion,
    customerId,
    vertical: verticalResult.vertical,
    verticalVersion: verticalResult.version,
    addons,
    practiceAreas,
    machine,
    users,
    personas,
    connectors,
    googleAuth,
    scope,
    escalation,
    sendPolicy,
    memory,
    voiceLibrary: checkVoiceLibrary(root, errors),
    voiceCohorts: checkVoiceCohorts(root, errors),
    seat: checkSeat(root, errors),
    outputClasses: checkOutputClasses(root, errors),
    businessHours: checkBusinessHours(root, errors),
    digest: checkDigest(root, errors),
    logging: checkLogging(root, errors),
    pause: checkPause(root, errors),
    observability: checkObservability(root, errors),
    webhookTriggers,
    custodyExceptions,
    complianceEnabled: checkComplianceEnabled(root, errors),
    authority: checkAuthority(root, errors),
    credentialCustodyDefault: checkCredentialCustodyDefault(root, errors),
    mcpConnector,
    relationship: checkRelationship(root, errors),
  }
}

function assembleCustomerYaml(root: Record<string, unknown>, p: ParsedSections): CustomerYaml {
  // Every nullable section here is guaranteed non-null by the caller — we
  // early-returned on errors.length > 0 before reaching this point.
  return {
    schema_version: p.schemaVersion as SchemaVersion,
    customer_id: p.customerId as string,
    customer_name: root['customer_name'] as string,
    vertical: p.vertical as Vertical,
    vertical_version: p.verticalVersion,
    addons: p.addons,
    practice_areas: p.practiceAreas,
    fly_region: root['fly_region'] as string,
    model: root['model'] as string,
    escalation_model: (root['escalation_model'] as string) ?? null,
    hermes_ref: root['hermes_ref'] as string,
    machine: p.machine as MachineSpec,
    users: p.users,
    personas: p.personas,
    connectors: p.connectors,
    google_auth: p.googleAuth,
    scope: p.scope,
    escalation: p.escalation,
    send_policy: p.sendPolicy,
    voice_library: p.voiceLibrary,
    voice_cohorts: p.voiceCohorts,
    seat: p.seat,
    output_classes: p.outputClasses,
    business_hours: p.businessHours,
    digest: p.digest,
    memory: p.memory as Memory,
    logging: p.logging,
    pause: p.pause,
    observability: p.observability,
    webhook_triggers: p.webhookTriggers,
    custody_exceptions: p.custodyExceptions ?? [],
    compliance_enabled: p.complianceEnabled,
    authority: p.authority,
    credential_custody_default: p.credentialCustodyDefault,
    mcp_connector: p.mcpConnector,
    relationship: p.relationship,
  }
}
