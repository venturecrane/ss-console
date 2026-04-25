/**
 * Unified enrichment pipeline — issue #471.
 *
 * Merges what used to be two admin-triggered endpoints (`promote` and
 * `dossier`) into a single `enrichEntity()` call that runs automatically at
 * signal ingest time. The admin-click gate was the bottleneck — at ~8
 * signals/day and ~$0.25/enrichment, the cost is trivial compared with the
 * engagement value of a qualified lead, but the value only lands if every
 * signal actually gets enriched.
 *
 * ### Modes
 *
 * - `full` (default) — the complete 12-module pipeline. Called from lead-gen
 *   workers on newly-ingested signals and from the "promote" admin wrapper.
 * - `reviews-and-news` — cheap refresh limited to review_analysis,
 *   review_synthesis, and news_search. Surfaced in admin as the "Re-enrich"
 *   button for stale-data refresh on entities that already have a full
 *   enrichment on file.
 *
 * ### Idempotency / cost control
 *
 * Full-mode enrichment is skipped when an `intelligence_brief` context entry
 * already exists — the brief is the last module to run in full mode, so its
 * presence means a prior full pipeline completed. Calling `enrichEntity` a
 * second time from a worker (dedupe race, retry, manual re-run) will not
 * re-bill Claude for an already-enriched entity. Re-enrich mode bypasses this
 * check by design — the caller explicitly asked for a refresh.
 *
 * ### Why a merge and not two calls
 *
 * The two endpoints fetched identical entity/context rows, ran
 * `generateOutreachDraft` twice (dossier overwrote promote's draft), and used
 * the same appendContext plumbing. Combining them removes the duplicate
 * outreach call, assembles context once per stage, and gives the worker a
 * single natural insertion point.
 */

import type { Entity } from '../db/entities'
import { getEntity, updateEntity } from '../db/entities'
import { appendContext, assembleEntityContext, listContext } from '../db/context'
import { generateOutreachDraft } from '../claude/outreach'
import { lookupGooglePlaces } from './google-places'
import { analyzeWebsite } from './website-analyzer'
import { lookupOutscraper } from './outscraper'
import { lookupAcc } from './acc'
import { lookupRoc } from './roc'
import { analyzeReviewPatterns } from './review-analysis'
import { benchmarkCompetitors } from './competitors'
import { searchNews } from './news'
import { deepWebsiteAnalysis, type DeepWebsiteAnalysis } from './deep-website'
import { synthesizeReviews } from './review-synthesis'
import { lookupLinkedIn } from './linkedin'
import { generateDossier } from './dossier'
import {
  instrumentModule,
  fingerprint,
  type ModuleOutcome,
  type InstrumentResult,
} from './instrument'
import type { ModuleId } from './modules'

export type EnrichMode = 'full' | 'reviews-and-news'

export interface EnrichOptions {
  mode?: EnrichMode
  /**
   * Force re-enrichment even when a prior intelligence brief exists. Admin
   * "Re-enrich" uses this implicitly via `reviews-and-news`; this flag is
   * available for a future "force full re-enrich" action if we add one.
   */
  force?: boolean
  /**
   * Provenance string written to every enrichment_runs row produced during
   * this call. Examples: 'cron:new-business', 'admin:promote',
   * 'admin:re-enrich', 'admin:retry:deep_website', 'ingest:signals'.
   * Defaults to 'unknown' if not provided — callers should set it.
   */
  triggered_by?: string
}

export interface EnrichResult {
  entityId: string
  mode: EnrichMode
  /** Provenance — same value passed in EnrichOptions.triggered_by. */
  triggered_by: string
  /** Module source names that completed successfully in this run. */
  completed: string[]
  /** Module source names that were skipped (missing API key, wrong vertical, already enriched). */
  skipped: string[]
  /** Module source names that threw — logged but non-blocking. */
  errors: string[]
  /** True if the run did nothing because a prior full enrichment exists. */
  alreadyEnriched: boolean
}

