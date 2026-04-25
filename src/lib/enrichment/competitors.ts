/**
 * Local competitor benchmarking via Google Places.
 * Searches for businesses in the same vertical and area, compares metrics.
 */

const PLACES_API_URL = 'https://places.googleapis.com/v1/places:searchText'

export interface CompetitorBenchmark {
  competitors: Array<{
    name: string
    rating: number
    review_count: number
  }>
  entity_rank_by_reviews: number | null
  entity_rank_by_rating: number | null
  total_competitors: number
  summary: string
}

const VERTICAL_QUERIES: Record<string, string> = {
  home_services: 'home services contractor',
  professional_services: 'professional services firm',
  contractor_trades: 'contractor',
  retail_salon: 'salon spa',
  restaurant_food: 'restaurant',
}

export async function benchmarkCompetitors(
  entityName: string,
  vertical: string | null,
  area: string | null,
  entityRating: number | null,
  entityReviewCount: number | null,
  apiKey: string
): Promise<CompetitorBenchmark | null> {
  const verticalQuery = (vertical && VERTICAL_QUERIES[vertical]) || 'business'
  const locationQuery = area || 'Phoenix, AZ'
  const query = `${verticalQuery} ${locationQuery}`

  const response = await fetch(PLACES_API_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Goog-Api-Key': apiKey,
      'X-Goog-FieldMask': 'places.displayName,places.rating,places.userRatingCount',
    },
    body: JSON.stringify({
      textQuery: query,
      locationBias: {
        circle: {
          center: { latitude: 33.4484, longitude: -112.074 },
          radius: 25000,
        },
      },
      maxResultCount: 10,
    }),
  })

  if (!response.ok) return null

  const data = (await response.json()) as {
    places?: Array<{
      displayName?: { text: string }
      rating?: number
      userRatingCount?: number
    }>
  }

  const competitors = (data.places ?? [])
    .filter((p) => p.displayName?.text?.toLowerCase() !== entityName.toLowerCase())
    .map((p) => ({
      name: p.displayName?.text ?? '',
      rating: p.rating ?? 0,
      review_count: p.userRatingCount ?? 0,
    }))
    .slice(0, 5)

  if (competitors.length === 0) return null

  // Rank entity among competitors
  let rankByReviews: number | null = null
  let rankByRating: number | null = null

  if (entityReviewCount != null) {
    const allByReviews = [...competitors.map((c) => c.review_count), entityReviewCount].sort(
      (a, b) => b - a
    )
    rankByReviews = allByReviews.indexOf(entityReviewCount) + 1
  }

  if (entityRating != null) {
    const allByRating = [...competitors.map((c) => c.rating), entityRating].sort((a, b) => b - a)
    rankByRating = allByRating.indexOf(entityRating) + 1
  }

  const avgRating = competitors.reduce((sum, c) => sum + c.rating, 0) / competitors.length
  const avgReviews = competitors.reduce((sum, c) => sum + c.review_count, 0) / competitors.length

  const parts: string[] = []
  if (rankByRating) parts.push(`Ranked #${rankByRating} of ${competitors.length + 1} by rating`)
  if (rankByReviews) parts.push(`#${rankByReviews} by review count`)
  parts.push(`Local avg: ${avgRating.toFixed(1)} stars, ${Math.round(avgReviews)} reviews`)

  return {
    competitors,
    entity_rank_by_reviews: rankByReviews,
    entity_rank_by_rating: rankByRating,
    total_competitors: competitors.length,
    summary: parts.join('. ') + '.',
  }
}
