import type { D1Database } from '@cloudflare/workers-types'
import { z } from 'zod'
import type { McpConnector } from '../customer-yaml/types'
import { parseMcpConnector } from './connector-projection'
import { normalizeEmail } from '../../identity/email'

const CUSTOMER_SLUG = /^[a-z0-9][a-z0-9-]{0,31}$/
const MCP_RESOURCE_PREFIX = '/api/operator'
const httpsUrlSchema = z.url().refine((value) => new URL(value).protocol === 'https:', {
  message: 'MCP OAuth URLs must use HTTPS',
})

const bindingRowSchema = z.object({
  entity_id: z.string().min(1),
  customer_slug: z.string().regex(CUSTOMER_SLUG),
  issuer: httpsUrlSchema,
  resource_uri: httpsUrlSchema,
  client_id: z.string().min(1).nullable(),
  clerk_app_id: z.string().min(1).nullable(),
  clerk_org_id: z.string().min(1).nullable(),
  mcp_connector_json: z.string().nullable(),
})

const userRowSchema = z.object({
  id: z.string().min(1),
  email: z.email(),
  clerk_user_id: z.string().min(1).nullable(),
})

const grantRowSchema = z.object({
  clerk_user_id: z.string().min(1),
  email: z.email(),
  profile: z.string().min(1),
})

export interface AuthorizedMcpPrincipal {
  localUserId: string
  clerkUserId: string
  email: string
  profile: string
}

export interface ClerkCustomerBinding {
  issuer: string
  resourceUri: string
  clientId: string | null
  clerkAppId: string | null
}

export interface ResolvedMcpCustomer {
  entityId: string
  customerId: string
  clerkOrgId: string | null
  connector: McpConnector
  clerk: ClerkCustomerBinding
  principals: AuthorizedMcpPrincipal[]
}

export function buildMcpResourcePath(customerSlug: string): string {
  if (!CUSTOMER_SLUG.test(customerSlug)) throw new Error('invalid MCP customer slug')
  return `${MCP_RESOURCE_PREFIX}/${customerSlug}/mcp`
}

export function buildMcpMetadataPath(customerSlug: string): string {
  return `/.well-known/oauth-protected-resource${buildMcpResourcePath(customerSlug)}`
}

export function parseMcpResourcePath(pathname: string): string | null {
  const match = /^\/api\/operator\/([a-z0-9][a-z0-9-]{0,31})\/mcp\/?$/.exec(pathname)
  return match?.[1] ?? null
}

export function parseMcpMetadataResource(resource: string | undefined): string | null {
  const path = `/${(resource ?? '').replace(/^\/+/, '')}`
  return parseMcpResourcePath(path)
}

function resolvePrincipals(
  connector: McpConnector,
  userRows: readonly z.infer<typeof userRowSchema>[]
): AuthorizedMcpPrincipal[] {
  // Both sides folded through the shared identity normalization: the authored
  // `mcp_connector.access[]` email is hand-typed into customer.yaml and the
  // users row carries whatever casing its IdP returned. A miss here does not
  // error — the entry is dropped from the principal set, so the person simply
  // has no MCP access and nothing says why.
  const usersByEmail = new Map(userRows.map((user) => [normalizeEmail(user.email), user]))
  return connector.access.flatMap((entry) => {
    const user = usersByEmail.get(normalizeEmail(entry.email))
    if (!user) return []
    const clerkUserIds = [
      ...(entry.clerk_subjects ?? []),
      ...(entry.clerk_subject ? [entry.clerk_subject] : []),
      ...(entry.clerk_subject || entry.clerk_subjects ? [] : [user.clerk_user_id]),
    ].filter((subject): subject is string => subject !== null)
    return [...new Set(clerkUserIds)].map((clerkUserId) => ({
      localUserId: user.id,
      clerkUserId,
      email: user.email,
      profile: entry.profile,
    }))
  })
}

/**
 * Merge live access grants (ADR 0057) into the authored principal set. A grant
 * is a dynamic authorization — JIT-created on an "open" issuance policy, or
 * seeded for an "allowlist" policy — that authorizes a Clerk subject until its
 * bounded `expires_at`. Authored `mcp_connector.access[]` principals take
 * precedence: a subject already authored is left untouched (its local user id
 * and profile win). A granted subject's `localUserId` follows the one rule in
 * `localUserIdForSubject`: the local `users.id` when the entity has a row for
 * that Clerk subject, else the Clerk subject itself (a JIT firm employee who is
 * not a portal user). The JIT path in `mcp-route.ts` applies the same rule at
 * grant time, so an audit row's `local_user_id` means the same thing whether
 * the grant was minted a second ago or read back on the next request.
 */
