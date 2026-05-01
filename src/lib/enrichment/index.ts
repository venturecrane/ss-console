/**
 * Per-module enrichment wrappers + admin retry runner.
 *
 * Orchestration moved to `src/lib/enrichment/workflow.ts` (issue #631) — the
 * legacy `enrichEntity()` + `runReviewsAndNews()` orchestrators were deleted
 * because their `ctx.waitUntil`-detached invocation shape was being killed
 * by Cloudflare Workers' post-response CPU budget on lead-gen ingest
 * batches. The 30-day measurement on 2026-04-30 found 86% of created
 * entities had no enrichment activity at all — the orchestrator promises
 * never got CPU time before the Worker isolate was killed.
 *
 * Cloudflare Workflows replaces that orchestration: each `try*` module
 * wrapper below is invoked from a discrete `step.do(...)` call in the
 * `EnrichmentWorkflow` class, with per-step durability, retries, and
 * dashboard observability. The wrappers themselves are unchanged from the
 * legacy implementation — `instrumentModule` writes the `enrichment_runs`
 * row, `applyOutcome` accumulates the in-memory result. The workflow
 * uses a fresh per-step `EnrichResult` since the database is the source
 * of truth.
 *
 * The `runSingleModule` admin retry path (per-module Retry button) keeps
 * its own thin orchestrator below — it's a synchronous admin-triggered
 * single-module run, not subject to the cron-loop CPU budget that motivated
 * the workflow refactor.
 */

import type { Entity } from '../db/entities'
import { getEntity, updateEntity } from '../db/entities'
import { appendContext, assembleEntityContext } from '../db/context'
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
 * Build a fresh per-call `EnrichResult` accumulator. The Workflow's step
 * bodies use a per-step accumulator so each step's `applyOutcome` calls
 * stay local — the persistent record is `enrichment_runs`.
 */
export function createEnrichResult(
  entityId: string,
  mode: EnrichMode,
  triggered_by: string
): EnrichResult {
  return {
    entityId,
    mode,
    triggered_by,
    completed: [],
    skipped: [],
    errors: [],
    alreadyEnriched: false,
  }
}

/**
 * Map a wrapper outcome into the legacy in-memory EnrichResult arrays so
 * callers that read result.completed continue to work. The persisted
 * enrichment_runs row is the durable record; this is best-effort accounting
 * for the in-memory return value.
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

export type EnrichEnv = {
  DB: D1Database
  ANTHROPIC_API_KEY?: string
  GOOGLE_PLACES_API_KEY?: string
  OUTSCRAPER_API_KEY?: string
  PROXYCURL_API_KEY?: string
  SERPAPI_API_KEY?: string
}

// ---------------------------------------------------------------------------
// Individual module wrappers — each is best-effort, instruments its own
// `enrichment_runs` row, and is idempotent (a second run with the same
// inputs writes another row but does not corrupt state).
//
// Tier 1 modules (`tryPlaces`, `tryOutscraper`) update entity.phone /
// entity.website via `updateEntity`; downstream modules MUST re-load the
// entity from D1 to see the fresh state. The Workflow class enforces this
// by calling `getEntity(...)` at the top of every step body.
// ---------------------------------------------------------------------------

export async function tryPlaces(
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
      return { kind: 'succeeded', context_entry_id: ce.id }
    }
  )
  applyOutcome(result, 'google_places', outcome)
}

export async function tryWebsite(
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
}

export async function tryOutscraper(
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
      return { kind: 'succeeded', context_entry_id: ce.id }
    }
  )
  applyOutcome(result, 'outscraper', outcome)
}

export async function tryAcc(
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

export async function tryRoc(
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

export async function tryReviewAnalysis(
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

export async function tryCompetitors(
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

export async function tryNews(
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

export async function tryDeepWebsite(
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

export async function tryReviewSynthesis(
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

export async function tryLinkedIn(
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

export async function tryIntelligenceBrief(
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

/**
 * Outreach draft generation. Wrapped in `instrumentModule` (issue #631)
 * so failures land a `failed` row in `enrichment_runs` instead of vanishing
 * into a console.error — the legacy implementation surfaced the failure
 * only via `result.errors` (in-memory) and a console line, so a permanent
 * outreach failure was effectively invisible.
 *
 * Uses the synthetic `outreach_draft` ModuleId added to `modules.ts`.
 */
export async function tryOutreach(
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
      module: 'outreach_draft',
      mode: result.mode,
      triggered_by: result.triggered_by,
    },
    async (): Promise<ModuleOutcome> => {
      if (!env.ANTHROPIC_API_KEY) return { kind: 'skipped', reason: 'missing_api_key:anthropic' }
      const context = await assembleEntityContext(env.DB, entity.id, { maxBytes: 24_000 })
      if (!context) return { kind: 'skipped', reason: 'no_context' }
      const draft = await generateOutreachDraft(
        env.ANTHROPIC_API_KEY,
        entity.name,
        context,
        entity.vertical
      )
      const ce = await appendContext(env.DB, orgId, {
        entity_id: entity.id,
        type: 'outreach_draft',
        content: draft,
        source: 'claude',
        metadata: {
          model: 'claude-sonnet-4-20250514',
          trigger: result.mode === 'full' ? 'at_ingest' : 're_enrich',
          // Issue #594 — record which vertical guidance the prompt used so
          // re-runs and audits can see the variant. Null/unrecognized
          // verticals record as null and the generic backbone was used.
          vertical: entity.vertical ?? null,
        },
      })
      return { kind: 'succeeded', context_entry_id: ce.id }
    }
  )
  applyOutcome(result, 'outreach_draft', outcome)
}

// ---------------------------------------------------------------------------
// Single-module runner — used by the admin "Retry" button per module.
// Synchronous, single-module, single-shot — not subject to the cron-loop
// CPU budget that motivated the workflow refactor (#631).
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
  outreach_draft: tryOutreach,
}

/**
 * Execute a single named module against an entity. Used by the admin
 * per-module Retry button. Records a row in enrichment_runs with the
 * provided triggered_by. Bypasses idempotency checks (the caller
 * explicitly asked to re-run this one module).
 */
export async function runSingleModule(
  env: EnrichEnv,
  orgId: string,
  entityId: string,
  module: ModuleId,
  options: { triggered_by: string } = { triggered_by: 'admin:retry' }
): Promise<EnrichResult> {
  const result = createEnrichResult(entityId, 'reviews-and-news', options.triggered_by)

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
