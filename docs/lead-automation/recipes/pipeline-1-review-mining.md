# Make.com Recipe: Pipeline 1 — Review Mining

**Purpose:** Step-by-step guide to build two Make.com scenarios that discover Phoenix-area businesses and scan their Google reviews for operational pain signals.

**Prerequisites:**

- Google Cloud API key (Places API enabled — see api-accounts-checklist.md)
- Outscraper API key (Medium plan or pay-as-you-go)
- Anthropic API key
- Google Sheets: "SMD Lead Generation" spreadsheet with "Review Signal Leads" tab (see google-sheets-schema.md)
- Make.com Data Store: `seen_businesses` created (see make-data-store-schema.md)

---

## Scenario A: Business Discovery (Monthly)

Builds and refreshes the master list of businesses to monitor for review signals. Runs once per month to add new businesses across 14 discovery queries.

### Scenario Overview

```
Schedule (1st of month)
  -> Set Variable: discovery queries array (14 queries)
  -> Iterator: each query
    -> HTTP: Google Places API Text Search
    -> Iterator: each business result
      -> Data Store: check seen_businesses (dedup by place_id)
      -> Filter: only new businesses
      -> Data Store: add to seen_businesses
```

**Total modules per new business:** ~4
**Expected monthly volume:** 14 queries x 20 results = 280 businesses checked. After dedup, ~20-50 new businesses per month (most are seen on repeat runs).
**Operations per run:** ~300-500

---

### Step-by-Step Build

#### Step 1: Create the Scenario

1. In Make.com -> Scenarios -> Create a new scenario
2. Name: `Lead Gen: P1a Business Discovery`
3. Set the schedule: **1st of every month at 5:00 AM** (MST/Arizona time — Arizona does not observe DST)

#### Step 2: Add the Trigger — Scheduled

1. The scenario trigger is **Schedule** (built-in)
2. Set to run once monthly on the 1st at 5:00 AM
3. This fires the first module in the chain

#### Step 3: Module 1 — Set Variable (Discovery Queries)

**Module: Tools -> Set multiple variables**

- Variable 1: `queries` (array)
- Value:

```json
[
  "plumber Phoenix AZ",
  "HVAC contractor Phoenix AZ",
  "electrician Phoenix AZ",
  "pest control Phoenix AZ",
  "landscaping company Phoenix AZ",
  "plumber Scottsdale AZ",
  "plumber Chandler AZ",
  "plumber Mesa AZ",
  "accounting firm Phoenix AZ",
  "law firm Phoenix AZ",
  "insurance agency Phoenix AZ",
  "dental office Phoenix AZ",
  "auto repair Phoenix AZ",
  "veterinarian Phoenix AZ"
]
```

These 14 queries are defined in `outscraper-queries.md`. They cover home services, professional services, and secondary verticals across the Phoenix metro area.

#### Step 4: Module 2 — Iterator (Query List)

**Module: Flow Control -> Iterator**

- Source array: `{{queries}}` (from the Set Variable module)
- This processes each query term one at a time

#### Step 5: Module 3 — HTTP (Google Places Text Search)

**Module: HTTP -> Make a request**

| Setting        | Value                                                |
| -------------- | ---------------------------------------------------- |
| URL            | `https://places.googleapis.com/v1/places:searchText` |
| Method         | POST                                                 |
| Headers        | See below                                            |
| Body type      | JSON                                                 |
| Parse response | Yes                                                  |

**Headers:**

