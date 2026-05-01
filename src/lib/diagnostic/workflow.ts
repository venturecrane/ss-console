/**
 * Cloudflare Workflows orchestration for the Engine 1 /scan diagnostic
 * (#614).
 *
 * Why this exists
 * ---------------
 * The previous orchestrator (`runDiagnosticScan` in `index.ts`) ran inline
 * via `ctx.waitUntil()` from `/api/scan/verify`. That worked for the
 * thin-footprint short-circuit path (~2-3s wall clock) but silently lost
 * the happy path: each completed scan is a 6-module pipeline whose total
 * wall clock — places + outscraper + 4 Claude calls — easily exceeds
 * the Cloudflare Workers `ctx.waitUntil` budget (~30s on the paid plan).
 * The 2026-04-27 phoenixanimalexterminator.com run is the exemplar:
 * 5/6 modules ran cleanly, intelligence_brief never started, no email
 * was ever sent, no failure marker was recorded. The Worker isolate was
 * killed mid-Claude-call.
 *
 * Cloudflare Workflows is the durable-execution primitive for exactly
 * this shape: per-step checkpointing, automatic retries with backoff,
 * total runtime up to hours, observable in the dashboard. Each module is
 * a `step.do(...)` call. The orchestrator's state lives in the Workflow
 * engine's storage, not in the Worker isolate's RAM, so an isolate kill
 * mid-step just causes that step to retry on a new isolate.
 *
 * Architecture
 * ------------
 *   /api/scan/verify
 *      ├─ markScanVerified(db, row.id)
 *      └─ env.SCAN_WORKFLOW.create({ params: { scanRequestId } })
 *           |
 *           v
 *   ScanDiagnosticWorkflow.run(event, step)
 *      step.do('init')                       — find-or-create entity, signal ctx
 *      step.do('places-and-outscraper')      — Tier 1 contact data + strict-match guard
 *      step.do('thin-footprint-gate')        — early exit if footprint too thin
 *      step.do('parallel-tier2')             — website_analysis + review_synthesis (in parallel)
 *      step.do('deep-website')               — Tier 3 (Claude)
 *      step.do('intelligence-brief')         — final synthesis (longest-running)
 *      step.do('render-and-email')           — anti-fabrication renderer + Resend
 *      step.do('mark-completed')             — final scan_status update
 *
 * Failure semantics
 * -----------------
 * Each `step.do` retries automatically on thrown errors per its config.
 * After retries exhausted, the step throws and the Workflow's outer
 * try/catch in `run()` records `scan_status='failed'` with the failing
 * module + truncated error in `scan_status_reason`, fires the existing
 * admin alert via `src/lib/diagnostic/admin-alert.ts`, and ends the
 * Workflow. No retry beyond the per-step config — once we've decided a
 * module is dead, the prospect doesn't get a half-fabricated report.
 *
 * Anti-fabrication preserved
 * --------------------------
 * The pruned-pipeline-only contract from #598 + the strict-match guard
 * from #612 are unchanged. Tier 2 + Tier 3 modules only run after the
 * gate accepts the footprint. The renderer in `render.ts` still enforces
 * anti-fabrication per section. This file is structural: it changes the
 * orchestrator's runtime, not its semantics.
 */

import { WorkflowEntrypoint, type WorkflowEvent, type WorkflowStep } from 'cloudflare:workers'

import { ORG_ID } from '../constants'
import { findOrCreateEntity, getEntity, type Entity } from '../db/entities'
import { appendContext } from '../db/context'
import { getScanRequest, updateScanRequestRun, type ScanRequest } from '../db/scan-requests'
import { createMagicLink, PROSPECT_MAGIC_LINK_EXPIRY_MS } from '../auth/magic-link'
import { createOutsideView } from '../db/outside-views'
import { renderedReportToArtifactJsonV1 } from '../db/outside-views/adapter'
import { renderDiagnosticReport, type RenderedReport } from './render'
import { sendScanFailureAlert } from './admin-alert'
import {
  ScanModuleError,
  SOURCE_PIPELINE,
  evaluateThinFootprintGate,
  humanizeDomain,
  runDeepWebsite,
  runIntelligenceBrief,
  runOutscraper,
  runPlaces,
  runReviewSynthesis,
  runWebsiteAnalysis,
  sendDiagnosticReportEmail,
  sendOutsideViewReadyEmail,
  sendThinFootprintEmail,
  type DiagnosticEnv,
} from './index'