/**
 * Map a wrapper outcome into the legacy in-memory EnrichResult arrays so
 * existing callers (workers, promote endpoint) that read result.completed
 * continue to work. The persisted enrichment_runs row is the durable
 * record; this is best-effort accounting for the in-memory return value.
 */
function applyOutcome(result: EnrichResult, module: ModuleId, outcome: InstrumentResult): void {
  switch (outcome.status) {
    case 'succeeded':
      result.completed.push(module)
      return
    case 'no_data':
    case 'skipped':
      result.skipped.push(module)
      return
    case 'failed':
      result.errors.push(module)
      return
    case 'running':
      // Unreachable under instrumentModule (always settles to terminal).
      return
  }
}

type EnrichEnv = {
  DB: D1Database
  ANTHROPIC_API_KEY?: string
  GOOGLE_PLACES_API_KEY?: string
  OUTSCRAPER_API_KEY?: string
  PROXYCURL_API_KEY?: string
  SERPAPI_API_KEY?: string
}

/**
 * Run the unified enrichment pipeline for a single entity.
 *
 * All modules are best-effort — a single module failure does not abort the
 * run. Callers should not await this from a hot request path: workers use
 * `ctx.waitUntil(enrichEntity(...))` so ingest does not block on Claude.
 */
export async function enrichEntity(
  env: EnrichEnv,
  orgId: string,
  entityId: string,
  options: EnrichOptions = {}
): Promise<EnrichResult> {
  const mode: EnrichMode = options.mode ?? 'full'
  const result: EnrichResult = {
    entityId,
    mode,
    triggered_by: options.triggered_by ?? 'unknown',
    completed: [],
    skipped: [],
    errors: [],
    alreadyEnriched: false,
  }

  const entity = await getEntity(env.DB, orgId, entityId)
  if (!entity) {
    result.errors.push('entity_not_found')
    return result
  }

  // Idempotency: full-mode skips if a prior intelligence_brief exists. The
  // brief is the last full-mode module, so its presence means a prior run
  // completed. reviews-and-news intentionally bypasses this — it's the
  // explicit "refresh" path.
  if (mode === 'full' && !options.force) {
    const existing = await listContext(env.DB, entityId, { type: 'enrichment' })
    const hasBrief = existing.some((e) => e.source === 'intelligence_brief')
    if (hasBrief) {
      result.alreadyEnriched = true
      return result
    }
  }

  if (mode === 'reviews-and-news') {
    await runReviewsAndNews(env, orgId, entity, result)
    await regenerateOutreach(env, orgId, entity, result)
    return result
  }

  // mode === 'full': run the full 12-module pipeline.
  let current = entity

  // --- Tier 1: Contact + tech signals (parallel-safe but sequential for
  //             readability and because some modules update entity.phone /
  //             website, which downstream modules rely on). ---

  current = await tryPlaces(env, orgId, current, result)
  current = await tryWebsite(env, orgId, current, result)
  current = await tryOutscraper(env, orgId, current, result)
  await tryAcc(env, orgId, current, result)
  await tryRoc(env, orgId, current, result)

  // --- Tier 2: Review patterns + competitors + news. ---

  await tryReviewAnalysis(env, orgId, current, result)
  await tryCompetitors(env, orgId, current, result)
  await tryNews(env, orgId, current, result)

  // --- Tier 3: Deep intelligence (the old dossier path). ---

  await tryDeepWebsite(env, orgId, current, result)
  await tryReviewSynthesis(env, orgId, current, result)
  await tryLinkedIn(env, orgId, current, result)
  await tryIntelligenceBrief(env, orgId, current, result)

  // --- Outreach draft from the full enriched context. Runs once at the end,
  //     not twice (the old promote+dossier pair called this at both steps). ---

  await regenerateOutreach(env, orgId, current, result)

  // Surface a next_action so the admin inbox shows "review and send" instead
  // of leaving the signal inert. Only set if nothing's there already.
  if (!current.next_action) {
    try {
      await updateEntity(env.DB, orgId, entityId, {
        next_action: 'Review enrichment and send outreach email',
        next_action_at: new Date().toISOString(),
      })
    } catch (err) {
      console.error('[enrichEntity] failed to set next_action:', err)
    }
  }

  return result
}

