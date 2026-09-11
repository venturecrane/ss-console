/**
 * POST /api/admin/operator/[customer]/mcp-grants
 *
 * SMD issues or revokes a Claude-connector access grant (ADR 0057). The grant
 * table is the authoritative authorization layer and the kill switch, read live
 * on every MCP request — so a revoke here cuts the principal on their next call.
 *
 * Form fields:
 *   action        — 'issue' | 'revoke'
 *   clerk_user_id — the Clerk subject the grant authorizes (required)
 *   email         — the grantee's email           (issue only, required)
 *   profile       — the persona the session runs as (issue only, required)
 *   ttl_days      — bounded TTL, clamped to [1, 90]; default 30 (issue only)
 *   reason        — optional free-text note for the audit ledger
 *
 * Every issue/revoke writes an immutable row to operator_mcp_grant_audit with the
 * SMD actor. Status banner on the connectors page:
 *   ?status=grant_issued | grant_revoked | grant_not_found | invalid_* | not_found
 *
 * Admin-only (middleware on /api/admin/* + explicit re-check).
 */

import type { APIContext, APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { resolveEntityIdBySlug } from '../../../../../lib/admin/operator-overview'
import {
  adminIssueGrant,
  clampTtlDays,
  GRANT_TTL_DEFAULT_DAYS,
  revokeGrant,
} from '../../../../../lib/operator/mcp/grant-store'
import { requireAdminSession } from '../../../../../lib/auth/admin-session'

function redirectToConnectors(slug: string, status: string): Response {
  const target = `/admin/operator/${encodeURIComponent(slug)}/connectors?status=${encodeURIComponent(status)}`
  return new Response(null, { status: 303, headers: { Location: target } })
}

function optionalString(raw: FormDataEntryValue | null): string | null {
  return typeof raw === 'string' && raw.trim() !== '' ? raw.trim() : null
}

type ParsedForm =
  | { action: 'revoke'; clerkUserId: string; reason: string | null }
  | {
      action: 'issue'
      clerkUserId: string
      email: string
      profile: string
      ttlDays: number
      reason: string | null
    }

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/

/** Parse + validate without casting. Returns a status string on error. */
function parseForm(form: FormData): { error: string } | { parsed: ParsedForm } {
  const action = optionalString(form.get('action'))
  const clerkUserId = optionalString(form.get('clerk_user_id'))
  const reason = optionalString(form.get('reason'))
  if (!clerkUserId) return { error: 'invalid_subject' }

  if (action === 'revoke') {
    return { parsed: { action: 'revoke', clerkUserId, reason } }
  }
  if (action !== 'issue') return { error: 'invalid_action' }

  const email = optionalString(form.get('email'))
  if (!email || !EMAIL_RE.test(email)) return { error: 'invalid_email' }
  const profile = optionalString(form.get('profile'))
  if (!profile) return { error: 'invalid_profile' }

  const rawTtl = optionalString(form.get('ttl_days'))
  const ttlDays = rawTtl === null ? GRANT_TTL_DEFAULT_DAYS : clampTtlDays(Number(rawTtl))

  return { parsed: { action: 'issue', clerkUserId, email, profile, ttlDays, reason } }
}

async function handlePost(ctx: APIContext): Promise<Response> {
  const auth = requireAdminSession(ctx.locals)
  if (!auth.ok) return auth.response
  const { session } = auth

  const slug = ctx.params.customer ?? ''
  const entityId = await resolveEntityIdBySlug(env.DB, slug)
  if (!entityId) return redirectToConnectors(slug, 'not_found')

  const result = parseForm(await ctx.request.formData())
  if ('error' in result) return redirectToConnectors(slug, result.error)
  const parsed = result.parsed

  const auditCtx = { entityId, actor: session.email, reason: parsed.reason }

  if (parsed.action === 'issue') {
    await adminIssueGrant(
      env.DB,
      {
        customerSlug: slug,
        clerkUserId: parsed.clerkUserId,
        email: parsed.email,
        profile: parsed.profile,
        ttlDays: parsed.ttlDays,
      },
      auditCtx
    )
    return redirectToConnectors(slug, 'grant_issued')
  }

  const { changed } = await revokeGrant(
    env.DB,
    { customerSlug: slug, clerkUserId: parsed.clerkUserId },
    auditCtx
  )
  return redirectToConnectors(slug, changed ? 'grant_revoked' : 'grant_not_found')
}

export const POST: APIRoute = (ctx) => handlePost(ctx)
