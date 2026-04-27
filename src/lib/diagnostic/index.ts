/**
 * Engine 1 diagnostic orchestrator (#598).
 *
 * Runs the *pruned* 6-module pipeline behind smd.services/scan, with a
 * P0 thin-footprint pre-flight gate. Per the scoping doc
 * (`docs/strategy/diagnostic-scoping-2026-04-27.md`), the public scan path
 * uses ONLY:
 *
 *   places + outscraper + website_analysis + review_synthesis +
 *   deep_website + intelligence_brief
 *
 * It deliberately DROPS competitors, news_search, and linkedin from the
 * scan path. Those three remain in the internal 12-module pipeline that
 * runs when a scan converts to a booked call, but on the public scan path
 * they triple latency, add fabrication risk, and produce low-signal output.
 *
 * Lifecycle:
 *
 *   1. The verify endpoint calls `runDiagnosticScan` via `ctx.waitUntil()`
 *      so the prospect's verification click returns instantly.
 *   2. We find-or-create an entity for the scanned domain.
 *   3. We run google_places + outscraper as the pre-flight (cheap; ~$0.02).
 *   4. We evaluate the thin-footprint gate. If it trips, we mark the
 *      scan_request as `thin_footprint` and email a "let's talk live"
 *      response — never a fabricated report.
 *   5. Otherwise we run website_analysis + review_synthesis + deep_website
 *      + intelligence_brief.
 *   6. We email the rendered 1-page report (`render.ts` enforces
 *      anti-fabrication rules per section).
 *
 * The orchestrator is best-effort throughout — a single module failure
 * does not abort the run. A complete pipeline failure is recorded as
 * `scan_status='failed'` and surfaces in the audit log.
 */

import { ORG_ID } from '../constants'
import { findOrCreateEntity, getEntity, updateEntity, type Entity } from '../db/entities'
import { appendContext, listContext, assembleEntityContext } from '../db/context'
import { lookupGooglePlaces } from '../enrichment/google-places'
import { lookupOutscraper } from '../enrichment/outscraper'
import { analyzeWebsite } from '../enrichment/website-analyzer'
import { synthesizeReviews } from '../enrichment/review-synthesis'
import { deepWebsiteAnalysis } from '../enrichment/deep-website'
import { generateDossier } from '../enrichment/dossier'
import { getScanRequest, updateScanRequestRun, type ScanRequest } from '../db/scan-requests'
import { sendOutreachEmail } from '../email/resend'
import { diagnosticReportEmailHtml, thinFootprintEmailHtml } from '../email/diagnostic-email'
import { renderDiagnosticReport, type RenderedReport } from './render'

export interface DiagnosticEnv {
  DB: D1Database
  ANTHROPIC_API_KEY?: string
  GOOGLE_PLACES_API_KEY?: string
  OUTSCRAPER_API_KEY?: string
  RESEND_API_KEY?: string
  APP_BASE_URL?: string
}

export interface DiagnosticResult {
  scan_request_id: string
  status: 'completed' | 'thin_footprint' | 'failed'
  entity_id: string | null
  thin_footprint_skipped: boolean
  modules_ran: string[]
  email_sent: boolean
  error?: string
}

const SOURCE_PIPELINE = 'inbound_scan'

/**
 * Entry point for the verify endpoint's `ctx.waitUntil(...)` call. Loads
 * the scan_request, runs the pipeline, persists results, sends email.
 *
 * Never throws — all failures are recorded in scan_status and returned
 * in DiagnosticResult.error. Throwing here would silently lose the row
 * because waitUntil callers don't see exceptions.
 */
export async function runDiagnosticScan(
  env: DiagnosticEnv,
  scanRequestId: string
): Promise<DiagnosticResult> {
  const result: DiagnosticResult = {
    scan_request_id: scanRequestId,
    status: 'failed',
    entity_id: null,
    thin_footprint_skipped: false,
    modules_ran: [],
    email_sent: false,
  }

  const scanRequest = await getScanRequest(env.DB, scanRequestId)
  if (!scanRequest) {
    result.error = 'scan_request_not_found'
    return result
  }
  if (scanRequest.scan_status === 'completed') {
    // Idempotency — verify endpoint may fire twice on a double-click.
    result.status = 'completed'
    result.entity_id = scanRequest.entity_id
    return result
  }

  const startedAt = new Date().toISOString()
  await updateScanRequestRun(env.DB, scanRequestId, { scan_started_at: startedAt })

  try {
    return await runScanInner(env, scanRequest, result)
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    console.error('[diagnostic] runScanInner threw:', err)
    await updateScanRequestRun(env.DB, scanRequestId, {
      scan_status: 'failed',
      scan_completed_at: new Date().toISOString(),
      error_message: message.slice(0, 500),
    })
    result.error = message
    return result
  }
}

