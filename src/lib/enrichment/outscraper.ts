/**
 * Outscraper Google Maps Places enrichment.
 *
 * Calls the Outscraper Places API to get a full business profile (40+ fields)
 * including owner info, social media links, hours, and — with the domains_service
 * parameter — scraped emails and contacts from the business website.
 *
 * Uses the same OUTSCRAPER_API_KEY as the review-mining Worker.
 * Pay-as-you-go pricing, first 500 records free.
 */

const OUTSCRAPER_PLACES_URL = 'https://api.app.outscraper.com/maps/search-v3'

export interface OutscraperEnrichment {
  // Identity
  name: string
  phone: string | null
  website: string | null
  address: string | null

  // Owner
  owner_name: string | null
  owner_link: string | null
  verified: boolean

  // Social media
  facebook: string | null
  instagram: string | null
  linkedin: string | null
  twitter: string | null
  youtube: string | null

  // Emails (from domains_service enrichment)
  emails: string[]

  // Business details
  working_hours: string | null
  business_status: string | null
  about: string | null
  description: string | null

  // Ratings
  rating: number | null
  review_count: number | null
  reviews_per_score: Record<string, number> | null

  // Scheduling/booking
  booking_link: string | null

  // Website tech signals (from domains_service enrichment)
  website_generator: string | null
  has_facebook_pixel: boolean
  has_google_tag_manager: boolean

  // Photos
  photos_count: number | null
}

/**
 * Look up a business via Outscraper Google Maps Places API with email enrichment.
 *
 * @param name - Business name to search for
 * @param area - Location (e.g., "Phoenix, AZ")
 * @param apiKey - Outscraper API key (same as review mining)
 */
export async function lookupOutscraper(
  name: string,
  area: string | null,
  apiKey: string
): Promise<OutscraperEnrichment | null> {
  const query = area ? `${name}, ${area}` : `${name}, Phoenix, AZ`

  const params = new URLSearchParams({
    query,
    limit: '1',
    async: 'false',
    domains_service: 'true',
  })

  try {
    const response = await fetch(`${OUTSCRAPER_PLACES_URL}?${params.toString()}`, {
      headers: { 'X-API-KEY': apiKey },
      signal: AbortSignal.timeout(15000),
    })

    if (!response.ok) {
      console.error(`[outscraper] ${response.status} for "${name}"`)
      return null
    }

    const data = (await response.json()) as {
      data?: Array<Array<Record<string, unknown>>>
    }

    // Outscraper returns nested arrays: data[0][0] is the first result
    const place = data?.data?.[0]?.[0]
    if (!place) return null

    // Basic name similarity check
    const placeName = String(place.name ?? '').toLowerCase()
    const searchName = name.toLowerCase().split(' ')[0]
    if (!placeName.includes(searchName) && !searchName.includes(placeName.split(' ')[0])) {
      return null
    }

    return {
      name: String(place.name ?? name),
      phone: stringOrNull(place.phone),
      website: stringOrNull(place.website),
      address: stringOrNull(place.address),

      owner_name: stringOrNull(place.owner_title),
      owner_link: stringOrNull(place.owner_link),
      verified: place.verified === true,

      facebook: stringOrNull(place.Facebook),
      instagram: stringOrNull(place.Instagram),
      linkedin: stringOrNull(place.Linkedin),
      twitter: stringOrNull(place.Twitter),
      youtube: stringOrNull(place.Youtube),

      emails: extractEmails(place),

      working_hours: stringOrNull(place.working_hours),
      business_status: stringOrNull(place.business_status),
      about: stringOrNull(place.about),
      description: stringOrNull(place.description),

      rating: typeof place.rating === 'number' ? place.rating : null,
      review_count: typeof place.reviews === 'number' ? place.reviews : null,
      reviews_per_score:
        place.reviews_per_score && typeof place.reviews_per_score === 'object'
          ? (place.reviews_per_score as Record<string, number>)
          : null,

      booking_link:
        stringOrNull(place.booking_appointment_link) ?? stringOrNull(place.reservation_links),

      website_generator: stringOrNull(place.website_generator),
      has_facebook_pixel: place.website_has_fb_pixel === true,
      has_google_tag_manager: place.website_has_gtm === true,

      photos_count: typeof place.photos_count === 'number' ? place.photos_count : null,
    }
  } catch (err) {
    console.error(`[outscraper] Error for "${name}":`, err)
    return null
  }
}

function stringOrNull(value: unknown): string | null {
  if (typeof value === 'string' && value.trim()) return value.trim()
  return null
}

function extractEmails(place: Record<string, unknown>): string[] {
  const emails: string[] = []
  for (const key of ['email_1', 'email_2', 'email_3']) {
    const val = place[key]
    if (typeof val === 'string' && val.includes('@')) {
      emails.push(val.trim())
    }
  }
  return emails
}
