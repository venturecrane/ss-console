import { describe, expect, it } from 'vitest'
import { computeSlug, normalizeBusinessName } from '../src/lib/entities/slug'

describe('lead-gen dedup normalization', () => {
  it('strips common business suffixes from slug normalization', () => {
    expect(computeSlug('ProGuard Roofing LLC')).toBe('proguard-roofing')
    expect(computeSlug('Acme Electric Co.')).toBe('acme-electric')
  })

  // Regression for #751 bug 2 (SerpAPI location-string drift):
  // The same business at the same address but with a slightly different
  // location string from SerpAPI must produce the same slug — otherwise
  // every cron run creates a new entity for the same business.
  it('produces a single slug for one business regardless of area variants', () => {
    const variants = [
      computeSlug('Old Town Towing', 'Phoenix, AZ'),
      computeSlug('Old Town Towing', 'Phoenix, AZ, United States'),
      computeSlug('Old Town Towing', 'Phoenix, AZ, Estados Unidos'),
      computeSlug('Old Town Towing', 'AZ'),
      computeSlug('Old Town Towing', null),
      computeSlug('Old Town Towing'),
    ]
    for (const slug of variants) {
      expect(slug).toBe('old-town-towing')
    }
  })

  it('normalizes names for fuzzy comparison', () => {
    expect(normalizeBusinessName('Arizona Comfort Solutions, Inc.')).toBe(
      'arizona comfort solutions'
    )
  })
})