/**
 * Bindings the Workflow needs at runtime. Mirrors `DiagnosticEnv` from
 * the legacy orchestrator — keep them in sync.
 *
 * Outside View Phase 1 PR-B (ADR 0002) added:
 *   - PORTAL_BASE_URL: builds the magic-link URL pointing at
 *     portal.smd.services/auth/verify
 *   - OUTSIDE_VIEW_PORTAL_DELIVERY: feature flag controlling email
 *     destination (legacy report email vs Outside View magic-link email)
 */
export interface ScanWorkflowBindings {
  DB: D1Database
  ANTHROPIC_API_KEY?: string
  GOOGLE_PLACES_API_KEY?: string
  OUTSCRAPER_API_KEY?: string
  RESEND_API_KEY?: string
  APP_BASE_URL?: string
  PORTAL_BASE_URL?: string
  OUTSIDE_VIEW_PORTAL_DELIVERY?: string
}

/**
 * Outside View feature flag check (ADR 0002 Phase 1 PR-B). The flag is
 * "1" or "true" to enable; any other value (including unset) is OFF.
 * Default OFF at merge so the cutover ships behind a flag and can be
 * verified via shadow-write before user-visible behavior flips.
 */
function isOutsideViewDeliveryOn(flag: string | undefined): boolean {
  return flag === '1' || flag === 'true'
}

/** Params passed to `env.SCAN_WORKFLOW.create({ params: ... })`. */
export interface ScanWorkflowParams {
  scanRequestId: string
}

/**
 * Step retry budgets. Tuned per module:
 *
 *   - places + outscraper       — 1 retry. These are cheap HTTP calls
 *                                  that fail loudly on rate-limit or
 *                                  outage. A second attempt costs ~$0.02.
 *   - website_analysis +        — 2 retries with backoff. Anthropic
 *     review_synthesis +          occasionally returns 429 / 529; a
 *     deep_website                 retry usually wins.
 *   - intelligence_brief        — 2 retries with backoff. Same
 *                                  rationale; this is the longest call
 *                                  and the most likely to hit a
 *                                  transient.
 *   - render + email + db       — 1 retry. Pure deterministic work
 *                                  except for the Resend call; Resend
 *                                  is reliable enough that a single
 *                                  retry is sufficient.
 *
 * All retries throw the underlying ScanModuleError (or a plain Error for
 * non-module steps); after the limit is exhausted Workflows propagates
 * the throw to the outer run() handler.
 */
// `WorkflowSleepDuration` is a template literal type
// `${number} ${label}s`. Plain `string` doesn't satisfy it; we declare
// the values with `as const` so the exact literal narrows correctly.
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
  entityId: string
  entityName: string
  /** Echo of fields we re-read in later steps so we don't have to query D1
   *  again from the next step (D1 reads are fine inside steps too, this
   *  is just an optimization). */
  entityWebsite: string | null
}

interface PlacesStepResult {
  /** True when Places returned a result AND the strict domain-match guard
   *  accepted it. False covers "no Places result" + "Places returned a
   *  different business". */
  strictMatched: boolean
  modulesRan: string[]
}

interface GateStepResult {
  thin: boolean
  reason: string
  /** When thin=true, the gate-tripped email send result. When thin=false,
   *  email_sent is irrelevant (the happy-path email is sent later). */
  emailSent: boolean
}

interface RenderStepResult {
  emailSent: boolean
}

/**
 * The Workflow class. Cloudflare instantiates this on demand inside an
 * isolate; `run()` is the orchestration body. Each `step.do` call is
 * checkpointed: if the isolate dies, the next isolate replays from the
 * last checkpoint, re-using completed step results from the state store.
 */
export class ScanDiagnosticWorkflow extends WorkflowEntrypoint<
  ScanWorkflowBindings,
  ScanWorkflowParams
