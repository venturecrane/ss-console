import type { APIRoute } from 'astro'
import { getEntity } from '../../../../../lib/db/entities'
import { appendContext, assembleEntityContext } from '../../../../../lib/db/context'
import { deepWebsiteAnalysis } from '../../../../../lib/enrichment/deep-website'
import type { DeepWebsiteAnalysis } from '../../../../../lib/enrichment/deep-website'
import { synthesizeReviews } from '../../../../../lib/enrichment/review-synthesis'
import { lookupLinkedIn } from '../../../../../lib/enrichment/linkedin'
import { generateDossier } from '../../../../../lib/enrichment/dossier'
import { generateOutreachDraft } from '../../../../../lib/claude/outreach'

/**
 * POST /api/admin/entities/[id]/dossier
 *
 * Generate a deep intelligence dossier for an entity.
 * Runs Tier 4 enrichment modules then synthesizes into a brief.
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
  if (!entityId) return redirect('/admin/entities?error=missing', 302)

  try {
    const env = locals.runtime.env
    const entity = await getEntity(env.DB, session.orgId, entityId)
    if (!entity) return redirect('/admin/entities?error=not_found', 302)

    const anthropicKey = env.ANTHROPIC_API_KEY as string | undefined
    if (!anthropicKey) {
      return redirect(`/admin/entities/${entityId}?error=no_api_key`, 302)
    }

    // Module 11: Deep website analysis (if website available)
    if (entity.website) {
      try {
        const deepAnalysis = await deepWebsiteAnalysis(entity.website, anthropicKey)
        if (deepAnalysis) {
          await appendContext(env.DB, session.orgId, {
            entity_id: entityId,
            type: 'enrichment',
            content: formatDeepWebsite(deepAnalysis),
            source: 'deep_website',
            metadata: deepAnalysis as unknown as Record<string, unknown>,
          })
        }
      } catch (err) {
        console.error('[dossier] Deep website analysis failed:', err)
      }
    }

    // Module 12: Cross-platform review synthesis
    try {
      const allContext = await assembleEntityContext(env.DB, entityId, {
        maxBytes: 20_000,
        typeFilter: ['signal', 'enrichment'],
      })
      if (allContext) {
        const synthesis = await synthesizeReviews(allContext, anthropicKey)
        if (synthesis) {
          await appendContext(env.DB, session.orgId, {
            entity_id: entityId,
            type: 'enrichment',
            content: `Review synthesis: ${synthesis.customer_sentiment} Trend: ${synthesis.sentiment_trend}. Themes: ${synthesis.top_themes.join(', ')}. Problems: ${synthesis.operational_problems.map((p) => `${p.problem} (${p.confidence})`).join(', ')}.`,
            source: 'review_synthesis',
            metadata: synthesis as unknown as Record<string, unknown>,
          })
        }
      }
    } catch (err) {
      console.error('[dossier] Review synthesis failed:', err)
    }

    // Module 13: LinkedIn (if API key available)
    if (env.PROXYCURL_API_KEY) {
      try {
        const linkedin = await lookupLinkedIn(
          entity.name,
          entity.area,
          env.PROXYCURL_API_KEY as string
        )
        if (linkedin) {
          await appendContext(env.DB, session.orgId, {
            entity_id: entityId,
            type: 'enrichment',
            content: `LinkedIn: ${linkedin.company_name}. ${linkedin.employee_count ? `~${linkedin.employee_count} employees.` : ''} ${linkedin.industry ? `Industry: ${linkedin.industry}.` : ''} ${linkedin.description ? linkedin.description.slice(0, 200) : ''}`,
            source: 'linkedin',
            metadata: linkedin as unknown as Record<string, unknown>,
          })
        }
      } catch (err) {
        console.error('[dossier] LinkedIn lookup failed:', err)
      }
    }

    // Module 14: Generate the intelligence brief
    const fullContext = await assembleEntityContext(env.DB, entityId, { maxBytes: 32_000 })
    if (fullContext) {
      const brief = await generateDossier(fullContext, entity.name, anthropicKey)
      if (brief) {
        await appendContext(env.DB, session.orgId, {
          entity_id: entityId,
          type: 'enrichment',
          content: brief,
          source: 'intelligence_brief',
          metadata: { model: 'claude-sonnet-4-20250514', trigger: 'dossier' },
        })
      }
    }

    // Regenerate outreach draft with full dossier context
    try {
      const outreachContext = await assembleEntityContext(env.DB, entityId, { maxBytes: 24_000 })
      if (outreachContext) {
        const draft = await generateOutreachDraft(anthropicKey, entity.name, outreachContext)
        await appendContext(env.DB, session.orgId, {
          entity_id: entityId,
          type: 'outreach_draft',
          content: draft,
          source: 'claude',
          metadata: { model: 'claude-sonnet-4-20250514', trigger: 'dossier' },
        })
      }
    } catch (err) {
      console.error('[dossier] Outreach draft regeneration failed:', err)
    }

    return redirect(`/admin/entities/${entityId}?dossier=1`, 302)
  } catch (err) {
    console.error('[api/admin/entities/dossier] Error:', err)
    return redirect(`/admin/entities/${entityId}?error=dossier_failed`, 302)
  }
}

function formatDeepWebsite(analysis: DeepWebsiteAnalysis): string {
  const parts: string[] = ['Deep website analysis:']
  if (analysis.owner_profile.name)
    parts.push(`Owner: ${analysis.owner_profile.name} (${analysis.owner_profile.title ?? 'owner'})`)
  if (analysis.owner_profile.background)
    parts.push(`Background: ${analysis.owner_profile.background}`)
  if (analysis.team.size_estimate) parts.push(`Team: ~${analysis.team.size_estimate} people`)
  if (analysis.team.named_employees.length > 0)
    parts.push(
      `Named staff: ${analysis.team.named_employees.map((e) => `${e.name} (${e.role})`).join(', ')}`
    )
  if (analysis.business_profile.services.length > 0)
    parts.push(`Services: ${analysis.business_profile.services.join(', ')}`)
  if (analysis.business_profile.certifications.length > 0)
    parts.push(`Certifications: ${analysis.business_profile.certifications.join(', ')}`)
  if (analysis.business_profile.awards.length > 0)
    parts.push(`Awards: ${analysis.business_profile.awards.join(', ')}`)
  parts.push(
    `Digital maturity: ${analysis.digital_maturity.score}/10 — ${analysis.digital_maturity.reasoning}`
  )
  parts.push(
    `Online booking: ${analysis.digital_maturity.online_booking ? 'Yes' : 'No'}, Chat: ${analysis.digital_maturity.chat_widget ? 'Yes' : 'No'}, Blog active: ${analysis.digital_maturity.blog_active ? 'Yes' : 'No'}`
  )
  if (analysis.contact_info.email) parts.push(`Email: ${analysis.contact_info.email}`)
  return parts.join('\n')
}