// ---------------------------------------------------------------------------
// reviews-and-news (cheap refresh path)
// ---------------------------------------------------------------------------

async function runReviewsAndNews(
  env: EnrichEnv,
  orgId: string,
  entity: Entity,
  result: EnrichResult
): Promise<void> {
  await tryReviewAnalysis(env, orgId, entity, result)
  await tryReviewSynthesis(env, orgId, entity, result)
  await tryNews(env, orgId, entity, result)
  // Backfill a missing intelligence_brief. Entities whose initial full
  // pipeline crashed mid-run (Error 1101 era, Goodman's Landscape et al)
  // ended up at `prospect` with partial enrichment and no brief; Re-enrich
  // is the natural place to heal that. Only runs when a brief doesn't
  // already exist, so repeat Re-enrich clicks don't re-bill Claude.
  const existingBrief = await listContext(env.DB, entity.id, { type: 'enrichment' })
  const hasBrief = existingBrief.some((e) => e.source === 'intelligence_brief')
  if (!hasBrief) {
    await tryIntelligenceBrief(env, orgId, entity, result)
  }
}

// ---------------------------------------------------------------------------
// Individual module wrappers — each is best-effort and records its source
// name in the result. Extracted from promote.ts / dossier.ts verbatim so the
// behavioral semantics match what admins saw when clicking the buttons.
// ---------------------------------------------------------------------------

async function tryPlaces(
  env: EnrichEnv,
  orgId: string,
  entity: Entity,
  result: EnrichResult
): Promise<Entity> {
  let refreshedEntity: Entity = entity
  const outcome = await instrumentModule(
    {
      db: env.DB,
      org_id: orgId,
      entity_id: entity.id,
      module: 'google_places',
      mode: result.mode,
      triggered_by: result.triggered_by,
    },
    async (): Promise<ModuleOutcome> => {
      if (entity.phone && entity.website)
        return { kind: 'skipped', reason: 'already_have_phone_and_website' }
      if (!env.GOOGLE_PLACES_API_KEY)
        return { kind: 'skipped', reason: 'missing_api_key:google_places' }
      const places = await lookupGooglePlaces(entity.name, entity.area, env.GOOGLE_PLACES_API_KEY)
      if (!places) return { kind: 'no_data', reason: 'no_match' }
      await updateEntity(env.DB, orgId, entity.id, {
        phone: places.phone ?? entity.phone ?? undefined,
        website: places.website ?? entity.website ?? undefined,
      })
      const ce = await appendContext(env.DB, orgId, {
        entity_id: entity.id,
        type: 'enrichment',
        content: `Google Places: ${places.phone ? `Phone: ${places.phone}` : 'No phone found'}. ${places.website ? `Website: ${places.website}` : 'No website found'}. Rating: ${places.rating ?? 'N/A'} (${places.reviewCount ?? 0} reviews). Status: ${places.businessStatus ?? 'unknown'}.`,
        source: 'google_places',
        metadata: places as unknown as Record<string, unknown>,
      })
      const refreshed = await getEntity(env.DB, orgId, entity.id)
      if (refreshed) refreshedEntity = refreshed
      return { kind: 'succeeded', context_entry_id: ce.id }
    }
  )
  applyOutcome(result, 'google_places', outcome)
  return refreshedEntity
}

