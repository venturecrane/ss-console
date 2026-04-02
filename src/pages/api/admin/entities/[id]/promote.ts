import type { APIRoute } from 'astro'
import { getEntity, transitionStage, updateEntity } from '../../../../../lib/db/entities'
import { appendContext, assembleEntityContext } from '../../../../../lib/db/context'
import { generateOutreachDraft } from '../../../../../lib/claude/outreach'
import { scheduleProspectCadence } from '../../../../../lib/follow-ups/scheduler'
import { lookupGooglePlaces } from '../../../../../lib/enrichment/google-places'
import { analyzeWebsite } from '../../../../../lib/enrichment/website-analyzer'
import { lookupYelp } from '../../../../../lib/enrichment/yelp'
import { lookupAcc } from '../../../../../lib/enrichment/acc'
import { lookupRoc } from '../../../../../lib/enrichment/roc'

/**
 * POST /api/admin/entities/[id]/promote
 *
 * One-click promote: signal → prospect.
 *
 * 1. Transition stage to prospect
 * 2. Run enrichment modules (all best-effort, independent)
 *    - Google Places lookup (if missing phone/website)
 *    - Website analysis + tech stack detection
 *    - Yelp Fusion cross-reference
 *    - ACC filing lookup
 *    - ROC license check (trades only)
 * 3. Generate outreach draft from enriched context
 * 4. Schedule prospect follow-up cadence
 * 5. Set next_action
 *
 * Each enrichment module is independent — failures don't block others or the promote.
 */