async function runScanInner(
  env: DiagnosticEnv,
  scanRequest: ScanRequest,
  result: DiagnosticResult
): Promise<DiagnosticResult> {
  // ---------------------------------------------------------------
  // Step 1: find-or-create entity for the scanned domain.
  //
  // The entity name we have at this point is the bare domain. We find-or-
  // create on the slug derived from the name + area (Phoenix, AZ as the
  // default area for this venture). Outscraper later updates the entity's
  // canonical name / phone / website.
  // ---------------------------------------------------------------
  const placeholderName = humanizeDomain(scanRequest.domain)
  const foc = await findOrCreateEntity(env.DB, ORG_ID, {
    name: placeholderName,
    website: `https://${scanRequest.domain}`,
    area: 'Phoenix, AZ',
    source_pipeline: SOURCE_PIPELINE,
  })
  let entity: Entity = foc.entity
  result.entity_id = entity.id
  await updateScanRequestRun(env.DB, scanRequest.id, { entity_id: entity.id })

  // Always log the inbound submission as a 'signal' so downstream
  // attribution + funnel telemetry treats this as a first-class lead-gen
  // signal.
  await appendContext(env.DB, ORG_ID, {
    entity_id: entity.id,
    type: 'signal',
    source: SOURCE_PIPELINE,
    content: `Inbound diagnostic scan submitted from ${scanRequest.email}. Domain: ${scanRequest.domain}.${scanRequest.linkedin_url ? ` LinkedIn: ${scanRequest.linkedin_url}.` : ''}`,
    metadata: {
      scan_request_id: scanRequest.id,
      requester_email: scanRequest.email,
      submitted_domain: scanRequest.domain,
      linkedin_url: scanRequest.linkedin_url,
    },
  })

  // ---------------------------------------------------------------
  // Step 2: pre-flight (places + outscraper). These are the cheap
  // identity probes — ~$0.02 total. Their output drives the
  // thin-footprint gate.
  // ---------------------------------------------------------------
  if (env.GOOGLE_PLACES_API_KEY) {
    const places = await runPlaces(env, entity, scanRequest)
    if (places) {
      result.modules_ran.push('google_places')
      // Refresh entity in case places updated phone/website.
      const refreshed = await getEntity(env.DB, ORG_ID, entity.id)
      if (refreshed) entity = refreshed
    }
  }

  if (env.OUTSCRAPER_API_KEY) {
    const oc = await runOutscraper(env, entity, scanRequest)
    if (oc) {
      result.modules_ran.push('outscraper')
      const refreshed = await getEntity(env.DB, ORG_ID, entity.id)
      if (refreshed) entity = refreshed
    }
  }

  // ---------------------------------------------------------------
  // Step 3: thin-footprint gate. Refuse the rest of the pipeline if
  // the prospect's footprint is too thin to support a non-fabricated
  // report. Per the scoping doc + CLAUDE.md no-fabrication rule, the
  // refusal sends a short "let's talk live" email and routes to /book.
  // ---------------------------------------------------------------
  const gate = await evaluateThinFootprintGate(env, entity, scanRequest.domain)
  if (gate.thin) {
    result.thin_footprint_skipped = true
    await updateScanRequestRun(env.DB, scanRequest.id, {
      scan_status: 'thin_footprint',
      thin_footprint_skipped: true,
      scan_completed_at: new Date().toISOString(),
      error_message: `thin_footprint:${gate.reason}`,
    })
    const sent = await sendThinFootprintEmail(env, scanRequest, entity, gate.reason)
    result.email_sent = sent
    if (sent) {
      await updateScanRequestRun(env.DB, scanRequest.id, {
        email_sent_at: new Date().toISOString(),
      })
    }
    result.status = 'thin_footprint'
    return result
  }

  // ---------------------------------------------------------------
  // Step 4: pruned pipeline — website_analysis + review_synthesis +
  // deep_website + intelligence_brief.
  //
  // Each module is best-effort. A single module failing degrades a
  // section of the report (anti-fabrication rendering omits sections
  // with insufficient data) but does not abort the scan.
  // ---------------------------------------------------------------
  if (entity.website && env.ANTHROPIC_API_KEY) {
    const ok = await runWebsiteAnalysis(env, entity, scanRequest)
    if (ok) result.modules_ran.push('website_analysis')
  }

  if (env.ANTHROPIC_API_KEY) {
    const ok = await runReviewSynthesis(env, entity, scanRequest)
    if (ok) result.modules_ran.push('review_synthesis')
  }

  if (entity.website && env.ANTHROPIC_API_KEY) {
    const ok = await runDeepWebsite(env, entity, scanRequest)
    if (ok) result.modules_ran.push('deep_website')
  }

  let briefMarkdown: string | null = null
  if (env.ANTHROPIC_API_KEY) {
    briefMarkdown = await runIntelligenceBrief(env, entity, scanRequest)
    if (briefMarkdown) result.modules_ran.push('intelligence_brief')
  }

  // ---------------------------------------------------------------
  // Step 5: render + email the report.
  //
  // `renderDiagnosticReport` enforces anti-fabrication rules per
  // section. If a required section has insufficient signals it is
  // omitted or labelled "Insufficient data" — never invented.
  // ---------------------------------------------------------------
  const rendered = await renderDiagnosticReport(env.DB, entity, briefMarkdown)
  const sent = await sendDiagnosticReportEmail(env, scanRequest, entity, rendered)
  result.email_sent = sent

  await updateScanRequestRun(env.DB, scanRequest.id, {
    scan_status: 'completed',
    scan_completed_at: new Date().toISOString(),
    email_sent_at: sent ? new Date().toISOString() : null,
  })
  result.status = 'completed'
  return result
}

