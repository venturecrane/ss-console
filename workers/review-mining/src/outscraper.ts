/**
 * Outscraper + Google Places API client for review mining.
 *
 * Two-phase pipeline:
 * 1. Discover businesses via Google Places Text Search (14 queries)
 * 2. Fetch recent reviews via Outscraper for each discovered business
 */

export interface DiscoveredBusiness {
  place_id: string
  name: string
  address: string
  rating: number
  total_reviews: number
  category: string
  phone: string | null
  website: string | null
}

export interface BusinessWithReviews {
  place_id: string
  name: string
  address: string
  category: string
  area: string
  rating: number
  total_reviews: number
  phone: string | null
  website: string | null
  reviews: ReviewData[]
}

export interface ReviewData {
  author: string
  rating: number
  text: string
  date: string
}

// Discovery queries moved to generator_config. Defaults live in
// src/lib/generators/types.ts and are merged at worker invocation.

/**
 * Discover businesses via Google Places Text Search API.
 */
export interface GeoBias {
  center: { lat: number; lon: number }
  radiusKm: number
}

export async function discoverBusinesses(
  query: string,
  apiKey: string,
  geo?: GeoBias
): Promise<DiscoveredBusiness[]> {
  const center = geo?.center ?? { lat: 33.4484, lon: -112.074 }
  const radiusMeters = (geo?.radiusKm ?? 50) * 1000
  const response = await fetch(`https://places.googleapis.com/v1/places:searchText`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Goog-Api-Key': apiKey,
      'X-Goog-FieldMask':
        'places.id,places.displayName,places.formattedAddress,places.rating,places.userRatingCount,places.primaryTypeDisplayName,places.nationalPhoneNumber,places.websiteUri',
    },
    body: JSON.stringify({
      textQuery: query,
      locationBias: {
        circle: {
          center: { latitude: center.lat, longitude: center.lon },
          radius: radiusMeters,
        },
      },
      maxResultCount: 20,
    }),
  })

  if (!response.ok) {
    const text = await response.text()
    console.error(`Google Places ${response.status}: ${text.slice(0, 200)}`)
    return []
  }

  const data = (await response.json()) as {
    places?: Array<{
      id: string
      displayName?: { text: string }
      formattedAddress?: string
      rating?: number
      userRatingCount?: number
      primaryTypeDisplayName?: { text: string }
      nationalPhoneNumber?: string
      websiteUri?: string
    }>
  }

  return (data.places ?? []).map((p) => ({
    place_id: p.id,
    name: p.displayName?.text ?? '',
    address: p.formattedAddress ?? '',
    rating: p.rating ?? 0,
    total_reviews: p.userRatingCount ?? 0,
    category: p.primaryTypeDisplayName?.text ?? query.split(' ')[0],
    phone: p.nationalPhoneNumber ?? null,
    website: p.websiteUri ?? null,
  }))
}

/**
 * Fetch recent reviews for a batch of businesses via Outscraper.
 * Returns reviews from the past 7 days.
 */
export async function fetchReviews(
  businesses: DiscoveredBusiness[],
  apiKey: string
): Promise<BusinessWithReviews[]> {
  const cutoff = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000)
  const results: BusinessWithReviews[] = []

  // Outscraper requires one request per place_id (comma-separated is treated as one query)
  for (const business of businesses) {
    try {
      const params = new URLSearchParams({
        query: business.place_id,
        reviewsLimit: '10',
        sort: 'newest',
        language: 'en',
        async: 'false',
      })

      const response = await fetch(
        `https://api.app.outscraper.com/maps/reviews-v3?${params.toString()}`,
        {
          headers: { 'X-API-KEY': apiKey },
        }
      )

      if (!response.ok) {
        console.error(`Outscraper ${response.status} for "${business.name}"`)
        continue
      }

      const data = (await response.json()) as {
        data?: Array<{
          google_id?: string
          name?: string
          reviews_data?: Array<{
            author_title?: string
            review_rating?: number
            review_text?: string
            review_datetime_utc?: string
          }>
        }>
      }

      const place = data.data?.[0]
      if (!place?.reviews_data) continue

      const recentReviews = place.reviews_data
        .filter((r) => r.review_text && new Date(r.review_datetime_utc ?? '') >= cutoff)
        .map((r) => ({
          author: r.author_title ?? 'Anonymous',
          rating: r.review_rating ?? 3,
          text: r.review_text ?? '',
          date: r.review_datetime_utc ?? '',
        }))

      if (recentReviews.length > 0) {
        results.push({
          place_id: business.place_id,
          name: business.name,
          address: business.address,
          category: business.category,
          area: extractArea(business.address),
          rating: business.rating,
          total_reviews: business.total_reviews,
          phone: business.phone,
          website: business.website,
          reviews: recentReviews,
        })
      }
    } catch (err) {
      console.error(
        `Outscraper error for "${business.name}": ${err instanceof Error ? err.message : String(err)}`
      )
    }
  }

  return results
}

/** Extract Phoenix sub-area from address. */
function extractArea(address: string): string {
  const cities = [
    'Scottsdale',
    'Chandler',
    'Gilbert',
    'Tempe',
    'Mesa',
    'Glendale',
    'Peoria',
    'Surprise',
    'Goodyear',
    'Avondale',
    'Cave Creek',
    'Fountain Hills',
    'Paradise Valley',
  ]
  for (const city of cities) {
    if (address.includes(city)) return `${city}, AZ`
  }
  return 'Phoenix, AZ'
}