> {
  async run(event: WorkflowEvent<ScanWorkflowParams>, step: WorkflowStep): Promise<void> {
    const { scanRequestId } = event.payload
    const env = this.env

    // Diagnostic env shape used by the legacy module wrappers. We rebuild
    // it once here so the wrappers see exactly the same surface they did
    // under runDiagnosticScan.
    const diagEnv: DiagnosticEnv = {
      DB: env.DB,
      ANTHROPIC_API_KEY: env.ANTHROPIC_API_KEY,
      GOOGLE_PLACES_API_KEY: env.GOOGLE_PLACES_API_KEY,
      OUTSCRAPER_API_KEY: env.OUTSCRAPER_API_KEY,
      RESEND_API_KEY: env.RESEND_API_KEY,
      APP_BASE_URL: env.APP_BASE_URL,
    }

    // Top-level error handling. After retries are exhausted, any thrown
    // error here is the workflow's terminal failure. Record it on the
    // scan_request, fire the admin alert, and end. We never let the
    // exception escape this method — Workflows treats an uncaught throw
    // as an Errored state, which is fine, but we want our own bookkeeping
    // (scan_status='failed', admin alert) to run even on terminal
    // failure.
    try {
      await this.runInner(diagEnv, scanRequestId, step)
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      const failingModule = err instanceof ScanModuleError ? err.module : 'orchestrator'
      console.error('[scan-workflow] terminal failure:', err)

      // Look up scan_request to surface domain/email in the alert and so
      // we can update the row. If even this read fails we swallow the
      // secondary error — the alert path is best-effort.
      let scanRequest: ScanRequest | null = null
      try {
        scanRequest = await getScanRequest(env.DB, scanRequestId)
      } catch (lookupErr) {
        console.error('[scan-workflow] failed to read scan_request for alert:', lookupErr)
      }

      try {
        await updateScanRequestRun(env.DB, scanRequestId, {
          scan_status: 'failed',
          scan_completed_at: new Date().toISOString(),
          error_message: message.slice(0, 500),
          scan_status_reason: `${failingModule}: ${message}`.slice(0, 500),
        })
      } catch (writeErr) {
        console.error('[scan-workflow] failed to mark scan_request failed:', writeErr)
      }

      try {
        await sendScanFailureAlert(env.RESEND_API_KEY, {
          scanRequestId,
          submittedDomain: scanRequest?.domain ?? '(unknown)',
          requesterEmail: scanRequest?.email ?? '(unknown)',
          failingModule,
          errorMessage: message,
        })
      } catch (alertErr) {
        console.error('[scan-workflow] failed to send admin alert:', alertErr)
      }
      // Don't rethrow — the Workflow can end cleanly now that we've
      // recorded the failure. Rethrowing would put the Workflow into the
      // Errored state, which is fine but also no longer useful (we've
      // already stored everything we need on the scan_request row).
    }
  }

  /**
   * Inner orchestration. Split out so the outer `run` can wrap a single
   * try/catch around the whole pipeline.
   */
  private async runInner(
    env: DiagnosticEnv,
    scanRequestId: string,
    step: WorkflowStep
  ): Promise<void> {
    // ---------------------------------------------------------------
    // Pre-flight: load the scan_request row and short-circuit if it is
    // already terminal. Idempotency for double-clicks of the magic link
    // (the verify endpoint may dispatch the workflow twice).
    // ---------------------------------------------------------------
    const scanRequest = await step.do(
      'load-scan-request',
      { retries: RETRY_INFRA, timeout: TIMEOUT_INFRA },
      async () => {
        const row = await getScanRequest(env.DB, scanRequestId)
        if (!row) {
          // Row missing entirely is non-retryable — there's nothing for
          // a retry to find. Throw a plain error and let the outer
          // handler record it as orchestrator failure.
          throw new Error(`scan_request not found: ${scanRequestId}`)
        }
        return {
          id: row.id,
          email: row.email,
          domain: row.domain,
          linkedin_url: row.linkedin_url,
          scan_status: row.scan_status,
          entity_id: row.entity_id,
        }
      }
    )

    if (scanRequest.scan_status === 'completed') {
      // Workflow was dispatched twice (verify endpoint double-click).
      // Nothing to do.
      return
    }

    // Mark scan_started_at on the row. Idempotent — Workflows may replay
    // this step on a retry; the column ends up with the latest start
    // time in the worst case, which is fine.
    await step.do('mark-started', { retries: RETRY_INFRA, timeout: TIMEOUT_INFRA }, async () => {
      await updateScanRequestRun(env.DB, scanRequestId, {
        scan_started_at: new Date().toISOString(),
      })
      return { ok: true }
    })

    // ---------------------------------------------------------------
    // Step: init — find-or-create entity for the scanned domain, log
    // the inbound submission as a 'signal' for downstream attribution.
    // ---------------------------------------------------------------
    const init = await step.do<InitStepResult>(
      'init-entity',
      { retries: RETRY_INFRA, timeout: TIMEOUT_INFRA },
      async () => {
        const placeholderName = humanizeDomain(scanRequest.domain)
        const foc = await findOrCreateEntity(env.DB, ORG_ID, {
          name: placeholderName,
          website: `https://${scanRequest.domain}`,
          area: 'Phoenix, AZ',
          source_pipeline: SOURCE_PIPELINE,
        })
        const entity: Entity = foc.entity

        await updateScanRequestRun(env.DB, scanRequest.id, { entity_id: entity.id })

        await appendContext(env.DB, ORG_ID, {
          entity_id: entity.id,
          type: 'signal',
          source: SOURCE_PIPELINE,
          content: `Inbound diagnostic scan submitted from ${scanRequest.email}. Domain: ${scanRequest.domain}.${
            scanRequest.linkedin_url ? ` LinkedIn: ${scanRequest.linkedin_url}.` : ''
          }`,
          metadata: {
            scan_request_id: scanRequest.id,
            requester_email: scanRequest.email,
            submitted_domain: scanRequest.domain,
            linkedin_url: scanRequest.linkedin_url,
          },
        })

        return {
          entityId: entity.id,
          entityName: entity.name,
          entityWebsite: entity.website ?? null,
        }
      }
    )

    // Helper to re-load the canonical entity row inside a step. Module
    // wrappers update phone/website on the entities table, so subsequent
    // steps need the fresh row.
    const loadEntity = async (): Promise<Entity> => {
      const e = await getEntity(env.DB, ORG_ID, init.entityId)
      if (!e) {
        throw new Error(`entity disappeared mid-workflow: ${init.entityId}`)
      }
      return e
    }

    // Build a ScanRequest-shaped object the legacy module wrappers need.
    // We rehydrate it from the loaded snapshot rather than re-reading D1.
    const scanRequestSnapshot: ScanRequest = {
      id: scanRequest.id,
      email: scanRequest.email,
      domain: scanRequest.domain,
      linkedin_url: scanRequest.linkedin_url,
      verification_token_hash: '',
      verified_at: null,
      scan_started_at: null,
      scan_completed_at: null,
      scan_status: scanRequest.scan_status,
      thin_footprint_skipped: 0,
      entity_id: init.entityId,
      email_sent_at: null,
      request_ip: null,
      error_message: null,
      scan_status_reason: null,
      workflow_run_id: null,
      created_at: '',
    }

    // ---------------------------------------------------------------
    // Step: Tier 1 (places + outscraper). Cheap identity probes with
    // strict-match guard. Output drives the thin-footprint gate.
    //
    // We run them sequentially inside a single step because outscraper
    // depends on whether places matched (we skip outscraper if places
    // didn't strictly match — its name search would risk the same
    // fuzzy-match problem). Sequential inside one step is fine; the step
    // is the unit of retry.
    // ---------------------------------------------------------------
    const placesResult = await step.do<PlacesStepResult>(
      'tier1-places-and-outscraper',
      { retries: RETRY_TIER1, timeout: TIMEOUT_TIER1 },
      async () => {
        const modulesRan: string[] = []
        let strictMatched = false

        if (env.GOOGLE_PLACES_API_KEY) {
          const entity = await loadEntity()
          const r = await runPlaces(env, entity, scanRequestSnapshot)
          if (r.strictMatched) {
            modulesRan.push('google_places')
            strictMatched = true
          }
        }

        if (env.OUTSCRAPER_API_KEY && strictMatched) {
          // Re-load entity in case places updated phone/website columns.
          const entity = await loadEntity()
          const ok = await runOutscraper(env, entity, scanRequestSnapshot)
          if (ok) modulesRan.push('outscraper')
        }

        return { strictMatched, modulesRan }
      }
    )

    // ---------------------------------------------------------------
    // Step: thin-footprint gate. If tripped, send the polite email and
    // end the workflow — Tier 2 / Tier 3 are skipped entirely.
    //
    // The gate evaluation itself is deterministic given the enrichment
    // rows already written by the previous step, so it doesn't need a
    // dedicated step (it's data-only). We run it inline and route into
    // a step.do for the email send, which is the actual side-effect
    // worth checkpointing.
    // ---------------------------------------------------------------
    {
      const entity = await loadEntity()
      const gate = await evaluateThinFootprintGate(env, entity, scanRequest.domain, {
        placesStrictMatched: env.GOOGLE_PLACES_API_KEY ? placesResult.strictMatched : undefined,
      })

      if (gate.thin) {
        const _gateResult = await step.do<GateStepResult>(
          'thin-footprint-email',
          { retries: RETRY_INFRA, timeout: TIMEOUT_INFRA },
          async () => {
            const e = await loadEntity()
            const sent = await sendThinFootprintEmail(env, scanRequestSnapshot, e, gate.reason)
            return { thin: true, reason: gate.reason, emailSent: sent }
          }
        )

        await step.do(
          'mark-thin-footprint',
          { retries: RETRY_INFRA, timeout: TIMEOUT_INFRA },
          async () => {
            const completedAt = new Date().toISOString()
            await updateScanRequestRun(env.DB, scanRequest.id, {
              scan_status: 'thin_footprint',
              thin_footprint_skipped: true,
              scan_completed_at: completedAt,
              error_message: `thin_footprint:${gate.reason}`,
              scan_status_reason: gate.reason,
              email_sent_at: _gateResult.emailSent ? completedAt : null,
            })
            return { ok: true }
          }
        )
        return
      }
    }

    // ---------------------------------------------------------------
    // Step: Tier 2 — website_analysis + review_synthesis in parallel.
    //
    // Both depend on Anthropic. We run them in parallel inside a single
    // step.do because:
    //
    //   1. They have no inter-dependency
    //   2. Promise.all keeps wall clock ≈ max(t1, t2) instead of t1 + t2
    //   3. The step's retry covers both — if one throws, the entire
    //      step retries; the other's wasted work is acceptable cost
    //
    // If we wanted them retryable independently we'd need two step.do
    // calls; per the issue's design ("the inner Promise.all is fine")
    // the parallel-in-one-step pattern is what we want.
    // ---------------------------------------------------------------
    const tier2 = await step.do<{ modulesRan: string[] }>(
      'tier2-parallel-website-and-reviews',
      { retries: RETRY_TIER2, timeout: TIMEOUT_TIER2 },
      async () => {
        const modulesRan: string[] = []
        const entity = await loadEntity()

        const websiteRan = entity.website && env.ANTHROPIC_API_KEY
        const reviewsRan = !!env.ANTHROPIC_API_KEY

        const [websiteOk, reviewOk] = await Promise.all([
          websiteRan
            ? runWebsiteAnalysis(env, entity, scanRequestSnapshot)
            : Promise.resolve(false),
          reviewsRan
            ? runReviewSynthesis(env, entity, scanRequestSnapshot)
            : Promise.resolve(false),
        ])

        if (websiteOk) modulesRan.push('website_analysis')
        if (reviewOk) modulesRan.push('review_synthesis')
        return { modulesRan }
      }
    )

    // ---------------------------------------------------------------
    // Step: Tier 3 — deep_website. Depends on Tier 2 (its prompt may
    // reference the website_analysis output).
    // ---------------------------------------------------------------
    await step.do<{ ran: boolean }>(
      'tier3-deep-website',
      { retries: RETRY_TIER2, timeout: TIMEOUT_TIER2 },
      async () => {
        const entity = await loadEntity()
        if (!entity.website || !env.ANTHROPIC_API_KEY) return { ran: false }
        const ok = await runDeepWebsite(env, entity, scanRequestSnapshot)
        return { ran: !!ok }
      }
    )

    // ---------------------------------------------------------------
    // Step: intelligence_brief — final synthesis Claude call. Longest
    // single-call latency in the pipeline; this is the call whose
    // wall-clock killed the inline waitUntil orchestrator. With
    // Workflows, a 25s call is just a step that takes 25s.
    // ---------------------------------------------------------------
    const briefStep = await step.do<{ markdown: string | null }>(
      'intelligence-brief',
      { retries: RETRY_TIER3, timeout: TIMEOUT_TIER3 },
      async () => {
        const entity = await loadEntity()
        if (!env.ANTHROPIC_API_KEY) return { markdown: null }
        const md = await runIntelligenceBrief(env, entity, scanRequestSnapshot)
        return { markdown: md ?? null }
      }
    )

    // ---------------------------------------------------------------
    // Step: render + email. The renderer is pure deterministic logic
    // over the enrichment rows we just wrote; the Resend call is the
    // actual side-effect.
    //
    // Outside View Phase 1 PR-B (ADR 0002): writes an outside_views row
    // with the rendered artifact and mints a 24h portal magic-link
    // ALWAYS (shadow-write), regardless of feature flag state. This lets
    // Captain inspect generated artifacts in admin/SQL before flipping
    // OUTSIDE_VIEW_PORTAL_DELIVERY on. With the flag OFF the legacy
    // diagnosticReportEmailHtml email goes out as before; with the flag
    // ON the new outsideViewReadyEmailHtml magic-link email goes out
    // and the legacy path is skipped.
    //
    // Privilege-escalation defense (per /critique 3 Devil's Advocate
    // #4): if the submission email matches an existing role='client'
    // user, do NOT mint a portal link. The legacy email still fires so
    // the prospect (in this case actually a client) gets their report,
    // but we never hand a 24h auth credential to a client's address via
    // a public, unauthenticated form.
    // ---------------------------------------------------------------
    const renderResult = await step.do<RenderStepResult>(
      'render-and-email',
      { retries: RETRY_INFRA, timeout: TIMEOUT_INFRA },
      async () => {
        const entity = await loadEntity()
        const rendered = await renderDiagnosticReport(env.DB, entity, briefStep.markdown)

        const portalDelivery = await prepareOutsideViewDelivery(
          this.env,
          scanRequestSnapshot,
          entity,
          rendered
        )

        const flagOn = isOutsideViewDeliveryOn(this.env.OUTSIDE_VIEW_PORTAL_DELIVERY)
        const useOutsideViewEmail = flagOn && portalDelivery.portalLinkUrl !== null

        const sent = useOutsideViewEmail
          ? await sendOutsideViewReadyEmail(
              env,
              scanRequestSnapshot,
              entity,
              portalDelivery.portalLinkUrl as string,
              rendered.displayName
            )
          : await sendDiagnosticReportEmail(env, scanRequestSnapshot, entity, rendered)

        return { emailSent: sent }
      }
    )

    // ---------------------------------------------------------------
    // Step: mark-completed — flip scan_status='completed' and stamp
    // email_sent_at if we actually sent.
    // ---------------------------------------------------------------
    await step.do('mark-completed', { retries: RETRY_INFRA, timeout: TIMEOUT_INFRA }, async () => {
      const completedAt = new Date().toISOString()
      await updateScanRequestRun(env.DB, scanRequest.id, {
        scan_status: 'completed',
        scan_completed_at: completedAt,
        email_sent_at: renderResult.emailSent ? completedAt : null,
      })
      return { ok: true }
    })

    // Quiet the linter on the unused result we destructured for type
    // narrowing.
    void tier2
  }
}