// ---------------------------------------------------------------------------
// Thin-footprint gate
// ---------------------------------------------------------------------------

export interface ThinFootprintEvaluation {
  thin: boolean
  /** Machine-readable reason. Stored in error_message; never client-rendered. */
  reason: string
  reviewCount: number | null
  hasUsableWebsite: boolean
}

/**
 * Decide whether the entity has enough public footprint to support a
 * non-fabricated report. Per the scoping doc:
 *
 *   - No website AND no Google Places match -> thin
 *   - No website AND <5 reviews             -> thin
 *   - Otherwise                              -> proceed
 *
 * Reviews are read from the most recent google_places enrichment row's
 * metadata.reviewCount, falling back to outscraper's review_count. Both
 * modules persist to context, so we read context (not entity columns).
 */
export async function evaluateThinFootprintGate(
  env: DiagnosticEnv,
  entity: Entity,
  submittedDomain: string
): Promise<ThinFootprintEvaluation> {
  const enrichment = await listContext(env.DB, entity.id, { type: 'enrichment' })
  let reviewCount: number | null = null
  let placesMatched = false

  for (const row of enrichment) {
    if (row.source === 'google_places' && row.metadata) {
      try {
        const m = JSON.parse(row.metadata) as Record<string, unknown>
        if (typeof m.reviewCount === 'number') reviewCount = m.reviewCount
        placesMatched = true
      } catch {
        /* ignore */
      }
    } else if (row.source === 'outscraper' && row.metadata) {
      try {
        const m = JSON.parse(row.metadata) as Record<string, unknown>
        if (typeof m.review_count === 'number' && reviewCount == null) {
          reviewCount = m.review_count
        }
      } catch {
        /* ignore */
      }
    }
  }

  const hasUsableWebsite = !!entity.website && !!entity.website.trim()

  if (!hasUsableWebsite && !placesMatched) {
    return {
      thin: true,
      reason: 'no_website_no_places',
      reviewCount,
      hasUsableWebsite,
    }
  }

  if (!hasUsableWebsite && (reviewCount == null || reviewCount < 5)) {
    return {
      thin: true,
      reason: 'no_website_low_reviews',
      reviewCount,
      hasUsableWebsite,
    }
  }

  // We deliberately don't gate on reviewCount alone if the website exists.
  // A new business with a real site and 2 reviews still produces a useful
  // report (digital-maturity + tech-stack signals). We DO note submittedDomain
  // here as a structural reminder that the gate also fires implicitly when
  // the website-analysis fetch later returns <500 chars; that's enforced in
  // the renderer, not here.
  void submittedDomain

  return { thin: false, reason: 'ok', reviewCount, hasUsableWebsite }
}

