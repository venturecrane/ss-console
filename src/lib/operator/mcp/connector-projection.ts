/**
 * The projected `customer_configs.mcp_connector_json` column, parsed into a
 * runtime `McpConnector`.
 *
 * DEFENSIVE / FAIL-CLOSED: a null column, malformed JSON, or a shape-wrong
 * value all resolve to the disabled default rather than throwing. Two
 * reasons: (1) this column is read on the live client portal, and a corrupt
 * value must not 500 the page the way a corrupt `personas_json` does; (2) for
 * the MCP endpoint, "config we can't trust" must mean "grant nothing", never
 * "open up". A well-formed enabled block with valid `access[]` entries is
 * honored exactly; anything else collapses to disabled + empty access.
 *
 * Lived in `src/lib/portal/customer-config.ts` until 2026-09-10, which made
 * the MCP customer resolver (the lower Operator layer) import from the portal
 * layer (code review 2026-09-10, Architecture 4). The portal module
 * re-exports `parseMcpConnector`, so its callers are unchanged.
 */

import { z } from 'zod'
import {
  ACCEPTED_DATA_POSTURES,
  ACCEPTED_MCP_POLICIES,
  MCP_GRANT_TTL_DEFAULT_DAYS,
  MCP_GRANT_TTL_MAX_DAYS,
  type McpConnector,
} from '../customer-yaml/types'

/** Fail-closed `mcp_connector`: no user reaches the Operator through Claude. */
const MCP_CONNECTOR_FAIL_CLOSED: McpConnector = {
  enabled: false,
  data_posture: 'open',
  policy: 'allowlist',
  allowed_domains: [],
  default_profile: null,
  ttl_days: MCP_GRANT_TTL_DEFAULT_DAYS,
  access: [],
}

const DOMAIN_RE = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/

/** Defensive parse of the projected allowed_domains (lowercased, valid hosts only). */
function readAllowedDomains(raw: unknown): string[] {
  if (!Array.isArray(raw)) return []
  const out: string[] = []
  for (const entry of raw) {
    const value = typeof entry === 'string' ? entry.trim().toLowerCase() : ''
    if (value && DOMAIN_RE.test(value)) out.push(value)
  }
  return out
}

/** Defensive parse of the projected ttl_days; out-of-range/garbage → default. */
function readTtlDays(raw: unknown): number {
  if (
    typeof raw !== 'number' ||
    !Number.isInteger(raw) ||
    raw < 1 ||
    raw > MCP_GRANT_TTL_MAX_DAYS
  ) {
    return MCP_GRANT_TTL_DEFAULT_DAYS
  }
  return raw
}

const mcpConnectorProjectionSchema = z.object({
  enabled: z.unknown().optional(),
  data_posture: z.unknown().optional(),
  policy: z.unknown().optional(),
  allowed_domains: z.unknown().optional(),
  default_profile: z.unknown().optional(),
  ttl_days: z.unknown().optional(),
  access: z.unknown().optional(),
})

const mcpConnectorAccessSchema = z.object({
  email: z.email(),
  profile: z.string().min(1),
  clerk_subject: z
    .string()
    .regex(/^user_[A-Za-z0-9]+$/)
    .optional(),
  clerk_subjects: z
    .array(z.string().regex(/^user_[A-Za-z0-9]+$/))
    .min(1)
    .optional(),
})

export function parseMcpConnector(json: string | null | undefined): McpConnector {
  if (json === null || json === undefined) return { ...MCP_CONNECTOR_FAIL_CLOSED, access: [] }
  let raw: unknown
  try {
    raw = JSON.parse(json)
  } catch {
    return { ...MCP_CONNECTOR_FAIL_CLOSED, access: [] }
  }
  const parsed = mcpConnectorProjectionSchema.safeParse(raw)
  if (!parsed.success) return { ...MCP_CONNECTOR_FAIL_CLOSED, access: [] }
  const posture = z.enum(ACCEPTED_DATA_POSTURES).safeParse(parsed.data.data_posture)
  const policy = z.enum(ACCEPTED_MCP_POLICIES).safeParse(parsed.data.policy)
  const defaultProfile =
    typeof parsed.data.default_profile === 'string' && parsed.data.default_profile.trim() !== ''
      ? parsed.data.default_profile
      : null
  const access = Array.isArray(parsed.data.access)
    ? parsed.data.access.flatMap((entry) => {
        const result = mcpConnectorAccessSchema.safeParse(entry)
        return result.success ? [result.data] : []
      })
    : []
  return {
    enabled: parsed.data.enabled === true,
    data_posture: posture.success ? posture.data : 'open',
    policy: policy.success ? policy.data : 'allowlist',
    allowed_domains: readAllowedDomains(parsed.data.allowed_domains),
    default_profile: defaultProfile,
    ttl_days: readTtlDays(parsed.data.ttl_days),
    access,
  }
}
