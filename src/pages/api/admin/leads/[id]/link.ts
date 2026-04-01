import type { APIRoute } from 'astro'
import { linkToClient } from '../../../../../lib/db/lead-signals'

/**
 * POST /api/admin/leads/[id]/link
 *
 * Link a lead signal to an existing client record.
 * Sets triage_status to 'promoted' and associates the client_id.
 *
 * Protected by auth middleware (requires admin role).
 */
export const POST: APIRoute = async ({ params, request, locals, redirect }) => {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const signalId = params.id
  if (!signalId) {
    return redirect('/admin/leads?error=missing', 302)
  }

  try {
    const formData = await request.formData()
    const clientId = formData.get('client_id')

    if (!clientId || typeof clientId !== 'string' || !clientId.trim()) {
      return redirect('/admin/leads?error=missing', 302)
    }

    const env = locals.runtime.env
    await linkToClient(env.DB, session.orgId, signalId, clientId.trim())

    return redirect('/admin/leads?saved=1', 302)
  } catch (err) {
    console.error('[api/admin/leads/link] Error:', err)
    return redirect('/admin/leads?error=server', 302)
  }
}
