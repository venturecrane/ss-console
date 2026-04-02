/**
 * Yelp Fusion API enrichment — cross-reference business for rating/review data.
 * Free tier: 500 requests/day.
 */

const YELP_API_URL = 'https://api.yelp.com/v3/businesses/search'

export interface YelpEnrichment {
  yelp_id: string
  name: string
  rating: number
  review_count: number
  claimed: boolean
  categories: string[]
  phone: string | null
  url: string
}

export async function lookupYelp(
  name: string,
  area: string | null,
  apiKey: string
): Promise<YelpEnrichment | null> {
  const params = new URLSearchParams({
    term: name,
    location: area ?? 'Phoenix, AZ',
    limit: '1',
  })

  try {
    const response = await fetch(`${YELP_API_URL}?${params.toString()}`, {
      headers: { Authorization: `Bearer ${apiKey}` },
    })

    if (!response.ok) return null

    const data = (await response.json()) as {
      businesses?: Array<{
        id: string
        name: string
        rating: number
        review_count: number
        is_claimed: boolean
        categories: Array<{ alias: string; title: string }>
        phone: string
        url: string
      }>
    }

    const biz = data.businesses?.[0]
    if (!biz) return null

    // Basic name similarity check to avoid false matches
    const nameLower = name.toLowerCase()
    const yelpLower = biz.name.toLowerCase()
    if (
      !yelpLower.includes(nameLower.split(' ')[0]) &&
      !nameLower.includes(yelpLower.split(' ')[0])
    ) {
      return null // Names too different, likely wrong business
    }

    return {
      yelp_id: biz.id,
      name: biz.name,
      rating: biz.rating,
      review_count: biz.review_count,
      claimed: biz.is_claimed,
      categories: biz.categories.map((c) => c.title),
      phone: biz.phone || null,
      url: biz.url,
    }
  } catch {
    return null
  }
}
