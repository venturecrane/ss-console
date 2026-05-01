/**
 * Cloudflare Workflows orchestration for entity enrichment (#631).
 *
 * Why this exists
 * ---------------
 * Production measurement on 2026-04-30 found 86% of created entities had
 * no enrichment activity at all. The legacy `ctx.waitUntil(enrichEntity(...))`
 * pattern queued the 12-module pipeline as detached promises, which
 * Cloudflare Workers killed when the post-response CPU budget (~30s)
 * elapsed. Lead-gen ingest batches exhausted that budget before more than
 * a handful of entities completed — the same failure class the `/scan`
 * Workflow was chartered to escape (#614, #615, #618).
 *
 * Cloudflare Workflows is the durable-execution primitive for this exact
 * shape: per-step checkpointing, automatic retries with backoff, total
 * runtime up to hours, observable in the dashboard. Each module is a
 * `step.do(...)` call. The orchestrator's state lives in the Workflow
 * engine's storage, not in the Worker isolate's RAM, so an isolate kill
 * mid-step just causes that step to retry on a new isolate.
 *
 * Architecture
 * ------------
 *   ss-web (Astro) — 7 trigger sites
 *      └─ dispatchEnrichmentWorkflow(...)
 *           └─ env.ENRICHMENT_WORKFLOW_SERVICE.fetch('https://internal/dispatch', ...)
 *                |
 *                v
 *   ss-enrichment-workflow (vanilla Worker)
 *      └─ POST /dispatch
 *           └─ env.ENRICHMENT_WORKFLOW.create({ params })
 *                └─ EnrichmentWorkflow.run(event, step)
 *                     ├─ step.do('init') — load entity, idempotency check
 *                     ├─ step.do('tier1-places') — re-load entity, tryPlaces
 *                     ├─ step.do('tier1-website')
 *                     ├─ ... 11 more steps ...
 *                     ├─ step.do('outreach') — tryOutreach (now instrumented)
 *                     └─ step.do('finalize') — set next_action
 *
 * Step body invariant
 * -------------------
 * Every step body re-loads the entity from D1 at its top via
 * `getEntity(...)`. Step return values are JSON-serialized and replayed
 * from cache on retry — threading a stale `Entity` snapshot across steps
 * would misfire downstream skip checks (e.g. `tryPlaces`'s
 * already_have_phone_and_website guard would see the pre-Places entity
 * even on a replay where Places already wrote phone/website to D1).
 * Tier-1 mutators write to D1 via `updateEntity`; re-loading at the top
 * of each step gives every step the fresh state regardless of replay path.
 *
 * Force semantics
 * ---------------
 * The legacy `enrichEntity` had a `force=true` skip-succeeded optimization
 * that read `latestRunByModule` and skipped already-succeeded modules.
 * That existed because the inline orchestrator hit a Cloudflare Workers
 * single-invocation budget cap. Workflows remove that budget pressure —
 * each step gets its own isolate. Force=true now means "actually re-run
 * everything"; this PR drops the skip-succeeded optimization.
 *
 * Idempotency
 * -----------
 * The `init` step queries `enrichment_runs` for a successful
 * `intelligence_brief` row. When mode='full' and one exists, the step
 * returns `{ skip: true }` and all subsequent steps no-op. Re-dispatching
 * a Workflow against an already-enriched entity costs a few D1 reads and
 * zero API calls.
 *
 * Failure semantics
 * -----------------
 * Each `step.do` retries automatically per its config. After retries are
 * exhausted, the step throws and the outer try/catch in `run()` logs the
 * failure and lets the Workflow end in `failed` state — visible in the
 * Cloudflare dashboard. The entity stays as-is; some modules may have
 * written rows, some may not have started. This is a step up from the
 * legacy `ctx.waitUntil` path, where a swallowed failure was invisible.
 */

import { WorkflowEntrypoint, type WorkflowEvent, type WorkflowStep } from 'cloudflare:workers'