async function tryWebsite(
  env: EnrichEnv,
  orgId: string,
  entity: Entity,
  result: EnrichResult
): Promise<Entity> {
  const outcome = await instrumentModule(
    {
      db: env.DB,
      org_id: orgId,
      entity_id: entity.id,
      module: 'website_analysis',
      mode: result.mode,
      triggered_by: result.triggered_by,
    },
    async (): Promise<ModuleOutcome> => {
      if (!entity.website) return { kind: 'skipped', reason: 'missing_input:website' }
      if (!env.ANTHROPIC_API_KEY) return { kind: 'skipped', reason: 'missing_api_key:anthropic' }
      const analysis = await analyzeWebsite(entity.website, env.ANTHROPIC_API_KEY)
      if (!analysis) return { kind: 'no_data', reason: 'no_analysis' }
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

      const ce = await appendContext(env.DB, orgId, {
        entity_id: entity.id,
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
      return { kind: 'succeeded', context_entry_id: ce.id }
    }
  )
  applyOutcome(result, 'website_analysis', outcome)
  return entity
}

async function tryOutscraper(
  env: EnrichEnv,
  orgId: string,
  entity: Entity,
  result: EnrichResult
): Promise<Entity> {
  let refreshedEntity: Entity = entity
  const outcome = await instrumentModule(
    {
      db: env.DB,
      org_id: orgId,
      entity_id: entity.id,
      module: 'outscraper',
      mode: result.mode,
      triggered_by: result.triggered_by,
    },
    async (): Promise<ModuleOutcome> => {
      if (!env.OUTSCRAPER_API_KEY) return { kind: 'skipped', reason: 'missing_api_key:outscraper' }
      const osc = await lookupOutscraper(entity.name, entity.area, env.OUTSCRAPER_API_KEY)
      if (!osc) return { kind: 'no_data', reason: 'no_match' }
      await updateEntity(env.DB, orgId, entity.id, {
        phone: osc.phone ?? entity.phone ?? undefined,
        website: osc.website ?? entity.website ?? undefined,
      })
      const contentParts = [
        'Outscraper business profile:',
        osc.owner_name ? `Owner: ${osc.owner_name}` : null,
        osc.emails.length > 0 ? `Email: ${osc.emails.join(', ')}` : null,
        osc.phone ? `Phone: ${osc.phone}` : null,
        osc.working_hours ? `Hours: ${osc.working_hours}` : null,
        osc.verified ? 'Google listing: Verified' : 'Google listing: Unverified',
        osc.rating != null ? `Rating: ${osc.rating} (${osc.review_count ?? 0} reviews)` : null,
        osc.booking_link ? `Online booking: Yes` : 'Online booking: Not detected',
        osc.facebook ? `Facebook: ${osc.facebook}` : null,
        osc.instagram ? `Instagram: ${osc.instagram}` : null,
        osc.linkedin ? `LinkedIn: ${osc.linkedin}` : null,
        osc.website_generator ? `Platform: ${osc.website_generator}` : null,
        osc.has_facebook_pixel ? 'Has Facebook Pixel' : null,
        osc.has_google_tag_manager ? 'Has Google Tag Manager' : null,
        osc.about ? `About: ${osc.about}` : null,
      ].filter(Boolean)

      const ce = await appendContext(env.DB, orgId, {
        entity_id: entity.id,
        type: 'enrichment',
        content: contentParts.join('\n'),
        source: 'outscraper',
        metadata: osc as unknown as Record<string, unknown>,
      })
      const refreshed = await getEntity(env.DB, orgId, entity.id)
      if (refreshed) refreshedEntity = refreshed
      return { kind: 'succeeded', context_entry_id: ce.id }
    }
  )
  applyOutcome(result, 'outscraper', outcome)
  return refreshedEntity
}

async function tryAcc(
  env: EnrichEnv,
  orgId: string,
  entity: Entity,
  result: EnrichResult
): Promise<void> {
  const outcome = await instrumentModule(
    {
      db: env.DB,
      org_id: orgId,
      entity_id: entity.id,
      module: 'acc_filing',
      mode: result.mode,
      triggered_by: result.triggered_by,
    },
    async (): Promise<ModuleOutcome> => {
      const acc = await lookupAcc(entity.name)
      if (!acc) return { kind: 'no_data', reason: 'no_filing_match' }
      const ce = await appendContext(env.DB, orgId, {
        entity_id: entity.id,
        type: 'enrichment',
        content: `ACC Filing: ${acc.entity_name} (${acc.entity_type ?? 'unknown type'}). Filed: ${acc.filing_date ?? 'unknown'}. Status: ${acc.status ?? 'unknown'}. Registered agent: ${acc.registered_agent ?? 'not found'}.`,
        source: 'acc_filing',
        metadata: acc as unknown as Record<string, unknown>,
      })
      return { kind: 'succeeded', context_entry_id: ce.id }
    }
  )
  applyOutcome(result, 'acc_filing', outcome)
}

async function tryRoc(
  env: EnrichEnv,
  orgId: string,
  entity: Entity,
  result: EnrichResult
): Promise<void> {
  const outcome = await instrumentModule(
    {
      db: env.DB,
      org_id: orgId,
      entity_id: entity.id,
      module: 'roc_license',
      mode: result.mode,
      triggered_by: result.triggered_by,
    },
    async (): Promise<ModuleOutcome> => {
      if (entity.vertical !== 'home_services' && entity.vertical !== 'contractor_trades') {
        return { kind: 'skipped', reason: 'wrong_vertical' }
      }
      const roc = await lookupRoc(entity.name)
      if (!roc) return { kind: 'no_data', reason: 'no_license_match' }
      const ce = await appendContext(env.DB, orgId, {
        entity_id: entity.id,
        type: 'enrichment',
        content: `ROC License: ${roc.license_number ?? 'N/A'} (${roc.classification ?? 'unknown classification'}). Status: ${roc.status ?? 'unknown'}. Complaints: ${roc.complaint_count ?? 'N/A'}.`,
        source: 'roc_license',
        metadata: roc as unknown as Record<string, unknown>,
      })
      return { kind: 'succeeded', context_entry_id: ce.id }
    }
  )
  applyOutcome(result, 'roc_license', outcome)
}

async function tryReviewAnalysis(
  env: EnrichEnv,
  orgId: string,
  entity: Entity,
  result: EnrichResult
): Promise<void> {
  const outcome = await instrumentModule(
    {
      db: env.DB,
      org_id: orgId,
      entity_id: entity.id,
      module: 'review_analysis',
      mode: result.mode,
      triggered_by: result.triggered_by,
    },
    async (): Promise<ModuleOutcome> => {
      if (!env.ANTHROPIC_API_KEY) return { kind: 'skipped', reason: 'missing_api_key:anthropic' }
      const signalContext = await assembleEntityContext(env.DB, entity.id, {
        maxBytes: 8_000,
        typeFilter: ['signal'],
      })
      if (!signalContext) return { kind: 'skipped', reason: 'no_signal_context' }
      const reviewAnalysis = await analyzeReviewPatterns(signalContext, env.ANTHROPIC_API_KEY)
      if (!reviewAnalysis) return { kind: 'no_data', reason: 'no_analysis' }
      const ce = await appendContext(env.DB, orgId, {
        entity_id: entity.id,
        type: 'enrichment',
        content: `Review patterns: ${reviewAnalysis.response_pattern} responses, ${reviewAnalysis.engagement_level} engagement. ${reviewAnalysis.owner_accessible ? 'Owner appears accessible.' : ''} ${reviewAnalysis.insights}`,
        source: 'review_analysis',
        metadata: reviewAnalysis as unknown as Record<string, unknown>,
      })
      return { kind: 'succeeded', context_entry_id: ce.id }
    }
  )
  applyOutcome(result, 'review_analysis', outcome)
}

async function tryCompetitors(
  env: EnrichEnv,
  orgId: string,
  entity: Entity,
  result: EnrichResult
): Promise<void> {
  const outcome = await instrumentModule(
    {
      db: env.DB,
      org_id: orgId,
      entity_id: entity.id,
      module: 'competitors',
      mode: result.mode,
      triggered_by: result.triggered_by,
    },
    async (): Promise<ModuleOutcome> => {
      if (!env.GOOGLE_PLACES_API_KEY)
        return { kind: 'skipped', reason: 'missing_api_key:google_places' }
      const benchmark = await benchmarkCompetitors(
        entity.name,
        entity.vertical,
        entity.area,
        entity.pain_score,
        null,
        env.GOOGLE_PLACES_API_KEY
      )
      if (!benchmark) return { kind: 'no_data', reason: 'no_benchmark' }
      const ce = await appendContext(env.DB, orgId, {
        entity_id: entity.id,
        type: 'enrichment',
        content: `Competitor benchmarking: ${benchmark.summary} Top competitors: ${benchmark.competitors.map((c) => `${c.name} (${c.rating}★, ${c.review_count} reviews)`).join(', ')}.`,
        source: 'competitors',
        metadata: benchmark as unknown as Record<string, unknown>,
      })
      return { kind: 'succeeded', context_entry_id: ce.id }
    }
  )
  applyOutcome(result, 'competitors', outcome)
}

async function tryNews(
  env: EnrichEnv,
  orgId: string,
  entity: Entity,
  result: EnrichResult
): Promise<void> {
  const outcome = await instrumentModule(
    {
      db: env.DB,
      org_id: orgId,
      entity_id: entity.id,
      module: 'news_search',
      mode: result.mode,
      triggered_by: result.triggered_by,
    },
    async (): Promise<ModuleOutcome> => {
      if (!env.SERPAPI_API_KEY) return { kind: 'skipped', reason: 'missing_api_key:serpapi' }
      if (!env.ANTHROPIC_API_KEY) return { kind: 'skipped', reason: 'missing_api_key:anthropic' }
      const news = await searchNews(
        entity.name,
        entity.area,
        env.SERPAPI_API_KEY,
        env.ANTHROPIC_API_KEY
      )
      if (!news) return { kind: 'no_data', reason: 'no_results' }
      const ce = await appendContext(env.DB, orgId, {
        entity_id: entity.id,
        type: 'enrichment',
        content: `News/press: ${news.summary} (${news.mentions.length} mentions found)`,
        source: 'news_search',
        metadata: { mentions: news.mentions, summary: news.summary },
      })
      return { kind: 'succeeded', context_entry_id: ce.id }
    }
  )
  applyOutcome(result, 'news_search', outcome)
}

async function tryDeepWebsite(
  env: EnrichEnv,
  orgId: string,
  entity: Entity,
  result: EnrichResult
): Promise<void> {
  const outcome = await instrumentModule(
    {
      db: env.DB,
      org_id: orgId,
      entity_id: entity.id,
      module: 'deep_website',
      mode: result.mode,
      triggered_by: result.triggered_by,
    },
    async (): Promise<ModuleOutcome> => {
      if (!entity.website) return { kind: 'skipped', reason: 'missing_input:website' }
      if (!env.ANTHROPIC_API_KEY) return { kind: 'skipped', reason: 'missing_api_key:anthropic' }
      const analysis = await deepWebsiteAnalysis(entity.website, env.ANTHROPIC_API_KEY)
      if (!analysis) return { kind: 'no_data', reason: 'no_analysis' }
      const ce = await appendContext(env.DB, orgId, {
        entity_id: entity.id,
        type: 'enrichment',
        content: formatDeepWebsite(analysis),
        source: 'deep_website',
        metadata: analysis as unknown as Record<string, unknown>,
      })
      return { kind: 'succeeded', context_entry_id: ce.id }
    }
  )
  applyOutcome(result, 'deep_website', outcome)
}

async function tryReviewSynthesis(
  env: EnrichEnv,
  orgId: string,
  entity: Entity,
  result: EnrichResult
): Promise<void> {
  let inputFingerprint: string | null = null
  try {
    const ctx = await assembleEntityContext(env.DB, entity.id, {
      maxBytes: 20_000,
      typeFilter: ['signal', 'enrichment'],
    })
    if (ctx) inputFingerprint = await fingerprint(ctx)
  } catch {
    // Fingerprint is informational; do not block the run on failure.
  }

  const outcome = await instrumentModule(
    {
      db: env.DB,
      org_id: orgId,
      entity_id: entity.id,
      module: 'review_synthesis',
      mode: result.mode,
      triggered_by: result.triggered_by,
      input_fingerprint: inputFingerprint,
    },
    async (): Promise<ModuleOutcome> => {
      if (!env.ANTHROPIC_API_KEY) return { kind: 'skipped', reason: 'missing_api_key:anthropic' }
      const allContext = await assembleEntityContext(env.DB, entity.id, {
        maxBytes: 20_000,
        typeFilter: ['signal', 'enrichment'],
      })
      if (!allContext) return { kind: 'skipped', reason: 'no_context' }
      const synthesis = await synthesizeReviews(allContext, env.ANTHROPIC_API_KEY)
      if (!synthesis) return { kind: 'no_data', reason: 'no_synthesis' }
      const ce = await appendContext(env.DB, orgId, {
        entity_id: entity.id,
        type: 'enrichment',
        content: `Review synthesis: ${synthesis.customer_sentiment} Trend: ${synthesis.sentiment_trend}. Themes: ${synthesis.top_themes.join(', ')}. Problems: ${synthesis.operational_problems.map((p) => `${p.problem} (${p.confidence})`).join(', ')}.`,
        source: 'review_synthesis',
        metadata: synthesis as unknown as Record<string, unknown>,
      })
      return { kind: 'succeeded', context_entry_id: ce.id }
    }
  )
  applyOutcome(result, 'review_synthesis', outcome)
}

async function tryLinkedIn(
  env: EnrichEnv,
  orgId: string,
  entity: Entity,
  result: EnrichResult
): Promise<void> {
  const outcome = await instrumentModule(
    {
      db: env.DB,
      org_id: orgId,
      entity_id: entity.id,
      module: 'linkedin',
      mode: result.mode,
      triggered_by: result.triggered_by,
    },
    async (): Promise<ModuleOutcome> => {
      if (!env.PROXYCURL_API_KEY) return { kind: 'skipped', reason: 'missing_api_key:proxycurl' }
      const linkedin = await lookupLinkedIn(entity.name, entity.area, env.PROXYCURL_API_KEY)
      if (!linkedin) return { kind: 'no_data', reason: 'no_match' }
      const ce = await appendContext(env.DB, orgId, {
        entity_id: entity.id,
        type: 'enrichment',
        content: `LinkedIn: ${linkedin.company_name}. ${linkedin.employee_count ? `~${linkedin.employee_count} employees.` : ''} ${linkedin.industry ? `Industry: ${linkedin.industry}.` : ''} ${linkedin.description ? linkedin.description.slice(0, 200) : ''}`,
        source: 'linkedin',
        metadata: linkedin as unknown as Record<string, unknown>,
      })
      return { kind: 'succeeded', context_entry_id: ce.id }
    }
  )
  applyOutcome(result, 'linkedin', outcome)
}

async function tryIntelligenceBrief(
  env: EnrichEnv,
  orgId: string,
  entity: Entity,
  result: EnrichResult
): Promise<void> {
  let inputFingerprint: string | null = null
  try {
    const ctx = await assembleEntityContext(env.DB, entity.id, { maxBytes: 32_000 })
    if (ctx) inputFingerprint = await fingerprint(ctx)
  } catch {
    // Informational only.
  }

  const outcome = await instrumentModule(
    {
      db: env.DB,
      org_id: orgId,
      entity_id: entity.id,
      module: 'intelligence_brief',
      mode: result.mode,
      triggered_by: result.triggered_by,
      input_fingerprint: inputFingerprint,
    },
    async (): Promise<ModuleOutcome> => {
      if (!env.ANTHROPIC_API_KEY) return { kind: 'skipped', reason: 'missing_api_key:anthropic' }
      const fullContext = await assembleEntityContext(env.DB, entity.id, { maxBytes: 32_000 })
      if (!fullContext) return { kind: 'skipped', reason: 'no_context' }
      const brief = await generateDossier(fullContext, entity.name, env.ANTHROPIC_API_KEY)
      if (!brief) return { kind: 'no_data', reason: 'no_brief' }
      const ce = await appendContext(env.DB, orgId, {
        entity_id: entity.id,
        type: 'enrichment',
        content: brief,
        source: 'intelligence_brief',
        metadata: { model: 'claude-sonnet-4-20250514', trigger: 'at_ingest' },
      })
      return { kind: 'succeeded', context_entry_id: ce.id }
    }
  )
  applyOutcome(result, 'intelligence_brief', outcome)
}

async function regenerateOutreach(
  env: EnrichEnv,
  orgId: string,
  entity: Entity,
  result: EnrichResult
): Promise<void> {
  if (!env.ANTHROPIC_API_KEY) {
    result.skipped.push('outreach_draft')
    return
  }
  try {
    const context = await assembleEntityContext(env.DB, entity.id, { maxBytes: 24_000 })
    if (!context) {
      result.skipped.push('outreach_draft')
      return
    }
    const draft = await generateOutreachDraft(env.ANTHROPIC_API_KEY, entity.name, context)
    await appendContext(env.DB, orgId, {
      entity_id: entity.id,
      type: 'outreach_draft',
      content: draft,
      source: 'claude',
      metadata: {
        model: 'claude-sonnet-4-20250514',
        trigger: result.mode === 'full' ? 'at_ingest' : 're_enrich',
      },
    })
    result.completed.push('outreach_draft')
  } catch (err) {
    console.error('[enrichEntity] outreach_draft failed:', err)
    result.errors.push('outreach_draft')
  }
}

// ---------------------------------------------------------------------------
// Single-module runner — used by the admin "Retry" button per module.
// ---------------------------------------------------------------------------

const SINGLE_RUNNERS: Record<
  ModuleId,
  (env: EnrichEnv, orgId: string, entity: Entity, result: EnrichResult) => Promise<unknown>
> = {
  google_places: tryPlaces,
  website_analysis: tryWebsite,
  outscraper: tryOutscraper,
  acc_filing: tryAcc,
  roc_license: tryRoc,
  review_analysis: tryReviewAnalysis,
  competitors: tryCompetitors,
  news_search: tryNews,
  deep_website: tryDeepWebsite,
  review_synthesis: tryReviewSynthesis,
  linkedin: tryLinkedIn,
  intelligence_brief: tryIntelligenceBrief,
}

/**
 * Execute a single named module against an entity. Used by the admin
 * per-module Retry button. Records a row in enrichment_runs with the
 * provided triggered_by. Bypasses the full-mode brief idempotency check
 * (the caller explicitly asked to re-run this one module).
 */
export async function runSingleModule(
  env: EnrichEnv,
  orgId: string,
  entityId: string,
  module: ModuleId,
  options: { triggered_by: string } = { triggered_by: 'admin:retry' }
): Promise<EnrichResult> {
  const result: EnrichResult = {
    entityId,
    mode: 'reviews-and-news',
    triggered_by: options.triggered_by,
    completed: [],
    skipped: [],
    errors: [],
    alreadyEnriched: false,
  }

  const entity = await getEntity(env.DB, orgId, entityId)
  if (!entity) {
    result.errors.push('entity_not_found')
    return result
  }

  const runner = SINGLE_RUNNERS[module]
  if (!runner) {
    result.errors.push('unknown_module')
    return result
  }

  await runner(env, orgId, entity, result)
  return result
}

// ---------------------------------------------------------------------------
// Shared formatters — copied from dossier.ts during the merge.
// ---------------------------------------------------------------------------

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