// ---------------------------------------------------------------------------
// Module wrappers — minimal, no instrumentation. The full enrichment
// pipeline writes to enrichment_runs; the public scan path uses simpler
// "did it succeed" telemetry through scan_requests.scan_status. Each module
// writes its own context row so the report renderer reads from one source
// of truth.
// ---------------------------------------------------------------------------

async function runPlaces(
  env: DiagnosticEnv,
  entity: Entity,
  scanRequest: ScanRequest
): Promise<boolean> {
  if (!env.GOOGLE_PLACES_API_KEY) return false
  try {
    const places = await lookupGooglePlaces(entity.name, entity.area, env.GOOGLE_PLACES_API_KEY)
    if (!places) return false
    await updateEntity(env.DB, ORG_ID, entity.id, {
      phone: places.phone ?? entity.phone ?? undefined,
      website: places.website ?? entity.website ?? undefined,
    })
    await appendContext(env.DB, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'google_places',
      content: `Google Places: ${places.phone ? `Phone: ${places.phone}.` : 'No phone found.'} ${places.website ? `Website: ${places.website}.` : 'No website found.'} Rating: ${places.rating ?? 'N/A'} (${places.reviewCount ?? 0} reviews).`,
      metadata: places as unknown as Record<string, unknown>,
      source_ref: scanRequest.id,
    })
    return true
  } catch (err) {
    console.error('[diagnostic] google_places failed:', err)
    return false
  }
}

async function runOutscraper(
  env: DiagnosticEnv,
  entity: Entity,
  scanRequest: ScanRequest
): Promise<boolean> {
  if (!env.OUTSCRAPER_API_KEY) return false
  try {
    const osc = await lookupOutscraper(entity.name, entity.area, env.OUTSCRAPER_API_KEY)
    if (!osc) return false
    await updateEntity(env.DB, ORG_ID, entity.id, {
      phone: osc.phone ?? entity.phone ?? undefined,
      website: osc.website ?? entity.website ?? undefined,
    })
    await appendContext(env.DB, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'outscraper',
      content: `Outscraper profile: rating ${osc.rating ?? 'n/a'} (${osc.review_count ?? 0} reviews). ${osc.verified ? 'Verified.' : 'Unverified.'}`,
      metadata: osc as unknown as Record<string, unknown>,
      source_ref: scanRequest.id,
    })
    return true
  } catch (err) {
    console.error('[diagnostic] outscraper failed:', err)
    return false
  }
}

async function runWebsiteAnalysis(
  env: DiagnosticEnv,
  entity: Entity,
  scanRequest: ScanRequest
): Promise<boolean> {
  if (!entity.website || !env.ANTHROPIC_API_KEY) return false
  try {
    const analysis = await analyzeWebsite(entity.website, env.ANTHROPIC_API_KEY)
    if (!analysis) return false
    await appendContext(env.DB, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'website_analysis',
      content: `Website analysis: ${analysis.pages_analyzed.length} pages. Quality: ${analysis.quality}.`,
      metadata: analysis as unknown as Record<string, unknown>,
      source_ref: scanRequest.id,
    })
    return true
  } catch (err) {
    console.error('[diagnostic] website_analysis failed:', err)
    return false
  }
}

async function runReviewSynthesis(
  env: DiagnosticEnv,
  entity: Entity,
  scanRequest: ScanRequest
): Promise<boolean> {
  if (!env.ANTHROPIC_API_KEY) return false
  try {
    const allContext = await assembleEntityContext(env.DB, entity.id, {
      maxBytes: 20_000,
      typeFilter: ['signal', 'enrichment'],
    })
    if (!allContext) return false
    const synthesis = await synthesizeReviews(allContext, env.ANTHROPIC_API_KEY)
    if (!synthesis) return false
    await appendContext(env.DB, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'review_synthesis',
      content: `Review synthesis: ${synthesis.customer_sentiment} Trend: ${synthesis.sentiment_trend}.`,
      metadata: synthesis as unknown as Record<string, unknown>,
      source_ref: scanRequest.id,
    })
    return true
  } catch (err) {
    console.error('[diagnostic] review_synthesis failed:', err)
    return false
  }
}

