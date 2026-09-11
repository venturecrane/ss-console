/**
 * Credential-custody guard — ADR 0044 Decision 8 / ADR 0045 §7 (#1841).
 *
 * Non-refused `code_execution` exposure lets agent-authored code read the
 * gateway process environment, where every raw connector/channel credential
 * lives — bypassing first-class tool classification entirely (the exact
 * bypass class ADR 0045 exists to close). This guard rejects a customer.yaml
 * that authors code_execution alongside gateway-held credential surfaces,
 * unless each offending surface is explicitly accepted in the top-level
 * `custody_exceptions` list.
 *
 * Eligibility is enum-limited to IDENTITY-CHANNEL adapters (the seat's own
 * channels — blast radius is the seat impersonating itself). Client-data
 * adapters (smokeball, clio, microsoft-graph, ...) are NEVER
 * exception-eligible: ADR 0045 — no paying client launches with a raw
 * privileged connector credential reachable from the gateway.
 *
 * Surfaces are the AUTHORED approximation of "a raw cred is in the gateway
 * env": enabled `connectors{}` entries whose backend is not broker-mediated,
 * the `telegram` channel block, and a persona `send_as.agentmail_identity`.
 * The live-runtime env scan (ADR 0045 verification item 10) is the runtime
 * backstop, not this validator. Disposition record + rationale per adapter:
 * `operator/contracts/connector-custody-dispositions.md`.
 *
 * Mirrors the overlay validator (`bootstrap/validate.py`
 * `_validate_custody_guard`) — parity pinned by the fixtures contract.
 */

import type { CapabilityName } from '../capabilities/types'
import type { Connector, Persona, ValidationError } from './types'
import { isPlainObject } from './helpers'

/** Identity-channel adapters that MAY be excepted (never client-data). */
const CUSTODY_EXCEPTION_ELIGIBLE = ['telegram', 'agentmail', 'brave'] as const
export type CustodyExceptionAdapter = (typeof CUSTODY_EXCEPTION_ELIGIBLE)[number]

/**
 * connectors{} backends whose credential lives behind the broker boundary.
 * None today: Google rides the `google_auth` block, broker-held by
 * construction, not a connectors{} backend. Grows as ADR 0045 migration
 * step 7 moves connectors behind the broker.
 */
const BROKER_MEDIATED_BACKENDS: ReadonlySet<string> = new Set()

/**
 * Parse the optional top-level `custody_exceptions` list. Returns the parsed
 * list (empty when absent) or null on a validation error.
 */
export function checkCustodyExceptions(
  root: Record<string, unknown>,
  errors: ValidationError[]
): CustodyExceptionAdapter[] | null {
  const raw = root['custody_exceptions']
  if (raw === undefined || raw === null) return []
  if (!Array.isArray(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'custody_exceptions',
      message: 'custody_exceptions must be a list when present',
    })
    return null
  }
  const out: CustodyExceptionAdapter[] = []
  // Array.isArray narrows `unknown` to `any[]`; name the elements unknown so
  // each one is proven eligible below rather than assumed.
  const entries: unknown[] = raw
  for (let i = 0; i < entries.length; i++) {
    const entry = entries[i]
    if (
      typeof entry !== 'string' ||
      !(CUSTODY_EXCEPTION_ELIGIBLE as readonly string[]).includes(entry)
    ) {
      errors.push({
        code: 'IneligibleCustodyException',
        path: `custody_exceptions[${i}]`,
        message:
          `${JSON.stringify(entry)} is not exception-eligible — identity-channel adapters only ` +
          `(${CUSTODY_EXCEPTION_ELIGIBLE.join(', ')}); client-data connectors can never be ` +
          'excepted (ADR 0045)',
      })
      return null
    }
    if (out.includes(entry as CustodyExceptionAdapter)) {
      errors.push({
        code: 'IneligibleCustodyException',
        path: `custody_exceptions[${i}]`,
        message: `duplicate entry "${entry}"`,
      })
      return null
    }
    out.push(entry as CustodyExceptionAdapter)
  }
  return out
}

/**
 * The guard itself. Called after personas/connectors parse; reads the raw
 * `telegram` block directly (that section is validate-only and returns no
 * parsed value).
 */
export function checkCustodyGuard(
  root: Record<string, unknown>,
  personas: Persona[],
  connectors: Partial<Record<CapabilityName, Connector>>,
  exceptions: CustodyExceptionAdapter[] | null,
  errors: ValidationError[]
): void {
  if (exceptions === null) return // the exceptions list itself failed validation
  const offenders = personas.filter((p) => {
    const ceiling = p.entitlements.exposure['code_execution']
    return ceiling !== undefined && ceiling !== 'refused'
  })
  if (offenders.length === 0) return

  const surfaces = new Set<string>()
  for (const c of Object.values(connectors)) {
    if (c && c.enabled && !BROKER_MEDIATED_BACKENDS.has(c.backend)) {
      surfaces.add(c.adapter)
    }
  }
  const telegram = root['telegram']
  // Block present and not explicitly disabled counts (fail-closed): the bot
  // token is a gateway-env credential whenever the channel is wired.
  if (isPlainObject(telegram) && telegram['enabled'] !== false) {
    surfaces.add('telegram')
  }
  for (const p of personas) {
    if (p.send_as !== null) surfaces.add('agentmail')
  }

  const uncovered = [...surfaces].filter((s) => !(exceptions as string[]).includes(s)).sort()
  if (uncovered.length === 0) return
  errors.push({
    code: 'CustodyGuardViolation',
    path: 'personas.entitlements.exposure.code_execution',
    message:
      `personas (${offenders.map((p) => p.slug).join(', ')}) author non-refused code_execution ` +
      `while gateway-held credential surfaces exist without an authored custody exception: ` +
      `[${uncovered.join(', ')}]. Executed code can read these credentials from the gateway ` +
      'env, bypassing tool classification (ADR 0044 Decision 8 / ADR 0045, #1841). Either ' +
      'author code_execution: refused, move the connector behind the broker, or accept an ' +
      'IDENTITY-CHANNEL surface explicitly via top-level custody_exceptions (client-data ' +
      'connectors are never eligible).',
  })
}