function mergeGrantPrincipals(
  authored: readonly AuthorizedMcpPrincipal[],
  grantRows: readonly z.infer<typeof grantRowSchema>[],
  userRows: readonly z.infer<typeof userRowSchema>[]
): AuthorizedMcpPrincipal[] {
  const bySubject = new Map(authored.map((principal) => [principal.clerkUserId, principal]))
  for (const grant of grantRows) {
    if (bySubject.has(grant.clerk_user_id)) continue
    bySubject.set(grant.clerk_user_id, {
      localUserId: localUserIdForSubject(userRows, grant.clerk_user_id),
      clerkUserId: grant.clerk_user_id,
      email: grant.email,
      profile: grant.profile,
    })
  }
  return [...bySubject.values()]
}

/**
 * The one rule for what `localUserId` means for a Clerk subject that is not an
 * authored principal: the entity's `users.id` when such a row carries the
 * subject, else the subject itself. Two id spaces used to share the column
 * with nothing saying which was which (2026-09-09 review, Security C6).
 */
export function localUserIdForSubject(
  userRows: readonly { id: string; clerk_user_id: string | null }[],
  clerkUserId: string
): string {
  const user = userRows.find((row) => row.clerk_user_id === clerkUserId)
  return user ? user.id : clerkUserId
}

/**
 * DB-backed form of `localUserIdForSubject` for the JIT path, which holds a
 * resolved customer but not its user rows.
 */
export async function resolveLocalUserIdForSubject(
  db: D1Database,
  entityId: string,
  clerkUserId: string
): Promise<string> {
  const row = await db
    .prepare('SELECT id FROM users WHERE entity_id = ? AND clerk_user_id = ?')
    .bind(entityId, clerkUserId)
    .first<{ id: string }>()
  return row ? row.id : clerkUserId
}

export async function loadMcpCustomer(
  db: D1Database,
  customerSlug: string
): Promise<ResolvedMcpCustomer | null> {
  if (!CUSTOMER_SLUG.test(customerSlug)) return null
  const rawBinding = await db
    .prepare(
      'SELECT b.entity_id, b.customer_slug, b.issuer, b.resource_uri, b.client_id, ' +
        'b.clerk_app_id, e.clerk_org_id, c.mcp_connector_json ' +
        'FROM mcp_clerk_bindings b ' +
        'LEFT JOIN customer_configs c ON c.entity_id = b.entity_id ' +
        'LEFT JOIN entities e ON e.id = b.entity_id ' +
        'WHERE b.customer_slug = ?'
    )
    .bind(customerSlug)
    .first<unknown>()
  if (!rawBinding) return null
  const binding = bindingRowSchema.parse(rawBinding)

  const rawUsers = await db
    .prepare('SELECT id, email, clerk_user_id FROM users WHERE entity_id = ?')
    .bind(binding.entity_id)
    .all<unknown>()
  const users = z.array(userRowSchema).parse(rawUsers.results ?? [])
  const connector = parseMcpConnector(binding.mcp_connector_json)

  // Live access grants (ADR 0057): the dynamic authorization + kill-switch layer.
  // Only un-revoked, un-expired rows authorize. ISO-8601 UTC compares
  // chronologically, so the expiry filter runs in SQL — a revoked or lapsed
  // grant is simply absent from the principal set on the very next request.
  const rawGrants = await db
    .prepare(
      'SELECT clerk_user_id, email, profile FROM mcp_issued_grants ' +
        'WHERE customer_slug = ? AND revoked_at IS NULL ' +
        "AND expires_at > strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
    )
    .bind(binding.customer_slug)
    .all<unknown>()
  const grants = z.array(grantRowSchema).parse(rawGrants.results ?? [])

  return {
    entityId: binding.entity_id,
    customerId: binding.customer_slug,
    clerkOrgId: binding.clerk_org_id,
    connector,
    clerk: {
      issuer: binding.issuer,
      resourceUri: binding.resource_uri,
      clientId: binding.client_id,
      clerkAppId: binding.clerk_app_id,
    },
    principals: mergeGrantPrincipals(resolvePrincipals(connector, users), grants, users),
  }
}
