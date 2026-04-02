/**
 * Google Places enrichment — look up business by name + area.
 * Used for entities that don't have phone/website (e.g., from permit pipelines).
 */

const PLACES_API_URL = 'https://places.googleapis.com/v1/places:searchText'

export interface PlacesEnrichment {
  phone: string | null
  website: string | null
  rating: number | null
  reviewCount: number | null
  businessStatus: string | null
  address: string | null
}

export async function lookupGooglePlaces(
  name: string,
  area: string | null,
  apiKey: string
): Promise<PlacesEnrichment | null> {
  const query = area ? `"${name}" near ${area}` : `"${name}" Phoenix AZ`

  const response = await fetch(PLACES_API_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Goog-Api-Key': apiKey,
      'X-Goog-FieldMask':
        'places.id,places.displayName,places.nationalPhoneNumber,places.websiteUri,places.rating,places.userRatingCount,places.businessStatus,places.formattedAddress',
    },
    body: JSON.stringify({
      textQuery: query,
      locationBias: {
        circle: {
          center: { latitude: 33.4484, longitude: -112.074 },
          radius: 50000,
        },
      },
      maxResultCount: 1,
    }),
  })

  if (!response.ok) return null

  const data = (await response.json()) as {
    places?: Array<{
      nationalPhoneNumber?: string
      websiteUri?: string
      rating?: number
      userRatingCount?: number
      businessStatus?: string
      formattedAddress?: string
    }>
  }

  const place = data.places?.[0]
  if (!place) return null

  return {
    phone: place.nationalPhoneNumber ?? null,
    website: place.websiteUri ?? null,
    rating: place.rating ?? null,
    reviewCount: place.userRatingCount ?? null,
    businessStatus: place.businessStatus ?? null,
    address: place.formattedAddress ?? null,
  }
}