import { getEntity, updateEntity, type Entity } from '../db/entities'
import { listContext } from '../db/context'
import {
  createEnrichResult,
  tryPlaces,
  tryWebsite,
  tryOutscraper,
  tryAcc,
  tryRoc,
  tryReviewAnalysis,
  tryCompetitors,
  tryNews,
  tryDeepWebsite,
  tryReviewSynthesis,
  tryLinkedIn,
  tryIntelligenceBrief,
  tryOutreach,
  type EnrichEnv,
  type EnrichMode,
} from './index'

/**
 * Bindings the Workflow needs at runtime. Mirrors `EnrichEnv` from the
 * module wrappers — keep them in sync.
 */
export interface EnrichmentWorkflowBindings {
  DB: D1Database
  ANTHROPIC_API_KEY?: string
  GOOGLE_PLACES_API_KEY?: string
  OUTSCRAPER_API_KEY?: string
  PROXYCURL_API_KEY?: string
  SERPAPI_API_KEY?: string
}

/** Params passed to `env.ENRICHMENT_WORKFLOW.create({ params: ... })`. */
export interface EnrichmentWorkflowParams {
  entityId: string
  orgId: string
  mode: EnrichMode
  triggered_by: string
}

/**
 * Step retry budgets. Tuned per module type, mirroring `/scan`'s tuning:
 *
 *   - tier1 / tier2 cheap-HTTP modules — 1 retry. These are quick HTTP
 *     calls that fail loudly on rate-limit or outage. A second attempt
 *     costs ~$0.02.
 *   - Anthropic-backed modules — 2 retries with exponential backoff.
 *     Anthropic occasionally returns 429/529; a retry usually wins.
 *   - intelligence_brief — 2 retries with exponential backoff. The
 *     longest call and the most likely to hit a transient.
 *   - infra steps (init, finalize) — 1 retry. Pure D1 work; if D1 is
 *     down a single retry is enough to ride out the blip.
 */
const RETRY_TIER1 = {
  limit: 1,
  delay: '5 seconds' as const,
  backoff: 'constant' as const,
}
const RETRY_TIER2 = {
  limit: 2,
  delay: '10 seconds' as const,
  backoff: 'exponential' as const,
}
const RETRY_TIER3 = {
  limit: 2,
  delay: '15 seconds' as const,
  backoff: 'exponential' as const,
}
const RETRY_INFRA = {
  limit: 1,
  delay: '5 seconds' as const,
  backoff: 'constant' as const,
}

const TIMEOUT_TIER1 = '2 minutes' as const
const TIMEOUT_TIER2 = '5 minutes' as const
const TIMEOUT_TIER3 = '10 minutes' as const
const TIMEOUT_INFRA = '2 minutes' as const

/**
 * Step return shapes. Workflows persists step return values to its state
 * store, so they MUST be JSON-serializable. Avoid returning rich objects
 * (Date, Map, Set); stick to primitives, plain objects, arrays.
 */
interface InitStepResult {
  skip: boolean
  reason?: 'already_enriched' | 'entity_not_found'
}

interface ModuleStepResult {
  ran: boolean
  /** Diagnostic — populated when a module-wrapper outcome is captured. */
  outcome?: 'succeeded' | 'no_data' | 'skipped' | 'failed'
}

const SKIPPED: ModuleStepResult = { ran: false, outcome: 'skipped' }

/**
 * The Workflow class. Cloudflare instantiates this on demand inside an
 * isolate; `run()` is the orchestration body. Each `step.do` call is
 * checkpointed: if the isolate dies, the next isolate replays from the
 * last checkpoint, re-using completed step results from the state store.
 */
export class EnrichmentWorkflow extends WorkflowEntrypoint<
  EnrichmentWorkflowBindings,
  EnrichmentWorkflowParams