/**
 * Outside View shadow-write + magic-link mint (ADR 0002 Phase 1 PR-B).
 *
 * Always runs at the render-and-email step terminal, regardless of
 * `OUTSIDE_VIEW_PORTAL_DELIVERY` flag state. The flag controls only the
 * email destination — the artifact is persisted into `outside_views`
 * either way so Captain can inspect shadow-rendered rows in admin/SQL
 * before flipping the flag.
 *
 * Returns `{ portalLinkUrl }` on success. `portalLinkUrl` is non-null
 * iff a prospect path was minted; null when:
 *   1. The submission email matches an existing role='client' user
 *      (privilege-escalation defense — never mint a portal magic-link
 *      to a client via the public scan form).
 *   2. The shadow-write throws (DB error, magic-link insert failure).
 *      We swallow the error rather than failing the workflow so the
 *      legacy email path can still fire.
 *
 * Caller decides whether to use `portalLinkUrl` based on the feature
 * flag. With flag OFF, caller ignores `portalLinkUrl` and sends the
 * legacy email. With flag ON, caller sends the new magic-link email
 * IF `portalLinkUrl` is non-null, otherwise falls back to legacy.
 */
async function prepareOutsideViewDelivery(
  env: ScanWorkflowBindings,
  scanRequest: ScanRequest,
  entity: Entity,
  rendered: RenderedReport
): Promise<{ portalLinkUrl: string | null }> {
  try {
    const existingUser = await env.DB.prepare(
      `SELECT id, role FROM users WHERE org_id = ? AND email = ? LIMIT 1`
    )
      .bind(ORG_ID, scanRequest.email)
      .first<{ id: string; role: string }>()

    if (existingUser?.role === 'client') {
      // Privilege-escalation defense (per /critique 3 Devil's Advocate
      // #4). Never mint a portal magic-link to an existing client via
      // the public, unauthenticated /scan form. The legacy email path
      // still fires; the client gets their report.
      console.log(
        `[outside-view] skipping prospect path: existing client matched on ${scanRequest.email}`
      )
      return { portalLinkUrl: null }
    }

    // Find or create prospect user. Reuse a pre-existing prospect row
    // (e.g. from a prior scan that completed but never converted) rather
    // than create duplicates.
    let prospectUserId: string
    if (existingUser?.role === 'prospect') {
      prospectUserId = existingUser.id
    } else {
      prospectUserId = crypto.randomUUID()
      await env.DB.prepare(
        `INSERT INTO users (id, org_id, email, name, role, entity_id)
         VALUES (?, ?, ?, ?, 'prospect', ?)`
      )
        .bind(
          prospectUserId,
          ORG_ID,
          scanRequest.email,
          // No human name yet; use the email address as a placeholder.
          // The name is internal-only (admin entity view); prospects
          // never see it.
          scanRequest.email,
          entity.id
        )
        .run()
    }

    // Insert the outside_views row (v1 stores the existing RenderedReport
    // shape verbatim; canonical 5-field contract ships in v2).
    await createOutsideView(env.DB, {
      org_id: ORG_ID,
      entity_id: entity.id,
      scan_request_id: scanRequest.id,
      depth: 'd1',
      artifact_version: 1,
      artifact_json: renderedReportToArtifactJsonV1(rendered),
      rendered_at: new Date().toISOString(),
    })

    // Mint a 24h portal magic-link bound to the prospect user.
    const token = await createMagicLink(
      env.DB,
      {
        orgId: ORG_ID,
        userId: prospectUserId,
        email: scanRequest.email,
      },
      PROSPECT_MAGIC_LINK_EXPIRY_MS
    )

    // Build the verify URL on the portal host. PORTAL_BASE_URL is
    // preferred; fall back to APP_BASE_URL if unset (the portal lives
    // on the same Worker under a subdomain rewrite). The verify
    // endpoint at /auth/verify on portal.smd.services consumes the
    // token, sets the session cookie, and lands the prospect at
    // /portal which redirects them onward to /portal/outside-view.
    const portalBase = env.PORTAL_BASE_URL ?? env.APP_BASE_URL ?? 'https://portal.smd.services'
    const portalLinkUrl = `${portalBase.replace(/\/$/, '')}/auth/verify?token=${encodeURIComponent(token)}`

    return { portalLinkUrl }
  } catch (err) {
    // Shadow-write failures must not fail the workflow. The legacy email
    // path is the safety net. Log and return null so the caller falls
    // back to the legacy email regardless of flag state.
    console.error('[outside-view] prepareOutsideViewDelivery failed:', err)
    return { portalLinkUrl: null }
  }
}
