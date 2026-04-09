/**
 * Booking entity enrichment handler.
 *
 * Runs enrichment on entities that booked via the public /book page.
 * Public booking skips enrichment to avoid 10-30s latency on the booking
 * page, so this handler catches them up asynchronously.
 *
 * Selection criteria:
 *   - source_pipeline = 'website_booking'
 *   - stage = 'assessing'
 *   - No 'enrichment' context entries yet
 *
 * Runs the same enrichment modules as the admin promote endpoint
 * (src/pages/api/admin/entities/[id]/promote.ts) but does NOT generate
 * an outreach draft — the entity already has a booked assessment call.
 *
 * Each enrichment module is independent — failures don't block others
 * or the overall handler.
 */

import { ORG_ID } from '../../../../src/lib/constants.js'
import { getEntity, updateEntity } from '../../../../src/lib/db/entities.js'
import { appendContext, assembleEntityContext } from '../../../../src/lib/db/context.js'
import { lookupGooglePlaces } from '../../../../src/lib/enrichment/google-places.js'
import { analyzeWebsite } from '../../../../src/lib/enrichment/website-analyzer.js'
import { lookupOutscraper } from '../../../../src/lib/enrichment/outscraper.js'
import { lookupAcc } from '../../../../src/lib/enrichment/acc.js'
import { lookupRoc } from '../../../../src/lib/enrichment/roc.js'
import { analyzeReviewPatterns } from '../../../../src/lib/enrichment/review-analysis.js'
import { benchmarkCompetitors } from '../../../../src/lib/enrichment/competitors.js'
import { searchNews } from '../../../../src/lib/enrichment/news.js'

export interface BookingEnrichmentEnv {
  DB: D1Database
  GOOGLE_PLACES_API_KEY?: string
  ANTHROPIC_API_KEY?: string
  OUTSCRAPER_API_KEY?: string
  SERPAPI_API_KEY?: string
}

const BATCH_LIMIT = 5

/**
 * Find booking entities that need enrichment and run all modules.
 * Returns a summary string for the handler result log.
 */
export async function runBookingEnrichment(env: BookingEnrichmentEnv): Promise<string> {
  const candidates = await env.DB.prepare(
    `SELECT e.id FROM entities e
       WHERE e.org_id = ?
         AND e.source_pipeline = 'website_booking'
         AND e.stage = 'assessing'
         AND NOT EXISTS (
           SELECT 1 FROM context c
           WHERE c.entity_id = e.id AND c.type = 'enrichment'
         )
       ORDER BY e.created_at ASC
       LIMIT ?`
  )
    .bind(ORG_ID, BATCH_LIMIT)
    .all<{ id: string }>()

  if (candidates.results.length === 0) {
    return 'no candidates'
  }

  const results: string[] = []

  for (const row of candidates.results) {
    const entityResult = await enrichEntity(env, row.id)
    results.push(`${row.id.slice(0, 8)}:${entityResult}`)
  }

  return `enriched ${candidates.results.length} — ${results.join(', ')}`
}