| Header             | Value                                                                                                                                                       |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Content-Type`     | `application/json`                                                                                                                                          |
| `X-Goog-Api-Key`   | `{{GOOGLE_API_KEY}}` (from scenario variable)                                                                                                               |
| `X-Goog-FieldMask` | `places.id,places.displayName,places.formattedAddress,places.nationalPhoneNumber,places.websiteUri,places.rating,places.userRatingCount,places.primaryType` |

**Request body (JSON):**

```json
{
  "textQuery": "{{iterator.value}}",
  "locationBias": {
    "circle": {
      "center": { "latitude": 33.4484, "longitude": -112.074 },
      "radius": 50000.0
    }
  },
  "maxResultCount": 20
}
```

**Error handling:** Add an error route -> Resume (continue to next query if one fails)

**Note:** Each query returns up to 20 results. The `locationBias` centers on downtown Phoenix with a 50km radius covering the metro area. Google's $200/mo free credit covers ~6,250 Text Searches — 14 queries per month is well within budget.

#### Step 6: Module 4 — Iterator (Business Results)

**Module: Flow Control -> Iterator**

- Source array: `{{HTTP.body.places}}` (the places array from the Google Places response)
- If `places` is empty or undefined, the iterator produces 0 items -> scenario continues to next query

#### Step 7: Module 5 — Data Store (Dedup Check)

**Module: Data Store -> Get a record**

- Data store: `seen_businesses`
- Key: `{{iterator2.id}}` (the Google Places `place_id`)

The `place_id` is a globally unique identifier for each business — it is the natural dedup key.

#### Step 8: Filter — Only New Businesses

**Add a filter between the Data Store module and the next module:**

- Condition: Data Store module **did NOT return a record** (the record does not exist)
- In Make.com: the filter checks if the Data Store output bundle is empty
- Label: "New business only"

#### Step 9: Module 6 — Data Store (Add to Master List)

**Module: Data Store -> Add/replace a record**

- Data store: `seen_businesses`
- Key: `{{iterator2.id}}`
- Fields:

| Field              | Value                                  |
| ------------------ | -------------------------------------- |
| `place_id`         | `{{iterator2.id}}`                     |
| `business_name`    | `{{iterator2.displayName.text}}`       |
| `last_scanned`     | `{{formatDate(now; "YYYY-MM-DD")}}`    |
| `last_review_date` | (leave empty — no reviews scanned yet) |
| `last_pain_score`  | (leave empty — not scored yet)         |

---

## Scenario B: Weekly Review Scan

Pulls recent reviews for all businesses in the master list, scores them with Claude for operational pain signals, and writes qualified leads to Google Sheets.

### Scenario Overview

```
Schedule (every Monday 6am)
  -> Data Store: search all records in seen_businesses
  -> Array Aggregator: collect all place_ids
  -> Iterator: batch into groups of 10 place_ids
    -> HTTP: Outscraper Reviews API (batch of 10, cutoff=7 days ago)
    -> Iterator: each business in response
      -> Filter: has new reviews (reviews_data not empty)
      -> Anthropic: score reviews with Claude (review-scoring-prompt)
      -> Parse JSON
      -> Filter: pain_score >= 7
      -> Google Sheets: append to "Review Signal Leads"
      -> Data Store: update seen_businesses (last_scanned, last_pain_score)
```

**Total modules per scored business:** ~7
**Expected weekly volume:** 200-500 businesses checked. Of those, maybe 20-50 have new reviews. Of those, maybe 5-15 score >= 7.
**Operations per run:** ~450-1,350 (depends on how many businesses have new reviews)

---

### Step-by-Step Build

#### Step 1: Create the Scenario

1. In Make.com -> Scenarios -> Create a new scenario
2. Name: `Lead Gen: P1b Weekly Review Scan`
3. Set the schedule: **Every Monday at 6:00 AM** (MST/Arizona time)

#### Step 2: Add the Trigger — Scheduled

1. The scenario trigger is **Schedule** (built-in)
2. Set to run once weekly on Monday at 6:00 AM

#### Step 3: Module 1 — Data Store (Search All Businesses)

**Module: Data Store -> Search records**

- Data store: `seen_businesses`
- Filter: (none — return all records)
- Limit: 500 (adjust based on how many businesses are in the master list)

This returns all place_ids from the master list built by Scenario A.

#### Step 4: Module 2 — Array Aggregator (Collect Place IDs)

**Module: Tools -> Array aggregator**

- Source module: Data Store (search records)
- Aggregated field: `place_id`

This collects all place_ids into a single array for batching.

#### Step 5: Module 3 — Set Variable (Batch into Groups of 10)

Outscraper supports batch requests — multiple place_ids in a single API call. Batch into groups of 10 to reduce HTTP round trips while staying within response size limits.

**Module: Tools -> Set multiple variables**

- Variable: `batch_size` = `10`

Then use a **Math -> Ceil** or **Tools -> Set variable** to calculate the number of batches:

- `batch_count` = `{{ceil(length(arrayAggregator.array) / 10)}}`

#### Step 6: Module 4 — Iterator (Batches)

**Module: Flow Control -> Iterator**

- Use a numeric iterator from 0 to `{{batch_count - 1}}`
- For each iteration, slice the place_id array: `{{slice(arrayAggregator.array; iterator.value * 10; (iterator.value + 1) * 10)}}`

**Alternative simpler approach:** If batch slicing is complex in Make.com's UI, use the Array Aggregator module with a group size of 10 directly. This groups the incoming Data Store results into bundles of 10.

#### Step 7: Module 5 — HTTP (Outscraper Reviews API)

**Module: HTTP -> Make a request**

| Setting          | Value                                          |
| ---------------- | ---------------------------------------------- |
| URL              | `https://api.outscraper.com/maps/reviews-v3`   |
| Method           | GET                                            |
| Headers          | `Authorization: Bearer {{OUTSCRAPER_API_KEY}}` |
| Query parameters | See below                                      |
| Parse response   | Yes                                            |

