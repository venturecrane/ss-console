# Outscraper Query Specifications — Pipeline 1 (Review Mining)

**Purpose:** Exact API queries for the Make.com Review Mining scenarios. These queries hit Outscraper's Google Maps Reviews API to pull recent reviews for Phoenix-area businesses by category.

**Account:** Outscraper Medium plan ($49/mo) or pay-as-you-go (~$20-50/mo depending on volume).
**API Base:** `https://api.outscraper.com`
**Docs:** outscraper.com/docs

---

## Authentication

All requests include the API key in the Authorization header:

```
Authorization: Bearer {OUTSCRAPER_API_KEY}
```

In Make.com: use the HTTP module with a custom header.

---

## Two-Phase Architecture

Pipeline 1 uses two Make.com scenarios:

### Phase 1: Business Discovery (Monthly)

Uses **Google Places API** (not Outscraper) to build the master list of businesses to monitor. See Google Cloud setup in `api-accounts-checklist.md`.

Google Places Text Search:

```
GET https://places.googleapis.com/v1/places:searchText
Content-Type: application/json
X-Goog-Api-Key: {GOOGLE_API_KEY}
X-Goog-FieldMask: places.id,places.displayName,places.formattedAddress,places.nationalPhoneNumber,places.websiteUri,places.rating,places.userRatingCount,places.primaryType

{
  "textQuery": "plumber Phoenix AZ",
  "locationBias": {
    "circle": {
      "center": { "latitude": 33.4484, "longitude": -112.0740 },
      "radius": 50000.0
    }
  },
  "maxResultCount": 20
}
```

Discovery queries (run once monthly to refresh the master list):

| #   | Query                            | Vertical              |
| --- | -------------------------------- | --------------------- |
| 1   | `plumber Phoenix AZ`             | home_services         |
| 2   | `HVAC contractor Phoenix AZ`     | home_services         |
| 3   | `electrician Phoenix AZ`         | home_services         |
| 4   | `pest control Phoenix AZ`        | home_services         |
| 5   | `landscaping company Phoenix AZ` | home_services         |
| 6   | `plumber Scottsdale AZ`          | home_services         |
| 7   | `plumber Chandler AZ`            | home_services         |
| 8   | `plumber Mesa AZ`                | home_services         |
| 9   | `accounting firm Phoenix AZ`     | professional_services |
| 10  | `law firm Phoenix AZ`            | professional_services |
| 11  | `insurance agency Phoenix AZ`    | professional_services |
| 12  | `dental office Phoenix AZ`       | professional_services |
| 13  | `auto repair Phoenix AZ`         | other                 |
| 14  | `veterinarian Phoenix AZ`        | other                 |

Each query returns up to 20 results. With pagination (nextPageToken), up to 60. Start with 20 per query — that's 280 businesses in the initial list. Google's $200/mo free credit covers ~6,250 Text Searches.

Store results in the `seen_businesses` Make.com Data Store with place_id as the key.

### Phase 2: Review Scanning (Weekly)

Uses **Outscraper** to pull recent reviews for all businesses in the master list.

---

## Outscraper Reviews API

### Endpoint: Get Reviews

```
GET https://api.outscraper.com/maps/reviews-v3
  ?query={PLACE_ID}
  &reviewsLimit=10
  &sort=newest
  &cutoff={UNIX_TIMESTAMP_7_DAYS_AGO}
  &async=false
```

### Parameters

| Parameter      | Value                  | Notes                                                                  |
| -------------- | ---------------------- | ---------------------------------------------------------------------- |
| `query`        | Google Places place_id | e.g., `ChIJ_abc123`                                                    |
| `reviewsLimit` | `10`                   | Max reviews to return per business                                     |
| `sort`         | `newest`               | Sort by most recent first                                              |
| `cutoff`       | Unix timestamp         | Only reviews after this date. Set to 7 days ago.                       |
| `async`        | `false`                | Synchronous response (wait for results). Use `true` for large batches. |
| `language`     | `en`                   | English reviews only                                                   |

