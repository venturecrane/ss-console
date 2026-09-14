import { describe, it, expect } from 'vitest'
import { isPublicMarketingUrl } from '../src/lib/seo/sitemap-filter.mjs'

const SITE = 'https://smd.services'

describe('sitemap filter (astro.config.mjs)', () => {
  it('keeps the public marketing surface', () => {
    const publicRoutes = [
      '/',
      '/about/',
      '/book/',
      '/contact/',
      '/industries/',
      '/operator/',
      '/packs/law-firm/',
      '/case-studies/personal-injury-law-firm/',
      '/packs/home-services/',
      '/privacy/',
      '/terms/',
    ]
    for (const route of publicRoutes) {
      expect(isPublicMarketingUrl(`${SITE}${route}`), route).toBe(true)
    }
  })

  it('excludes back-office, auth, dev, and flag-gated routes', () => {
    const privateRoutes = [
      '/admin/',
      '/admin/clients/',
      '/admin/operator/provision/',
      '/portal/',
      '/portal/products/operator/settings/users/',
      '/auth/sign-in/',
      '/api/booking/slots',
      '/dev/portal-components/',
      '/design-preview/portal-quotes-detail/',
      '/assessment/',
      '/assessment/report-preview/',
      '/patterns/',
      '/book/manage/',
      '/get-started/',
      '/404/',
    ]
    for (const route of privateRoutes) {
      expect(isPublicMarketingUrl(`${SITE}${route}`), route).toBe(false)
    }
  })

  it('does not over-match prefixes against similarly named public paths', () => {
    // /book must survive the /book/manage exclusion.
    expect(isPublicMarketingUrl(`${SITE}/book/`)).toBe(true)
  })
})