**Query parameters:**

| Key            | Value                                                                    |
| -------------- | ------------------------------------------------------------------------ |
| `query`        | `{{join(currentBatch; ",")}}` (comma-separated place_ids for this batch) |
| `reviewsLimit` | `10`                                                                     |
| `sort`         | `newest`                                                                 |
| `cutoff`       | `{{formatDate(addDays(now; -7); "X")}}`                                  |
| `async`        | `false`                                                                  |
| `language`     | `en`                                                                     |

**Cutoff calculation:** The expression `{{formatDate(addDays(now; -7); "X")}}` produces a Unix timestamp for 7 days ago. The `"X"` format code outputs seconds since epoch.

**Error handling:** Add an error route -> Resume (continue to next batch if one fails)

#### Step 8: Module 6 — Iterator (Businesses in Response)

**Module: Flow Control -> Iterator**

- Source array: `{{HTTP.body}}` (Outscraper returns an array of business objects)

#### Step 9: Filter — Has New Reviews

**Add a filter after the Iterator:**

- Condition: `{{length(iterator2.reviews_data)}}` is greater than `0`
- Label: "Has new reviews"

Businesses with no reviews in the past 7 days return an empty `reviews_data` array. Skip them — no API cost for Claude, no operations spent.

#### Step 10: Module 7 — Anthropic (Claude Review Scoring)

**Module: Anthropic (Claude) -> Create a Message**

| Setting       | Value                                                                                                         |
| ------------- | ------------------------------------------------------------------------------------------------------------- |
| Model         | `claude-sonnet-4-6` (cost-effective for scoring tasks)                                                        |
| Max tokens    | `2048` (reviews generate longer output than job qualification)                                                |
| System prompt | Paste the full content of `REVIEW_SCORING_SYSTEM_PROMPT` from `src/lead-gen/prompts/review-scoring-prompt.ts` |

**User message:**

```
Analyze the following reviews for operational pain signals.

Business: {{iterator2.name}}
Place ID: {{iterator2.place_id}}
Category: {{iterator2.category}}
Area: (extract from address or set to "Phoenix Metro")
Overall Rating: {{iterator2.rating}}/5
Total Reviews: {{iterator2.reviews}}

Recent Reviews ({{length(iterator2.reviews_data)}}):
{{#each iterator2.reviews_data}}
- ({{this.review_rating}} stars, {{this.review_datetime_utc}}) "{{this.review_text}}"
{{/each}}

Produce a single JSON object matching the ReviewScoring schema.
```

**Note on iteration syntax:** Make.com does not natively support `{{#each}}`. Instead, use a **Text Aggregator** module before the Anthropic module to concatenate the reviews into a single string, or iterate the reviews array and aggregate. The simpler approach: use a **Set Variable** module with Make.com's `join()` function to build the review text block from the `reviews_data` array.

**Batch scoring alternative:** To save operations, you can batch 5-10 businesses per Claude call using the `buildBatchReviewScoringUserPrompt` format from the prompt file. This requires collecting businesses with new reviews using an aggregator, then making a single Claude call per batch. More efficient but more complex to wire up. Start with single-business scoring and optimize later.

#### Step 11: Module 8 — Parse JSON

**Module: JSON -> Parse JSON**

