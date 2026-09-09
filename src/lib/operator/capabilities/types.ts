/**
 * Shared types for the Operator capability layer.
 *
 * Per ADR 0006, skills bind to capability interfaces; vendor adapters
 * implement them; customer.yaml wires which adapter serves which capability.
 * This module is the only place skill code, adapter code, and the
 * conformance harness all agree on shared shapes (DateRange, error
 * contracts, capability disclosure).
 *
 * Anything specific to one capability lives in that capability's own file
 * (matter shapes in practice-management.ts, draft shapes in email.ts, etc.).
 * Keep this module narrow.
 */

/**
 * Canonical capability names. The eleven Platform PRD §7.2 capabilities plus
 * WebSearch (ADR 0070), recorded here as a closed string union so adapters
 * cannot register against an unrecognized name. Add new capabilities here only
 * when an ADR records the addition.
 *
 * `WebSearch` is a connector-only capability: it has NO skill-facing adapter
 * interface in this layer (no `web-search.ts`). It exists solely so the
 * `connectors:` map — keyed by this union — can bind a `native:<provider>`
 * backend (ADR 0070, e.g. `native:brave-free`). The search tool is Hermes'
 * NATIVE `web_search` (a bundled web provider), which the agent calls directly;
 * the overlay's translate.py resolves `native:<provider>` to config
 * `web.search_backend`. Its conformance entry (BANNED_METHOD_NAMES) is therefore
 * empty — there is no adapter to conform. (The first ADR 0070 cut wrapped Brave
 * in an MCP server, `mcp:brave`; that redundant layer was retired 2026-07-08.)
 */
export type CapabilityName =
  | 'PracticeManagement'
  | 'Email'
  | 'Calendar'
  | 'DocumentStorage'
  | 'ESign'
  | 'CourtAccess'
  | 'Payments'
  | 'Accounting'
  | 'IntakeCRM'
  | 'CallTracking'
  | 'InternalComms'
  | 'WebSearch'

/**
 * Inclusive-start / exclusive-end ISO 8601 date range. Used by every
 * capability that lists time-bound records. `until` may be `null` to mean
 * "no upper bound" (e.g. "list every time entry since the matter opened").
 *
 * @public Capability-adapter contract, consumed by src/lib/operator/capabilities/email.ts and its
 * sibling adapters, which knip reports unused (not runtime-wired). Retiring the layer is
 * a Captain decision, not this gate's.
 */
export interface DateRange {
  /** ISO 8601 timestamp. Inclusive. */
  since: string
  /** ISO 8601 timestamp. Exclusive. `null` means open-ended. */
  until: string | null
}

/**
 * Result of `describe_capabilities()`. Every adapter returns one of these
 * so the dashboard can render "what Marcus used to write this" and the
 * boot-time conformance harness can verify the adapter satisfies the
 * interface's required-method set.
 */
export interface CapabilitySet {
  /** Name of the capability this adapter implements. */
  capability: CapabilityName
  /** Adapter slug — e.g. "filevine", "microsoft-graph", "docusign". */
  adapter: string
  /** Adapter version string. Semver recommended but not enforced. */
  version: string
  /**
   * Method names this adapter implements. Must be a superset of the
   * interface's required-method set; may include any subset of the
   * interface's optional methods.
   */
  supported_methods: string[]
  /**
   * Optional methods this adapter declares it does NOT implement. Skills
   * that depend on an unsupported optional method should degrade
   * gracefully or surface an explicit "this adapter doesn't support X"
   * message; the conformance harness asserts adapters do not silently
   * stub these.
   */
  unsupported_methods: string[]
  /**
   * Per-method field-coverage disclosure for the dashboard "what Marcus
   * used" sourcing block (PRD §12, Synthesis Theme 21). Optional in the
   * interface but strongly recommended — without it the dashboard renders
   * "source unknown" rather than the field-level attribution per ADR 0006.
   */
  field_coverage?: Record<string, FieldCoverage>
}

/**
 * Per-method field disclosure. Says which fields of the return shape
 * are populated from source data, which are not populated by this
 * adapter (so the skill should not rely on them), and which were derived
 * by adapter inference rather than read directly from the source system.
 */
export interface FieldCoverage {
  populated: string[]
  not_populated: string[]
  derived: string[]
}

/**
 * Adapter health status returned by `health_check()`. Adapters use this
 * to signal whether the underlying integration is currently usable. The
 * control plane polls this and surfaces the per-customer dashboard
 * aliveness signal (PRD §12, issue #875).
 */
