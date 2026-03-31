# SerpAPI Query Specifications — Pipeline 2 (Job Monitor)

**Purpose:** Exact API queries for the Make.com Job Posting Monitor scenario. These queries hit SerpAPI's Google Jobs endpoint, which aggregates from Indeed, LinkedIn, ZipRecruiter, Glassdoor, and direct company postings.

**Account:** SerpAPI Developer plan, $50/mo, 5,000 searches/month.
**Endpoint:** `https://serpapi.com/search`

---

## Authentication

All requests include the API key as a query parameter:

```
?api_key={SERPAPI_API_KEY}
```

In Make.com: use the HTTP module with the API key stored in a connection or scenario variable. Never hardcode.

---

## Query Template

```
GET https://serpapi.com/search
  ?engine=google_jobs
  &q={JOB_TITLE}
  &location=Phoenix, Arizona, United States
  &chips=date_posted:3days
  &api_key={SERPAPI_API_KEY}
```

### Parameters

| Parameter  | Value                             | Notes                                                            |
| ---------- | --------------------------------- | ---------------------------------------------------------------- |
| `engine`   | `google_jobs`                     | Google Jobs aggregation engine                                   |
| `q`        | Job title search term             | See list below                                                   |
| `location` | `Phoenix, Arizona, United States` | Covers greater Phoenix metro                                     |
| `chips`    | `date_posted:3days`               | Only recent postings. Options: `today`, `3days`, `week`, `month` |
| `api_key`  | Your SerpAPI key                  |                                                                  |

### Response Structure (key fields)

```json
{
  "jobs_results": [
    {
      "title": "Office Manager",
      "company_name": "Desert Breeze Plumbing",
      "location": "Phoenix, AZ",
      "description": "Full job description text...",
      "detected_extensions": {
        "posted_at": "2 days ago",
        "schedule_type": "Full-time",
        "salary": "$45,000–$55,000 a year"
      },
      "job_id": "eyJqb2JfdGl0bGUiOi...",
      "apply_options": [
        {
          "title": "Indeed",
          "link": "https://www.indeed.com/..."
        }
      ]
    }
  ],
  "search_metadata": {
    "status": "Success",
    "total_time_taken": 2.5
  }
}
```

**Fields to extract for the Google Sheet:**

- `company_name` → Column A (Company Name)
- `title` → Column B (Job Title Posted)
- `location` → Column C (Location)
- `"Google Jobs"` → Column D (Source)
- `description` → Pass to Claude for qualification
- `apply_options[0].link` → Column K (Job Posting URL)
- Date of scan → Column L (Date Found)

---

## The 8 Monitored Queries

Run all 8 daily. Each query = 1 SerpAPI credit. 8 queries/day × 30 days = 240 credits/month (well within the 5,000/month plan).

| #   | Query (`q` parameter)          | Rationale                                                      |
| --- | ------------------------------ | -------------------------------------------------------------- |
| 1   | `office manager`               | Classic owner bottleneck signal — hiring to offload everything |
| 2   | `operations manager`           | Direct operations pain signal                                  |
| 3   | `dispatcher`                   | Scheduling chaos — especially in home services and trades      |
| 4   | `scheduling coordinator`       | Scheduling chaos signal                                        |
| 5   | `customer service coordinator` | Manual communication + lead leakage                            |
| 6   | `office administrator`         | Owner bottleneck — the "everything" role                       |
| 7   | `front desk manager`           | Patient/client-facing scheduling + communication               |
| 8   | `service coordinator`          | Home services dispatch + scheduling                            |

### Optional Expansion Queries

Add these if the base 8 aren't generating enough volume:

| #   | Query                      | Rationale                                         |
| --- | -------------------------- | ------------------------------------------------- |
| 9   | `bookkeeper`               | Financial blindness — business is behind on books |
| 10  | `administrative assistant` | Owner bottleneck at smaller companies             |
| 11  | `receptionist`             | Manual communication — phone chaos                |
| 12  | `project coordinator`      | Contractor/trades — team invisibility             |

---

## Deduplication Strategy

SerpAPI returns a `job_id` field per result. This is a base64-encoded string unique to each posting.

**Dedup key:** `SHA256(company_name + title + location)` — more stable than `job_id` which can change across queries.

**Make.com Data Store check:**

1. Before processing each job, hash `company_name + title + location`
2. Check `seen_jobs` Data Store: does this key exist?
3. If yes → skip (filter module, no operation cost)
4. If no → process through Claude → write to Data Store after qualification

---

## Pagination

Google Jobs results are NOT paginated in the traditional sense. SerpAPI returns up to ~15 results per query. For Phoenix-specific job titles, this is typically sufficient — most queries return 5-15 results.

If you need more results, you can use the `start` parameter:

```
&start=10  # Skip first 10, get next page
```

For our use case, a single page per query is enough. Save the credits.

---

## Error Handling

| HTTP Status                | Meaning              | Action                                    |
| -------------------------- | -------------------- | ----------------------------------------- |
| 200                        | Success              | Process results                           |
| 200 + empty `jobs_results` | No matching jobs     | Normal — skip, no error                   |
| 401                        | Invalid API key      | Send alert email via Gmail, stop scenario |
| 429                        | Rate limited         | Retry after 60 seconds                    |
| 500+                       | SerpAPI server error | Retry once, then skip this run            |

In Make.com: add an error handler route on the HTTP module. Route 429/500 errors to a Gmail alert email. Route 401 to a "break" directive (stops and alerts).

---

## Cost Calculations

| Scenario                         | Credits/Month | Cost                    |
| -------------------------------- | ------------- | ----------------------- |
| 8 queries × 1 run/day × 30 days  | 240           | Included in $50/mo plan |
| 12 queries × 1 run/day × 30 days | 360           | Included in $50/mo plan |
| 8 queries × 2 runs/day × 30 days | 480           | Included in $50/mo plan |
| **Max budget**                   | **5,000**     | **$50/mo**              |

We're using <10% of the plan capacity. Room to add more queries or increase frequency without additional cost.

---

## Craigslist RSS (Supplementary)

Free. No API key needed. Monitored via Make.com's RSS module.

### RSS Feed URLs

| #   | Job Title              | Craigslist RSS URL                                                                  |
| --- | ---------------------- | ----------------------------------------------------------------------------------- |
| 1   | office manager         | `https://phoenix.craigslist.org/search/jjj?query=office+manager&format=rss`         |
| 2   | dispatcher             | `https://phoenix.craigslist.org/search/jjj?query=dispatcher&format=rss`             |
| 3   | scheduling coordinator | `https://phoenix.craigslist.org/search/jjj?query=scheduling+coordinator&format=rss` |
| 4   | office administrator   | `https://phoenix.craigslist.org/search/jjj?query=office+administrator&format=rss`   |

### RSS Response Fields

- `title` — Job title (often includes company name)
- `link` — URL to the Craigslist posting
- `pubDate` — Publication date
- `description` — First ~300 characters of the posting

**Limitation:** Craigslist postings are often anonymous (no company name). The description is truncated. Signal quality is lower than Google Jobs, but it catches the smallest businesses that only post on Craigslist.

In Make.com: use the "Watch RSS Feed Items" module, set to check every 6 hours. New items trigger the same Claude qualification as SerpAPI results, but with `source: "craigslist"` and the caveat that company name may be "Anonymous" or extracted from the title.