- JSON string: `{{anthropic.content[1].text}}` (Claude's response text)
- This converts the string into a structured object for downstream modules

#### Step 12: Filter — Pain Score Threshold

**Add a filter after the JSON parse:**

- Condition: `{{json.pain_score}}` is greater than or equal to `7`
- Label: "Pain score >= 7"

Businesses scoring 1-6 are not worth outreach. They either have no operational signals (1-3) or isolated incidents that don't indicate a pattern (4-6).

#### Step 13: Module 9 — Google Sheets (Append Row)

**Module: Google Sheets -> Add a Row**

- Spreadsheet: "SMD Lead Generation"
- Sheet: "Review Signal Leads"
- Column mapping:

| Column            | Value                                                               |
| ----------------- | ------------------------------------------------------------------- |
| A: Business Name  | `{{json.business_name}}`                                            |
| B: Phone          | `{{iterator2.phone}}` (from Outscraper response, if available)      |
| C: Website        | `{{iterator2.site}}` (from Outscraper response, if available)       |
| D: Category       | `{{iterator2.category}}`                                            |
| E: Area           | Extract from address or use `{{iterator2.address}}`                 |
| F: Google Rating  | `{{iterator2.rating}}`                                              |
| G: Review Count   | `{{iterator2.reviews}}`                                             |
| H: Pain Score     | `{{json.pain_score}}`                                               |
| I: Top Problems   | `{{join(json.top_problems; ", ")}}`                                 |
| J: Evidence       | `{{json.signals[1].quote}}` (first signal quote — or join multiple) |
| K: Outreach Angle | `{{json.outreach_angle}}`                                           |
| L: Date Found     | `{{formatDate(now; "YYYY-MM-DD")}}`                                 |
| M: Status         | `New`                                                               |

#### Step 14: Module 10 — Data Store (Update Business Record)

**Module: Data Store -> Add/replace a record**

- Data store: `seen_businesses`
- Key: `{{iterator2.place_id}}`
- Fields:

| Field              | Value                                                                         |
| ------------------ | ----------------------------------------------------------------------------- |
| `place_id`         | `{{iterator2.place_id}}`                                                      |
| `business_name`    | `{{iterator2.name}}`                                                          |
| `last_scanned`     | `{{formatDate(now; "YYYY-MM-DD")}}`                                           |
| `last_review_date` | `{{iterator2.reviews_data[1].review_datetime_utc}}` (most recent review date) |
| `last_pain_score`  | `{{json.pain_score}}`                                                         |

**Important:** This module should also run for businesses that scored below 7 — update `last_scanned` regardless of score. To achieve this, place this module on a separate path BEFORE the pain score filter, or duplicate it. The simplest approach: use a **Router** after the JSON parse module. One path goes through the pain score filter to Sheets. The other path always runs the Data Store update.

---

## Testing Checklist

### Scenario A (Business Discovery)

- [ ] Run scenario manually (right-click -> Run once)
- [ ] Verify Google Places API returns results (check HTTP module output)
- [ ] Verify at least some businesses are new (pass the dedup filter)
- [ ] Verify new businesses appear in the `seen_businesses` Data Store
- [ ] Run again — second run should have fewer new businesses (dedup working)
- [ ] Check operations count — should be within estimated 300-500 per run

### Scenario B (Weekly Review Scan)

- [ ] Run scenario manually after Scenario A has populated `seen_businesses`
- [ ] Verify Outscraper returns review data (check HTTP module output)
- [ ] Verify businesses with no new reviews are filtered out
- [ ] Verify Claude produces valid JSON (check Anthropic module output — valid ReviewScoring schema)
- [ ] Verify businesses with pain_score >= 7 appear in the Google Sheet
- [ ] Verify `seen_businesses` Data Store records are updated with `last_scanned` and `last_pain_score`
- [ ] Check operations count — should be within estimated 450-1,350 per run
- [ ] Let scenario run automatically for 3+ weeks, then review all scored leads for accuracy

---

## Tuning

After 2-3 weeks of running:

1. **False positives (high pain scores for businesses without real operational pain):** Review the scored signals. If Claude is flagging service quality complaints as operational issues, add more disqualification examples to the system prompt. The distinction between "rude plumber" (service quality) and "never called me back" (operational) is the critical calibration.

2. **False negatives (operational pain missed):** Check businesses that scored 4-6. If they have clear operational patterns that Claude missed, add similar examples to the prompt's calibration section.

3. **Too many results:** Raise the pain score threshold from 7 to 8. Only the most clearly patterned operational failures will pass.

4. **Too few results:** Lower the threshold to 6, or expand the discovery queries in Scenario A to cover more verticals and geographic areas.

5. **High Outscraper costs:** Reduce the batch frequency from weekly to biweekly. Or filter the `seen_businesses` list to only scan businesses that have been added or updated in the past 90 days (use the cleanup rule from make-data-store-schema.md).

6. **Operations budget:** Batch scoring (multiple businesses per Claude call) significantly reduces operations. Implement batch scoring once single-business scoring is validated.

> **Note:** Notifications are handled by the Daily Digest scenario (see pipeline-2-job-monitor.md, Daily Digest Scenario section), not per-lead.
