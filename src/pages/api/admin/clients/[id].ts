import type { APIRoute } from 'astro'
import { updateClient } from '../../../../lib/db/clients'

/**
 * PUT /api/admin/clients/:id
 *
 * Updates an existing client from form data and redirects back to the client detail page.
 *
 * Protected by auth middleware (requires admin role).
 *
 * Accepts the same fields as create. Only non-empty fields are updated.
 */
export const POST: APIRoute = async ({ request, locals, redirect, params }) => {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const clientId = params.id
  if (!clientId) {
    return new Response(JSON.stringify({ error: 'Client ID required' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  try {
    const formData = await request.formData()
    const businessName = formData.get('business_name')
    const source = formData.get('source')

    // Validate required fields
    if (
      !businessName ||
      typeof businessName !== 'string' ||
      !businessName.trim() ||
      !source ||
      typeof source !== 'string' ||
      !source.trim()
    ) {
      return redirect(`/admin/clients/${clientId}?error=missing`, 302)
    }

    const env = locals.runtime.env
    const vertical = formData.get('vertical')
    const employeeCount = formData.get('employee_count')
    const yearsInBusiness = formData.get('years_in_business')
    const referredBy = formData.get('referred_by')
    const status = formData.get('status')
    const notes = formData.get('notes')

    const updated = await updateClient(env.DB, session.orgId, clientId, {
      business_name: businessName.trim(),
      vertical:
        vertical && typeof vertical === 'string' && vertical.trim() ? vertical.trim() : null,
      employee_count:
        employeeCount && typeof employeeCount === 'string' && employeeCount.trim()
          ? parseInt(employeeCount, 10) || null
          : null,
      years_in_business:
        yearsInBusiness && typeof yearsInBusiness === 'string' && yearsInBusiness.trim()
          ? parseInt(yearsInBusiness, 10) || null
          : null,
      source: source.trim(),
      referred_by:
        referredBy && typeof referredBy === 'string' && referredBy.trim()
          ? referredBy.trim()
          : null,
      status: status && typeof status === 'string' && status.trim() ? status.trim() : undefined,
      notes: notes && typeof notes === 'string' ? notes.trim() || null : undefined,
    })

    if (!updated) {
      return redirect('/admin/clients?error=not_found', 302)
    }

    return redirect(`/admin/clients/${clientId}?saved=1`, 302)
  } catch (err) {
    console.error('[api/admin/clients/[id]] Update error:', err)
    return redirect(`/admin/clients/${clientId}?error=server`, 302)
  }
}
