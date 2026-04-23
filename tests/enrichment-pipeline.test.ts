import { describe, it, expect } from 'vitest'
import { readFileSync, existsSync } from 'fs'
import { resolve } from 'path'

/**
 * Lock-in tests for issue #471 — merge Promote + Dossier into a single
 * at-ingest enrichment pipeline.
 *
 * These tests assert the structural contract: one unified entry point,
 * idempotency on re-runs, both admin endpoints delegating to it, and every
 * per-entity lead-gen worker wiring enrichment at ingest time. A regression
 * on any of these silently returns us to the 2-button admin-gated world.
 */
describe('enrichment pipeline (issue #471)', () => {
  const enrichmentSrc = () => readFileSync(resolve('src/lib/enrichment/index.ts'), 'utf-8')
  const promoteSrc = () =>
    readFileSync(resolve('src/pages/api/admin/entities/[id]/promote.ts'), 'utf-8')
  const dossierSrc = () =>
    readFileSync(resolve('src/pages/api/admin/entities/[id]/dossier.ts'), 'utf-8')

  describe('unified entry point', () => {
    it('src/lib/enrichment/index.ts exists', () => {
      expect(existsSync(resolve('src/lib/enrichment/index.ts'))).toBe(true)
    })

    it('exports enrichEntity function', () => {
      expect(enrichmentSrc()).toContain('export async function enrichEntity')
    })

    it('supports both "full" and "reviews-and-news" modes', () => {
      const code = enrichmentSrc()
      expect(code).toContain("'full'")
      expect(code).toContain("'reviews-and-news'")
    })

    it('idempotency: full-mode skips when an intelligence_brief already exists', () => {
      // Full re-enrichment must not re-bill Claude for the same signal.
      const code = enrichmentSrc()
      expect(code).toMatch(/hasBrief/)
      expect(code).toMatch(/alreadyEnriched/)
    })

    it('covers the 12 modules from the old promote + dossier pair', () => {
      // If a module is removed, it is either a Captain-approved feature
      // deprecation or a regression. This list encodes the baseline contract.
      const code = enrichmentSrc()
      for (const source of [
        'google_places',
        'website_analysis',
        'outscraper',
        'acc_filing',
        'roc_license',
        'review_analysis',
        'competitors',
        'news_search',
        'deep_website',
        'review_synthesis',
        'linkedin',
        'intelligence_brief',
      ]) {
        expect(code).toContain(source)
      }
    })
  })

  describe('admin endpoints delegate to enrichEntity', () => {
    it('promote.ts calls enrichEntity instead of running modules inline', () => {
      const code = promoteSrc()
      expect(code).toContain("from '../../../../../lib/enrichment'")
      expect(code).toContain('enrichEntity(')
      // The old inline imports must be gone — otherwise promote.ts is still
      // running a parallel pipeline and drift will reappear.
      expect(code).not.toContain('analyzeWebsite')
      expect(code).not.toContain('lookupOutscraper')
      expect(code).not.toContain('searchNews')
    })

    it('dossier.ts calls enrichEntity in reviews-and-news mode', () => {
      const code = dossierSrc()
      expect(code).toContain("from '../../../../../lib/enrichment'")
      expect(code).toContain("mode: 'reviews-and-news'")
      expect(code).not.toContain('deepWebsiteAnalysis')
      expect(code).not.toContain('synthesizeReviews(')
      expect(code).not.toContain('generateDossier(')
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
  })

  describe('lead-gen workers run enrichEntity at ingest', () => {
    const workers = ['new-business', 'job-monitor', 'review-mining']

    for (const worker of workers) {
      it(`${worker} worker imports enrichEntity`, () => {
        const code = readFileSync(resolve(`workers/${worker}/src/index.ts`), 'utf-8')
        expect(code).toContain("from '../../../src/lib/enrichment/index.js'")
        expect(code).toContain('enrichEntity(')
      })

      it(`${worker} worker uses ctx.waitUntil to avoid blocking ingest`, () => {
        const code = readFileSync(resolve(`workers/${worker}/src/index.ts`), 'utf-8')
        expect(code).toContain('ctx.waitUntil(')
      })
    }
  })
})
