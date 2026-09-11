import type { APIContext, APIRoute } from 'astro'
import { getQuote } from '../../../../../lib/db/quotes'
import { getEntity } from '../../../../../lib/db/entities'
import { getContact } from '../../../../../lib/db/contacts'
import { scheduleProposalCadence } from '../../../../../lib/follow-ups/scheduler'
import { authorizeAndSendSOW } from '../../../../../lib/sow/service'
import { env } from 'cloudflare:workers'
import { requireAdminSession } from '../../../../../lib/auth/admin-session'
import { errorResponse } from '../../../../../lib/api/helpers'

/**
 * POST /api/admin/quotes/:id/sign
 *
 * Sends a quote's SOW PDF to SignWell for e-signature collection.
 *
 * Preconditions:
 * - Quote status must be 'draft' or 'sent'
 * - Latest SOW revision must exist
 * - Explicit signer_contact_id required
 * - No open signature request yet
 *
 * Process:
 * 1. Retrieve SOW PDF from R2
 * 2. Snapshot signer details
 * 3. Record send authorization
 * 4. Create signature request in SignWell
 * 5. Transition quote to 'sent' if currently 'draft'
 * 6. Redirect back to quote detail page
 *
 * Protected by auth middleware (requires admin role).
 */

type Redirect = APIContext['redirect']

async function validateSignPreconditions(
  redirect: Redirect,
  orgId: string,
  quoteId: string,
  formData: FormData
) {
  const quote = await getQuote(env.DB, orgId, quoteId)
  if (!quote) {
    return { error: redirect('/admin/entities?error=not_found', 302) }
  }

  if (quote.status !== 'draft' && quote.status !== 'sent') {
    return {
      error: redirect(
        `/admin/entities/${quote.entity_id}/quotes/${quoteId}?error=invalid_transition`,
        302
      ),
    }
  }

  const signerContactId = formData.get('signer_contact_id')
  if (!signerContactId || typeof signerContactId !== 'string') {
    return {
      error: redirect(
        `/admin/entities/${quote.entity_id}/quotes/${quoteId}?error=no_contact_email`,
        302
      ),
    }
  }

  const entity = await getEntity(env.DB, orgId, quote.entity_id)
  if (!entity) {
    return {
      error: redirect(
        `/admin/entities/${quote.entity_id}/quotes/${quoteId}?error=client_not_found`,
        302
      ),
    }
  }

  const signerContact = await getContact(env.DB, orgId, signerContactId)
  if (!signerContact?.email) {
    return {
      error: redirect(
        `/admin/entities/${quote.entity_id}/quotes/${quoteId}?error=no_contact_email`,
        302
      ),
    }
  }

  return { quote, entity, signerContact }
}

async function handlePost({ request, locals, redirect, params }: APIContext): Promise<Response> {
  const auth = requireAdminSession(locals)
  if (!auth.ok) return auth.response
  const { session } = auth

  const quoteId = params.id
  if (!quoteId) {
    return errorResponse(400, 'validation_failed', 'Quote ID required.')
  }

  // Verify SignWell API key is configured
  const apiKey = env.SIGNWELL_API_KEY
  if (!apiKey) {
    console.error('[api/admin/quotes/[id]/sign] SIGNWELL_API_KEY not configured')
    return redirect(`/admin/entities?error=server`, 302)
  }

  try {
    const formData = await request.formData()
    const validation = await validateSignPreconditions(redirect, session.orgId, quoteId, formData)
    if ('error' in validation) return validation.error as Response

    const { quote, entity, signerContact } = validation

    const signatureRequest = await authorizeAndSendSOW({
      db: env.DB,
      storage: env.STORAGE,
      apiKey,
      orgId: session.orgId,
      actorId: session.userId,
      quote,
      entityName: entity.name,
      signer: {
        contactId: signerContact.id,
        name: signerContact.name,
        email: signerContact.email!,
        title: signerContact.title,
      },
      callbackBaseEnv: env,
    })

    try {
      if (signatureRequest.sent_at) {
        await scheduleProposalCadence(
          env.DB,
          session.orgId,
          quoteId,
          quote.entity_id,
          signatureRequest.sent_at
        )
      }
    } catch (err) {
      console.error(
        '[api/admin/quotes/[id]/sign] Follow-up scheduling error:',
        err instanceof Error ? err.message : String(err)
      )
    }

    return redirect(`/admin/entities/${quote.entity_id}/quotes/${quoteId}?saved=1`, 302)
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    console.error('[api/admin/quotes/[id]/sign] Error:', msg, err instanceof Error ? err.stack : '')
    return redirect(
      `/admin/entities?error=server&detail=${encodeURIComponent(msg.slice(0, 200))}`,
      302
    )
  }
}

export const POST: APIRoute = (ctx) => handlePost(ctx)