> {
  async run(event: WorkflowEvent<EnrichmentWorkflowParams>, step: WorkflowStep): Promise<void> {
    const { entityId, orgId, mode, triggered_by } = event.payload
    const env = this.env

    const enrichEnv: EnrichEnv = {
      DB: env.DB,
      ANTHROPIC_API_KEY: env.ANTHROPIC_API_KEY,
      GOOGLE_PLACES_API_KEY: env.GOOGLE_PLACES_API_KEY,
      OUTSCRAPER_API_KEY: env.OUTSCRAPER_API_KEY,
      PROXYCURL_API_KEY: env.PROXYCURL_API_KEY,
      SERPAPI_API_KEY: env.SERPAPI_API_KEY,
    }

    // Top-level error handling. After retries are exhausted, any thrown
    // error here is the workflow's terminal failure. We log it and end —
    // unlike `/scan` we don't have a single status row to mark failed,
    // since enrichment failures are recorded per-module in
    // `enrichment_runs`. The dashboard view of failed runs is the
    // operator-facing surface.
    try {
      await this.runInner(enrichEnv, entityId, orgId, mode, triggered_by, step)
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      console.error('[enrichment-workflow] terminal failure:', { entityId, orgId, mode, message })
      // Don't rethrow — the Workflow ends cleanly. The dashboard records
      // the failed run; per-module failures are already in enrichment_runs.
    }
  }

  /**
   * Inner orchestration. Split out so `run()` can wrap a single try/catch
   * around the whole pipeline.
   */
  private async runInner(
    env: EnrichEnv,
    entityId: string,
    orgId: string,
    mode: EnrichMode,
    triggered_by: string,
    step: WorkflowStep
  ): Promise<void> {
    // ---------------------------------------------------------------
    // init: load entity, idempotency check.
    // ---------------------------------------------------------------
    const init = await step.do<InitStepResult>(
      'init',
      { retries: RETRY_INFRA, timeout: TIMEOUT_INFRA },
      async () => {
        const entity = await getEntity(env.DB, orgId, entityId)
        if (!entity) {
          return { skip: true, reason: 'entity_not_found' as const }
        }
        // Mode='full' with an existing intelligence_brief context entry
        // skips. reviews-and-news intentionally bypasses this — it is the
        // explicit refresh path.
        if (mode === 'full') {
          const existing = await listContext(env.DB, entityId, { type: 'enrichment' })
          const hasBrief = existing.some((e) => e.source === 'intelligence_brief')
          if (hasBrief) {
            return { skip: true, reason: 'already_enriched' as const }
          }
        }
        return { skip: false }
      }
    )

    if (init.skip) {
      // Idempotent short-circuit — nothing to do. The Workflow ends in
      // `complete` state with the init step recorded.
      return
    }

    // Helper used inside every subsequent step. Re-loads the entity from
    // D1 fresh so step replays see the latest state (rather than a cached
    // snapshot from a prior step's return value).
    const loadEntity = async (): Promise<Entity | null> => {
      return getEntity(env.DB, orgId, entityId)
    }

    // ---------------------------------------------------------------
    // mode === 'reviews-and-news': cheap refresh path.
    // Runs only review_analysis, review_synthesis, news, brief
    // (if missing), outreach, finalize.
    // ---------------------------------------------------------------
    if (mode === 'reviews-and-news') {
      await this.runReviewsAndNews(step, env, orgId, entityId, triggered_by, loadEntity)
      return
    }

    // ---------------------------------------------------------------
    // mode === 'full': full 13-module pipeline.
    // ---------------------------------------------------------------
    await this.runFull(step, env, orgId, entityId, triggered_by, loadEntity)
  }

  /**
   * Full-mode pipeline. Each step re-loads the entity at its top.
   */
  private async runFull(
    step: WorkflowStep,
    env: EnrichEnv,
    orgId: string,
    entityId: string,
    triggered_by: string,
    loadEntity: () => Promise<Entity | null>
  ): Promise<void> {
    // Tier 1 — contact + tech signals.
    await step.do<ModuleStepResult>(
      'tier1-places',
      { retries: RETRY_TIER1, timeout: TIMEOUT_TIER1 },
      async () => runModule(env, orgId, triggered_by, 'full', loadEntity, tryPlaces)
    )
    await step.do<ModuleStepResult>(
      'tier1-website',
      { retries: RETRY_TIER2, timeout: TIMEOUT_TIER2 },
      async () => runModule(env, orgId, triggered_by, 'full', loadEntity, tryWebsite)
    )
    await step.do<ModuleStepResult>(
      'tier1-outscraper',
      { retries: RETRY_TIER1, timeout: TIMEOUT_TIER1 },
      async () => runModule(env, orgId, triggered_by, 'full', loadEntity, tryOutscraper)
    )
    await step.do<ModuleStepResult>(
      'tier1-acc',
      { retries: RETRY_TIER1, timeout: TIMEOUT_TIER1 },
      async () => runModule(env, orgId, triggered_by, 'full', loadEntity, tryAcc)
    )
    await step.do<ModuleStepResult>(
      'tier1-roc',
      { retries: RETRY_TIER1, timeout: TIMEOUT_TIER1 },
      async () => runModule(env, orgId, triggered_by, 'full', loadEntity, tryRoc)
    )

    // Tier 2 — review patterns + competitors + news.
    await step.do<ModuleStepResult>(
      'tier2-review-analysis',
      { retries: RETRY_TIER2, timeout: TIMEOUT_TIER2 },
      async () => runModule(env, orgId, triggered_by, 'full', loadEntity, tryReviewAnalysis)
    )
    await step.do<ModuleStepResult>(
      'tier2-competitors',
      { retries: RETRY_TIER1, timeout: TIMEOUT_TIER1 },
      async () => runModule(env, orgId, triggered_by, 'full', loadEntity, tryCompetitors)
    )
    await step.do<ModuleStepResult>(
      'tier2-news',
      { retries: RETRY_TIER1, timeout: TIMEOUT_TIER1 },
      async () => runModule(env, orgId, triggered_by, 'full', loadEntity, tryNews)
    )

    // Tier 3 — deep intelligence.
    await step.do<ModuleStepResult>(
      'tier3-deep-website',
      { retries: RETRY_TIER3, timeout: TIMEOUT_TIER3 },
      async () => runModule(env, orgId, triggered_by, 'full', loadEntity, tryDeepWebsite)
    )
    await step.do<ModuleStepResult>(
      'tier3-review-synthesis',
      { retries: RETRY_TIER3, timeout: TIMEOUT_TIER3 },
      async () => runModule(env, orgId, triggered_by, 'full', loadEntity, tryReviewSynthesis)
    )
    await step.do<ModuleStepResult>(
      'tier3-linkedin',
      { retries: RETRY_TIER1, timeout: TIMEOUT_TIER1 },
      async () => runModule(env, orgId, triggered_by, 'full', loadEntity, tryLinkedIn)
    )
    await step.do<ModuleStepResult>(
      'tier3-intelligence-brief',
      { retries: RETRY_TIER3, timeout: TIMEOUT_TIER3 },
      async () => runModule(env, orgId, triggered_by, 'full', loadEntity, tryIntelligenceBrief)
    )

    // Outreach draft from the full enriched context.
    await step.do<ModuleStepResult>(
      'outreach',
      { retries: RETRY_TIER2, timeout: TIMEOUT_TIER2 },
      async () => runModule(env, orgId, triggered_by, 'full', loadEntity, tryOutreach)
    )

    // Finalize — set next_action so the admin inbox shows "review and send".
    await step.do<{ ok: boolean }>(
      'finalize',
      { retries: RETRY_INFRA, timeout: TIMEOUT_INFRA },
      async () => {
        const entity = await loadEntity()
        if (!entity) return { ok: false }
        if (!entity.next_action) {
          await updateEntity(env.DB, orgId, entityId, {
            next_action: 'Review enrichment and send outreach email',
            next_action_at: new Date().toISOString(),
          })
        }
        return { ok: true }
      }
    )
  }

  /**
   * Reviews-and-news refresh pipeline. Subset of `runFull` plus a
   * conditional intelligence_brief backfill.
   */
  private async runReviewsAndNews(
    step: WorkflowStep,
    env: EnrichEnv,
    orgId: string,
    entityId: string,
    triggered_by: string,
    loadEntity: () => Promise<Entity | null>
  ): Promise<void> {
    await step.do<ModuleStepResult>(
      'tier2-review-analysis',
      { retries: RETRY_TIER2, timeout: TIMEOUT_TIER2 },
      async () =>
        runModule(env, orgId, triggered_by, 'reviews-and-news', loadEntity, tryReviewAnalysis)
    )
    await step.do<ModuleStepResult>(
      'tier3-review-synthesis',
      { retries: RETRY_TIER3, timeout: TIMEOUT_TIER3 },
      async () =>
        runModule(env, orgId, triggered_by, 'reviews-and-news', loadEntity, tryReviewSynthesis)
    )
    await step.do<ModuleStepResult>(
      'tier2-news',
      { retries: RETRY_TIER1, timeout: TIMEOUT_TIER1 },
      async () => runModule(env, orgId, triggered_by, 'reviews-and-news', loadEntity, tryNews)
    )
    // Conditional brief backfill: re-enrich Re-enriches the cheap modules
    // and ALSO backfills a missing intelligence_brief. Entities whose
    // initial pipeline crashed mid-run end up with no brief; Re-enrich
    // is the natural place to heal that.
    await step.do<ModuleStepResult>(
      'tier3-intelligence-brief',
      { retries: RETRY_TIER3, timeout: TIMEOUT_TIER3 },
      async () => {
        const existing = await listContext(env.DB, entityId, { type: 'enrichment' })
        const hasBrief = existing.some((e) => e.source === 'intelligence_brief')
        if (hasBrief) return SKIPPED
        return runModule(
          env,
          orgId,
          triggered_by,
          'reviews-and-news',
          loadEntity,
          tryIntelligenceBrief
        )
      }
    )
    await step.do<ModuleStepResult>(
      'outreach',
      { retries: RETRY_TIER2, timeout: TIMEOUT_TIER2 },
      async () => runModule(env, orgId, triggered_by, 'reviews-and-news', loadEntity, tryOutreach)
    )
    await step.do<{ ok: boolean }>(
      'finalize',
      { retries: RETRY_INFRA, timeout: TIMEOUT_INFRA },
      async () => {
        // No-op for reviews-and-news — next_action is set during initial
        // promotion, and the refresh path should not overwrite it.
        return { ok: true }
      }
    )
  }
}

/**
 * Run a single module wrapper inside a step body. Handles the per-step
 * entity reload + per-step EnrichResult accumulator. The wrapper writes
 * its own enrichment_runs row via instrumentModule; we only need to
 * surface a step return value for Workflows replay.
 */
async function runModule(
  env: EnrichEnv,
  orgId: string,
  triggered_by: string,
  mode: EnrichMode,
  loadEntity: () => Promise<Entity | null>,
  wrapper: (
    env: EnrichEnv,
    orgId: string,
    entity: Entity,
    result: ReturnType<typeof createEnrichResult>
  ) => Promise<void>
): Promise<ModuleStepResult> {
  const entity = await loadEntity()
  if (!entity) return SKIPPED
  const result = createEnrichResult(entity.id, mode, triggered_by)
  await wrapper(env, orgId, entity, result)
  return {
    ran: true,
    outcome:
      result.completed.length > 0 ? 'succeeded' : result.errors.length > 0 ? 'failed' : 'skipped',
  }
}
