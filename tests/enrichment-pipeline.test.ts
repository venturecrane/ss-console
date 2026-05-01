import { describe, it, expect } from 'vitest'
import { readFileSync, existsSync } from 'fs'
import { resolve } from 'path'

/**
 * Lock-in tests for issue #631 — migrate entity enrichment from the
 * legacy `ctx.waitUntil(enrichEntity(...))` pattern to a Cloudflare
 * Workflow. The earlier #471 lock-in tests asserted that all callers
 * went through `enrichEntity`; this file replaces them with the new
 * contract: all callers go through `dispatchEnrichmentWorkflow`, the
 * Workflow class is the orchestrator, and the legacy `enrichEntity`
 * orchestrator no longer exists.
 *
 * If any of these regress, we silently slide back into the bug class
 * #631 was filed to escape — `ctx.waitUntil` killing 86% of enrichments.
 */
describe('enrichment workflow (issue #631)', () => {
  const enrichmentIndex = () => readFileSync(resolve('src/lib/enrichment/index.ts'), 'utf-8')
  const workflowSrc = () => readFileSync(resolve('src/lib/enrichment/workflow.ts'), 'utf-8')
  const dispatchSrc = () => readFileSync(resolve('src/lib/enrichment/dispatch.ts'), 'utf-8')

  describe('orchestrator location', () => {
    it('src/lib/enrichment/workflow.ts exists', () => {
      expect(existsSync(resolve('src/lib/enrichment/workflow.ts'))).toBe(true)
    })

    it('src/lib/enrichment/dispatch.ts exists', () => {
      expect(existsSync(resolve('src/lib/enrichment/dispatch.ts'))).toBe(true)
    })

    it('exports EnrichmentWorkflow class', () => {
      expect(workflowSrc()).toContain('export class EnrichmentWorkflow')
    })

    it('exports dispatchEnrichmentWorkflow function', () => {
      expect(dispatchSrc()).toContain('export async function dispatchEnrichmentWorkflow')
    })

    it('legacy enrichEntity orchestrator is deleted', () => {
      // The unified entry point that #471 introduced — and that #631
      // replaced with the Workflow — must not return as a parallel path.
      expect(enrichmentIndex()).not.toContain('export async function enrichEntity')
      expect(enrichmentIndex()).not.toContain('async function runReviewsAndNews')
    })

    it('module wrappers remain exported for the Workflow to import', () => {
      // The 12 try* wrappers are the steps; the Workflow imports each.
      const code = enrichmentIndex()
      for (const fn of [
        'export async function tryPlaces',
        'export async function tryWebsite',
        'export async function tryOutscraper',
        'export async function tryAcc',
        'export async function tryRoc',
        'export async function tryReviewAnalysis',
        'export async function tryCompetitors',
        'export async function tryNews',
        'export async function tryDeepWebsite',
        'export async function tryReviewSynthesis',
        'export async function tryLinkedIn',
        'export async function tryIntelligenceBrief',
        'export async function tryOutreach',
      ]) {
        expect(code).toContain(fn)
      }
    })
  })

  describe('Workflow covers all 12 modules + outreach', () => {
    it('Workflow imports the 12 module wrappers + tryOutreach', () => {
      const code = workflowSrc()
      for (const wrapper of [
        'tryPlaces',
        'tryWebsite',
        'tryOutscraper',
        'tryAcc',
        'tryRoc',
        'tryReviewAnalysis',
        'tryCompetitors',
        'tryNews',
        'tryDeepWebsite',
        'tryReviewSynthesis',
        'tryLinkedIn',
        'tryIntelligenceBrief',
        'tryOutreach',
      ]) {
        expect(code).toContain(wrapper)
      }
    })

    it('Workflow declares both modes and an init idempotency check', () => {
      const code = workflowSrc()
      expect(code).toContain("'full'")
      expect(code).toContain("'reviews-and-news'")
      expect(code).toMatch(/intelligence_brief/)
    })
  })

  describe('admin endpoints dispatch the Workflow (no inline orchestration)', () => {
    const promoteSrc = () =>
      readFileSync(resolve('src/pages/api/admin/entities/[id]/promote.ts'), 'utf-8')
    const dossierSrc = () =>
      readFileSync(resolve('src/pages/api/admin/entities/[id]/dossier.ts'), 'utf-8')
    const runFullSrc = () =>
      readFileSync(resolve('src/pages/api/admin/entities/[id]/enrichment/run-full.ts'), 'utf-8')

    it('promote.ts dispatches via dispatchEnrichmentWorkflow', () => {
      const code = promoteSrc()
      expect(code).toContain('dispatchEnrichmentWorkflow(')
      expect(code).not.toContain('enrichEntity(')
      // No inline module orchestration.
      expect(code).not.toContain('analyzeWebsite')
      expect(code).not.toContain('lookupOutscraper')
    })

    it('dossier.ts dispatches via dispatchEnrichmentWorkflow in reviews-and-news mode', () => {
      const code = dossierSrc()
      expect(code).toContain('dispatchEnrichmentWorkflow(')
      expect(code).toContain("mode: 'reviews-and-news'")
      expect(code).not.toContain('enrichEntity(')
    })

    it('run-full.ts dispatches via dispatchEnrichmentWorkflow', () => {
      const code = runFullSrc()
      expect(code).toContain('dispatchEnrichmentWorkflow(')
      expect(code).not.toContain('enrichEntity(')
    })
  })

  describe('admin UI — Generate Dossier button replaced with Re-enrich', () => {
    const astroSrc = () => readFileSync(resolve('src/pages/admin/entities/[id].astro'), 'utf-8')

    it('removes the "Generate Dossier" label', () => {
      expect(astroSrc()).not.toContain('Generate Dossier')
    })

    it('exposes a Re-enrich (reviews + news) button', () => {
      expect(astroSrc()).toContain('Re-enrich')
    })

    it('renders a queued-state banner for fire-and-forget Re-enrich', () => {
      // Issue #631 — Re-enrich is now async; the success redirect uses
      // ?dossier=queued and the page renders a "queued" banner.
      expect(astroSrc()).toContain("dossierGenerated === 'queued'")
    })
  })

  describe('lead-gen workers dispatch the Workflow at ingest', () => {
    const workers = ['new-business', 'job-monitor', 'review-mining']

    for (const worker of workers) {
      it(`${worker} worker imports dispatchEnrichmentWorkflow`, () => {
        const code = readFileSync(resolve(`workers/${worker}/src/index.ts`), 'utf-8')
        expect(code).toContain("from '../../../src/lib/enrichment/dispatch.js'")
        expect(code).toContain('dispatchEnrichmentWorkflow(')
        expect(code).not.toContain('enrichEntity(')
      })

      it(`${worker} worker uses ctx.waitUntil so the cron loop is not blocked`, () => {
        const code = readFileSync(resolve(`workers/${worker}/src/index.ts`), 'utf-8')
        expect(code).toContain('ctx.waitUntil(')
      })

      it(`${worker} worker declares the ENRICHMENT_WORKFLOW_SERVICE binding`, () => {
        const wranglerToml = readFileSync(resolve(`workers/${worker}/wrangler.toml`), 'utf-8')
        expect(wranglerToml).toContain('binding = "ENRICHMENT_WORKFLOW_SERVICE"')
        expect(wranglerToml).toContain('service = "ss-enrichment-workflow"')
      })
    }
  })

  describe('ss-web wires the service binding for the dispatchers', () => {
    it('root wrangler.toml declares ENRICHMENT_WORKFLOW_SERVICE', () => {
      const code = readFileSync(resolve('wrangler.toml'), 'utf-8')
      expect(code).toContain('binding = "ENRICHMENT_WORKFLOW_SERVICE"')
      expect(code).toContain('service = "ss-enrichment-workflow"')
    })

    it('src/env.d.ts types the ENRICHMENT_WORKFLOW_SERVICE binding', () => {
      const code = readFileSync(resolve('src/env.d.ts'), 'utf-8')
      expect(code).toContain('ENRICHMENT_WORKFLOW_SERVICE?:')
    })

    it('workers/enrichment-workflow exists with [[workflows]] entry', () => {
      expect(existsSync(resolve('workers/enrichment-workflow/wrangler.toml'))).toBe(true)
      const code = readFileSync(resolve('workers/enrichment-workflow/wrangler.toml'), 'utf-8')
      expect(code).toContain('class_name = "EnrichmentWorkflow"')
    })
  })
})