export const POST: APIRoute = async ({ params, locals, redirect }) => {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const entityId = params.id
  if (!entityId) {
    return redirect('/admin/entities?error=missing', 302)
  }

  try {
    const env = locals.runtime.env

    // 1. Transition stage
    await transitionStage(env.DB, session.orgId, entityId, 'prospect', 'Promoted from signal.')

    const entity = await getEntity(env.DB, session.orgId, entityId)
    if (!entity) {
      return redirect('/admin/entities?error=not_found', 302)
    }

    // 2. Run enrichment modules (all best-effort, parallel where possible)
    const enrichmentResults: string[] = []

    // 2a. Google Places lookup (if missing phone or website)
    if ((!entity.phone || !entity.website) && env.GOOGLE_PLACES_API_KEY) {
      try {
        const places = await lookupGooglePlaces(
          entity.name,
          entity.area,
          env.GOOGLE_PLACES_API_KEY as string
        )
        if (places) {
          // Update entity phone/website if discovered
          await updateEntity(env.DB, session.orgId, entityId, {
            phone: places.phone ?? entity.phone ?? undefined,
            website: places.website ?? entity.website ?? undefined,
          })
          await appendContext(env.DB, session.orgId, {
            entity_id: entityId,
            type: 'enrichment',
            content: `Google Places: ${places.phone ? `Phone: ${places.phone}` : 'No phone found'}. ${places.website ? `Website: ${places.website}` : 'No website found'}. Rating: ${places.rating ?? 'N/A'} (${places.reviewCount ?? 0} reviews). Status: ${places.businessStatus ?? 'unknown'}.`,
            source: 'google_places',
            metadata: places as unknown as Record<string, unknown>,
          })
          enrichmentResults.push('google_places')
          // Re-fetch entity to get updated phone/website for subsequent modules
          const refreshed = await getEntity(env.DB, session.orgId, entityId)
          if (refreshed) Object.assign(entity, refreshed)
        }
      } catch (err) {
        console.error('[promote] Google Places enrichment failed:', err)
      }
    }

    // 2b. Website analysis + tech stack (if website available)
    const websiteUrl = entity.website
    if (websiteUrl && env.ANTHROPIC_API_KEY) {
      try {
        const analysis = await analyzeWebsite(websiteUrl, env.ANTHROPIC_API_KEY)
        if (analysis) {
          const techTools = [
            ...analysis.tech_stack.scheduling,
            ...analysis.tech_stack.crm,
            ...analysis.tech_stack.reviews,
            ...analysis.tech_stack.payments,
            ...analysis.tech_stack.communication,
          ]
          const missingTools: string[] = []
          if (analysis.tech_stack.scheduling.length === 0) missingTools.push('No scheduling tool')
          if (analysis.tech_stack.crm.length === 0) missingTools.push('No CRM')
          if (analysis.tech_stack.reviews.length === 0) missingTools.push('No review management')

          const contentParts = [
            `Website analysis (${analysis.pages_analyzed.length} pages):`,
            analysis.owner_name ? `Owner/Founder: ${analysis.owner_name}` : null,
            analysis.team_size ? `Team size: ~${analysis.team_size} people` : null,
            analysis.founding_year ? `Founded: ${analysis.founding_year}` : null,
            analysis.contact_email ? `Email: ${analysis.contact_email}` : null,
            analysis.services.length > 0 ? `Services: ${analysis.services.join(', ')}` : null,
            `Site quality: ${analysis.quality}`,
            techTools.length > 0
              ? `Tools detected: ${techTools.join(', ')}`
              : 'No business tools detected on website',
            missingTools.length > 0 ? `Gaps: ${missingTools.join(', ')}` : null,
            `Platform: ${analysis.tech_stack.platform.join(', ') || 'Custom/unknown'}`,
          ].filter(Boolean)

          await appendContext(env.DB, session.orgId, {
            entity_id: entityId,
            type: 'enrichment',
            content: contentParts.join('\n'),
            source: 'website_analysis',
            metadata: {
              owner_name: analysis.owner_name,
              team_size: analysis.team_size,
              employee_count: analysis.team_size,
              founding_year: analysis.founding_year,
              contact_email: analysis.contact_email,
              services: analysis.services,
              quality: analysis.quality,
              tech_stack: analysis.tech_stack,
              pages_analyzed: analysis.pages_analyzed,
            },
          })
          enrichmentResults.push('website_analysis')
        }
      } catch (err) {
        console.error('[promote] Website analysis failed:', err)
      }
    }

    // 2c. Yelp Fusion cross-reference
    if (env.YELP_API_KEY) {
      try {
        const yelp = await lookupYelp(entity.name, entity.area, env.YELP_API_KEY as string)
        if (yelp) {
          await appendContext(env.DB, session.orgId, {
            entity_id: entityId,
            type: 'enrichment',
            content: `Yelp: ${yelp.rating} stars (${yelp.review_count} reviews). ${yelp.claimed ? 'Claimed' : 'Unclaimed'} profile. Categories: ${yelp.categories.join(', ')}.`,
            source: 'yelp',
            metadata: yelp as unknown as Record<string, unknown>,
          })
          enrichmentResults.push('yelp')
        }
      } catch (err) {
        console.error('[promote] Yelp enrichment failed:', err)
      }
    }

    // 2d. ACC filing lookup
    try {
      const acc = await lookupAcc(entity.name)
      if (acc) {
        await appendContext(env.DB, session.orgId, {
          entity_id: entityId,
          type: 'enrichment',
          content: `ACC Filing: ${acc.entity_name} (${acc.entity_type ?? 'unknown type'}). Filed: ${acc.filing_date ?? 'unknown'}. Status: ${acc.status ?? 'unknown'}. Registered agent: ${acc.registered_agent ?? 'not found'}.`,
          source: 'acc_filing',
          metadata: acc as unknown as Record<string, unknown>,
        })
        enrichmentResults.push('acc_filing')
      }
    } catch (err) {
      console.error('[promote] ACC lookup failed:', err)
    }

    // 2e. ROC license check (trades only)
    if (entity.vertical === 'home_services' || entity.vertical === 'contractor_trades') {
      try {
        const roc = await lookupRoc(entity.name)
        if (roc) {
          await appendContext(env.DB, session.orgId, {
            entity_id: entityId,
            type: 'enrichment',
            content: `ROC License: ${roc.license_number ?? 'N/A'} (${roc.classification ?? 'unknown classification'}). Status: ${roc.status ?? 'unknown'}. Complaints: ${roc.complaint_count ?? 'N/A'}.`,
            source: 'roc_license',
            metadata: roc as unknown as Record<string, unknown>,
          })
          enrichmentResults.push('roc_license')
        }
      } catch (err) {
        console.error('[promote] ROC lookup failed:', err)
      }
    }

    console.log(
      `[promote] Enrichment complete for ${entity.name}: ${enrichmentResults.join(', ') || 'none'}`
    )

    // 3. Generate outreach draft (best-effort, uses enriched context)
    try {
      if (env.ANTHROPIC_API_KEY) {
        const context = await assembleEntityContext(env.DB, entityId, { maxBytes: 16_000 })
        if (context) {
          const draft = await generateOutreachDraft(env.ANTHROPIC_API_KEY, entity.name, context)
          await appendContext(env.DB, session.orgId, {
            entity_id: entityId,
            type: 'outreach_draft',
            content: draft,
            source: 'claude',
            metadata: {
              model: 'claude-sonnet-4-20250514',
              trigger: 'promote',
              enrichment_sources: enrichmentResults,
            },
          })
        }
      }
    } catch (err) {
      console.error('[promote] Outreach generation failed (non-blocking):', err)
    }

    // 4. Schedule prospect follow-up cadence
    try {
      await scheduleProspectCadence(env.DB, session.orgId, entityId, new Date().toISOString())
    } catch (err) {
      console.error('[promote] Follow-up cadence scheduling failed (non-blocking):', err)
    }

    // 5. Set next action
    await updateEntity(env.DB, session.orgId, entityId, {
      next_action: 'Review and send outreach email',
      next_action_at: new Date().toISOString(),
    })

    return redirect(`/admin/entities/${entityId}?promoted=1`, 302)
  } catch (err) {
    console.error('[api/admin/entities/promote] Error:', err)
    const message = err instanceof Error ? err.message : 'server'
    return redirect(`/admin/entities?error=${encodeURIComponent(message)}`, 302)
  }
}
