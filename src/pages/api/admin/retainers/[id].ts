import type { APIRoute } from 'astro'
import { getRetainer, updateRetainerStatus } from '../../../../lib/db/retainers'
import type { RetainerStatus } from '../../../../lib/db/retainers'

export const POST: APIRoute = async ({ request, locals, redirect, params }) => {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const retainerId = params.id
  if (!retainerId) {
    return new Response(JSON.stringify({ error: 'Retainer ID required' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const env = locals.runtime.env

  try {
    const existing = await getRetainer(env.DB, session.orgId, retainerId)
    if (!existing) {
      return redirect('/admin/entities?error=not_found', 302)
    }

    const formData = await request.formData()
    const action = formData.get('action')

    if (action === 'transition_status') {
      const newStatus = formData.get('new_status')
      if (!newStatus || typeof newStatus !== 'string') {
        return redirect(`/admin/entities/${existing.entity_id}?error=invalid_status`, 302)
      }

      try {
        await updateRetainerStatus(env.DB, session.orgId, retainerId, newStatus as RetainerStatus)
      } catch (statusErr) {
        console.error('[api/admin/retainers/[id]] Status transition error:', statusErr)
        return redirect(`/admin/entities/${existing.entity_id}?error=invalid_transition`, 302)
      }

      return redirect(`/admin/entities/${existing.entity_id}?retainer_updated=1`, 302)
    }

    return redirect(`/admin/entities/${existing.entity_id}?error=unknown_action`, 302)
  } catch (err) {
    console.error('[api/admin/retainers/[id]] Error:', err)
    return redirect('/admin/entities?error=server', 302)
  }
}
