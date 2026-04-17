import { describe, expect, it } from 'vitest'
import {
  validateJobMonitor,
  validateNewBusiness,
  validateReviewMining,
  validateSocialListening,
} from '../src/lib/generators/validate'
import { DEFAULTS } from '../src/lib/generators/types'

describe('generator config validators', () => {
  describe('validateNewBusiness', () => {
    it('returns defaults when input is empty', () => {
      const { value, errors } = validateNewBusiness({})
      expect(errors).toEqual([])
      expect(value.target_verticals.length).toBeGreaterThan(0)
      expect(value.soda_sources.length).toBe(5)
      expect(value.revenue_range.min_usd).toBe(DEFAULTS.new_business.revenue_range.min_usd)
    })

    it('accepts a valid full config', () => {
      const input = {
        target_verticals: ['home_services', 'healthcare'],
        revenue_range: { min_usd: 1_000_000, max_usd: 5_000_000 },
        geos: ['Phoenix metro, AZ'],
        soda_sources: [
          { city: 'phoenix', enabled: true },
          { city: 'mesa', enabled: false },
        ],
      }
      const { value, errors } = validateNewBusiness(input)
      expect(errors).toEqual([])
      expect(value.target_verticals).toEqual(['home_services', 'healthcare'])
      expect(value.soda_sources).toEqual([
        { city: 'phoenix', enabled: true },
        { city: 'mesa', enabled: false },
      ])
    })

    it('accepts free-text verticals, rejects non-string entries', () => {
      const { value, errors } = validateNewBusiness({
        target_verticals: ['home services', 'custom vertical', 42, '   '],
      })
      expect(errors).toContain('target_verticals contains invalid entry: 42')
      expect(errors).toContain('target_verticals contains invalid entry: "   "')
      expect(value.target_verticals).toEqual(['home services', 'custom vertical'])
    })

    it('rejects invalid soda cities', () => {
      const { value, errors } = validateNewBusiness({
        soda_sources: [
          { city: 'phoenix', enabled: true },
          { city: 'albuquerque', enabled: true },
        ],
      })
      expect(errors).toContain('soda_sources: invalid city "albuquerque"')
      expect(value.soda_sources).toEqual([{ city: 'phoenix', enabled: true }])
    })

    it('flags min > max on revenue range', () => {
      const { errors } = validateNewBusiness({
        revenue_range: { min_usd: 10_000_000, max_usd: 1_000_000 },
      })
      expect(errors).toContain('revenue_range: min_usd must be <= max_usd')
    })

    it('fills missing fields with defaults and never errors on absent keys', () => {
      // Simulates the schema-evolution case: stored config written before
      // a new field existed. Validator must NOT error on the missing key.
      const partial = { target_verticals: ['home_services'] }
      const { value, errors } = validateNewBusiness(partial)
      expect(errors).toEqual([])
      expect(value.soda_sources.length).toBe(5)
      expect(value.revenue_range).toEqual(DEFAULTS.new_business.revenue_range)
    })
  })

  describe('validateJobMonitor', () => {
    it('errors when search_queries is empty', () => {
      const { value, errors } = validateJobMonitor({ search_queries: [] })
      expect(errors).toContain('search_queries cannot be empty')
      // still returns defaults so the worker never gets a crippled list
      expect(value.search_queries.length).toBeGreaterThan(0)
    })

    it('filters out non-string entries', () => {
      const { value, errors } = validateJobMonitor({
        search_queries: ['office manager', 123, '', 'dispatcher'],
      })
      expect(errors.some((e) => e.startsWith('search_queries contains invalid'))).toBe(true)
      expect(value.search_queries).toEqual(['office manager', 'dispatcher'])
    })
  })

  describe('validateReviewMining', () => {
    it('errors when geo_radius_km is out of range', () => {
      const { errors } = validateReviewMining({ geo_radius_km: 9999 })
      expect(errors).toContain('geo_radius_km must be > 0 and <= 500')
    })

    it('errors on lat/lon out of range', () => {
      const { errors } = validateReviewMining({
        geo_center: { lat: 999, lon: -500 },
      })
      expect(errors).toContain('geo_center out of range')
    })

    it('accepts a valid review-mining config', () => {
      const { errors } = validateReviewMining({
        discovery_queries: ['plumber Phoenix AZ'],
        geo_center: { lat: 33.4484, lon: -112.074 },
        geo_radius_km: 25,
      })
      expect(errors).toEqual([])
    })
  })

  describe('validateSocialListening', () => {
    it('returns defaults on completely empty input', () => {
      const { value, errors } = validateSocialListening({})
      expect(errors).toEqual([])
      expect(value.search_queries.length).toBe(DEFAULTS.social_listening.search_queries.length)
    })
  })

  describe('corrupt JSON handling (integration shape)', () => {
    // Simulates what getGeneratorConfig does: JSON.parse failure → empty
    // object passed to validator. Validator still returns usable defaults.
    it('empty object produces usable config', () => {
      const { value, errors } = validateNewBusiness({})
      expect(errors).toEqual([])
      expect(value.soda_sources.length).toBe(5)
    })
  })
})
