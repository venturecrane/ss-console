# SODA API Query Specifications — Pipeline 3 (New Business Detection)

**Purpose:** Exact API queries for the Make.com New Business Detection scenario. These queries hit the Socrata Open Data API (SODA) endpoints provided by Phoenix, Scottsdale, and Chandler to pull recent commercial building permits.

**Cost:** $0 — all city open data portals are free and require no API key for basic access.

---

## What We're Looking For

Commercial **tenant improvement (TI) permits** are the primary signal. A commercial TI means a business is moving into, expanding, or renovating a commercial space — a direct growth signal. These businesses are either:

- New businesses setting up shop (pre-chaos — great timing for us)
- Existing businesses expanding (mid-growth — likely feeling operational strain)

---

## SODA API Basics

All three cities use Socrata-powered open data portals. The API is REST-based with SQL-like filtering via `$where` clauses.

**No authentication required** for basic read access. Rate-limited to ~1,000 requests/hour without an app token. For our volume (3 queries/day), no token needed.

### Common Parameters

| Parameter | Usage             | Example                             |
| --------- | ----------------- | ----------------------------------- |
| `$where`  | SQL-like filter   | `$where=issue_date > '2026-03-23'`  |
| `$limit`  | Max rows returned | `$limit=100`                        |
| `$order`  | Sort order        | `$order=issue_date DESC`            |
| `$select` | Column projection | `$select=permit_number,description` |

Date format in `$where`: `'YYYY-MM-DD'` (single quotes, ISO format).

---

## Phoenix Open Data

**Portal:** phoenixopendata.com
**Dataset:** Building Permits

**Note:** The exact dataset resource ID must be confirmed by visiting the portal and finding the building permits dataset. Socrata dataset IDs are 4-character alphanumeric codes (e.g., `abcd-1234`). Visit the portal, search for "building permits" or "commercial permits," and note the resource ID from the URL.

### Query Template

```
GET https://www.phoenixopendata.com/resource/{DATASET_ID}.json
  ?$where=issue_date > '{7_DAYS_AGO}'
    AND permit_type_desc LIKE '%COMMERCIAL%'
  &$limit=100
  &$order=issue_date DESC
```

**Alternative filter approaches** (depends on exact column names in the dataset):

```
# If the dataset uses work_class or permit_category:
?$where=issue_date > '{7_DAYS_AGO}' AND work_class = 'Commercial'

# If the dataset uses description text search:
?$where=issue_date > '{7_DAYS_AGO}' AND description LIKE '%tenant improvement%'
```

### Expected Response Fields

Typical Socrata building permit fields (exact names vary by dataset):

| Field                               | Maps To                                 |
| ----------------------------------- | --------------------------------------- |
| `permit_number`                     | Dedup key for `seen_permits` Data Store |
| `description` or `work_description` | What's being built/modified             |
| `issue_date`                        | Filing/Permit Date                      |
| `owner_name` or `contractor_name`   | Business Name (may need enrichment)     |
| `address`                           | Address                                 |
| `permit_type` or `work_class`       | Filter for commercial TIs               |
| `valuation` or `est_project_cost`   | Scale signal (higher = larger project)  |

---

## Scottsdale Open Data

**Portal:** data.scottsdaleaz.gov
**Dataset:** Building Permits (search the portal for exact resource ID)

### Query Template

```
GET https://data.scottsdaleaz.gov/resource/{DATASET_ID}.json
  ?$where=issued_date > '{7_DAYS_AGO}'
    AND permit_type = 'Commercial'
  &$limit=100
  &$order=issued_date DESC
```

---

## Chandler Open Data

**Portal:** data.chandleraz.gov
**Dataset:** Building Permits (search the portal for exact resource ID)

### Query Template

```
GET https://data.chandleraz.gov/resource/{DATASET_ID}.json
  ?$where=issue_date > '{7_DAYS_AGO}'
    AND work_class = 'Commercial'
  &$limit=100
  &$order=issue_date DESC
```

---

## Setup Steps