async function runDeepWebsite(
  env: DiagnosticEnv,
  entity: Entity,
  scanRequest: ScanRequest
): Promise<boolean> {
  if (!entity.website || !env.ANTHROPIC_API_KEY) return false
  try {
    const analysis = await deepWebsiteAnalysis(entity.website, env.ANTHROPIC_API_KEY)
    if (!analysis) return false
    await appendContext(env.DB, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'deep_website',
      content: `Deep website analysis. Digital maturity score: ${analysis.digital_maturity?.score ?? 'n/a'}/10.`,
      metadata: analysis as unknown as Record<string, unknown>,
      source_ref: scanRequest.id,
    })
    return true
  } catch (err) {
    console.error('[diagnostic] deep_website failed:', err)
    return false
  }
}

async function runIntelligenceBrief(
  env: DiagnosticEnv,
  entity: Entity,
  scanRequest: ScanRequest
): Promise<string | null> {
  if (!env.ANTHROPIC_API_KEY) return null
  try {
    const fullContext = await assembleEntityContext(env.DB, entity.id, { maxBytes: 32_000 })
    if (!fullContext) return null
    const brief = await generateDossier(fullContext, entity.name, env.ANTHROPIC_API_KEY)
    if (!brief) return null
    await appendContext(env.DB, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'intelligence_brief',
      content: brief,
      metadata: { trigger: 'inbound_scan' },
      source_ref: scanRequest.id,
    })
    return brief
  } catch (err) {
    console.error('[diagnostic] intelligence_brief failed:', err)
    return null
  }
}

// ---------------------------------------------------------------------------
// Email rendering
// ---------------------------------------------------------------------------

async function sendDiagnosticReportEmail(
  env: DiagnosticEnv,
  scanRequest: ScanRequest,
  entity: Entity,
  rendered: RenderedReport
): Promise<boolean> {
  const html = diagnosticReportEmailHtml({
    businessName: entity.name,
    rendered,
    bookingUrl: buildBookingUrl(env),
  })
  const r = await sendOutreachEmail(
    env.RESEND_API_KEY,
    {
      to: scanRequest.email,
      subject: `Your operational read — ${entity.name}`,
      html,
    },
    { db: env.DB, orgId: ORG_ID, entityId: entity.id }
  )
  if (!r.success) {
    console.error('[diagnostic] failed to send report email:', r.error)
    return false
  }
  return true
}

async function sendThinFootprintEmail(
  env: DiagnosticEnv,
  scanRequest: ScanRequest,
  entity: Entity,
  reason: string
): Promise<boolean> {
  const html = thinFootprintEmailHtml({
    businessName: entity.name,
    submittedDomain: scanRequest.domain,
    reason,
    bookingUrl: buildBookingUrl(env),
  })
  const r = await sendOutreachEmail(
    env.RESEND_API_KEY,
    {
      to: scanRequest.email,
      subject: `About your scan — ${entity.name}`,
      html,
    },
    { db: env.DB, orgId: ORG_ID, entityId: entity.id }
  )
  if (!r.success) {
    console.error('[diagnostic] failed to send thin-footprint email:', r.error)
    return false
  }
  return true
}

function buildBookingUrl(env: DiagnosticEnv): string {
  const base = env.APP_BASE_URL ?? 'https://smd.services'
  return new URL('/book', base).toString()
}

/**
 * Domain -> human-friendly business name placeholder. We strip the TLD and
 * title-case the remaining label. Outscraper / Google Places typically
 * overwrite this with the canonical business name shortly after, but the
 * placeholder makes the entity row legible if the canonical lookup fails.
 *
 * Example: `azperfectcomfort.com` -> `Azperfectcomfort`
 *          `home-services-llc.com` -> `Home Services Llc`
 */
function humanizeDomain(domain: string): string {
  const label = domain.split('.')[0] ?? domain
  return label
    .split(/[-_]/)
    .filter(Boolean)
    .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
    .join(' ')
}
