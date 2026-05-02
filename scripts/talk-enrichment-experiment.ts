/**
 * /talk silent-enrichment feasibility test.
 *
 * Question: when a prospect submits a /talk message, can we silently look up
 * their business via the existing Google Places pipeline well enough to
 * pre-populate a lead record without asking?
 *
 * Method: walk a spread of plausible /talk-style openings. Hand-extract
 * (business name, area) the way Claude would in production. Run each
 * through the Places searchText API. Print the matched display name + phone
 * + website + address. We then score by hand:
 *   - HIT  = returned business looks like the one the prospect named
 *   - MISS = no result, or returned business looks unrelated
 *   - N/A  = prospect did not name a business; nothing to look up
 *
 * Run with:
 *   infisical run --env=prod --path=/ss -- npx tsx scripts/talk-enrichment-experiment.ts
 */

const PLACES_API_URL = 'https://places.googleapis.com/v1/places:searchText'

interface PlaceMatch {
  displayName: string | null
  phone: string | null
  website: string | null
  address: string | null
  rating: number | null
  reviewCount: number | null
  businessStatus: string | null
}

async function lookupPlaces(
  name: string,
  area: string | null,
  apiKey: string
): Promise<PlaceMatch | null> {
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
        circle: { center: { latitude: 33.4484, longitude: -112.074 }, radius: 50000 },
      },
      maxResultCount: 1,
    }),
  })
  if (!response.ok) return null
  const data = (await response.json()) as {
    places?: Array<{
      displayName?: { text?: string }
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
    displayName: place.displayName?.text ?? null,
    phone: place.nationalPhoneNumber ?? null,
    website: place.websiteUri ?? null,
    address: place.formattedAddress ?? null,
    rating: place.rating ?? null,
    reviewCount: place.userRatingCount ?? null,
    businessStatus: place.businessStatus ?? null,
  }
}

interface TestCase {
  label: string
  message: string
  extractedName: string | null
  extractedArea: string | null
  expectedShape: 'should-match' | 'common-name-ambiguous' | 'no-name-cannot-match'
}

const cases: TestCase[] = [
  {
    label: '01 generic + location only (current placeholder shape)',
    message:
      'We run a 14-truck HVAC company in Mesa. Trying to add a second crew next year. Quoting is the bottleneck. We lose deals waiting on estimates.',
    extractedName: null,
    extractedArea: 'Mesa AZ',
    expectedShape: 'no-name-cannot-match',
  },
  {
    label: '02 named business + location + first name',
    message:
      'Hi, this is Jane at Acme Plumbing in Tempe. Our scheduling is killing me — too many one-off calls slipping through.',
    extractedName: 'Acme Plumbing',
    extractedArea: 'Tempe AZ',
    expectedShape: 'common-name-ambiguous',
  },
  {
    label: '03 distinctive name + location',
    message:
      'I run Desert Sun Roofing out of Phoenix. About 8 employees. Looking to hire but quoting takes me forever and I am the bottleneck.',
    extractedName: 'Desert Sun Roofing',
    extractedArea: 'Phoenix AZ',
    expectedShape: 'should-match',
  },
  {
    label: '04 industry + location, no name',
    message:
      'We do residential plumbing in Tempe. Twelve guys. Been around about eight years. Trying to grow but I am spread too thin.',
    extractedName: null,
    extractedArea: 'Tempe AZ',
    expectedShape: 'no-name-cannot-match',
  },
  {
    label: '05 very common name + location',
    message: "Hi I'm Mike, I own Joe's HVAC in Scottsdale.",
    extractedName: "Joe's HVAC",
    extractedArea: 'Scottsdale AZ',
    expectedShape: 'common-name-ambiguous',
  },
  {
    label: '06 distinctive name + neighborhood',
    message:
      "We're a 20-person commercial cleaning company in north Phoenix called Spotless Pros. Trying to grow but operationally messy.",
    extractedName: 'Spotless Pros',
    extractedArea: 'Phoenix AZ',
    expectedShape: 'should-match',
  },
  {
    label: '07 distinctive name + location + scale',
    message:
      'Run an electrical contracting firm here in Mesa, Carson Electric. About 30 trucks. Need help with field operations.',
    extractedName: 'Carson Electric',
    extractedArea: 'Mesa AZ',
    expectedShape: 'should-match',
  },
  {
    label: '08 industry only no name',
    message: 'AC repair in Tempe — small shop, looking to scale.',
    extractedName: null,
    extractedArea: 'Tempe AZ',
    expectedShape: 'no-name-cannot-match',
  },
  {
    label: "09 very common name (Mike's Auto)",
    message: "Mike's Auto Repair, Glendale. We're stuck at 2 bays, want to expand to 4.",
    extractedName: "Mike's Auto Repair",
    extractedArea: 'Glendale AZ',
    expectedShape: 'common-name-ambiguous',
  },
  {
    label: '10 medium-distinctive name + location',
    message:
      'Sun Valley Painting, Chandler. 15 painters, mostly residential. Scheduling is a nightmare.',
    extractedName: 'Sun Valley Painting',
    extractedArea: 'Chandler AZ',
    expectedShape: 'should-match',
  },
]

async function main() {
  const apiKey = process.env.GOOGLE_PLACES_API_KEY
  if (!apiKey) {
    console.error(
      'GOOGLE_PLACES_API_KEY not set. Run via: infisical run --env=prod --path=/ss -- npx tsx scripts/talk-enrichment-experiment.ts'
    )
    process.exit(1)
  }

  console.log('# /talk silent-enrichment feasibility test')
  console.log('# 10 plausible /talk openings → Google Places lookup → returned record')
  console.log(
    '# Score by hand: HIT (looks right), MISS (no result OR wrong business), N/A (no name to look up)'
  )
  console.log()

  let attempted = 0
  let matched = 0
  let cannotLookup = 0
  for (const c of cases) {
    console.log(`## ${c.label}`)
    console.log(`message: ${c.message}`)
    console.log(
      `extracted: name=${JSON.stringify(c.extractedName)} area=${JSON.stringify(c.extractedArea)}`
    )
    console.log(`expected: ${c.expectedShape}`)
    if (!c.extractedName) {
      console.log(`result: NO LOOKUP (no business name to search)`)
      cannotLookup++
      console.log()
      continue
    }
    attempted++
    try {
      const result = await lookupPlaces(c.extractedName, c.extractedArea, apiKey)
      if (!result) {
        console.log(`result: API returned null (no match or HTTP error)`)
      } else {
        matched++
        console.log(`result:`)
        console.log(`  displayName: ${result.displayName}`)
        console.log(`  phone: ${result.phone}`)
        console.log(`  website: ${result.website}`)
        console.log(`  address: ${result.address}`)
        console.log(`  rating: ${result.rating} (${result.reviewCount} reviews)`)
        console.log(`  businessStatus: ${result.businessStatus}`)
      }
    } catch (err) {
      console.log(`result: ERROR ${err instanceof Error ? err.message : String(err)}`)
    }
    console.log()
    // Small delay to be polite to Places API
    await new Promise((r) => setTimeout(r, 200))
  }

  console.log('---')
  console.log(`Summary:`)
  console.log(`  Total cases: ${cases.length}`)
  console.log(`  Cannot lookup (no business name in message): ${cannotLookup}`)
  console.log(`  Lookups attempted: ${attempted}`)
  console.log(`  Lookups returned a result: ${matched} of ${attempted}`)
  console.log(`  (Match quality requires manual scoring — see displayName/address per case)`)
}

main().catch((e) => {
  console.error('Fatal:', e)
  process.exit(1)
})