Before the Make.com scenario will work, you need to identify the actual dataset resource IDs:

### For Each City:

1. Visit the open data portal URL
2. Search for "building permits" or "permits"
3. Find the dataset with commercial permit data
4. Note the 4-character resource ID from the URL (format: `xxxx-xxxx`)
5. Click "API" or "API Docs" to see the exact column names
6. Adjust the `$where` clause to match the actual column names
7. Test the query in a browser — append `.json` to the dataset URL

### Example Browser Test

```
https://www.phoenixopendata.com/resource/{ID}.json?$limit=5
```

This returns 5 rows of raw data showing all column names and data types.

---

## Make.com Integration

In Make.com, use the **HTTP → Make a request** module:

1. **URL:** The full SODA query URL (with `$where`, `$limit`, `$order`)
2. **Method:** GET
3. **Headers:** None required (no auth)
4. **Parse response:** Yes (JSON)
5. **Date calculation:** Use Make.com's `{{formatDate(addDays(now; -7); "YYYY-MM-DD")}}` for the 7-days-ago date

### Date Filter in Make.com

The `$where` clause needs the date dynamically calculated:

```
$where=issue_date > '{{formatDate(addDays(now; -7); "YYYY-MM-DD")}}'
  AND work_class = 'Commercial'
```

In Make.com, this is constructed in the URL field of the HTTP module. Use the expression editor to insert the date function.

---

## Normalization

Each city may use different column names. Normalize to a standard format before passing to Claude or writing to Sheets:

| Standard Field  | Phoenix | Scottsdale | Chandler |
| --------------- | ------- | ---------- | -------- |
| `permit_number` | TBD     | TBD        | TBD      |
| `business_name` | TBD     | TBD        | TBD      |
| `address`       | TBD     | TBD        | TBD      |
| `permit_date`   | TBD     | TBD        | TBD      |
| `permit_type`   | TBD     | TBD        | TBD      |
| `description`   | TBD     | TBD        | TBD      |

**Fill in the TBD values** during setup by examining actual API responses from each city.

---

## Semi-Automated Sources (ACC / ADOR)

These are NOT API-accessible. They require a human to submit a public records request.

### Arizona Corporation Commission (ACC) — New Entity Filings

**Process:**

1. Email the ACC public records office requesting new entity filings in Maricopa County for the past week
2. ACC responds with a data extract (legally required under ARS 39-121)
3. Save the file to a designated Google Drive folder
4. Make.com watches the folder (Google Drive → Watch Files module)
5. When a new file appears, parse it (CSV/Excel) and process each entity through the new business qualification prompt
6. Write qualified entities to the New Business Leads sheet

**ACC Contact:** records@azcc.gov (or current public records email — verify on azcc.gov)
**Cadence:** Weekly

### Arizona Department of Revenue (ADOR) — New TPT Licenses

**Process:** Same as ACC, but monthly cadence.
**Contact:** Verify current public records contact on azdor.gov.
**Signal quality:** Higher than ACC — a TPT license means the business is actually operating, not just filed paperwork.

### SBA Loan Data

**Source:** data.sba.gov — quarterly bulk CSV downloads
**Process:**

1. Download the quarterly 7(a) and 504 loan data
2. Filter for Maricopa County zip codes (850xx, 852xx, 853xx)
3. Process through the new business qualification prompt
4. Write to New Business Leads sheet with source = "sba_loan"

**Cadence:** Quarterly
**Lag:** Data is typically 3-6 months old. Good for batch prospecting, not real-time detection.

---

## Cost

| Source                                    | Monthly Cost               |
| ----------------------------------------- | -------------------------- |
| Phoenix SODA API                          | $0                         |
| Scottsdale SODA API                       | $0                         |
| Chandler SODA API                         | $0                         |
| ACC public records                        | $0 (free under ARS 39-121) |
| ADOR public records                       | $0                         |
| SBA data download                         | $0                         |
| Claude API (qualifying ~50 entities/week) | ~$3-5                      |
| **Total**                                 | **~$3-5/mo**               |
