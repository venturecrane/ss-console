import type { APIRoute } from 'astro'
import { createClient } from '../../../../lib/db/clients'
import { linkToClient } from '../../../../lib/db/lead-signals'

/**
 * POST /api/admin/clients
 *
 * Creates a new client from form data and redirects to the client detail page.
 *
 * Protected by auth middleware (requires admin role).
 *
 * Form fields:
 *   - business_name (required)
 *   - vertical
 *   - employee_count
 *   - years_in_business
 *   - source (required per OQ-002)
 *   - referred_by
 *   - status
 *   - notes
 */
export const POST: APIRoute = async ({ request, locals, redirect }) => {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
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
      return redirect('/admin/clients/new?error=missing', 302)
    }

    const env = locals.runtime.env
    const vertical = formData.get('vertical')
    const employeeCount = formData.get('employee_count')
    const yearsInBusiness = formData.get('years_in_business')
    const referredBy = formData.get('referred_by')
    const status = formData.get('status')
    const notes = formData.get('notes')

    const client = await createClient(env.DB, session.orgId, {
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
      status: status && typeof status === 'string' && status.trim() ? status.trim() : 'prospect',
      notes: notes && typeof notes === 'string' && notes.trim() ? notes.trim() : null,
    })

    // Link lead signal if this client was promoted from one
    const fromSignal = formData.get('from_signal')
    if (fromSignal && typeof fromSignal === 'string' && fromSignal.trim()) {
      await linkToClient(env.DB, session.orgId, fromSignal.trim(), client.id)
    }

    return redirect(`/admin/clients/${client.id}`, 302)
  } catch (err) {
    console.error('[api/admin/clients] Create error:', err)
    return redirect('/admin/clients/new?error=server', 302)
  }
}