### Response Structure (key fields)

```json
[
  {
    "name": "Reliable Rooter Plumbing",
    "place_id": "ChIJ_abc123",
    "rating": 4.2,
    "reviews": 87,
    "reviews_data": [
      {
        "author_title": "John Smith",
        "review_rating": 3,
        "review_text": "Had to call three times before anyone picked up...",
        "review_datetime_utc": "2026-03-25T14:30:00Z",
        "owner_answer": "We apologize for the inconvenience..."
      }
    ]
  }
]
```

**Fields to extract:**

- `name` → business_name for Claude prompt
- `place_id` → dedup key
- `rating` → overall_rating for context
- `reviews` → total_review_count
- `reviews_data[].author_title` → review author
- `reviews_data[].review_rating` → individual review star rating
- `reviews_data[].review_text` → the review text for AI analysis
- `reviews_data[].review_datetime_utc` → review date

### Batching

Outscraper supports batch requests — multiple place_ids in a single call:

```
GET https://api.outscraper.com/maps/reviews-v3
  ?query=ChIJ_abc123,ChIJ_def456,ChIJ_ghi789
  &reviewsLimit=10
  &sort=newest
  &cutoff={UNIX_TIMESTAMP}
```

Use batches of 10 place_ids per call to reduce HTTP round trips while staying within response size limits.

---

## Cutoff Date Calculation

The `cutoff` parameter uses Unix timestamps. In Make.com, calculate "7 days ago" using:

```
{{formatDate(addDays(now; -7); "X")}}
```

This Make.com expression:

1. Gets current datetime (`now`)
2. Subtracts 7 days (`addDays(now; -7)`)
3. Formats as Unix timestamp (`"X"`)

---

## Weekly Scan Flow

1. Read all place_ids from the `seen_businesses` Data Store
2. Batch into groups of 10
3. For each batch, call Outscraper Reviews API with `cutoff` = 7 days ago
4. For businesses with new reviews (reviews_data not empty):
   - Pass to Claude review scoring prompt (batch 5-10 businesses per prompt call)
   - For scored businesses with pain_score >= 7: write to Google Sheet (included in daily digest email)
   - Update `seen_businesses` Data Store with `last_scanned` and `last_pain_score`
5. For businesses with no new reviews: skip (no API cost, no operations spent)

---

## Cost Calculations

### Outscraper Pricing

Reviews extraction: ~$3 per 1,000 place_ids queried (regardless of how many reviews come back).

| Scenario                   | Place IDs/Week | Monthly Cost |
| -------------------------- | -------------- | ------------ |
| 200 businesses monitored   | 200/week       | ~$2.40/mo    |
| 500 businesses monitored   | 500/week       | ~$6.00/mo    |
| 1,000 businesses monitored | 1,000/week     | ~$12.00/mo   |

### Google Places API (Discovery)

Text Search: $32 per 1,000 requests.

| Scenario                                   | Queries/Month | Monthly Cost                     |
| ------------------------------------------ | ------------- | -------------------------------- |
| 14 queries × 1 run/month                   | 14            | ~$0.45 (within $200 free credit) |
| 14 queries × 4 runs/month (weekly refresh) | 56            | ~$1.80 (within free credit)      |

**Total Pipeline 1 cost: ~$6-12/mo** at 500 businesses monitored weekly.

---

## Outscraper Alternatives

If Outscraper pricing or reliability becomes an issue:

| Service                           | Reviews API            | Cost             | Key Difference                    |
| --------------------------------- | ---------------------- | ---------------- | --------------------------------- |
| **DataForSEO**                    | All reviews, paginated | ~$2/1K tasks     | Cheaper but no date cutoff filter |
| **SerpAPI** (Google Maps Reviews) | All reviews, paginated | $75-150/mo       | More reliable, higher cost        |
| **Apify** (Google Maps Scraper)   | All reviews            | ~$49/mo platform | More flexible, more setup         |

DataForSEO is the best cost alternative. You'd pull all reviews and filter by date client-side (in Make.com with a filter module after the HTTP response).