export interface HealthStatus {
  status: 'healthy' | 'degraded' | 'unhealthy'
  /** ISO 8601 timestamp of when the adapter last successfully reached
   * the underlying system. Null on unhealthy and never-reached. */
  last_ok_at: string | null
  /** Human-readable detail. Adapters should keep this short and
   * non-sensitive — it surfaces in the dashboard. */
  message?: string
  /** Adapter-specific machine-readable fields for debugging. Not
   * surfaced in the dashboard; logged for the control plane. */
  details?: Record<string, unknown>
}

/**
 * Closed set of error codes adapters throw. The conformance harness asserts
 * adapters only throw these codes — opaque errors are a bug because skills
 * cannot reason about them. Skill code switches on `code`, not on
 * `message`.
 *
 *  - `not_found`: adapters should prefer returning `null` over throwing
 *    this. Reserved for cases where the absence is semantically distinct
 *    from a normal "no row" (e.g. the parent matter was deleted).
 *  - `unauthorized`: the adapter's OAuth token is missing, expired, or
 *    lacks the required scope.
 *  - `rate_limited`: the underlying vendor rate-limited the call. Skills
 *    should back off; the runtime layer handles retry policy.
 *  - `transient`: a vendor-side server error or network blip. Retry safe.
 *  - `capability_not_supported`: the adapter does not implement this
 *    optional method. The capability_set should already declare this in
 *    `unsupported_methods`; this throw is the runtime defense.
 *  - `scope_violation`: the call would access data the customer.yaml
 *    scope envelope blocks (e.g. an email folder marked as blind).
 *  - `fabrication_blocked`: the adapter refused to return content that
 *    would violate invariant #8 (e.g. an inferred field with no source
 *    evidence). Per CLAUDE.md no-fabrication rule.
 *  - `validation_failed`: the call's input did not satisfy the adapter's
 *    or vendor's validation rules.
 *  - `unknown`: the adapter cannot map the underlying error to one of
 *    the above. Skill code treats this as non-retryable.
 */
export type AdapterErrorCode =
  | 'not_found'
  | 'unauthorized'
  | 'rate_limited'
  | 'transient'
  | 'capability_not_supported'
  | 'scope_violation'
  | 'fabrication_blocked'
  | 'validation_failed'
  | 'unknown'

/**
 * Canonical error type thrown by adapters. Skill code switches on
 * `code`; adapters set `cause` to the underlying vendor exception so the
 * audit log can record what actually went wrong without re-throwing
 * vendor-shaped objects.
 *
 * @public Capability-adapter contract, consumed by src/lib/operator/capabilities/conformance.ts
 * and src/lib/operator/capabilities/index.ts, which knip reports unused (not runtime-wired).
 * Retiring the layer is a Captain decision, not this gate's.
 */
export class AdapterError extends Error {
  readonly code: AdapterErrorCode
  readonly adapter: string
  readonly capability: CapabilityName
  /** Original vendor error if any. Audit-logged, never surfaced to the user. */
  readonly cause?: unknown

  constructor(
    code: AdapterErrorCode,
    capability: CapabilityName,
    adapter: string,
    message: string,
    cause?: unknown
  ) {
    super(message, cause === undefined ? undefined : { cause })
    this.name = 'AdapterError'
    this.code = code
    this.capability = capability
    this.adapter = adapter
    this.cause = cause
  }
}

/**
 * Every capability interface extends this base. Adapters implement the
 * two methods on top of the capability-specific surface.
 *
 * @public Capability-adapter contract, consumed by src/lib/operator/capabilities/email.ts and its
 * sibling adapters, which knip reports unused (not runtime-wired). Retiring the layer is
 * a Captain decision, not this gate's.
 */
export interface AdapterBase {
  /**
   * Returns the adapter's CapabilitySet — used by the conformance
   * harness, the dashboard sourcing block, and operator tooling.
   * Synchronous because it never crosses the network: it reflects
   * the adapter's static declaration.
   */
  describe_capabilities(): CapabilitySet

  /**
   * Returns the adapter's current health. Adapters may implement this
   * with a lightweight ping or a cached recent result; skills should
   * treat health checks as advisory, not authoritative.
   */
  health_check(): Promise<HealthStatus>
}

/**
 * A signed, opaque reference to an external resource. Adapters return
 * these instead of raw vendor IDs so skill code does not depend on the
 * vendor's ID shape. The reference round-trips through the adapter for
 * subsequent calls.
 *
 * @public Capability-adapter contract, consumed by src/lib/operator/capabilities/index.ts, which
 * knip reports unused (not runtime-wired). Retiring the layer is a Captain decision, not
 * this gate's.
 */
export interface OpaqueRef {
  /** Adapter-internal stable identifier. Treat as opaque. */
  id: string
  /** Adapter slug that issued this reference. Used to route subsequent calls. */
  adapter: string
  /** ISO 8601 timestamp when the reference was issued. */
  issued_at: string
}