async function enrichEntity(env: BookingEnrichmentEnv, entityId: string): Promise<string> {
  const entity = await getEntity(env.DB, ORG_ID, entityId)
  if (!entity) return 'not_found'

  const modules: string[] = []

  // --- Tier 1: Contact discovery ---

  // Google Places (if missing phone or website)
  if ((!entity.phone || !entity.website) && env.GOOGLE_PLACES_API_KEY) {
    try {
      const places = await lookupGooglePlaces(entity.name, entity.area, env.GOOGLE_PLACES_API_KEY)
      if (places) {
        await updateEntity(env.DB, ORG_ID, entityId, {
          phone: places.phone ?? entity.phone ?? undefined,
          website: places.website ?? entity.website ?? undefined,
        })
        const phonePart = places.phone ? `Phone: ${places.phone}` : 'No phone found'
        const webPart = places.website ? `Website: ${places.website}` : 'No website found'
        const ratingPart = `Rating: ${places.rating ?? 'N/A'} (${places.reviewCount ?? 0} reviews)`
        const statusPart = `Status: ${places.businessStatus ?? 'unknown'}`
        await appendContext(env.DB, ORG_ID, {
          entity_id: entityId,
          type: 'enrichment',
          content: `Google Places: ${phonePart}. ${webPart}. ${ratingPart}. ${statusPart}.`,
          source: 'google_places',
          metadata: places as unknown as Record<string, unknown>,
        })
        modules.push('google_places')
        const refreshed = await getEntity(env.DB, ORG_ID, entityId)
        if (refreshed) Object.assign(entity, refreshed)
      }
    } catch (err) {
      console.error(`[booking-enrichment] Google Places failed for ${entityId}:`, err)
    }
  }

  // Website analysis + tech stack
  if (entity.website && env.ANTHROPIC_API_KEY) {
    try {
      const analysis = await analyzeWebsite(entity.website, env.ANTHROPIC_API_KEY)
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

        await appendContext(env.DB, ORG_ID, {
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
        modules.push('website_analysis')
      }
    } catch (err) {
      console.error(`[booking-enrichment] Website analysis failed for ${entityId}:`, err)
    }
  }

  // Outscraper full business profile
  if (env.OUTSCRAPER_API_KEY) {
    try {
      const osc = await lookupOutscraper(entity.name, entity.area, env.OUTSCRAPER_API_KEY)
      if (osc) {
        await updateEntity(env.DB, ORG_ID, entityId, {
          phone: osc.phone ?? entity.phone ?? undefined,
          website: osc.website ?? entity.website ?? undefined,
        })
        if (osc.website && !entity.website) {
          const refreshed = await getEntity(env.DB, ORG_ID, entityId)
          if (refreshed) Object.assign(entity, refreshed)
        }

        const contentParts = [
          'Outscraper business profile:',
          osc.owner_name ? `Owner: ${osc.owner_name}` : null,
          osc.emails.length > 0 ? `Email: ${osc.emails.join(', ')}` : null,
          osc.phone ? `Phone: ${osc.phone}` : null,
          osc.working_hours ? `Hours: ${osc.working_hours}` : null,
          osc.verified ? 'Google listing: Verified' : 'Google listing: Unverified',
          osc.rating != null ? `Rating: ${osc.rating} (${osc.review_count ?? 0} reviews)` : null,
          osc.booking_link ? 'Online booking: Yes' : 'Online booking: Not detected',
          osc.facebook ? `Facebook: ${osc.facebook}` : null,
          osc.instagram ? `Instagram: ${osc.instagram}` : null,
          osc.linkedin ? `LinkedIn: ${osc.linkedin}` : null,
          osc.website_generator ? `Platform: ${osc.website_generator}` : null,
          osc.has_facebook_pixel ? 'Has Facebook Pixel' : null,
          osc.has_google_tag_manager ? 'Has Google Tag Manager' : null,
          osc.about ? `About: ${osc.about}` : null,
        ].filter(Boolean)

        await appendContext(env.DB, ORG_ID, {
          entity_id: entityId,
          type: 'enrichment',
          content: contentParts.join('\n'),
          source: 'outscraper',
          metadata: osc as unknown as Record<string, unknown>,
        })
        modules.push('outscraper')
      }
    } catch (err) {
      console.error(`[booking-enrichment] Outscraper failed for ${entityId}:`, err)
    }
  }

  // --- Tier 2: Public records ---

  // ACC filing
  try {
    const acc = await lookupAcc(entity.name)
    if (acc) {
      const filing = `${acc.entity_name} (${acc.entity_type ?? 'unknown type'})`
      const filed = `Filed: ${acc.filing_date ?? 'unknown'}`
      const accStatus = `Status: ${acc.status ?? 'unknown'}`
      const agent = `Registered agent: ${acc.registered_agent ?? 'not found'}`
      await appendContext(env.DB, ORG_ID, {
        entity_id: entityId,
        type: 'enrichment',
        content: `ACC Filing: ${filing}. ${filed}. ${accStatus}. ${agent}.`,
        source: 'acc_filing',
        metadata: acc as unknown as Record<string, unknown>,
      })
      modules.push('acc_filing')
    }
  } catch (err) {
    console.error(`[booking-enrichment] ACC lookup failed for ${entityId}:`, err)
  }

  // ROC license (trades only)
  if (entity.vertical === 'home_services' || entity.vertical === 'contractor_trades') {
    try {
      const roc = await lookupRoc(entity.name)
      if (roc) {
        const license = `${roc.license_number ?? 'N/A'} (${roc.classification ?? 'unknown classification'})`
        const rocStatus = `Status: ${roc.status ?? 'unknown'}`
        const complaints = `Complaints: ${roc.complaint_count ?? 'N/A'}`
        await appendContext(env.DB, ORG_ID, {
          entity_id: entityId,
          type: 'enrichment',
          content: `ROC License: ${license}. ${rocStatus}. ${complaints}.`,
          source: 'roc_license',
          metadata: roc as unknown as Record<string, unknown>,
        })
        modules.push('roc_license')
      }
    } catch (err) {
      console.error(`[booking-enrichment] ROC lookup failed for ${entityId}:`, err)
    }
  }

  // --- Tier 3: Deeper intelligence ---

  // Review response analysis
  if (env.ANTHROPIC_API_KEY) {
    try {
      const signalContext = await assembleEntityContext(env.DB, entityId, {
        maxBytes: 8_000,
        typeFilter: ['signal'],
      })
      if (signalContext) {
        const reviewAnalysis = await analyzeReviewPatterns(signalContext, env.ANTHROPIC_API_KEY)
        if (reviewAnalysis) {
          const accessible = reviewAnalysis.owner_accessible ? 'Owner appears accessible.' : ''
          await appendContext(env.DB, ORG_ID, {
            entity_id: entityId,
            type: 'enrichment',
            content: `Review patterns: ${reviewAnalysis.response_pattern} responses, ${reviewAnalysis.engagement_level} engagement. ${accessible} ${reviewAnalysis.insights}`,
            source: 'review_analysis',
            metadata: reviewAnalysis as unknown as Record<string, unknown>,
          })
          modules.push('review_analysis')
        }
      }
    } catch (err) {
      console.error(`[booking-enrichment] Review analysis failed for ${entityId}:`, err)
    }
  }

  // Competitor benchmarking
  if (env.GOOGLE_PLACES_API_KEY) {
    try {
      const benchmark = await benchmarkCompetitors(
        entity.name,
        entity.vertical,
        entity.area,
        entity.pain_score,
        null,
        env.GOOGLE_PLACES_API_KEY
      )
      if (benchmark) {
        const competitorList = benchmark.competitors
          .map((c) => `${c.name} (${c.rating}★, ${c.review_count} reviews)`)
          .join(', ')
        await appendContext(env.DB, ORG_ID, {
          entity_id: entityId,
          type: 'enrichment',
          content: `Competitor benchmarking: ${benchmark.summary} Top competitors: ${competitorList}.`,
          source: 'competitors',
          metadata: benchmark as unknown as Record<string, unknown>,
        })
        modules.push('competitors')
      }
    } catch (err) {
      console.error(`[booking-enrichment] Competitor benchmarking failed for ${entityId}:`, err)
    }
  }

  // News/press search
  if (env.SERPAPI_API_KEY && env.ANTHROPIC_API_KEY) {
    try {
      const news = await searchNews(
        entity.name,
        entity.area,
        env.SERPAPI_API_KEY,
        env.ANTHROPIC_API_KEY
      )
      if (news) {
        await appendContext(env.DB, ORG_ID, {
          entity_id: entityId,
          type: 'enrichment',
          content: `News/press: ${news.summary} (${news.mentions.length} mentions found)`,
          source: 'news_search',
          metadata: {
            mentions: news.mentions,
            summary: news.summary,
          },
        })
        modules.push('news_search')
      }
    } catch (err) {
      console.error(`[booking-enrichment] News search failed for ${entityId}:`, err)
    }
  }

  console.log(`[booking-enrichment] ${entity.name}: ${modules.join(', ') || 'none'}`)

  return modules.length > 0 ? modules.join('+') : 'no_data'
}
