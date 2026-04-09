import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

const handlerSrc = readFileSync(
  resolve('workers/follow-up-processor/src/handlers/booking-enrichment.ts'),
  'utf-8'
)
const indexSrc = readFileSync(resolve('workers/follow-up-processor/src/index.ts'), 'utf-8')
const wranglerToml = readFileSync(resolve('workers/follow-up-processor/wrangler.toml'), 'utf-8')

describe('booking-enrichment handler', () => {
  describe('entity selection query', () => {
    it('filters by source_pipeline = website_booking', () => {
      expect(handlerSrc).toContain("source_pipeline = 'website_booking'")
    })

    it('filters by stage = assessing', () => {
      expect(handlerSrc).toContain("stage = 'assessing'")
    })

    it('excludes entities that already have enrichment context', () => {
      expect(handlerSrc).toContain('NOT EXISTS')
      expect(handlerSrc).toContain("c.type = 'enrichment'")
    })

    it('limits batch size to prevent runaway processing', () => {
      expect(handlerSrc).toContain('BATCH_LIMIT')
      expect(handlerSrc).toContain('LIMIT ?')
    })

    it('processes oldest entities first', () => {
      expect(handlerSrc).toContain('ORDER BY e.created_at ASC')
    })
  })

  describe('enrichment modules', () => {
    it('runs Google Places lookup', () => {
      expect(handlerSrc).toContain('lookupGooglePlaces')
      expect(handlerSrc).toContain("source: 'google_places'")
    })

    it('runs website analysis', () => {
      expect(handlerSrc).toContain('analyzeWebsite')
      expect(handlerSrc).toContain("source: 'website_analysis'")
    })

    it('runs Outscraper lookup', () => {
      expect(handlerSrc).toContain('lookupOutscraper')
      expect(handlerSrc).toContain("source: 'outscraper'")
    })

    it('runs ACC filing lookup', () => {
      expect(handlerSrc).toContain('lookupAcc')
      expect(handlerSrc).toContain("source: 'acc_filing'")
    })

    it('runs ROC license check for trades verticals', () => {
      expect(handlerSrc).toContain('lookupRoc')
      expect(handlerSrc).toContain("source: 'roc_license'")
      expect(handlerSrc).toContain("entity.vertical === 'home_services'")
      expect(handlerSrc).toContain("entity.vertical === 'contractor_trades'")
    })

    it('runs review pattern analysis', () => {
      expect(handlerSrc).toContain('analyzeReviewPatterns')
      expect(handlerSrc).toContain("source: 'review_analysis'")
    })

    it('runs competitor benchmarking', () => {
      expect(handlerSrc).toContain('benchmarkCompetitors')
      expect(handlerSrc).toContain("source: 'competitors'")
    })

    it('runs news/press search', () => {
      expect(handlerSrc).toContain('searchNews')
      expect(handlerSrc).toContain("source: 'news_search'")
    })
  })

  describe('does NOT generate outreach draft', () => {
    it('does not import outreach generation', () => {
      expect(handlerSrc).not.toContain('generateOutreachDraft')
    })

    it('does not write outreach_draft context entries', () => {
      expect(handlerSrc).not.toContain("type: 'outreach_draft'")
    })
  })

  describe('error isolation', () => {
    it('wraps each enrichment module in try/catch', () => {
      const catchCount = (handlerSrc.match(/\} catch \(err\)/g) || []).length
      expect(catchCount).toBeGreaterThanOrEqual(8)
    })

    it('logs errors with entity context', () => {
      expect(handlerSrc).toContain('[booking-enrichment]')
      expect(handlerSrc).toContain('console.error')
    })
  })

  describe('context entries', () => {
    it('writes all enrichment context entries with type enrichment', () => {
      const contextCalls = handlerSrc.match(/type: 'enrichment'/g) || []
      expect(contextCalls.length).toBeGreaterThanOrEqual(8)
    })

    it('uses ORG_ID constant for org scoping', () => {
      expect(handlerSrc).toContain('ORG_ID')
      expect(handlerSrc).toContain("from '../../../../src/lib/constants.js'")
    })
  })
})

describe('Worker index wiring', () => {
  it('imports the booking-enrichment handler', () => {
    expect(indexSrc).toContain("from './handlers/booking-enrichment.js'")
  })

  it('runs booking-enrichment before health-check', () => {
    const enrichmentPos = indexSrc.indexOf('booking-enrichment')
    const healthCheckPos = indexSrc.indexOf('health-check', enrichmentPos)
    expect(enrichmentPos).toBeLessThan(healthCheckPos)
  })

  it('passes full env to booking-enrichment (not just DB)', () => {
    expect(indexSrc).toContain('runBookingEnrichment(env)')
  })

  it('declares enrichment API key bindings in Env interface', () => {
    expect(indexSrc).toContain('GOOGLE_PLACES_API_KEY')
    expect(indexSrc).toContain('ANTHROPIC_API_KEY')
    expect(indexSrc).toContain('OUTSCRAPER_API_KEY')
    expect(indexSrc).toContain('SERPAPI_API_KEY')
  })
})

describe('Worker wrangler.toml', () => {
  it('documents enrichment secrets', () => {
    expect(wranglerToml).toContain('GOOGLE_PLACES_API_KEY')
    expect(wranglerToml).toContain('ANTHROPIC_API_KEY')
    expect(wranglerToml).toContain('OUTSCRAPER_API_KEY')
    expect(wranglerToml).toContain('SERPAPI_API_KEY')
  })
})
